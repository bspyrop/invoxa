"""
Monthly Report page.

Allows the user to generate and preview the Google Sheets report for any
month/year, with charts and an "Open in Google Sheets" button.
"""

from __future__ import annotations

import streamlit as st
import pandas as pd

from agent.nodes.generate_report import generate_report as _generate_report_node
from services.firestore          import get_invoices_for_month, get_invoices_for_year
from utils.helpers               import (
    MONTHS,
    compute_monthly_stats,
    format_amount,
    year_range,
)
from utils.session               import (
    get_selected_month,
    get_selected_year,
    get_uid,
    set_selected_month,
    set_selected_year,
)


def render() -> None:
    """Render the Monthly Report page."""
    st.title("📊 Monthly Report")
    uid = get_uid()

    # ---- Month / Year selector ----
    c1, c2, c3 = st.columns([3, 2, 3])
    with c1:
        month = st.selectbox(
            "Month",
            MONTHS,
            index=MONTHS.index(get_selected_month()),
            key="report_month",
        )
    with c2:
        years = year_range()
        year = st.selectbox(
            "Year",
            years,
            index=years.index(get_selected_year()) if get_selected_year() in years else 0,
            key="report_year",
        )
    with c3:
        st.markdown("<br>", unsafe_allow_html=True)
        gen_btn = st.button("📤 Generate / Refresh Report", type="primary", use_container_width=True)

    set_selected_month(month)
    set_selected_year(year)

    # ---- Load preview data ----
    with st.spinner("Loading invoice data…"):
        try:
            invoices = get_invoices_for_month(uid, month, year)
        except Exception as exc:
            st.error(f"Could not load invoices: {exc}")
            return

    if not invoices:
        st.info(
            f"No processed invoices found for **{month} {year}**. "
            "Go to **Process Invoices** first."
        )
        return

    stats = compute_monthly_stats(invoices)

    # ---- Summary metrics ----
    st.markdown(f"### {month} {year} — Summary")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Total Expenses", format_amount(stats["total_amount"]))
    with m2:
        st.metric("Total Tax", format_amount(stats["total_tax"]))
    with m3:
        st.metric("Invoices", stats["invoice_count"])
    with m4:
        st.metric("Suppliers", stats["supplier_count"])

    st.markdown("---")

    # ---- Invoice table ----
    st.subheader("Invoice Table")
    df = pd.DataFrame(
        [
            {
                "Date":           inv.get("invoice_date", ""),
                "Supplier":       inv.get("supplier_name", ""),
                "Category":       inv.get("category", ""),
                "Description":    inv.get("description", ""),
                "Amount":         float(inv.get("amount", 0) or 0),
                "Tax":            float(inv.get("tax_amount", 0) or 0),
                "Currency":       inv.get("currency", ""),
                "Invoice Number": inv.get("invoice_number", ""),
            }
            for inv in invoices
        ]
    )
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ---- Charts ----
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("By Category")
        if stats["category_breakdown"]:
            df_cat = pd.DataFrame(
                list(stats["category_breakdown"].items()),
                columns=["Category", "Amount"],
            ).set_index("Category")
            st.bar_chart(df_cat, height=300)

    with col_right:
        st.subheader("Top Suppliers")
        if stats["supplier_breakdown"]:
            top_5 = dict(list(stats["supplier_breakdown"].items())[:5])
            df_sup = pd.DataFrame(
                list(top_5.items()), columns=["Supplier", "Amount"]
            ).set_index("Supplier")
            st.bar_chart(df_sup, height=300)

    st.markdown("---")

    # ---- Year summary chart ----
    st.subheader(f"Month-over-Month — {year}")
    try:
        yearly_invoices = get_invoices_for_year(uid, year)
    except Exception:
        yearly_invoices = []

    if yearly_invoices:
        monthly_totals = {m: 0.0 for m in MONTHS}
        for inv in yearly_invoices:
            m_name = inv.get("month", "")
            if m_name in monthly_totals:
                monthly_totals[m_name] += float(inv.get("amount", 0) or 0)

        df_trend = pd.DataFrame(
            {"Month": list(monthly_totals.keys()), "Total": list(monthly_totals.values())}
        ).set_index("Month")
        # Keep only months with data
        df_trend = df_trend[df_trend["Total"] > 0]
        if not df_trend.empty:
            st.line_chart(df_trend, height=250)

    st.markdown("---")

    # ---- Generate / open sheet ----
    if gen_btn:
        _do_generate(uid, month, year)

    report_url = st.session_state.get("report_url")
    if report_url:
        st.success("Report generated successfully!")
        st.link_button("📄 Open in Google Sheets", report_url, use_container_width=True)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _do_generate(uid: str, month: str, year: str) -> None:
    """Invoke the generate_report LangGraph node."""
    state = {
        "user_id": uid,
        "month":   month,
        "year":    year,
        "action":  "generate_report",
    }
    with st.spinner("Generating Google Sheets report…"):
        result = _generate_report_node(state)  # type: ignore[arg-type]

    error = result.get("error")
    if error:
        st.error(f"Report generation failed: {error}")
        return

    st.session_state["report_url"] = result.get("report_url")
    st.session_state["sheet_id"]   = result.get("sheet_id")
    st.rerun()
