"""
LangGraph node: extract_invoice_data

Downloads invoice files from Google Drive and uses GPT-4o vision to
extract structured invoice fields.  Saves results to Firestore.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.state import AgentState, InvoiceData
from agent.prompts.extraction_prompt import build_extraction_messages
from services.firestore import calc_ai_cost, log_ai_usage, log_error, save_invoice_with_line_items
from services.google_drive import download_file

logger = logging.getLogger(__name__)

# GPT-4o model used for vision extraction
VISION_MODEL = "gpt-4o"


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _pdf_to_base64_images(pdf_bytes: bytes) -> List[str]:
    """
    Convert PDF pages to base64 JPEG images.

    Tries PyMuPDF first (no external deps), then pdf2image/poppler.
    Returns empty list only if both fail.
    """
    # --- PyMuPDF (preferred, no poppler required) ---
    try:
        import fitz  # PyMuPDF

        doc    = fitz.open(stream=pdf_bytes, filetype="pdf")
        result = []
        for page in doc:
            mat = fitz.Matrix(2.0, 2.0)   # 2x zoom ≈ 144 dpi
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("jpeg")
            result.append(base64.b64encode(img_bytes).decode())
        doc.close()
        return result
    except Exception as exc:
        logger.warning("PyMuPDF failed: %s — trying pdf2image", exc)

    # --- pdf2image / poppler fallback ---
    try:
        from pdf2image import convert_from_bytes  # type: ignore

        images = convert_from_bytes(pdf_bytes, dpi=200, fmt="jpeg")
        result = []
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            result.append(base64.b64encode(buf.getvalue()).decode())
        return result
    except Exception as exc:
        logger.warning("pdf2image/poppler not available: %s", exc)

    return []


def _pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF using PyPDF2 (last resort for text-based PDFs)."""
    try:
        import PyPDF2  # type: ignore

        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        pages  = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages).strip()
    except Exception as exc:
        logger.warning("PyPDF2 text extraction failed: %s", exc)
        return ""


def _image_to_base64(image_bytes: bytes, mime_type: str) -> str:
    """Return a base64-encoded string for the given image bytes."""
    return base64.b64encode(image_bytes).decode()


def _bytes_to_base64_list(file_bytes: bytes, mime_type: str) -> List[str]:
    """Dispatch to PDF or image conversion depending on MIME type."""
    if mime_type == "application/pdf":
        return _pdf_to_base64_images(file_bytes)
    return [_image_to_base64(file_bytes, mime_type)]


# ---------------------------------------------------------------------------
# OpenAI extraction
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_openai_extraction(client: OpenAI, image_b64: str, mime_type: str) -> Tuple[Dict[str, Any], Any]:
    """
    Call GPT-4o with the invoice image.

    Returns:
        (parsed_data_dict, response.usage)
    """
    vision_mime = mime_type if mime_type.startswith("image/") else "image/jpeg"
    categories  = st.session_state.get("user_categories")

    messages = build_extraction_messages(image_b64, vision_mime, categories)
    response = client.chat.completions.create(
        model=VISION_MODEL,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=2000,
        temperature=0,
    )
    raw = response.choices[0].message.content or "{}"
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw), response.usage


def _call_openai_text_extraction(client: OpenAI, text: str) -> Tuple[Dict[str, Any], Any]:
    """Call GPT-4o with plain text (for PDFs where image conversion is unavailable).

    Returns:
        (parsed_data_dict, response.usage)
    """
    from agent.prompts.extraction_prompt import _build_system_prompt

    categories = st.session_state.get("user_categories")
    response = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {"role": "system", "content": _build_system_prompt(categories)},
            {"role": "user",   "content": f"Extract invoice data from this text:\n\n{text}"},
        ],
        max_tokens=2000,
        temperature=0,
    )
    raw = response.choices[0].message.content or "{}"
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw), response.usage


def _extract_from_file(
    client: OpenAI, file_bytes: bytes, mime_type: str, filename: str
) -> Tuple[Optional[Dict[str, Any]], Any]:
    """
    Extract invoice data from a single file.

    Returns:
        (data_dict | None, usage | None)
    """
    try:
        if mime_type == "application/pdf":
            images = _pdf_to_base64_images(file_bytes)
            if images:
                return _call_openai_extraction(client, images[0], "image/jpeg")
            text = _pdf_to_text(file_bytes)
            if text:
                logger.info("Using text extraction fallback for '%s'", filename)
                return _call_openai_text_extraction(client, text)
            logger.error("PDF '%s' yielded neither images nor text", filename)
            return None, None
        else:
            images = _bytes_to_base64_list(file_bytes, mime_type)
            if not images:
                return None, None
            return _call_openai_extraction(client, images[0], mime_type)

    except json.JSONDecodeError as exc:
        logger.error("JSON parse failed for '%s': %s", filename, exc)
        return None, None
    except Exception as exc:
        logger.error("Extraction failed for '%s': %s", filename, exc)
        return None, None


# ---------------------------------------------------------------------------
# Line-item validation
# ---------------------------------------------------------------------------

def validate_line_items(
    line_items: List[Dict[str, Any]],
    invoice_total: float,
    currency: str,
) -> List[str]:
    """
    Validate extracted line items and return a list of warning strings.
    Empty list means all valid. Warnings are informational — they do not block saving.
    """
    warnings: List[str] = []

    for i, item in enumerate(line_items):
        qty        = item.get("quantity", 0)
        unit_price = item.get("unit_price", 0)
        line_total = item.get("line_total", 0)

        if (qty or 0) <= 0:
            warnings.append(f"Item {i + 1}: quantity must be positive")

        if (unit_price or 0) < 0:
            warnings.append(f"Item {i + 1}: unit price cannot be negative")

        expected = round((qty or 0) * (unit_price or 0), 2)
        actual   = round(line_total or 0, 2)
        if abs(expected - actual) > 0.01:
            warnings.append(
                f"Item {i + 1} '{item.get('description', '')}': "
                f"line total {actual} ≠ qty × unit price {expected}"
            )

    if line_items and invoice_total:
        items_sum = sum(item.get("line_total", 0) or 0 for item in line_items)
        tolerance = max(0.10, invoice_total * 0.01)
        if abs(items_sum - invoice_total) > tolerance:
            warnings.append(
                f"Line items sum ({items_sum:.2f} {currency}) differs "
                f"from invoice total ({invoice_total:.2f} {currency})"
            )

    return warnings


# ---------------------------------------------------------------------------
# Line-items-only extraction (used by backfill utility)
# ---------------------------------------------------------------------------

def extract_line_items_only(
    file_bytes: bytes,
    mime_type: str,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Extract only line items from an invoice file.
    Returns [] if the invoice has no individual line items.
    Used for backfilling existing invoices — cheaper than a full re-extraction.
    api_key may be passed directly (e.g. from a CLI script); falls back to st.secrets.
    """
    if api_key is None:
        api_key = st.secrets.get("OPENAI_API_KEY", "")
    client  = OpenAI(api_key=api_key)

    prompt = (
        "Extract the line items from this invoice.\n"
        "Return ONLY a JSON array. No explanation. No markdown fences.\n\n"
        "[\n"
        '  {\n'
        '    "description": "product or service name",\n'
        '    "quantity":    1.0,\n'
        '    "unit":        "unit or empty string",\n'
        '    "unit_price":  0.00,\n'
        '    "line_total":  0.00,\n'
        '    "tax_rate":    null,\n'
        '    "tax_amount":  null\n'
        "  }\n"
        "]\n\n"
        "Return [] if there are no individual line items on this invoice.\n"
        "Never fabricate items. Only extract what is visibly printed."
    )

    try:
        images    = _bytes_to_base64_list(file_bytes, mime_type)
        vision_m  = mime_type if mime_type.startswith("image/") else "image/jpeg"
        image_b64 = images[0] if images else None

        if image_b64:
            response = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url":    f"data:{vision_m};base64,{image_b64}",
                                    "detail": "high",
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                max_tokens=2000,
                temperature=0,
            )
        else:
            text = _pdf_to_text(file_bytes)
            if not text:
                return []
            response = client.chat.completions.create(
                model=VISION_MODEL,
                messages=[{"role": "user", "content": f"{prompt}\n\nInvoice text:\n{text}"}],
                max_tokens=2000,
                temperature=0,
            )

        raw = response.choices[0].message.content or "[]"
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        return result if isinstance(result, list) else []

    except Exception as exc:
        logger.error("extract_line_items_only failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Node entry point
# ---------------------------------------------------------------------------

def extract_invoice_data(state: AgentState) -> AgentState:
    """
    Download and extract data from all unprocessed invoice files.

    Reads:
      state["invoices"]      — list of DriveFile dicts
      state["user_id"]
      state["month"], state["year"]

    Writes:
      state["extracted_data"] — list of InvoiceData dicts
      state["error"]          — error string if extraction failed completely
    """
    uid     = state.get("user_id", "")
    month   = state.get("month", "")
    year    = state.get("year", "")
    files   = state.get("invoices", [])

    if not files:
        return {**state, "extracted_data": [], "error": "No invoice files to process."}

    creds = st.session_state.get("google_credentials")
    if creds is None:
        msg = "Google credentials missing. Please sign in again."
        return {**state, "extracted_data": [], "error": msg}

    api_key = st.secrets.get("OPENAI_API_KEY", "")
    client  = OpenAI(api_key=api_key)

    extracted:       List[InvoiceData] = []
    all_line_items:  List[Dict[str, Any]] = []
    all_li_warnings: List[str] = []

    for drive_file in files:
        file_id   = drive_file.get("id", "")
        filename  = drive_file.get("name", "")
        mime_type = drive_file.get("mimeType", "application/pdf")

        logger.info("Downloading %s (%s)…", filename, file_id)
        try:
            file_bytes = download_file(creds, file_id)
        except Exception as exc:
            logger.error("Download failed for '%s': %s", filename, exc)
            log_error(uid, "extract_invoice_data:download", str(exc), {"file_id": file_id})
            continue

        data, usage = _extract_from_file(client, file_bytes, mime_type, filename)
        if data is None:
            log_error(uid, "extract_invoice_data:extraction", "Extraction returned None", {"filename": filename})
            continue

        # Log AI cost for this extraction call
        if usage:
            cost = calc_ai_cost(VISION_MODEL, usage.prompt_tokens, usage.completion_tokens)
            log_ai_usage(uid, VISION_MODEL, "extract", usage.prompt_tokens, usage.completion_tokens, cost, file_id)

        # Separate line items from header fields
        line_items: List[Dict[str, Any]] = data.pop("line_items", []) or []
        if not isinstance(line_items, list):
            line_items = []

        # Validate line items
        li_warnings = validate_line_items(
            line_items,
            float(data.get("amount") or 0),
            data.get("currency", "EUR"),
        )
        all_li_warnings.extend(li_warnings)

        # Merge in Drive metadata and line-item summary counts
        invoice: InvoiceData = {
            **data,  # type: ignore[misc]
            "original_filename": filename,
            "drive_file_id":     file_id,
            "month":             month,
            "year":              year,
            "processed_at":      datetime.now(timezone.utc).isoformat(),
            "line_items_count":  len(line_items),
            "line_items_total":  round(sum(i.get("line_total", 0) or 0 for i in line_items), 2),
        }

        # Persist invoice + line items atomically
        save_invoice_with_line_items(uid, file_id, invoice, line_items)

        extracted.append(invoice)
        all_line_items = line_items  # keep last invoice's items for single-invoice HITL flow
        logger.info("Extracted data for '%s': supplier=%s, amount=%s %s, line_items=%d",
                    filename,
                    data.get("supplier_name"),
                    data.get("amount"),
                    data.get("currency"),
                    len(line_items))

    return {
        **state,
        "extracted_data":     extracted,
        "line_items":         all_line_items if extracted else None,
        "line_item_warnings": all_li_warnings if all_li_warnings else None,
        "error":              None,
    }
