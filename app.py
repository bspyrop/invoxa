"""
Invoxa — AI-powered Expense Invoice Productivity Agent
Main Streamlit entry point.

Run locally:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from auth.firebase_auth import is_authenticated, render_login_page
from utils.session import get_uid, init_session

# ---------------------------------------------------------------------------
# Page configuration (must be first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Invoxa — Expense Agent",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Hide Streamlit's auto-generated multipage navigation (we use our own)
st.markdown(
    "<style>[data-testid='stSidebarNav'] {display: none;}</style>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

init_session()

# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

if not is_authenticated():
    render_login_page()
    st.stop()

# Load user categories from Firestore into session state (once per session)
if "user_categories" not in st.session_state:
    from services.firestore import get_categories
    from utils.session import set_user_categories
    set_user_categories(get_categories(get_uid()))

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

PAGE_ICONS = {
    "Dashboard":       "🏠",
    "Upload Invoice":  "⬆️",
    "Monthly Report":  "📊",
    "Chat":            "💬",
    "Settings":        "⚙️",
}

PAGE_NAMES = list(PAGE_ICONS.keys())

# Handle quick-action nav targets from dashboard buttons
if "nav_target" in st.session_state:
    target = st.session_state.pop("nav_target")
    if target in PAGE_NAMES:
        st.session_state["current_page"] = target

# Default to Dashboard on first load
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "Dashboard"

with st.sidebar:
    current = st.session_state.get("current_page", "Dashboard")
    selected_page = st.radio(
        "Navigation",
        PAGE_NAMES,
        index=PAGE_NAMES.index(current),
        format_func=lambda x: f"{PAGE_ICONS[x]}  {x}",
        label_visibility="collapsed",
    )
    if selected_page != current:
        st.session_state["current_page"] = selected_page
        st.rerun()

    st.markdown("---")

    # Compact user card in sidebar
    user = st.session_state.get("user", {})
    if user.get("photoURL"):
        col_p, col_n = st.sidebar.columns([1, 3])
        with col_p:
            st.image(user["photoURL"], width=36)
        with col_n:
            st.caption(user.get("displayName", ""))
            st.caption(user.get("email", ""))
    else:
        st.sidebar.caption(user.get("email", ""))

# ---------------------------------------------------------------------------
# Render selected page
# ---------------------------------------------------------------------------

if selected_page == "Dashboard":
    from pages.dashboard import render
    render()

elif selected_page == "Upload Invoice":
    from pages.process_invoices import render
    render()

elif selected_page == "Monthly Report":
    from pages.monthly_report import render
    render()

elif selected_page == "Chat":
    from pages.chat import render
    render()

elif selected_page == "Settings":
    from pages.settings import render
    render()
