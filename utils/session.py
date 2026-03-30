"""
Streamlit session state helpers for Invoxa.

Provides typed getters/setters so all pages share a consistent
session state schema.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from utils.helpers import current_month_year


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_session() -> None:
    """
    Initialise all required session state keys with safe default values.
    Call this at the top of every page.
    """
    month, year = current_month_year()

    defaults: Dict[str, Any] = {
        # Auth
        "user":               None,
        "access_token":       None,
        "refresh_token":      None,
        "id_token":           None,
        "google_credentials": None,
        # Month/year selector
        "selected_month":     month,
        "selected_year":      year,
        # Agent state
        "agent_invoices":     [],       # DriveFile list from list_invoices node
        "extracted_data":     [],       # InvoiceData list
        "current_file_index": 0,
        "anomaly_warnings":   [],
        "report_url":         None,
        "sheet_id":           None,
        # Chat
        "chat_history":       [],
        # Settings
        "expenses_root_folder": "Expenses",
        "report_name":          f"Expenses Report {year}",
        "default_currency":     "EUR",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def get_user() -> Optional[Dict[str, Any]]:
    """Return the current user dict or None."""
    return st.session_state.get("user")


def get_uid() -> str:
    """Return the Firebase UID of the current user, or empty string."""
    user = st.session_state.get("user")
    return user.get("uid", "") if user else ""


def get_google_credentials():
    """Return the google.oauth2.credentials.Credentials object or None."""
    return st.session_state.get("google_credentials")


# ---------------------------------------------------------------------------
# Month / year selector
# ---------------------------------------------------------------------------

def get_selected_month() -> str:
    """Return the currently selected month name."""
    return st.session_state.get("selected_month", current_month_year()[0])


def get_selected_year() -> str:
    """Return the currently selected year string."""
    return st.session_state.get("selected_year", current_month_year()[1])


def set_selected_month(month: str) -> None:
    """Set the selected month in session state."""
    st.session_state["selected_month"] = month


def set_selected_year(year: str) -> None:
    """Set the selected year in session state."""
    st.session_state["selected_year"] = year


# ---------------------------------------------------------------------------
# Invoice processing state
# ---------------------------------------------------------------------------

def get_agent_invoices() -> List[Dict[str, Any]]:
    """Return the list of Drive file metadata dicts from the last Drive scan."""
    return st.session_state.get("agent_invoices", [])


def set_agent_invoices(invoices: List[Dict[str, Any]]) -> None:
    """Store Drive file metadata returned by the list_invoices node."""
    st.session_state["agent_invoices"] = invoices


def get_extracted_data() -> List[Dict[str, Any]]:
    """Return the list of extracted InvoiceData dicts."""
    return st.session_state.get("extracted_data", [])


def set_extracted_data(data: List[Dict[str, Any]]) -> None:
    """Store extracted invoice data."""
    st.session_state["extracted_data"] = data


def get_current_file_index() -> int:
    """Return the index of the invoice currently being reviewed."""
    return st.session_state.get("current_file_index", 0)


def increment_file_index() -> None:
    """Move to the next invoice in the processing queue."""
    st.session_state["current_file_index"] = st.session_state.get("current_file_index", 0) + 1


def reset_processing_state() -> None:
    """Clear all invoice-processing state (after a run is complete)."""
    st.session_state["agent_invoices"]     = []
    st.session_state["extracted_data"]     = []
    st.session_state["current_file_index"] = 0
    st.session_state["anomaly_warnings"]   = []


# ---------------------------------------------------------------------------
# Anomaly warnings
# ---------------------------------------------------------------------------

def get_anomaly_warnings() -> List[Dict[str, Any]]:
    """Return the list of anomaly warnings from the last run."""
    return st.session_state.get("anomaly_warnings", [])


def set_anomaly_warnings(warnings: List[Dict[str, Any]]) -> None:
    """Store anomaly warnings."""
    st.session_state["anomaly_warnings"] = warnings


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def get_chat_history() -> List[Dict[str, str]]:
    """Return the conversation history [{role, content}]."""
    return st.session_state.get("chat_history", [])


def append_chat_message(role: str, content: str) -> None:
    """Append a single message to the chat history."""
    history = st.session_state.get("chat_history", [])
    history.append({"role": role, "content": content})
    st.session_state["chat_history"] = history


def clear_chat_history() -> None:
    """Wipe the chat conversation history."""
    st.session_state["chat_history"] = []


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def get_report_url() -> Optional[str]:
    """Return the URL of the last generated report."""
    return st.session_state.get("report_url")


def set_report_url(url: str) -> None:
    """Store the report URL."""
    st.session_state["report_url"] = url


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_expenses_root_folder() -> str:
    """Return the configured root folder name (default 'Expenses')."""
    return st.session_state.get("expenses_root_folder", "Expenses")


def get_report_name(year: Optional[str] = None) -> str:
    """Return the configured report spreadsheet name."""
    default = f"Expenses Report {year or get_selected_year()}"
    return st.session_state.get("report_name", default)


def get_default_currency() -> str:
    """Return the configured default currency (default 'EUR')."""
    return st.session_state.get("default_currency", "EUR")


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def get_user_categories() -> List[str]:
    """Return the user's active category list from session state."""
    from utils.helpers import CATEGORIES
    return st.session_state.get("user_categories", CATEGORIES)


def set_user_categories(categories: List[str]) -> None:
    """Store the user's category list in session state."""
    st.session_state["user_categories"] = categories
