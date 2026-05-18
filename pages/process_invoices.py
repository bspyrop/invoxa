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
from styles.apply_theme import apply_theme
from styles.badges import badge
from services.google_drive import delete_file, get_or_create_folder, upload_file
from services.firestore import delete_invoice, is_already_imported, save_categories, save_gmail_import
from utils.helpers import current_month_year
from utils.session import get_uid, get_user_categories, set_user_categories

# Session-state keys (all prefixed _inv_ to avoid collisions)
_KEY_BYTES   = "_inv_bytes"
_KEY_MIME    = "_inv_mime"
_KEY_FNAME   = "_inv_filename"
_KEY_PHASE   = "_inv_phase"      # None | "hitl" | "anomaly_hitl" | "done"
_KEY_THREAD  = "_inv_thread"     # LangGraph thread_id
_KEY_SNAP    = "_inv_snapshot"   # state dict returned by graph.invoke
_KEY_DRIVEID = "_inv_drive_id"   # Drive file ID of the uploaded temp file


def render() -> None:
    apply_theme()
    uid   = get_uid()
    phase = st.session_state.get(_KEY_PHASE)

    st.markdown("## Upload Invoice")
    st.markdown(
        '<p style="color:#6B7A99;font-size:13px;margin-top:-10px">'
        "Drop a file or scan your inbox — the agent extracts, you review, then it organises."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<hr style="border:none;border-top:0.5px solid #E2E6EF;margin:12px 0 20px">',
        unsafe_allow_html=True,
    )

    if phase is None:
        tab_upload, tab_gmail = st.tabs(["📁 Upload file", "📧 Check email"])
        with tab_upload:
            _render_upload(uid)
        with tab_gmail:
            _render_gmail_tab(uid)
    elif phase == "hitl":
        st.caption("Review the extracted data before saving.")
        _render_hitl(uid)
    elif phase == "anomaly_hitl":
        st.caption("Anomalies detected — review before finalising.")
        _render_anomaly_hitl(uid)
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
# Phase 1b — Gmail scan tab
# ---------------------------------------------------------------------------

def _render_gmail_tab(uid: str) -> None:
    from agent.nodes.classify_email import classify_email_as_invoice
    from services.gmail_scanner import get_gmail_service, list_candidate_emails

    creds = st.session_state.get("google_credentials")
    if not creds:
        st.warning("Gmail connection error. Please sign out and sign in again.")
        return

    try:
        service = get_gmail_service(creds)
    except Exception as exc:
        st.error(f"Could not build Gmail service: {exc}")
        return

    st.caption("Scan your Gmail inbox to find unread emails with invoice attachments.")

    if st.button("Scan inbox for invoices", key="_gmail_scan_btn"):
        with st.status("Scanning inbox…", expanded=True) as status:
            try:
                all_emails = list_candidate_emails(service)
            except Exception as exc:
                status.update(label="Gmail scan failed", state="error")
                err = str(exc)
                if "accessNotConfigured" in err or "has not been used" in err or "disabled" in err:
                    st.error(
                        "Gmail API is not enabled in your Google Cloud project. "
                        "Go to **Google Cloud Console → APIs & Services → Library** "
                        "and enable the **Gmail API**."
                    )
                elif "invalid_grant" in err or "401" in err:
                    st.warning("Session expired. Please sign out and sign in again.")
                else:
                    st.error(f"Gmail error: {exc}")
                return

            candidates = []
            any_non_imported = False

            for email in all_emails:
                for att in email["attachments"]:
                    if is_already_imported(uid, email["msg_id"], att["attachment_id"]):
                        continue
                    any_non_imported = True
                    try:
                        is_inv, conf = classify_email_as_invoice(
                            email["subject"],
                            email["sender"],
                            [att["filename"]],
                            uid=uid,
                        )
                    except Exception:
                        continue
                    if is_inv and conf >= 0.65:
                        candidates.append({
                            "msg_id":        email["msg_id"],
                            "subject":       email["subject"],
                            "sender":        email["sender"],
                            "date":          email["date"],
                            "attachment_id": att["attachment_id"],
                            "filename":      att["filename"],
                            "mime_type":     att["mime_type"],
                            "size_bytes":    att["size_bytes"],
                            "confidence":    conf,
                        })

            st.session_state["gmail_candidates"]    = candidates
            st.session_state["gmail_all_imported"]  = bool(all_emails) and not any_non_imported
            label = f"Found {len(candidates)} candidate invoice(s) ✓"
            status.update(label=label, state="complete")
        st.rerun()

    candidates    = st.session_state.get("gmail_candidates")
    all_imported  = st.session_state.get("gmail_all_imported", False)

    if candidates is None:
        return

    if not candidates:
        if all_imported:
            st.success("All invoice emails have already been imported.")
        else:
            st.info(
                "No invoice emails found. All attachments have either been imported "
                "already or were not recognised as invoices."
            )
        return

    for i, card in enumerate(list(candidates)):
        with st.container(border=True):
            col_info, col_btn = st.columns([4, 1])
            with col_info:
                size_kb = round(card["size_bytes"] / 1024, 1)
                conf    = card["confidence"]
                st.markdown(f"**{card['filename']}**")
                st.caption(card["sender"])
                st.caption(card["subject"][:60])
                st.caption(f"{card['date']} · {size_kb} KB")
                conf_variant = "success" if conf >= 0.85 else "warning"
                st.markdown(
                    badge(f"{int(conf * 100)}% confidence", conf_variant),
                    unsafe_allow_html=True,
                )
            with col_btn:
                if st.button("Import", key=f"_gmail_import_{i}", use_container_width=True):
                    _do_gmail_import(uid, card, service)


def _do_gmail_import(uid: str, card: dict, service) -> None:
    from services.gmail_scanner import download_attachment, label_email_processed

    msg_id        = card["msg_id"]
    attachment_id = card["attachment_id"]
    filename      = card["filename"]
    mime_type     = card["mime_type"]

    st.toast("Importing from Gmail…")

    try:
        file_bytes = download_attachment(service, msg_id, attachment_id)
    except Exception as exc:
        st.error(f"Could not download attachment: {exc}")
        return

    creds = st.session_state.get("google_credentials")
    try:
        root_folder    = st.session_state.get("expenses_root_folder", "Expenses")
        inbox_folder_id = _get_gmail_inbox_folder(creds, root_folder)
        drive_id       = upload_file(creds, inbox_folder_id, filename, file_bytes, mime_type)
    except Exception as exc:
        st.error(f"Could not upload to Drive: {exc}")
        return

    try:
        label_email_processed(service, msg_id)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Could not label email %s: %s", msg_id, exc)

    try:
        save_gmail_import(uid, {
            "msg_id":        msg_id,
            "attachment_id": attachment_id,
            "filename":      filename,
            "subject":       card["subject"],
            "sender":        card["sender"],
            "drive_file_id": drive_id,
            "invoice_id":    None,
            "status":        "imported",
        })
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Could not save gmail_import record: %s", exc)

    remaining = [
        c for c in st.session_state.get("gmail_candidates", [])
        if not (c["msg_id"] == msg_id and c["attachment_id"] == attachment_id)
    ]
    st.session_state["gmail_candidates"] = remaining

    st.session_state[_KEY_BYTES]   = file_bytes
    st.session_state[_KEY_MIME]    = mime_type
    st.session_state[_KEY_FNAME]   = filename
    st.session_state[_KEY_DRIVEID] = drive_id

    month, year = current_month_year()
    thread_id   = str(uuid.uuid4())
    config      = {"configurable": {"thread_id": thread_id}}

    initial_state = {
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
        "gmail_source":       True,
        "gmail_msg_id":       msg_id,
        "gmail_attachment_id": attachment_id,
        "gmail_subject":      card["subject"],
        "gmail_sender":       card["sender"],
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

    if snapshot.get("pending_approval"):
        st.session_state[_KEY_PHASE] = "hitl"
    else:
        st.session_state[_KEY_PHASE] = "done"

    st.rerun()


def _get_gmail_inbox_folder(creds, root_folder: str) -> str:
    root_id = get_or_create_folder(creds, root_folder)
    return get_or_create_folder(creds, "Inbox", parent_id=root_id)


# ---------------------------------------------------------------------------
# Phase 2 — HITL: review + confirm
# ---------------------------------------------------------------------------

def _render_hitl(uid: str) -> None:
    import base64

    snap       = st.session_state.get(_KEY_SNAP, {})
    extracted  = snap.get("extracted_data", [])
    invoice    = extracted[0] if extracted else {}
    suggested  = snap.get("suggested_filename", "invoice.pdf")
    file_bytes = st.session_state.get(_KEY_BYTES)
    mime_type  = st.session_state.get(_KEY_MIME, "application/pdf")

    st.markdown("### Review Extracted Data")

    col_left, col_right = st.columns(2)

    with col_left:
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
            supplier = st.text_input("Supplier", value=str(invoice.get("supplier_name") or ""))
            inv_date = st.text_input("Invoice Date (YYYY-MM-DD)", value=str(invoice.get("invoice_date") or ""))
            amount   = st.number_input(
                "Amount",
                value=float(invoice.get("amount") or 0),
                min_value=0.0,
                step=0.01,
                format="%.2f",
            )
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
            confirmed = col_yes.form_submit_button("Confirm & Save", type="primary", use_container_width=True)
            cancelled = col_no.form_submit_button("Cancel & Discard", use_container_width=True)

    with col_right:
        st.markdown("**Document Preview**")
        if file_bytes:
            if mime_type.startswith("image/"):
                st.image(file_bytes, use_container_width=True)
            else:
                b64 = base64.b64encode(file_bytes).decode()
                st.markdown(
                    f'<iframe src="data:application/pdf;base64,{b64}" '
                    f'width="100%" height="700px" type="application/pdf"></iframe>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No preview available.")

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

        st.session_state[_KEY_SNAP] = result

        warnings = result.get("anomaly_warnings") or []
        with st.expander(f"Debug — anomaly_warnings ({len(warnings)} found)", expanded=True):
            st.json(warnings)

        # Route to anomaly HITL if any warnings were raised
        if warnings:
            st.session_state[_KEY_PHASE] = "anomaly_hitl"
        else:
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
# Phase 2b — Anomaly HITL: show warnings, let user keep or discard
# ---------------------------------------------------------------------------

def _render_anomaly_hitl(uid: str) -> None:
    snap     = st.session_state.get(_KEY_SNAP, {})
    warnings = snap.get("anomaly_warnings", [])
    extracted = snap.get("extracted_data", [])
    inv = extracted[0] if extracted else {}

    st.markdown("### Anomalies Detected")
    st.markdown(
        f"**{inv.get('supplier_name', '—')}** · "
        f"{inv.get('amount', 0)} {inv.get('currency', 'EUR')} · "
        f"{inv.get('invoice_date', '—')}"
    )
    st.markdown("---")

    for w in warnings:
        st.warning(w.get("message", ""))

    st.markdown("---")
    st.markdown("Do you want to **keep** this invoice or **discard** it?")

    col_keep, col_discard = st.columns(2)
    keep    = col_keep.button("Keep Invoice", type="primary", use_container_width=True)
    discard = col_discard.button("Discard Invoice", type="secondary", use_container_width=True)

    if keep:
        st.session_state[_KEY_PHASE] = "done"
        st.rerun()

    if discard:
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
        st.info("Invoice discarded and removed.")
        st.rerun()


# ---------------------------------------------------------------------------
# Phase 3 — done: results + anomaly warnings
# ---------------------------------------------------------------------------

def _render_done() -> None:
    snap     = st.session_state.get(_KEY_SNAP, {})
    renamed  = snap.get("renamed_files", [])
    warnings = snap.get("anomaly_warnings", [])

    if renamed:
        st.success("Invoice processed and organised successfully!")
        for r in renamed:
            st.markdown(f"`{r.get('new_name', '')}`")
    else:
        extracted = snap.get("extracted_data", [])
        if extracted:
            inv = extracted[0]
            st.success("Invoice data saved.")
            st.markdown(
                f"**{inv.get('supplier_name', '—')}** · "
                f"{inv.get('amount', 0)} {inv.get('currency', 'EUR')} · "
                f"{inv.get('invoice_date', '—')}"
            )

    if warnings:
        st.markdown("---")
        st.markdown("### Anomaly Warnings")
        for w in warnings:
            st.warning(w.get("message", ""))

    st.markdown("---")
    if st.button("Upload Another Invoice", use_container_width=True):
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
