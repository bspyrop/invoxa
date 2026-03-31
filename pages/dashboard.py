"""
Dashboard page — shows quick stats, recent activity, and quick-action buttons.
"""

from __future__ import annotations

import streamlit as st

from services.firestore import delete_invoice, get_invoices_for_month, get_recent_invoices, get_total_ai_cost
from utils.helpers import MONTHS, compute_monthly_stats, current_month_year, format_amount, year_range
from utils.session import get_uid


def render() -> None:
    """Render the Dashboard page."""
    user  = st.session_state.get("user", {})
    uid   = get_uid()
    month, year = current_month_year()

    # ---- Header ----
    col_logo, _, col_profile = st.columns([3, 4, 3])

    with col_logo:
        st.markdown(
            """
            <div style="padding:6px 0;">
              <div style="font-size:2.4rem; line-height:1;">🧾</div>
              <div style="font-size:1.7rem; font-weight:800; letter-spacing:1px; color:#4a9eff; line-height:1.2; text-shadow: 0 0 12px rgba(74,158,255,0.4);">Invoxa</div>
              <div style="font-size:0.75rem; color:#9ca3af; letter-spacing:2px; text-transform:uppercase;">Expense Agent</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_profile:
        photo_url    = user.get("photoURL", "")
        display_name = user.get("displayName", "there")
        email        = user.get("email", "")
        avatar_html  = (
            f'<img src="{photo_url}" width="36" height="36" '
            f'style="border-radius:50%; margin-right:10px; vertical-align:middle;"/>'
            if photo_url else ""
        )
        st.markdown(
            f"""
            <div style="display:flex; align-items:center; justify-content:flex-end; padding:6px 0;">
              {avatar_html}
              <div style="text-align:right;">
                <div style="font-size:0.78rem; font-weight:600; color:#d1d5db;">Welcome back, {display_name} 👋</div>
                <div style="font-size:0.70rem; color:#6b7280;">{email}</div>
                <div style="font-size:0.68rem; color:#4b5563;">{month} {year}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ---- Quick stats ----
    with st.spinner("Loading stats…"):
        try:
            invoices = get_invoices_for_month(uid, month, year)
        except Exception:
            invoices = []

    stats = compute_monthly_stats(invoices)

    total_ai_cost = get_total_ai_cost(uid)

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric(
            "Total Expenses",
            format_amount(stats["total_amount"], st.session_state.get("default_currency", "EUR")),
        )
    with col2:
        st.metric(
            "Total Tax",
            format_amount(stats["total_tax"], st.session_state.get("default_currency", "EUR")),
        )
    with col3:
        st.metric("Invoices Processed", stats["invoice_count"])
    with col4:
        st.metric("Unique Suppliers", stats["supplier_count"])
    with col5:
        st.metric("🤖 AI Cost (total)", f"${total_ai_cost:.4f}")

    st.markdown("---")

    # ---- Quick actions ----
    st.subheader("Quick Actions")
    q1, q2, q3 = st.columns(3)
    with q1:
        if st.button("⬆️ Upload Invoice", use_container_width=True):
            st.session_state["nav_radio"]    = "Upload Invoice"
            st.session_state["current_page"] = "Upload Invoice"
            st.rerun()
    with q2:
        if st.button("📊 Generate Report", use_container_width=True):
            st.session_state["nav_radio"]    = "Monthly Report"
            st.session_state["current_page"] = "Monthly Report"
            st.rerun()
    with q3:
        if st.button("💬 Open Chat", use_container_width=True):
            st.session_state["nav_radio"]    = "Chat"
            st.session_state["current_page"] = "Chat"
            st.rerun()

    st.markdown("---")

    # ---- Category breakdown ----
    if stats["category_breakdown"]:
        st.subheader(f"Category Breakdown — {month} {year}")
        import pandas as pd
        df_cat = pd.DataFrame(
            list(stats["category_breakdown"].items()),
            columns=["Category", "Amount"],
        ).set_index("Category")
        st.bar_chart(df_cat, height=250)

    # ---- Recent activity ----
    st.subheader("Recent Activity")
    try:
        recent = get_recent_invoices(uid, limit=5)
    except Exception as _e:
        st.error(f"Could not load recent invoices: {_e}")
        recent = []

    if not recent:
        st.info("No invoices processed yet. Click **Process New Invoices** to get started.")
    else:
        # Confirm-delete state: store the invoice_id pending confirmation
        pending_delete = st.session_state.get("_pending_delete_id")

        for idx, inv in enumerate(recent):
            try:
                supplier  = str(inv.get("supplier_name", "Unknown"))
                amount    = inv.get("amount", 0)
                currency  = inv.get("currency", "EUR")
                inv_date  = str(inv.get("invoice_date", ""))
                category  = inv.get("category", "Other")
                month_inv = inv.get("month", "")
                year_inv  = inv.get("year", "")
                inv_id    = inv.get("_doc_id") or inv.get("drive_file_id") or str(idx)

                st.write(f"**{supplier}** — {category} — {format_amount(float(amount or 0), currency)}")
                st.caption(f"{inv_date} · {month_inv} {year_inv}")
                renamed = inv.get("renamed_filename", "")
                if renamed:
                    st.caption(f"✅ {renamed[:40]}")
                if st.button(f"Delete — {supplier} ({inv_date})", key=f"del_{idx}"):
                    st.session_state["_pending_delete_id"] = inv_id
                    st.rerun()
            except Exception as _ex:
                st.error(f"Row {idx} error: {_ex}")

            if pending_delete and pending_delete == inv_id:
                st.warning(f"Delete **{supplier}** ({inv_date})? This only removes it from Invoxa — the Drive file is kept.")
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button("Yes, delete", key=f"confirm_{idx}", type="primary"):
                        try:
                            delete_invoice(uid, inv_id)
                            st.session_state.pop("_pending_delete_id", None)
                            st.success("Invoice removed.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Delete failed: {exc}")
                with col_no:
                    if st.button("Cancel", key=f"cancel_{idx}"):
                        st.session_state.pop("_pending_delete_id", None)
                        st.rerun()

            st.divider()
