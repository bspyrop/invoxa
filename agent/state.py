"""
LangGraph agent state definition for the Invoxa expense agent.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class InvoiceData(TypedDict, total=False):
    """Extracted invoice fields returned by the OCR/LLM node."""
    supplier_name:    str
    invoice_number:   Optional[str]
    invoice_date:     Optional[str]   # ISO YYYY-MM-DD
    amount:           float
    currency:         str
    tax_amount:       float
    tax_rate:         Optional[float]
    category:         str
    description:      str
    # Populated after Drive operations
    original_filename: str
    renamed_filename:  Optional[str]
    drive_file_id:     str
    month:             str
    year:              str
    processed_at:      str


class DriveFile(TypedDict, total=False):
    """Metadata for a file returned by the Google Drive listing node."""
    id:          str
    name:        str
    mimeType:    str
    size:        Optional[str]
    createdTime: Optional[str]
    webViewLink: Optional[str]


class AnomalyWarning(TypedDict):
    """Structured anomaly warning emitted by the check_anomalies node."""
    type:    str   # "duplicate" | "missing_supplier" | "unusual_amount"
    message: str
    details: Dict[str, Any]


class AgentState(TypedDict, total=False):
    """Full state object threaded through every LangGraph node."""

    # ---- Identity ----
    user_id: str

    # ---- Routing ----
    action: str  # "process_invoices" | "generate_report" | "chat"

    # ---- Time scope ----
    month: str   # e.g. "January"
    year:  str   # e.g. "2025"

    # ---- Drive listing ----
    invoices: List[DriveFile]

    # ---- Extraction ----
    extracted_data:     List[InvoiceData]
    current_file_index: int          # which file we're currently processing
    current_invoice:    DriveFile    # the file being processed right now

    # ---- Human-in-the-loop ----
    pending_approval:   bool         # True while waiting for user confirmation
    user_approved_data: Optional[InvoiceData]   # data after user edits
    suggested_filename: Optional[str]

    # ---- Organising ----
    renamed_files: List[Dict[str, str]]   # [{old_name, new_name, drive_id}]

    # ---- Anomalies ----
    anomaly_warnings: List[AnomalyWarning]

    # ---- Report ----
    report_url:   Optional[str]
    sheet_id:     Optional[str]

    # ---- Chat ----
    chat_history: List[Dict[str, str]]   # [{role, content}]
    user_query:   Optional[str]
    agent_response: Optional[str]

    # ---- LangChain messages (managed by add_messages reducer) ----
    messages: Annotated[List[Any], add_messages]

    # ---- Error tracking ----
    error: Optional[str]
