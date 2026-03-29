"""
Upload Invoice page — fully automatic pipeline.

  1. User selects / drags a file
  2. GPT-4o extracts all invoice data
  3. File is uploaded to Google Drive: Expenses/{Month YYYY}/{MeaningfulName}.ext
  4. Data is saved to Firestore
  5. Success messages shown — no buttons or forms required
"""

from __future__ import annotations

import io
import calendar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import streamlit as st
from openai import OpenAI

from agent.nodes.extract_data     import _extract_from_file
from agent.nodes.suggest_filename import _build_filename
from services.firestore           import save_invoice
from services.google_drive        import get_month_folder_id
from utils.helpers                import CATEGORIES, current_month_year
from utils.session                import get_uid

_KEY_FILENAME = "inv_filename"
_KEY_BYTES    = "inv_bytes"
_KEY_MIME     = "inv_mime"
_KEY_RESULT   = "inv_result"   # dict with status, messages, data


def render() -> None:
    uid = get_uid()

    st.title("⬆️ Upload Invoice")
    st.caption("Drop a PDF or image — everything else is automatic.")

    uploaded = st.file_uploader(
        "Select or drag-and-drop an invoice",
        type=["pdf", "jpg", "jpeg", "png", "webp"],
        accept_multiple_files=False,
        key="inv_uploader",
    )

    # New file selected → cache bytes and trigger pipeline
    if uploaded is not None and st.session_state.get(_KEY_FILENAME) != uploaded.name:
        st.session_state[_KEY_BYTES]    = uploaded.read()
        st.session_state[_KEY_MIME]     = uploaded.type or "application/pdf"
        st.session_state[_KEY_FILENAME] = uploaded.name
        st.session_state.pop(_KEY_RESULT, None)

    # No file yet
    if not st.session_state.get(_KEY_BYTES):
        return

    filename  = st.session_state[_KEY_FILENAME]
    mime_type = st.session_state[_KEY_MIME]
    file_bytes = st.session_state[_KEY_BYTES]

    # Show preview
    if mime_type.startswith("image/"):
        st.image(file_bytes, width=360, caption=filename)
    else:
        st.markdown(f"📄 **{filename}** ({round(len(file_bytes)/1024, 1)} KB)")

    st.markdown("---")

    # Already processed — just show results
    if st.session_state.get(_KEY_RESULT):
        _show_result(st.session_state[_KEY_RESULT])
        if st.button("⬆️ Upload Another Invoice", use_container_width=True):
            for k in (_KEY_BYTES, _KEY_MIME, _KEY_FILENAME, _KEY_RESULT):
                st.session_state.pop(k, None)
            st.rerun()
        return

    # Run the pipeline
    result = _run_pipeline(uid, file_bytes, mime_type, filename)
    st.session_state[_KEY_RESULT] = result
    _show_result(result)

    if st.button("⬆️ Upload Another Invoice", use_container_width=True):
        for k in (_KEY_BYTES, _KEY_MIME, _KEY_FILENAME, _KEY_RESULT):
            st.session_state.pop(k, None)
        st.rerun()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(
    uid: str,
    file_bytes: bytes,
    mime_type: str,
    original_filename: str,
) -> Dict[str, Any]:
    """
    Extract → build filename → upload to Drive → save to Firestore.
    Returns a result dict with keys: success, steps (list of step dicts).
    """
    steps = []

    # ── Step 1: Extract ────────────────────────────────────────────────────
    with st.status("Reading invoice with GPT-4o…", expanded=True) as status:
        st.write("Analysing document…")
        client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY", ""))
        data   = _extract_from_file(client, file_bytes, mime_type, original_filename)

        if data is None:
            status.update(label="Extraction failed", state="error")
            return {"success": False, "steps": [{"ok": False, "msg": "GPT-4o could not read this file. Make sure it is a clear invoice PDF or image."}]}

        status.update(label="Data extracted ✓", state="complete")

    steps.append({
        "ok":  True,
        "icon": "🧠",
        "label": "Invoice data extracted",
        "detail": (
            f"Supplier: **{data.get('supplier_name', '—')}**  \n"
            f"Date: **{data.get('invoice_date', '—')}**  \n"
            f"Amount: **{data.get('amount', 0)} {data.get('currency', 'EUR')}**  \n"
            f"Category: **{data.get('category', '—')}**  \n"
            f"Description: {data.get('description', '—')}"
        ),
    })

    # ── Step 2: Build filename & resolve folder ─────────────────────────────
    month, year = _infer_month_year(data.get("invoice_date"))
    final_name  = _build_filename(data, original_filename)  # type: ignore[arg-type]

    # ── Step 3: Upload to Drive ─────────────────────────────────────────────
    with st.status(f"Uploading to Google Drive…", expanded=True) as status:
        st.write(f"Saving to **Expenses/{month} {year}/{final_name}**…")
        drive_id, drive_path, drive_error = _upload_to_drive(
            file_bytes, mime_type, final_name, month, year
        )

        if drive_id is None:
            status.update(label="Drive upload failed", state="error")
            steps.append({"ok": False, "icon": "☁️", "label": "Google Drive upload failed", "detail": drive_error})
            return {"success": False, "steps": steps}

        status.update(label="Uploaded to Drive ✓", state="complete")

    steps.append({
        "ok":    True,
        "icon":  "☁️",
        "label": "Saved to Google Drive",
        "detail": f"📁 `{drive_path}`",
    })

    # ── Step 4: Save to Firestore ───────────────────────────────────────────
    with st.status("Saving to Firestore…", expanded=True) as status:
        invoice = {
            **data,
            "original_filename": original_filename,
            "renamed_filename":  final_name,
            "drive_file_id":     drive_id,
            "month":             month,
            "year":              year,
            "processed_at":      datetime.now(timezone.utc).isoformat(),
            "source":            "upload",
        }
        try:
            save_invoice(uid, drive_id, invoice)
            status.update(label="Firestore updated ✓", state="complete")
        except Exception as exc:
            status.update(label="Firestore save failed", state="error")
            steps.append({"ok": False, "icon": "🔥", "label": "Firestore save failed", "detail": str(exc)})
            return {"success": False, "steps": steps}

    steps.append({
        "ok":    True,
        "icon":  "🔥",
        "label": "Invoice data stored in Firestore",
        "detail": (
            f"Invoice number: **{data.get('invoice_number') or '—'}**  \n"
            f"Tax amount: **{data.get('tax_amount', 0)} {data.get('currency', 'EUR')}**"
        ),
    })

    return {"success": True, "steps": steps}


# ---------------------------------------------------------------------------
# Result display
# ---------------------------------------------------------------------------

def _show_result(result: Dict[str, Any]) -> None:
    if result.get("success"):
        st.success("✅ Invoice processed successfully!")
    else:
        st.error("❌ Processing failed — see details below.")

    for step in result.get("steps", []):
        icon  = step.get("icon", "•")
        label = step.get("label", "")
        detail = step.get("detail", "")
        ok    = step.get("ok", True)

        if ok:
            with st.expander(f"{icon} {label}", expanded=True):
                st.markdown(detail)
        else:
            st.error(f"{icon} {label}: {detail}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upload_to_drive(
    file_bytes: bytes,
    mime_type: str,
    filename: str,
    month: str,
    year: str,
):
    """
    Upload to Expenses/{month year}/{filename}.
    Returns (drive_id, full_path, error_message).
    """
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        creds = st.session_state.get("google_credentials")
        if not creds:
            return None, None, "Google credentials not found. Please sign in again."

        root_folder = st.session_state.get("expenses_root_folder", "Expenses")
        folder_id   = get_month_folder_id(
            creds,
            root_folder_name=root_folder,
            month=month,
            year=year,
            create_if_missing=True,
        )
        if not folder_id:
            return None, None, f"Could not create folder Expenses/{month} {year} in Drive."

        service  = build("drive", "v3", credentials=creds, cache_discovery=False)
        metadata = {"name": filename, "parents": [folder_id]}
        media    = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=False)
        file_obj = service.files().create(body=metadata, media_body=media, fields="id").execute()
        drive_id = file_obj.get("id")
        full_path = f"{root_folder}/{month} {year}/{filename}"
        return drive_id, full_path, None

    except Exception as exc:
        return None, None, str(exc)


def _infer_month_year(invoice_date: Optional[str]):
    if invoice_date:
        try:
            parts = invoice_date.split("-")
            return calendar.month_name[int(parts[1])], parts[0]
        except Exception:
            pass
    return current_month_year()
