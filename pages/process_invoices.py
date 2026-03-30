"""
Upload Invoice page — full LangGraph pipeline with Human-in-the-Loop (HITL).

Flow:
  1. User drops a PDF/image file.
  2. File is uploaded to Google Drive (Expenses/ root, original name).
  3. Graph runs (action="upload_invoice"):
       extract_invoice_data  →  suggest_filename  →  INTERRUPT
  4. HITL: user reviews / edits extracted data and filename, then confirms.
  5. Graph resumes:
       rename_and_organize  →  check_anomalies  →  END
  6. Results and any anomaly warnings are displayed.
"""

from __future__ import annotations

import calendar
import uuid
from typing import Any, Dict, Optional

import streamlit as st

from agent.graph import graph
from services.google_drive import delete_file, get_or_create_folder, upload_file
from services.firestore import delete_invoice, save_categories
from utils.helpers import current_month_year
from utils.session import get_uid, get_user_categories, set_user_categories

# Session-state keys (all prefixed _inv_ to avoid collisions)
_KEY_BYTES   = "_inv_bytes"
_KEY_MIME    = "_inv_mime"
_KEY_FNAME   = "_inv_filename"
_KEY_PHASE   = "_inv_phase"      # None | "hitl" | "done"
_KEY_THREAD  = "_inv_thread"     # LangGraph thread_id
_KEY_SNAP    = "_inv_snapshot"   # state dict returned by graph.invoke
_KEY_DRIVEID = "_inv_drive_id"   # Drive file ID of the uploaded temp file


def render() -> None:
    uid   = get_uid()
    phase = st.session_state.get(_KEY_PHASE)

    st.title("⬆️ Upload Invoice")

    if phase is None:
        _render_upload(uid)
    elif phase == "hitl":
        st.caption("Review the extracted data before saving.")
        _render_hitl(uid)
    elif phase == "done":
        _render_done()


# ---------------------------------------------------------------------------
# Phase 1 — file uploader + Drive upload + graph invocation
# ---------------------------------------------------------------------------

def _render_upload(uid: str) -> None:
    st.caption("Drop a PDF or image — the agent extracts, you review, then it organises automatically.")

    uploaded = st.file_uploader(
        "Select or drag-and-drop an invoice",
        type=["pdf", "jpg", "jpeg", "png", "webp"],
        accept_multiple_files=False,
        key="inv_uploader",
    )

    if uploaded is None:
        return

    # Skip if same file is re-rendered without a new selection
    if st.session_state.get(_KEY_FNAME) == uploaded.name:
        return

    file_bytes = uploaded.read()
    mime_type  = uploaded.type or "application/pdf"
    filename   = uploaded.name

    st.session_state[_KEY_BYTES] = file_bytes
    st.session_state[_KEY_MIME]  = mime_type
    st.session_state[_KEY_FNAME] = filename

    if mime_type.startswith("image/"):
        st.image(file_bytes, width=360, caption=filename)
    else:
        st.markdown(f"📄 **{filename}** ({round(len(file_bytes) / 1024, 1)} KB)")

    # ── Step 1: Upload to Drive ────────────────────────────────────────────
    with st.status("Uploading to Google Drive…", expanded=True) as status:
        creds = st.session_state.get("google_credentials")
        if not creds:
            status.update(label="Not signed in", state="error")
            st.error("Google credentials not found — please sign in again.")
            return

        root_folder = st.session_state.get("expenses_root_folder", "Expenses")
        try:
            folder_id = get_or_create_folder(creds, root_folder)
            drive_id  = upload_file(creds, folder_id, filename, file_bytes, mime_type)
            st.session_state[_KEY_DRIVEID] = drive_id
            status.update(label="Uploaded to Drive ✓", state="complete")
        except Exception as exc:
            status.update(label="Drive upload failed", state="error")
            st.error(f"Could not upload to Drive: {exc}")
            return

    # ── Step 2: Run graph until HITL interrupt ─────────────────────────────
    month, year = current_month_year()
    thread_id   = str(uuid.uuid4())
    config      = {"configurable": {"thread_id": thread_id}}

    initial_state: Dict[str, Any] = {
        "user_id":            uid,
        "action":             "upload_invoice",
        "month":              month,
        "year":               year,
        "invoices":           [{"id": drive_id, "name": filename, "mimeType": mime_type}],
        "extracted_data":     [],
        "current_file_index": 0,
        "renamed_files":      [],
        "anomaly_warnings":   [],
        "error":              None,
    }

    with st.status("Extracting invoice data with GPT-4o…", expanded=True) as status:
        try:
            snapshot = graph.invoke(initial_state, config=config)
            status.update(label="Extraction complete ✓", state="complete")
        except Exception as exc:
            status.update(label="Extraction failed", state="error")
            st.error(f"Agent error during extraction: {exc}")
            return

    if snapshot.get("error"):
        st.error(f"Extraction failed: {snapshot['error']}")
        return

    st.session_state[_KEY_THREAD] = thread_id
    st.session_state[_KEY_SNAP]   = snapshot

    # pending_approval=True means suggest_filename ran and interrupted
    if snapshot.get("pending_approval"):
        st.session_state[_KEY_PHASE] = "hitl"
    else:
        st.session_state[_KEY_PHASE] = "done"

    st.rerun()


# ---------------------------------------------------------------------------
# Phase 2 — HITL: review + confirm
# ---------------------------------------------------------------------------

def _render_hitl(uid: str) -> None:
    snap      = st.session_state.get(_KEY_SNAP, {})
    extracted = snap.get("extracted_data", [])
    invoice   = extracted[0] if extracted else {}
    suggested = snap.get("suggested_filename", "invoice.pdf")

    st.subheader("📋 Review Extracted Data")

    # Category selection lives OUTSIDE the form so selecting triggers a rerun
    categories   = get_user_categories()
    options      = categories + ["+ Add new category…"]
    raw_cat      = invoice.get("category", categories[0])
    cat_idx      = categories.index(raw_cat) if raw_cat in categories else 0
    selected_cat = st.selectbox("Category", options, index=cat_idx, key="_hitl_cat")

    new_cat_input = ""
    if selected_cat == "+ Add new category…":
        new_cat_input = st.text_input("New category name", placeholder="e.g. Insurance", key="_hitl_new_cat")

    st.markdown("---")

    with st.form("hitl_review"):
        c1, c2 = st.columns(2)
        with c1:
            supplier = st.text_input("Supplier", value=str(invoice.get("supplier_name") or ""))
            inv_date = st.text_input("Invoice Date (YYYY-MM-DD)", value=str(invoice.get("invoice_date") or ""))
            amount   = st.number_input(
                "Amount",
                value=float(invoice.get("amount") or 0),
                min_value=0.0,
                step=0.01,
                format="%.2f",
            )
        with c2:
            currency = st.text_input("Currency (ISO)", value=str(invoice.get("currency") or "EUR"), max_chars=3)
            tax      = st.number_input(
                "Tax Amount",
                value=float(invoice.get("tax_amount") or 0),
                min_value=0.0,
                step=0.01,
                format="%.2f",
            )

        description = st.text_input("Description", value=str(invoice.get("description") or ""))

        st.markdown("---")
        st.caption("The file will be saved with this name inside the correct month folder.")
        filename = st.text_input("📁 File Name", value=suggested)

        col_yes, col_no = st.columns(2)
        confirmed = col_yes.form_submit_button("✅ Confirm & Save", type="primary", use_container_width=True)
        cancelled = col_no.form_submit_button("❌ Cancel & Discard", use_container_width=True)

    if confirmed:
        # Resolve category — use new name if "add new" was selected
        if selected_cat == "+ Add new category…":
            final_category = new_cat_input.strip() or "Other"
        else:
            final_category = selected_cat

        # Persist new category to Firestore + session if it's genuinely new
        if final_category and final_category not in get_user_categories():
            updated_cats = get_user_categories() + [final_category]
            try:
                save_categories(uid, updated_cats)
                set_user_categories(updated_cats)
            except Exception:
                pass  # non-fatal — category still used for this invoice

        month, year = _infer_month_year(inv_date)
        edited_invoice = {
            **invoice,
            "supplier_name": supplier,
            "invoice_date":  inv_date,
            "amount":        amount,
            "tax_amount":    tax,
            "currency":      currency.upper(),
            "category":      final_category,
            "description":   description,
            "month":         month,
            "year":          year,
        }

        thread_id = st.session_state[_KEY_THREAD]
        config    = {"configurable": {"thread_id": thread_id}}

        # Inject user edits into graph state, then resume
        graph.update_state(config, {
            "user_approved_data": edited_invoice,
            "suggested_filename": filename,
            "month":              month,
            "year":               year,
        })

        with st.spinner("Organising in Google Drive and checking anomalies…"):
            result = graph.invoke(None, config=config)

        st.session_state[_KEY_SNAP]  = result
        st.session_state[_KEY_PHASE] = "done"
        st.rerun()

    if cancelled:
        # Remove the temp Drive file and Firestore record
        drive_id = st.session_state.get(_KEY_DRIVEID)
        creds    = st.session_state.get("google_credentials")
        if drive_id and creds:
            try:
                delete_file(creds, drive_id)
            except Exception:
                pass
        if drive_id:
            try:
                delete_invoice(uid, drive_id)
            except Exception:
                pass
        _reset()
        st.info("Upload cancelled. The file has been removed.")
        st.rerun()


# ---------------------------------------------------------------------------
# Phase 3 — done: results + anomaly warnings
# ---------------------------------------------------------------------------

def _render_done() -> None:
    snap     = st.session_state.get(_KEY_SNAP, {})
    renamed  = snap.get("renamed_files", [])
    warnings = snap.get("anomaly_warnings", [])

    if renamed:
        st.success("✅ Invoice processed and organised successfully!")
        for r in renamed:
            st.markdown(f"📁 `{r.get('new_name', '')}`")
    else:
        extracted = snap.get("extracted_data", [])
        if extracted:
            inv = extracted[0]
            st.success("✅ Invoice data saved.")
            st.markdown(
                f"**{inv.get('supplier_name', '—')}** · "
                f"{inv.get('amount', 0)} {inv.get('currency', 'EUR')} · "
                f"{inv.get('invoice_date', '—')}"
            )

    if warnings:
        st.markdown("---")
        st.subheader("⚠️ Anomaly Warnings")
        for w in warnings:
            wtype = w.get("type", "")
            icon  = "🔴" if wtype == "duplicate" else "🟡"
            st.warning(f"{icon} {w.get('message', '')}")

    st.markdown("---")
    if st.button("⬆️ Upload Another Invoice", use_container_width=True):
        _reset()
        st.rerun()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset() -> None:
    for k in (_KEY_BYTES, _KEY_MIME, _KEY_FNAME, _KEY_PHASE, _KEY_THREAD, _KEY_SNAP, _KEY_DRIVEID):
        st.session_state.pop(k, None)


def _infer_month_year(inv_date: Optional[str]):
    if inv_date:
        try:
            parts = inv_date.split("-")
            return calendar.month_name[int(parts[1])], parts[0]
        except Exception:
            pass
    return current_month_year()
