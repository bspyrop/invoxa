"""
LangGraph node: suggest_filename

Generates a clean, meaningful filename for each extracted invoice following
the pattern: {SupplierName}_{Category}_{YYYY-MM-DD}_{Amount}{Currency}.ext
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

from agent.state import AgentState, InvoiceData

logger = logging.getLogger(__name__)


def _sanitise(value: str) -> str:
    """
    Remove characters that are unsafe in filenames and replace spaces.

    Args:
        value: Input string.

    Returns:
        Cleaned string with only alphanumeric, hyphens, and underscores.
    """
    cleaned = re.sub(r"[^\w\s\-]", "", value)
    cleaned = re.sub(r"[\s]+", "_", cleaned.strip())
    return cleaned


def _build_filename(invoice: InvoiceData, original_filename: str) -> str:
    """
    Construct a suggested filename from extracted invoice fields.

    Args:
        invoice:           Extracted invoice data.
        original_filename: The original Drive filename (used for extension).

    Returns:
        Suggested filename string, e.g. "AWS_Software_2025-03-15_150EUR.pdf".
    """
    _, ext = os.path.splitext(original_filename)
    ext = ext.lower() or ".pdf"

    supplier = _sanitise(str(invoice.get("supplier_name") or "Unknown"))
    category = _sanitise(str(invoice.get("category") or "Other"))
    date     = str(invoice.get("invoice_date") or "0000-00-00")
    currency = _sanitise(str(invoice.get("currency") or "EUR"))

    # Format amount without trailing zeros where possible
    raw_amount = invoice.get("amount", 0) or 0
    try:
        amount_float = float(raw_amount)
        amount_str   = f"{amount_float:.0f}" if amount_float == int(amount_float) else f"{amount_float:.2f}"
    except (TypeError, ValueError):
        amount_str = "0"

    return f"{supplier}_{category}_{date}_{amount_str}{currency}{ext}"


def suggest_filename(state: AgentState) -> AgentState:
    """
    Generate a suggested filename for each extracted invoice.

    For the current implementation, this node processes the *first* invoice
    that does not yet have a suggested filename and returns immediately so
    the Streamlit UI can show the suggestion and collect user approval
    (human-in-the-loop pattern).

    Reads:
      state["extracted_data"]      — list of InvoiceData dicts
      state["current_file_index"]  — index of the invoice being reviewed

    Writes:
      state["suggested_filename"]  — the generated filename string
      state["pending_approval"]    — True (signals UI to pause for user input)
    """
    extracted: List[InvoiceData] = state.get("extracted_data", [])
    idx: int = state.get("current_file_index", 0)

    if idx >= len(extracted):
        logger.info("suggest_filename: all invoices have been reviewed.")
        return {**state, "suggested_filename": None, "pending_approval": False}

    invoice = extracted[idx]
    original_filename: str = invoice.get("original_filename", "invoice.pdf")

    try:
        filename = _build_filename(invoice, original_filename)
    except Exception as exc:
        logger.error("Failed to build filename for invoice at index %d: %s", idx, exc)
        filename = f"Invoice_{idx + 1}.pdf"

    logger.info(
        "suggest_filename[%d]: '%s' → '%s'",
        idx,
        original_filename,
        filename,
    )

    return {
        **state,
        "suggested_filename": filename,
        "pending_approval":   True,
    }
