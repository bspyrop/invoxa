"""
Dashboard page — shows quick stats, recent activity, and quick-action buttons.
"""

from __future__ import annotations

import streamlit as st

from services.firestore import delete_invoice, get_invoices_for_month, get_line_items, get_recent_invoices, get_total_ai_cost
from styles.apply_theme import apply_theme
from styles.badges import badge
from utils.helpers import MONTHS, compute_monthly_stats, current_month_year, format_amount, year_range
from utils.session import get_uid


def _save_edited_line_items(uid: str, invoice_id: str, rows: list) -> None:
    """Overwrite the line_items sub-collection with the user-edited rows."""
    import uuid as _uuid
    from firebase_admin import firestore as _fs
    from services.firestore import _db

    db          = _db()
    invoice_ref = db.collection("users").document(uid).collection("invoices").document(invoice_id)

    # Delete existing items
    for old_doc in invoice_ref.collection("line_items").stream():
        old_doc.reference.delete()

    # Write updated items
    batch = db.batch()
    total = 0.0
    for row in rows:
        item_id  = str(_uuid.uuid4())
        item_ref = invoice_ref.collection("line_items").document(item_id)
        line_total = float(row.get("line_total") or 0)
        total += line_total
        batch.set(item_ref, {
            "item_id":      item_id,
            "description":  str(row.get("description", "")),
            "quantity":     float(row.get("quantity") or 1.0),
            "unit":         str(row.get("unit") or ""),
            "unit_price":   float(row.get("unit_price") or 0.0),
            "line_total":   line_total,
            "tax_rate":     row.get("tax_rate"),
            "tax_amount":   row.get("tax_amount"),
            "extracted_at": _fs.SERVER_TIMESTAMP,
        })
    batch.commit()

    # Update summary fields on the parent document
    invoice_ref.update({
        "line_items_count": len(rows),
        "line_items_total": round(total, 2),
    })


def _render_line_items_detail(uid: str, invoice_id: str, row_idx: int) -> None:
    """Render editable line items table for a single invoice in the dashboard."""
    import pandas as pd

    if not invoice_id:
        st.caption("No invoice ID available.")
        return

    line_items = get_line_items(uid, invoice_id)

    if not line_items:
        st.caption("No line items recorded for this invoice.")
        return

    df = pd.DataFrame(line_items)[
        ["description", "quantity", "unit", "unit_price", "line_total"]
    ]
    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "description": st.column_config.TextColumn("Description", width="large"),
            "quantity":    st.column_config.NumberColumn("Qty", format="%.2f", width="small"),
            "unit":        st.column_config.TextColumn("Unit", width="small"),
            "unit_price":  st.column_config.NumberColumn("Unit price", format="%.2f", width="small"),
            "line_total":  st.column_config.NumberColumn("Total", format="%.2f", width="small"),
        },
        hide_index=True,
        key=f"line_items_editor_{invoice_id}_{row_idx}",
    )

    if st.button("Save changes", type="primary", key=f"li_save_{invoice_id}_{row_idx}"):
        try:
            _save_edited_line_items(uid, invoice_id, edited_df.to_dict("records"))
            st.success("Line items updated.")
            st.rerun()
        except Exception as exc:
            st.error(f"Save failed: {exc}")


def render() -> None:
    """Render the Dashboard page."""
    apply_theme()
    uid         = get_uid()
    month, year = current_month_year()

    # ---- Header ----
    col_title, col_actions = st.columns([3, 1])
    with col_title:
        st.markdown("## Dashboard")
        st.markdown(
            '<p style="color:#6B7A99;font-size:13px;margin-top:-10px">'
            f"Expense overview — {month} {year}"
            "</p>",
            unsafe_allow_html=True,
        )
    with col_actions:
        if st.button("Process Invoices", type="primary", use_container_width=True):
            st.session_state["nav_radio"]    = "Upload Invoice"
            st.session_state["current_page"] = "Upload Invoice"
            st.rerun()

    st.markdown(
        '<hr style="border:none;border-top:0.5px solid #E2E6EF;margin:12px 0 20px">',
        unsafe_allow_html=True,
    )

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
        st.metric("AI Cost (total)", f"${total_ai_cost:.4f}")

    st.markdown("---")

    # ---- Quick actions ----
    st.markdown("### Quick Actions")
    q1, q2, q3 = st.columns(3)
    with q1:
        if st.button("Upload Invoice", use_container_width=True):
            st.session_state["nav_radio"]    = "Upload Invoice"
            st.session_state["current_page"] = "Upload Invoice"
            st.rerun()
    with q2:
        if st.button("Generate Report", use_container_width=True):
            st.session_state["nav_radio"]    = "Monthly Report"
            st.session_state["current_page"] = "Monthly Report"
            st.rerun()
    with q3:
        if st.button("Open Chat", use_container_width=True):
            st.session_state["nav_radio"]    = "Chat"
            st.session_state["current_page"] = "Chat"
            st.rerun()

    st.markdown("---")

    # ---- Recent activity ----
    st.markdown("### Recent Activity")
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

                source_badge = (
                    " " + badge("via Gmail", "info")
                    if inv.get("source") == "gmail" else ""
                )
                st.markdown(
                    f"**{supplier}** — {category} — {format_amount(float(amount or 0), currency)}{source_badge}",
                    unsafe_allow_html=True,
                )
                st.caption(f"{inv_date} · {month_inv} {year_inv}")
                renamed = inv.get("renamed_filename", "")
                if renamed:
                    st.caption(f"✅ {renamed[:40]}")
                with st.expander("Line items", expanded=False):
                    _render_line_items_detail(uid, inv_id, idx)

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
                            try:
                                from services.chroma_service import delete_invoice_chunks
                                delete_invoice_chunks(uid, inv_id)
                            except Exception:
                                pass
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
