"""
Google Drive API service wrapper for Invoxa.

All methods accept a google.oauth2.credentials.Credentials object so they
can be called with the user's OAuth2 tokens stored in session state.

Expected Drive folder structure:
  Expenses/
    January 2025/
      invoice.pdf
    February 2025/
      ...
"""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

INVOICE_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
}


def _drive_service(creds: Credentials):
    """Build and return an authenticated Drive v3 service client."""
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Folder helpers
# ---------------------------------------------------------------------------

def find_folder(creds: Credentials, name: str, parent_id: Optional[str] = None) -> Optional[str]:
    """
    Search for a folder by name, optionally within a parent folder.

    Args:
        creds:     OAuth2 credentials.
        name:      Exact folder name to search for.
        parent_id: Optional Drive folder ID to restrict the search.

    Returns:
        The folder's Drive ID, or None if not found.
    """
    service = _drive_service(creds)
    query   = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    try:
        result = service.files().list(q=query, fields="files(id, name)").execute()
        files  = result.get("files", [])
        return files[0]["id"] if files else None
    except HttpError as exc:
        logger.error("find_folder('%s') failed: %s", name, exc)
        return None


def get_or_create_folder(creds: Credentials, name: str, parent_id: Optional[str] = None) -> str:
    """
    Return an existing folder's ID or create it if absent.

    Args:
        creds:     OAuth2 credentials.
        name:      Folder name.
        parent_id: Parent folder ID (root if None).

    Returns:
        Drive folder ID.
    """
    existing = find_folder(creds, name, parent_id)
    if existing:
        return existing

    service = _drive_service(creds)
    meta: Dict[str, Any] = {
        "name":     name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]

    try:
        folder = service.files().create(body=meta, fields="id").execute()
        return folder["id"]
    except HttpError as exc:
        raise RuntimeError(f"Could not create folder '{name}': {exc}") from exc


# ---------------------------------------------------------------------------
# File listing
# ---------------------------------------------------------------------------

def list_invoices_in_folder(creds: Credentials, folder_id: str) -> List[Dict[str, Any]]:
    """
    List all invoice files (PDF / images) inside a specific Drive folder.

    Args:
        creds:     OAuth2 credentials.
        folder_id: ID of the month subfolder.

    Returns:
        List of file metadata dicts: id, name, mimeType, size, createdTime, webViewLink.
    """
    service = _drive_service(creds)
    mime_filter = " or ".join(f"mimeType = '{m}'" for m in INVOICE_MIME_TYPES)
    query       = f"'{folder_id}' in parents and ({mime_filter}) and trashed = false"

    files: List[Dict[str, Any]] = []
    page_token: Optional[str]   = None

    try:
        while True:
            kwargs: Dict[str, Any] = {
                "q":      query,
                "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, webViewLink)",
            }
            if page_token:
                kwargs["pageToken"] = page_token

            result     = service.files().list(**kwargs).execute()
            files     += result.get("files", [])
            page_token = result.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        logger.error("list_invoices_in_folder('%s') failed: %s", folder_id, exc)

    return files


def get_month_folder_id(
    creds: Credentials,
    root_folder_name: str,
    month: str,
    year: str,
    create_if_missing: bool = False,
) -> Optional[str]:
    """
    Resolve the Drive ID for Expenses/{Month YYYY}/.

    Args:
        creds:              OAuth2 credentials.
        root_folder_name:   Name of the root expenses folder (e.g. "Expenses").
        month:              Full month name (e.g. "January").
        year:               Four-digit year string (e.g. "2025").
        create_if_missing:  If True, creates the folder hierarchy if absent.

    Returns:
        Folder ID or None.
    """
    root_id = find_folder(creds, root_folder_name)
    if not root_id:
        if not create_if_missing:
            logger.warning("Root folder '%s' not found in Drive.", root_folder_name)
            return None
        root_id = get_or_create_folder(creds, root_folder_name)

    month_name = f"{month} {year}"
    if create_if_missing:
        return get_or_create_folder(creds, month_name, parent_id=root_id)
    return find_folder(creds, month_name, parent_id=root_id)


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(HttpError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def download_file(creds: Credentials, file_id: str) -> bytes:
    """
    Download a file from Google Drive as raw bytes.

    Args:
        creds:   OAuth2 credentials.
        file_id: Drive file ID.

    Returns:
        File content as bytes.

    Raises:
        HttpError after 3 retries if the download fails.
    """
    service = _drive_service(creds)
    request = service.files().get_media(fileId=file_id)
    buffer  = io.BytesIO()
    dl      = MediaIoBaseDownload(buffer, request)
    done    = False
    while not done:
        _, done = dl.next_chunk()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# File rename / move
# ---------------------------------------------------------------------------

def rename_file(creds: Credentials, file_id: str, new_name: str) -> bool:
    """
    Rename a Drive file in-place (does not move it).

    Args:
        creds:    OAuth2 credentials.
        file_id:  Drive file ID.
        new_name: The new filename (including extension).

    Returns:
        True on success, False on failure.
    """
    service = _drive_service(creds)
    try:
        service.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id, name",
        ).execute()
        return True
    except HttpError as exc:
        logger.error("rename_file('%s' → '%s') failed: %s", file_id, new_name, exc)
        return False


def move_file_to_folder(creds: Credentials, file_id: str, target_folder_id: str) -> bool:
    """
    Move a Drive file to a different folder (updates parent).

    Args:
        creds:            OAuth2 credentials.
        file_id:          Drive file ID.
        target_folder_id: ID of the destination folder.

    Returns:
        True on success, False on failure.
    """
    service = _drive_service(creds)
    try:
        # Get current parents
        file_meta = service.files().get(fileId=file_id, fields="parents").execute()
        old_parents = ",".join(file_meta.get("parents", []))
        service.files().update(
            fileId=file_id,
            addParents=target_folder_id,
            removeParents=old_parents,
            fields="id, parents",
        ).execute()
        return True
    except HttpError as exc:
        logger.error("move_file('%s' → '%s') failed: %s", file_id, target_folder_id, exc)
        return False


def rename_and_move_file(
    creds: Credentials,
    file_id: str,
    new_name: str,
    target_folder_id: str,
) -> bool:
    """
    Rename a file AND move it to a target folder atomically (single API call).

    Args:
        creds:            OAuth2 credentials.
        file_id:          Drive file ID.
        new_name:         The new filename.
        target_folder_id: ID of the destination folder.

    Returns:
        True on success, False on failure.
    """
    service = _drive_service(creds)
    try:
        file_meta   = service.files().get(fileId=file_id, fields="parents").execute()
        old_parents = ",".join(file_meta.get("parents", []))
        service.files().update(
            fileId=file_id,
            body={"name": new_name},
            addParents=target_folder_id,
            removeParents=old_parents,
            fields="id, name, parents",
        ).execute()
        return True
    except HttpError as exc:
        logger.error("rename_and_move_file('%s') failed: %s", file_id, exc)
        return False


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def find_duplicate_files(
    files: List[Dict[str, Any]],
    supplier_name: str,
    amount: float,
    tolerance: float = 0.01,
) -> List[Dict[str, Any]]:
    """
    Scan a list of already-processed invoices for potential duplicates.

    A duplicate is defined as having the same supplier name and an amount
    within `tolerance` of the new invoice's amount.

    Args:
        files:         List of processed invoice dicts (from Firestore).
        supplier_name: Supplier name of the new invoice.
        amount:        Amount of the new invoice.
        tolerance:     Fractional tolerance for amount comparison.

    Returns:
        List of matching invoice dicts that look like duplicates.
    """
    duplicates = []
    for inv in files:
        if inv.get("supplier_name", "").lower() != supplier_name.lower():
            continue
        existing_amount = float(inv.get("amount", 0) or 0)
        if existing_amount == 0:
            continue
        diff = abs(existing_amount - amount) / max(existing_amount, 1)
        if diff <= tolerance:
            duplicates.append(inv)
    return duplicates
