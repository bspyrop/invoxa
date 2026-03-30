"""
Settings page.

Lets the user configure Google Drive folder paths, report names,
default currency, and manage their account.
"""

from __future__ import annotations

import streamlit as st

from auth.firebase_auth import sign_out
from services.firestore import save_categories, update_user_settings
from utils.helpers      import CATEGORIES
from utils.session      import get_uid, get_user_categories, set_user_categories


def render() -> None:
    """Render the Settings page."""
    st.title("⚙️ Settings")
    uid = get_uid()

    # ---- Google Drive configuration ----
    st.subheader("Google Drive")
    with st.form("drive_settings"):
        root_folder = st.text_input(
            "Expenses root folder name",
            value=st.session_state.get("expenses_root_folder", "Expenses"),
            help="The top-level folder in your Google Drive that contains monthly subfolders.",
        )
        st.caption("Example structure: **Expenses** / January 2025 / invoice.pdf")
        save_drive = st.form_submit_button("Save Drive Settings")

    if save_drive:
        st.session_state["expenses_root_folder"] = root_folder.strip() or "Expenses"
        update_user_settings(uid, {"expenses_root_folder": root_folder.strip()})
        st.success("Drive settings saved.")

    st.markdown("---")

    # ---- Google Sheets configuration ----
    st.subheader("Google Sheets Report")
    with st.form("sheets_settings"):
        year         = st.session_state.get("selected_year", "2025")
        report_name  = st.text_input(
            "Report spreadsheet name",
            value=st.session_state.get("report_name", f"Expenses Report {year}"),
            help="The Google Sheets spreadsheet that Invoxa will write reports into.",
        )
        save_sheets  = st.form_submit_button("Save Sheets Settings")

    if save_sheets:
        st.session_state["report_name"] = report_name.strip() or f"Expenses Report {year}"
        update_user_settings(uid, {"report_name": report_name.strip()})
        st.success("Sheets settings saved.")

    st.markdown("---")

    # ---- Default currency ----
    st.subheader("Currency & Display")
    with st.form("currency_settings"):
        currencies    = ["EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD"]
        default_curr  = st.session_state.get("default_currency", "EUR")
        currency_idx  = currencies.index(default_curr) if default_curr in currencies else 0
        currency      = st.selectbox("Default currency", currencies, index=currency_idx)
        save_currency = st.form_submit_button("Save Currency")

    if save_currency:
        st.session_state["default_currency"] = currency
        update_user_settings(uid, {"default_currency": currency})
        st.success(f"Default currency set to {currency}.")

    st.markdown("---")

    # ---- Category Labels ----
    st.subheader("Category Labels")
    st.caption(
        "These categories are used by GPT-4o when classifying invoices and in the HITL review form. "
        "Add, remove, or rename them — changes are saved to Firestore and applied immediately."
    )

    current_cats = get_user_categories()

    with st.form("category_settings"):
        cats_text = st.text_area(
            "Categories (one per line)",
            value="\n".join(current_cats),
            height=220,
            help="Enter one category per line. The last entry is used as the fallback.",
        )
        col_save, col_reset = st.columns(2)
        save_cats  = col_save.form_submit_button("Save Categories", type="primary", use_container_width=True)
        reset_cats = col_reset.form_submit_button("Reset to Defaults", use_container_width=True)

    if save_cats:
        new_cats = [c.strip() for c in cats_text.splitlines() if c.strip()]
        if len(new_cats) < 2:
            st.error("Please enter at least 2 categories.")
        else:
            try:
                save_categories(uid, new_cats)
                set_user_categories(new_cats)
                st.success(f"Saved {len(new_cats)} categories.")
            except Exception as exc:
                st.error(f"Could not save: {exc}")

    if reset_cats:
        try:
            save_categories(uid, CATEGORIES)
            set_user_categories(CATEGORIES)
            st.success("Categories reset to defaults.")
            st.rerun()
        except Exception as exc:
            st.error(f"Could not reset: {exc}")

    st.markdown("---")

    # ---- Account ----
    st.subheader("Account")
    user = st.session_state.get("user", {})
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**{user.get('displayName', 'Unknown')}**  \n{user.get('email', '')}")
    with col2:
        if user.get("photoURL"):
            st.image(user["photoURL"], width=48)

    st.markdown("")
    if st.button("🔓 Sign Out", type="secondary"):
        sign_out()
        st.rerun()

    st.markdown("---")
    st.caption("Invoxa v1.0 — Powered by LangGraph + GPT-4o + Google Drive")
