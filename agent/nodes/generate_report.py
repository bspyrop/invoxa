"""
LangGraph node: generate_monthly_report

Creates or updates a Google Sheets spreadsheet with the monthly expense
report and a year-summary tab.
"""

from __future__ import annotations

import logging

import streamlit as st

from agent.state import AgentState
from services.firestore import get_invoices_for_month, get_invoices_for_year, log_activity, log_error
from services.google_drive import get_or_create_folder, find_folder
from services.google_sheets import (
    generate_monthly_report as _sheet_monthly,
    generate_year_summary,
    get_or_create_spreadsheet,
    get_spreadsheet_url,
)

logger = logging.getLogger(__name__)


def generate_report(state: AgentState) -> AgentState:
    """
    Build (or refresh) the Google Sheets report for the selected month/year.

    Reads:
      state["user_id"]
      state["month"], state["year"]

    Writes:
      state["report_url"] — shareable Sheets URL
      state["sheet_id"]   — spreadsheet ID
      state["error"]      — error string on failure
    """
    uid   = state.get("user_id", "")
    month = state.get("month", "")
    year  = state.get("year", "")

    creds = st.session_state.get("google_credentials")
    if creds is None:
        msg = "Google credentials missing — cannot write to Sheets."
        log_error(uid, "generate_report", msg)
        return {**state, "error": msg}

    report_title: str = st.session_state.get("report_name", f"Expenses Report {year}")

    # Fetch invoice data
    try:
        monthly_invoices = get_invoices_for_month(uid, month, year)
        yearly_invoices  = get_invoices_for_year(uid, year)
    except Exception as exc:
        msg = f"Failed to load invoices from Firestore: {exc}"
        logger.error(msg)
        log_error(uid, "generate_report", str(exc))
        return {**state, "error": msg}

    if not monthly_invoices:
        msg = f"No processed invoices found for {month} {year}. Process invoices first."
        return {**state, "error": msg}

    # Get or create spreadsheet
    try:
        spreadsheet_id = get_or_create_spreadsheet(creds, report_title)
    except Exception as exc:
        msg = f"Could not access/create spreadsheet '{report_title}': {exc}"
        logger.error(msg)
        log_error(uid, "generate_report", str(exc))
        return {**state, "error": msg}

    # Move spreadsheet into the Expenses folder
    root_folder = st.session_state.get("expenses_root_folder", "Expenses")
    try:
        _move_sheet_to_expenses_folder(creds, spreadsheet_id, root_folder)
        logger.info("Report moved to '%s' folder.", root_folder)
    except Exception as exc:
        logger.error("Could not move report to Expenses folder: %s", exc)
        log_error(uid, "generate_report:move", str(exc))

    # Write monthly tab
    try:
        _sheet_monthly(creds, spreadsheet_id, month, year, monthly_invoices)
    except Exception as exc:
        msg = f"Failed to write monthly tab: {exc}"
        logger.error(msg)
        log_error(uid, "generate_report", str(exc))
        return {**state, "error": msg}

    # Write year summary tab
    try:
        generate_year_summary(creds, spreadsheet_id, year, yearly_invoices)
    except Exception as exc:
        logger.warning("Year summary tab failed (non-fatal): %s", exc)

    report_url = get_spreadsheet_url(spreadsheet_id)
    log_activity(
        uid,
        "report_generated",
        {"month": month, "year": year, "spreadsheet_id": spreadsheet_id},
    )
    logger.info("Report generated: %s", report_url)

    return {
        **state,
        "report_url": report_url,
        "sheet_id":   spreadsheet_id,
        "error":      None,
    }


def _move_sheet_to_expenses_folder(creds, spreadsheet_id: str, root_folder_name: str) -> None:
    """Move the report spreadsheet into the Expenses root folder in Drive."""
    from googleapiclient.discovery import build

    # Get the Expenses folder ID (create if missing)
    folder_id = find_folder(creds, root_folder_name)
    if not folder_id:
        folder_id = get_or_create_folder(creds, root_folder_name)
    if not folder_id:
        raise RuntimeError(f"Could not find or create folder '{root_folder_name}'")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    # Get current parents of the spreadsheet
    file_meta   = service.files().get(fileId=spreadsheet_id, fields="parents").execute()
    current_parents = file_meta.get("parents", [])

    # Already in the right folder — nothing to do
    if folder_id in current_parents:
        logger.info("Report already in '%s' folder.", root_folder_name)
        return

    old_parents = ",".join(current_parents)
    service.files().update(
        fileId=spreadsheet_id,
        addParents=folder_id,
        removeParents=old_parents,
        fields="id, parents",
    ).execute()
    logger.info("Report spreadsheet moved to '%s' (id=%s).", root_folder_name, folder_id)
