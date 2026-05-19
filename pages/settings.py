"""
Settings page.

Lets the user configure Google Drive folder paths, report names,
default currency, and manage their account.
"""

from __future__ import annotations

import streamlit as st

from auth.firebase_auth import get_google_sign_in_url, sign_out
from services.firestore import get_ai_usage, get_total_ai_cost, save_categories, update_user_settings
from styles.apply_theme import apply_theme
from styles.badges import badge
from utils.helpers      import CATEGORIES
from utils.session      import get_uid, get_user_categories, set_user_categories


def render() -> None:
    """Render the Settings page."""
    apply_theme()
    uid = get_uid()

    # ---- Header ----
    st.markdown("## Settings")
    st.markdown(
        '<p style="color:#6B7A99;font-size:13px;margin-top:-10px">'
        "Configure integrations, categories, and account preferences."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<hr style="border:none;border-top:0.5px solid #E2E6EF;margin:12px 0 20px">',
        unsafe_allow_html=True,
    )

    # ---- Google Drive configuration ----
    with st.expander("Google Drive", expanded=False):
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

    # ---- Google Sheets configuration ----
    with st.expander("Google Sheets Report", expanded=False):
        with st.form("sheets_settings"):
            year        = st.session_state.get("selected_year", "2025")
            report_name = st.text_input(
                "Report spreadsheet name",
                value=st.session_state.get("report_name", f"Expenses Report {year}"),
                help="The Google Sheets spreadsheet that Invoxa will write reports into.",
            )
            save_sheets = st.form_submit_button("Save Sheets Settings")
        if save_sheets:
            st.session_state["report_name"] = report_name.strip() or f"Expenses Report {year}"
            update_user_settings(uid, {"report_name": report_name.strip()})
            st.success("Sheets settings saved.")

    # ---- Default currency ----
    with st.expander("Currency & Display", expanded=False):
        with st.form("currency_settings"):
            currencies   = ["EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD"]
            default_curr = st.session_state.get("default_currency", "EUR")
            currency_idx = currencies.index(default_curr) if default_curr in currencies else 0
            currency     = st.selectbox("Default currency", currencies, index=currency_idx)
            save_currency = st.form_submit_button("Save Currency")
        if save_currency:
            st.session_state["default_currency"] = currency
            update_user_settings(uid, {"default_currency": currency})
            st.success(f"Default currency set to {currency}.")

    # ---- Category Labels ----
    with st.expander("Category Labels", expanded=False):
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

    # ---- AI Cost Monitoring ----
    with st.expander("AI Cost Monitoring", expanded=False):
        st.caption("Token usage and USD cost for every OpenAI API call made by Invoxa.")

        total_cost = get_total_ai_cost(uid)
        usage_logs = get_ai_usage(uid, limit=200)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total AI Cost", f"${total_cost:.4f}")
        c2.metric("Total API Calls", len(usage_logs))

        if usage_logs:
            import pandas as pd

            df     = pd.DataFrame(usage_logs)
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
            df["cost_usd"]  = df["cost_usd"].apply(lambda x: f"${x:.5f}")

            df_raw  = pd.DataFrame(usage_logs)
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

            with st.expander("Full usage log (last 200 calls)"):
                cols = ["timestamp", "model", "action", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"]
                cols = [c for c in cols if c in df.columns]
                st.dataframe(df[cols], use_container_width=True, hide_index=True)
        else:
            st.info("No AI usage recorded yet. Upload an invoice or use the Chat to get started.")

    # ---- Gmail Integration ----
    with st.expander("Gmail Integration", expanded=False):
        st.caption(
            "Invoxa scans for unread emails with PDF or image attachments. "
            "Processed emails are labelled `invoxa-processed` in Gmail and will not be scanned again."
        )

        creds           = st.session_state.get("google_credentials")
        gmail_connected = st.session_state.get("gmail_authorized", False)

        if gmail_connected:
            st.markdown(badge("Connected", "success"), unsafe_allow_html=True)
        else:
            st.markdown(badge("Not connected", "default"), unsafe_allow_html=True)
            st.caption("Your current session doesn't have Gmail access.")
            sign_in_url = get_google_sign_in_url()
            st.markdown(
                f'<a href="{sign_in_url}" target="_self">'
                '<button style="background:#1B2A4A; color:white; border:none; padding:8px 18px; '
                'font-size:13px; border-radius:7px; cursor:pointer; margin-top:6px; font-weight:500;">'
                "Grant Gmail access</button></a>",
                unsafe_allow_html=True,
            )

        st.markdown("")

        if st.button("Clear import history", type="secondary", key="_gmail_clear_btn"):
            st.session_state["_gmail_confirm_clear"] = True

        if st.session_state.get("_gmail_confirm_clear"):
            st.warning(
                "This will remove all Gmail import records from Invoxa and remove the "
                "`invoxa-processed` label from all emails — they will appear in future scans again."
            )
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Yes, clear everything", type="primary", use_container_width=True, key="_gmail_clear_yes"):
                    from services.firestore import clear_gmail_imports
                    from services.gmail_scanner import get_gmail_service, remove_label_from_all
                    try:
                        clear_gmail_imports(uid)
                    except Exception as exc:
                        st.error(f"Could not clear Firestore records: {exc}")
                    if creds:
                        try:
                            remove_label_from_all(get_gmail_service(creds))
                        except Exception:
                            pass
                    st.session_state.pop("_gmail_confirm_clear", None)
                    st.session_state.pop("gmail_candidates", None)
                    st.success("Import history cleared.")
                    st.rerun()
            with col_no:
                if st.button("Cancel", use_container_width=True, key="_gmail_clear_no"):
                    st.session_state.pop("_gmail_confirm_clear", None)
                    st.rerun()

    # ---- Search Index ----
    with st.expander("Search Index", expanded=False):
        st.caption(
            "Invoxa uses a local ChromaDB vector index for fast semantic search. "
            "The index is rebuilt automatically on first use, but you can trigger a manual rebuild here."
        )
        try:
            from services.chroma_service import get_collection_count, RAG_THRESHOLD
            from services.firestore import get_all_invoices

            invoice_count = len(get_all_invoices(uid))
            chunk_count   = get_collection_count(uid)
            expected_min  = invoice_count * 2  # at least header + pointer per invoice

            si1, si2, si3 = st.columns(3)
            si1.metric("Indexed chunks", chunk_count)
            si2.metric("Invoices in Firestore", invoice_count)
            si3.metric("RAG threshold", RAG_THRESHOLD)

            if invoice_count == 0:
                st.info("No invoices yet — nothing to index.")
            elif chunk_count == 0:
                st.warning("Index is empty. Click **Rebuild index** to populate it.")
            elif chunk_count < expected_min:
                st.warning(
                    f"Index may be incomplete — {chunk_count} chunks for {invoice_count} invoices "
                    f"(expected at least {expected_min}). Consider rebuilding."
                )
            else:
                st.success(f"Index looks healthy — {chunk_count} chunks for {invoice_count} invoices.")

            if invoice_count > 0:
                if st.button("Rebuild index", type="primary", key="_chroma_rebuild_btn"):
                    from services.chroma_service import get_chroma_client, rebuild_if_empty

                    # Drop the existing collection so rebuild_if_empty re-indexes everything
                    try:
                        get_chroma_client().delete_collection(f"invoices_{uid}")
                    except Exception:
                        pass

                    progress_bar = st.progress(0, text="Rebuilding search index…")

                    def _on_progress(current: int, total: int) -> None:
                        pct = int((current / total) * 100)
                        progress_bar.progress(pct / 100, text=f"Indexing… {current}/{total}")

                    rebuild_if_empty(uid, progress_callback=_on_progress)
                    progress_bar.empty()
                    st.success("Index rebuilt successfully.")
                    st.rerun()

        except Exception as exc:
            st.warning(f"Search index unavailable: {exc}")

    # ---- Account ----
    with st.expander("Account", expanded=True):
        user = st.session_state.get("user", {})
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**{user.get('displayName', 'Unknown')}**  \n{user.get('email', '')}")
        with col2:
            if user.get("photoURL"):
                st.image(user["photoURL"], width=48)

        st.markdown("")
        if st.button("Sign Out", type="secondary"):
            sign_out()
            st.rerun()

    st.markdown("")
    st.caption("Invoxa v1.0 — Powered by LangGraph + GPT-4o + Google Drive")
