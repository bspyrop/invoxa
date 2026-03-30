"""
Firestore service — all read/write operations for Invoxa.

Data model:
  users/{uid}/                          ← user profile document
  users/{uid}/invoices/{invoice_id}/    ← extracted invoice records
  users/{uid}/suppliers/{supplier_name}/ ← long-term supplier memory
  users/{uid}/logs/{log_id}/            ← error / activity logs
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import firebase_admin
import streamlit as st
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Initialisation helper
# ---------------------------------------------------------------------------

def _db() -> Any:
    """Return a Firestore client, initialising Firebase Admin on first call."""
    try:
        firebase_admin.get_app()
    except ValueError:
        cred_dict = dict(st.secrets["firebase_admin"])
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

def get_user_profile(uid: str) -> Optional[Dict[str, Any]]:
    """Fetch the user profile document."""
    try:
        doc = _db().collection("users").document(uid).get()
        return doc.to_dict() if doc.exists else None
    except Exception as exc:
        logger.error("get_user_profile(%s) failed: %s", uid, exc)
        return None


def update_user_settings(uid: str, settings: Dict[str, Any]) -> None:
    """Merge arbitrary settings into the user profile document."""
    try:
        _db().collection("users").document(uid).set(settings, merge=True)
    except Exception as exc:
        logger.error("update_user_settings(%s) failed: %s", uid, exc)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

def save_invoice(uid: str, invoice_id: str, data: Dict[str, Any]) -> None:
    """
    Create or overwrite an invoice document under users/{uid}/invoices/.

    Args:
        uid:        Firebase UID of the owner.
        invoice_id: Unique ID (usually the Google Drive file ID).
        data:       Extracted invoice fields.
    """
    try:
        data["processed_at"] = datetime.now(timezone.utc).isoformat()
        _db().collection("users").document(uid).collection("invoices").document(invoice_id).set(data)
        _update_supplier_memory(uid, data)
    except Exception as exc:
        logger.error("save_invoice(%s, %s) failed: %s", uid, invoice_id, exc)


def get_invoice(uid: str, invoice_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single invoice document."""
    try:
        doc = (
            _db()
            .collection("users")
            .document(uid)
            .collection("invoices")
            .document(invoice_id)
            .get()
        )
        return doc.to_dict() if doc.exists else None
    except Exception as exc:
        logger.error("get_invoice(%s, %s) failed: %s", uid, invoice_id, exc)
        return None


def get_invoices_for_month(uid: str, month: str, year: str) -> List[Dict[str, Any]]:
    """Return all invoices for a specific month/year."""
    try:
        docs = (
            _db()
            .collection("users")
            .document(uid)
            .collection("invoices")
            .where("month", "==", month)
            .where("year", "==", year)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception as exc:
        logger.error("get_invoices_for_month(%s) failed: %s", uid, exc)
        return []


def get_all_invoices(uid: str) -> List[Dict[str, Any]]:
    """Return every invoice for a user (used by the chat node for context)."""
    try:
        docs = (
            _db()
            .collection("users")
            .document(uid)
            .collection("invoices")
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception as exc:
        logger.error("get_all_invoices(%s) failed: %s", uid, exc)
        return []


def get_invoices_for_year(uid: str, year: str) -> List[Dict[str, Any]]:
    """Return all invoices for a given year."""
    try:
        docs = (
            _db()
            .collection("users")
            .document(uid)
            .collection("invoices")
            .where("year", "==", year)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception as exc:
        logger.error("get_invoices_for_year(%s, %s) failed: %s", uid, year, exc)
        return []


def delete_invoice(uid: str, invoice_id: str) -> None:
    """Delete a single invoice document from Firestore."""
    try:
        _db().collection("users").document(uid).collection("invoices").document(invoice_id).delete()
        logger.info("Deleted invoice %s for user %s", invoice_id, uid)
    except Exception as exc:
        logger.error("delete_invoice(%s, %s) failed: %s", uid, invoice_id, exc)
        raise


def get_recent_invoices(uid: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Return the most recently processed invoices, with _doc_id injected."""
    try:
        docs = (
            _db()
            .collection("users")
            .document(uid)
            .collection("invoices")
            .order_by("processed_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        results = []
        for d in docs:
            row = d.to_dict()
            row["_doc_id"] = d.id  # always available regardless of stored fields
            results.append(row)
        return results
    except Exception as exc:
        logger.error("get_recent_invoices(%s) failed: %s", uid, exc)
        return []


# ---------------------------------------------------------------------------
# Supplier long-term memory
# ---------------------------------------------------------------------------

def _update_supplier_memory(uid: str, invoice: Dict[str, Any]) -> None:
    """
    Upsert the supplier summary document whenever an invoice is saved.

    Args:
        uid:     Firebase UID.
        invoice: Extracted invoice dict (must include supplier_name, amount, category).
    """
    supplier_name = invoice.get("supplier_name")
    if not supplier_name:
        return

    try:
        db      = _db()
        safe_id = supplier_name.replace("/", "_").replace(".", "_")
        ref     = db.collection("users").document(uid).collection("suppliers").document(safe_id)
        doc     = ref.get()
        now     = datetime.now(timezone.utc).isoformat()
        amount  = float(invoice.get("amount", 0) or 0)

        if doc.exists:
            existing = doc.to_dict()
            ref.update(
                {
                    "total_spend":    existing.get("total_spend", 0) + amount,
                    "invoice_count":  existing.get("invoice_count", 0) + 1,
                    "last_seen":      now,
                    "category":       invoice.get("category", existing.get("category", "Other")),
                }
            )
        else:
            ref.set(
                {
                    "name":          supplier_name,
                    "category":      invoice.get("category", "Other"),
                    "total_spend":   amount,
                    "invoice_count": 1,
                    "first_seen":    now,
                    "last_seen":     now,
                }
            )
    except Exception as exc:
        logger.error("_update_supplier_memory failed: %s", exc)


def get_all_suppliers(uid: str) -> List[Dict[str, Any]]:
    """Return all supplier summary documents for a user."""
    try:
        docs = (
            _db()
            .collection("users")
            .document(uid)
            .collection("suppliers")
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception as exc:
        logger.error("get_all_suppliers(%s) failed: %s", uid, exc)
        return []


def get_suppliers_for_month(uid: str, month: str, year: str) -> List[str]:
    """
    Return the list of unique supplier names that had invoices in a given month.
    """
    invoices = get_invoices_for_month(uid, month, year)
    return list({inv.get("supplier_name", "") for inv in invoices if inv.get("supplier_name")})


# ---------------------------------------------------------------------------
# Error / activity logging
# ---------------------------------------------------------------------------

def log_error(uid: str, context: str, error: str, details: Optional[Dict] = None) -> None:
    """
    Append an error record to users/{uid}/logs/.

    Args:
        uid:     Firebase UID (may be "anonymous" if not authenticated).
        context: Short label describing where the error occurred.
        error:   Error message string.
        details: Optional extra data dict.
    """
    try:
        db  = _db()
        doc = {
            "context":   context,
            "error":     str(error),
            "details":   details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        db.collection("users").document(uid).collection("logs").add(doc)
    except Exception as exc:
        logger.error("log_error failed (could not write to Firestore): %s", exc)


def log_activity(uid: str, action: str, details: Optional[Dict] = None) -> None:
    """
    Append an activity record to users/{uid}/logs/.

    Args:
        uid:     Firebase UID.
        action:  Short description of the action (e.g. "invoice_processed").
        details: Optional extra data dict.
    """
    try:
        db  = _db()
        doc = {
            "action":    action,
            "details":   details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        db.collection("users").document(uid).collection("logs").add(doc)
    except Exception as exc:
        logger.error("log_activity failed: %s", exc)
