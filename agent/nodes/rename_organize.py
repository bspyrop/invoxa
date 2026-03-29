"""
LangGraph node: rename_and_organize

After the user approves (or edits) the extracted data and suggested filename,
this node renames the Drive file and moves it to the correct month folder.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import streamlit as st

from agent.state import AgentState, InvoiceData
from services.firestore import log_activity, log_error, save_invoice
from services.google_drive import (
    get_month_folder_id,
    rename_and_move_file,
    rename_file,
)

logger = logging.getLogger(__name__)


def rename_and_organize(state: AgentState) -> AgentState:
    """
    Rename the current invoice file in Google Drive and update Firestore.

    Reads:
      state["user_id"]
      state["user_approved_data"]  — InvoiceData after user review (may be edited)
      state["suggested_filename"]  — filename approved by the user
      state["current_file_index"]  — which invoice in extracted_data is being processed
      state["extracted_data"]
      state["month"], state["year"]

    Writes:
      state["extracted_data"]  — updates renamed_filename for the processed invoice
      state["renamed_files"]   — appends {old_name, new_name, drive_id}
      state["pending_approval"] — set to False
      state["current_file_index"] — incremented to move to the next invoice
    """
    uid      = state.get("user_id", "")
    approved = state.get("user_approved_data") or {}
    filename = state.get("suggested_filename", "")
    idx      = state.get("current_file_index", 0)
    month    = state.get("month", "")
    year     = state.get("year", "")

    extracted: List[InvoiceData] = list(state.get("extracted_data", []))
    renamed:   List[Dict[str, str]] = list(state.get("renamed_files", []))

    if not filename or idx >= len(extracted):
        return {
            **state,
            "pending_approval":   False,
            "current_file_index": idx + 1,
        }

    invoice  = {**extracted[idx], **approved}
    file_id  = invoice.get("drive_file_id", "")
    old_name = invoice.get("original_filename", "")

    creds = st.session_state.get("google_credentials")
    if creds is None:
        msg = "Google credentials missing — cannot rename files."
        log_error(uid, "rename_and_organize", msg)
        return {**state, "error": msg, "pending_approval": False}

    root_folder_name: str = st.session_state.get("expenses_root_folder", "Expenses")

    try:
        folder_id = get_month_folder_id(
            creds,
            root_folder_name=root_folder_name,
            month=month,
            year=year,
            create_if_missing=True,
        )
    except Exception as exc:
        msg = f"Failed to resolve month folder: {exc}"
        logger.error(msg)
        log_error(uid, "rename_and_organize", str(exc))
        folder_id = None

    success = False
    if file_id:
        if folder_id:
            success = rename_and_move_file(creds, file_id, filename, folder_id)
        else:
            success = rename_file(creds, file_id, filename)

    if success:
        invoice["renamed_filename"] = filename
        extracted[idx] = invoice  # type: ignore[index]
        renamed.append({"old_name": old_name, "new_name": filename, "drive_id": file_id})
        # Persist updated filename to Firestore
        save_invoice(uid, file_id, invoice)
        log_activity(
            uid,
            "invoice_renamed",
            {"old_name": old_name, "new_name": filename, "drive_file_id": file_id},
        )
        logger.info("Renamed '%s' → '%s'", old_name, filename)
    else:
        logger.warning("Rename failed for file_id=%s, old_name=%s", file_id, old_name)
        log_error(uid, "rename_and_organize", "Drive rename failed", {"file_id": file_id})

    return {
        **state,
        "extracted_data":     extracted,
        "renamed_files":      renamed,
        "pending_approval":   False,
        "current_file_index": idx + 1,
        "error":              None,
    }
