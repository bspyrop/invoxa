"""
Settings page.

Lets the user configure Google Drive folder paths, report names,
default currency, and manage their account.
"""

from __future__ import annotations

import streamlit as st

from auth.firebase_auth import sign_out
from services.firestore import get_ai_usage, get_total_ai_cost, save_categories, update_user_settings
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

    # ---- AI Cost Monitoring ----
    st.subheader("🤖 AI Cost Monitoring")
    st.caption("Token usage and USD cost for every OpenAI API call made by Invoxa.")

    total_cost = get_total_ai_cost(uid)
    usage_logs = get_ai_usage(uid, limit=200)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total AI Cost", f"${total_cost:.4f}")
    c2.metric("Total API Calls", len(usage_logs))

    if usage_logs:
        import pandas as pd

        df = pd.DataFrame(usage_logs)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
        df["cost_usd"]  = df["cost_usd"].apply(lambda x: f"${x:.5f}")

        # Per-model summary
        df_raw = pd.DataFrame(usage_logs)
        summary = (
            df_raw.groupby("model")
            .agg(
                calls=("cost_usd", "count"),
                total_tokens=("total_tokens", "sum"),
                total_cost=("cost_usd", "sum"),
            )
            .reset_index()
        )
        summary["total_cost"] = summary["total_cost"].apply(lambda x: f"${x:.5f}")
        summary.columns = ["Model", "Calls", "Total Tokens", "Total Cost"]
        c3.metric("Models Used", len(summary))

        st.markdown("**By Model**")
        st.dataframe(summary, use_container_width=True, hide_index=True)

        # Per-action summary
        action_summary = (
            df_raw.groupby("action")
            .agg(
                calls=("cost_usd", "count"),
                total_cost=("cost_usd", "sum"),
            )
            .reset_index()
        )
        action_summary["total_cost"] = action_summary["total_cost"].apply(lambda x: f"${x:.5f}")
        action_summary.columns = ["Action", "Calls", "Total Cost"]

        st.markdown("**By Action**")
        st.dataframe(action_summary, use_container_width=True, hide_index=True)

        # Full log
        with st.expander("Full usage log (last 200 calls)"):
            cols = ["timestamp", "model", "action", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"]
            cols = [c for c in cols if c in df.columns]
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
    else:
        st.info("No AI usage recorded yet. Upload an invoice or use the Chat to get started.")

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
