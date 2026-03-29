"""
LangGraph node: list_invoices

Connects to Google Drive and returns the invoice files found in the
Expenses/{Month YYYY}/ subfolder.
"""

from __future__ import annotations

import logging

import streamlit as st

from agent.state import AgentState
from services.google_drive import get_month_folder_id, list_invoices_in_folder
from services.firestore import log_error

logger = logging.getLogger(__name__)


def list_invoices(state: AgentState) -> AgentState:
    """
    Enumerate invoice files in Google Drive for the selected month/year.

    Reads:
      state["user_id"], state["month"], state["year"]

    Writes:
      state["invoices"] — list of DriveFile metadata dicts
      state["error"]    — error message string if something went wrong
    """
    uid   = state.get("user_id", "")
    month = state.get("month", "")
    year  = state.get("year", "")

    creds = st.session_state.get("google_credentials")
    if creds is None:
        msg = "Google credentials not found in session state. Please sign in again."
        logger.error(msg)
        return {**state, "invoices": [], "error": msg}

    root_folder_name: str = st.session_state.get("expenses_root_folder", "Expenses")

    try:
        folder_id = get_month_folder_id(
            creds,
            root_folder_name=root_folder_name,
            month=month,
            year=year,
            create_if_missing=False,
        )
    except Exception as exc:
        msg = f"Error resolving Drive folder for {month} {year}: {exc}"
        logger.error(msg)
        log_error(uid, "list_invoices", str(exc))
        return {**state, "invoices": [], "error": msg}

    if not folder_id:
        msg = f"No folder found in Google Drive for '{month} {year}' under '{root_folder_name}'."
        logger.warning(msg)
        return {**state, "invoices": [], "error": msg}

    try:
        files = list_invoices_in_folder(creds, folder_id)
    except Exception as exc:
        msg = f"Failed to list files in '{month} {year}': {exc}"
        logger.error(msg)
        log_error(uid, "list_invoices", str(exc))
        return {**state, "invoices": [], "error": msg}

    logger.info("list_invoices: found %d files for %s %s", len(files), month, year)
    return {**state, "invoices": files, "error": None}
