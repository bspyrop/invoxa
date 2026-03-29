"""
Google Sheets API service wrapper for Invoxa.

Handles creation and updates of the "Expenses Report {YYYY}" spreadsheet
including per-month tabs and a Year Summary tab.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

MONTHS_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

INVOICE_TABLE_HEADERS = [
    "Date", "Supplier", "Category", "Description",
    "Amount", "Tax", "Currency", "Invoice Number",
]


def _sheets_service(creds: Credentials):
    """Build and return an authenticated Sheets v4 service client."""
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _drive_service(creds: Credentials):
    """Build and return an authenticated Drive v3 service client (for file search)."""
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Spreadsheet management
# ---------------------------------------------------------------------------

def find_spreadsheet(creds: Credentials, title: str) -> Optional[str]:
    """
    Search the user's Drive for a spreadsheet with the given title.

    Returns:
        The spreadsheet ID or None if not found.
    """
    drive = _drive_service(creds)
    query = (
        f"name = '{title}' "
        "and mimeType = 'application/vnd.google-apps.spreadsheet' "
        "and trashed = false"
    )
    try:
        result = drive.files().list(q=query, fields="files(id, name)").execute()
        files  = result.get("files", [])
        return files[0]["id"] if files else None
    except HttpError as exc:
        logger.error("find_spreadsheet('%s') failed: %s", title, exc)
        return None


def create_spreadsheet(creds: Credentials, title: str) -> str:
    """
    Create a new Google Spreadsheet with the given title.

    Returns:
        The new spreadsheet ID.
    """
    sheets = _sheets_service(creds)
    body   = {"properties": {"title": title}}
    try:
        result = sheets.spreadsheets().create(body=body, fields="spreadsheetId").execute()
        return result["spreadsheetId"]
    except HttpError as exc:
        raise RuntimeError(f"Could not create spreadsheet '{title}': {exc}") from exc


def get_or_create_spreadsheet(creds: Credentials, title: str) -> str:
    """Return an existing spreadsheet ID or create a new one."""
    existing = find_spreadsheet(creds, title)
    return existing if existing else create_spreadsheet(creds, title)


def remove_sheet1_if_present(creds: Credentials, spreadsheet_id: str) -> None:
    """
    Delete the default 'Sheet1' tab if it still exists.
    Must only be called after at least one other tab has been added,
    otherwise Drive will refuse (can't delete the last sheet).
    """
    sheets = _sheets_service(creds)
    try:
        meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        all_sheets = meta.get("sheets", [])
        # Only delete if there is more than one tab
        if len(all_sheets) <= 1:
            return
        for s in all_sheets:
            props = s["properties"]
            if props["title"] == "Sheet1":
                sheets.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [{"deleteSheet": {"sheetId": props["sheetId"]}}]},
                ).execute()
                logger.info("Deleted default Sheet1 tab.")
                break
    except HttpError as exc:
        logger.warning("Could not delete Sheet1: %s", exc)


def get_spreadsheet_url(spreadsheet_id: str) -> str:
    """Return the browser URL for a given spreadsheet ID."""
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


# ---------------------------------------------------------------------------
# Sheet (tab) helpers
# ---------------------------------------------------------------------------

def get_existing_sheets(creds: Credentials, spreadsheet_id: str) -> Dict[str, int]:
    """
    Return a dict mapping sheet title → sheetId for all tabs.

    Args:
        creds:           OAuth2 credentials.
        spreadsheet_id:  Target spreadsheet ID.

    Returns:
        {sheet_title: sheet_gid}
    """
    sheets = _sheets_service(creds)
    try:
        meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        return {
            s["properties"]["title"]: s["properties"]["sheetId"]
            for s in meta.get("sheets", [])
        }
    except HttpError as exc:
        logger.error("get_existing_sheets failed: %s", exc)
        return {}


def add_sheet(creds: Credentials, spreadsheet_id: str, sheet_title: str) -> int:
    """
    Add a new tab to a spreadsheet and return its sheetId.

    Args:
        creds:           OAuth2 credentials.
        spreadsheet_id:  Target spreadsheet ID.
        sheet_title:     Name of the new tab.

    Returns:
        The new sheet's numeric sheetId.
    """
    sheets = _sheets_service(creds)
    body   = {"requests": [{"addSheet": {"properties": {"title": sheet_title}}}]}
    try:
        result = sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body
        ).execute()
        new_sheet = result["replies"][0]["addSheet"]["properties"]
        return new_sheet["sheetId"]
    except HttpError as exc:
        raise RuntimeError(f"Could not add sheet '{sheet_title}': {exc}") from exc


def clear_sheet(creds: Credentials, spreadsheet_id: str, sheet_title: str) -> None:
    """Clear all content in a sheet tab."""
    sheets = _sheets_service(creds)
    try:
        sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=sheet_title
        ).execute()
    except HttpError as exc:
        logger.error("clear_sheet('%s') failed: %s", sheet_title, exc)


def write_values(
    creds: Credentials,
    spreadsheet_id: str,
    range_name: str,
    values: List[List[Any]],
) -> None:
    """
    Write a 2-D list of values to a sheet range.

    Args:
        creds:           OAuth2 credentials.
        spreadsheet_id:  Target spreadsheet ID.
        range_name:      A1 notation range (e.g. "January 2025!A1").
        values:          Rows × columns list.
    """
    sheets = _sheets_service(creds)
    body   = {"values": values}
    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
    except HttpError as exc:
        logger.error("write_values('%s') failed: %s", range_name, exc)
        raise


def format_header_row(
    creds: Credentials, spreadsheet_id: str, sheet_id: int, num_cols: int
) -> None:
    """Bold and background-colour the first row of a sheet."""
    sheets = _sheets_service(creds)
    body   = {
        "requests": [
            {
                "repeatCell": {
                    "range": {
                        "sheetId":          sheet_id,
                        "startRowIndex":    0,
                        "endRowIndex":      1,
                        "startColumnIndex": 0,
                        "endColumnIndex":   num_cols,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 0.267, "green": 0.447, "blue": 0.769
                            },
                            "textFormat": {
                                "bold":            True,
                                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            },
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId":    sheet_id,
                        "dimension":  "COLUMNS",
                        "startIndex": 0,
                        "endIndex":   num_cols,
                    }
                }
            },
        ]
    }
    try:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body
        ).execute()
    except HttpError as exc:
        logger.error("format_header_row failed: %s", exc)


# ---------------------------------------------------------------------------
# High-level report generation
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(HttpError),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(3),
    reraise=True,
)
def generate_monthly_report(
    creds: Credentials,
    spreadsheet_id: str,
    month: str,
    year: str,
    invoices: List[Dict[str, Any]],
) -> None:
    """
    Create or overwrite the tab for a given month with all invoice data.

    Args:
        creds:           OAuth2 credentials.
        spreadsheet_id:  Target spreadsheet ID.
        month:           Month name (e.g. "January").
        year:            Four-digit year string.
        invoices:        List of invoice dicts from Firestore.
    """
    tab_title = f"{month} {year}"

    existing = get_existing_sheets(creds, spreadsheet_id)
    if tab_title in existing:
        clear_sheet(creds, spreadsheet_id, tab_title)
        sheet_id = existing[tab_title]
    else:
        sheet_id = add_sheet(creds, spreadsheet_id, tab_title)

    # ---- Invoice table ----
    rows: List[List[Any]] = [INVOICE_TABLE_HEADERS]
    for inv in invoices:
        rows.append(
            [
                inv.get("invoice_date", ""),
                inv.get("supplier_name", ""),
                inv.get("category", ""),
                inv.get("description", ""),
                inv.get("amount", 0),
                inv.get("tax_amount", 0),
                inv.get("currency", ""),
                inv.get("invoice_number", ""),
            ]
        )

    # ---- Summary ----
    total_amount = sum(float(i.get("amount", 0) or 0) for i in invoices)
    total_tax    = sum(float(i.get("tax_amount", 0) or 0) for i in invoices)

    category_totals: Dict[str, float] = {}
    for inv in invoices:
        cat = inv.get("category", "Other")
        category_totals[cat] = category_totals.get(cat, 0) + float(inv.get("amount", 0) or 0)

    supplier_totals: Dict[str, float] = {}
    for inv in invoices:
        sup = inv.get("supplier_name", "Unknown")
        supplier_totals[sup] = supplier_totals.get(sup, 0) + float(inv.get("amount", 0) or 0)

    # Blank row + summary header
    rows += [
        [],
        ["SUMMARY"],
        ["Total Expenses", total_amount],
        ["Total Tax",      total_tax],
        [],
        ["CATEGORY BREAKDOWN"],
    ]
    for cat, total in sorted(category_totals.items(), key=lambda x: x[1], reverse=True):
        rows.append([cat, total])

    rows += [[], ["SUPPLIER LIST"]]
    for sup, total in sorted(supplier_totals.items(), key=lambda x: x[1], reverse=True):
        rows.append([sup, total])

    write_values(creds, spreadsheet_id, f"{tab_title}!A1", rows)
    format_header_row(creds, spreadsheet_id, sheet_id, len(INVOICE_TABLE_HEADERS))
    # Remove the blank Sheet1 now that we have at least one real tab
    remove_sheet1_if_present(creds, spreadsheet_id)


def generate_year_summary(
    creds: Credentials,
    spreadsheet_id: str,
    year: str,
    all_invoices: List[Dict[str, Any]],
) -> None:
    """
    Create or overwrite the "Year Summary" tab with cross-month analysis.

    Args:
        creds:           OAuth2 credentials.
        spreadsheet_id:  Target spreadsheet ID.
        year:            Four-digit year string.
        all_invoices:    All invoices for this year (from Firestore).
    """
    tab_title = "Year Summary"
    existing  = get_existing_sheets(creds, spreadsheet_id)
    if tab_title in existing:
        clear_sheet(creds, spreadsheet_id, tab_title)
        sheet_id = existing[tab_title]
    else:
        sheet_id = add_sheet(creds, spreadsheet_id, tab_title)

    # Month-by-month totals
    monthly: Dict[str, float] = {m: 0.0 for m in MONTHS_ORDER}
    for inv in all_invoices:
        m = inv.get("month", "")
        if m in monthly:
            monthly[m] += float(inv.get("amount", 0) or 0)

    # Annual supplier totals
    supplier_totals: Dict[str, float] = {}
    for inv in all_invoices:
        sup = inv.get("supplier_name", "Unknown")
        supplier_totals[sup] = supplier_totals.get(sup, 0) + float(inv.get("amount", 0) or 0)

    # Annual category totals
    category_totals: Dict[str, float] = {}
    for inv in all_invoices:
        cat = inv.get("category", "Other")
        category_totals[cat] = category_totals.get(cat, 0) + float(inv.get("amount", 0) or 0)

    rows: List[List[Any]] = [
        [f"YEAR SUMMARY — {year}"],
        [],
        ["MONTH-BY-MONTH TOTALS"],
        ["Month", "Total Expenses"],
    ]
    for month in MONTHS_ORDER:
        rows.append([month, monthly[month]])

    rows += [
        [],
        ["ANNUAL CATEGORY BREAKDOWN"],
        ["Category", "Total"],
    ]
    for cat, total in sorted(category_totals.items(), key=lambda x: x[1], reverse=True):
        rows.append([cat, total])

    rows += [
        [],
        ["ALL SUPPLIERS — ANNUAL SPEND"],
        ["Supplier", "Total"],
    ]
    for sup, total in sorted(supplier_totals.items(), key=lambda x: x[1], reverse=True):
        rows.append([sup, total])

    write_values(creds, spreadsheet_id, f"{tab_title}!A1", rows)
    format_header_row(creds, spreadsheet_id, sheet_id, 2)
