"""
LangGraph node: check_anomalies

Analyses extracted invoices for:
  1. Duplicate invoices (same supplier + similar amount in same month)
  2. Missing recurring suppliers (seen in prior months but absent this month)
  3. Unusually high amounts (> 2× the supplier's historical average)
"""

from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List

from agent.state import AgentState, AnomalyWarning, InvoiceData
from services.firestore import get_all_invoices, get_suppliers_for_month

logger = logging.getLogger(__name__)

# Suppliers that appear in N or more prior months are considered "recurring"
RECURRING_THRESHOLD = 2


def _find_duplicates(
    new_invoices: List[InvoiceData],
    existing_invoices: List[Dict[str, Any]],
    tolerance: float = 0.01,
) -> List[AnomalyWarning]:
    """
    Detect invoices in new_invoices that closely match something already in Firestore.

    Args:
        new_invoices:      Freshly extracted invoices for this run.
        existing_invoices: All Firestore invoices for this user/month.
        tolerance:         Fractional amount tolerance for amount comparison.

    Returns:
        List of AnomalyWarning dicts.
    """
    warnings: List[AnomalyWarning] = []
    for inv in new_invoices:
        supplier = (inv.get("supplier_name") or "").lower()
        amount   = float(inv.get("amount", 0) or 0)
        for ex in existing_invoices:
            if (ex.get("supplier_name") or "").lower() != supplier:
                continue
            ex_amount = float(ex.get("amount", 0) or 0)
            if ex_amount == 0:
                continue
            diff = abs(ex_amount - amount) / max(ex_amount, 1)
            if diff <= tolerance:
                warnings.append(
                    AnomalyWarning(
                        type="duplicate",
                        message=(
                            f"Possible duplicate: {inv.get('supplier_name')} "
                            f"{amount} {inv.get('currency', '')} — matches an existing invoice."
                        ),
                        details={
                            "new_invoice":      inv.get("original_filename"),
                            "existing_invoice": ex.get("original_filename"),
                            "amount":           amount,
                        },
                    )
                )
    return warnings


def _find_missing_recurring(
    uid: str,
    current_month: str,
    current_year: str,
    new_invoices: List[InvoiceData],
    all_invoices: List[Dict[str, Any]],
) -> List[AnomalyWarning]:
    """
    Identify suppliers that regularly appeared in prior months but are absent now.

    Args:
        uid:           Firebase UID.
        current_month: Month being processed (e.g. "March").
        current_year:  Year being processed (e.g. "2025").
        new_invoices:  Freshly extracted invoices.
        all_invoices:  All Firestore invoices for this user.

    Returns:
        List of AnomalyWarning dicts.
    """
    from collections import defaultdict

    current_suppliers = {
        (inv.get("supplier_name") or "").lower()
        for inv in new_invoices
    }

    # Count how many distinct months each supplier appeared in (excluding current)
    supplier_months: Dict[str, set] = defaultdict(set)
    for inv in all_invoices:
        if inv.get("month") == current_month and inv.get("year") == current_year:
            continue
        sup = (inv.get("supplier_name") or "").lower()
        if sup:
            supplier_months[sup].add(f"{inv.get('month', '')}_{inv.get('year', '')}")

    warnings: List[AnomalyWarning] = []
    for sup, months in supplier_months.items():
        if len(months) >= RECURRING_THRESHOLD and sup not in current_suppliers:
            warnings.append(
                AnomalyWarning(
                    type="missing_supplier",
                    message=(
                        f"Recurring supplier '{sup.title()}' is missing from "
                        f"{current_month} {current_year} (appeared in "
                        f"{len(months)} previous month(s))."
                    ),
                    details={"supplier": sup, "previous_months": len(months)},
                )
            )
    return warnings


def _find_unusual_amounts(
    new_invoices: List[InvoiceData],
    all_invoices: List[Dict[str, Any]],
    multiplier: float = 2.0,
) -> List[AnomalyWarning]:
    """
    Flag invoices whose amounts are more than `multiplier`× the supplier average.

    Args:
        new_invoices:  Freshly extracted invoices.
        all_invoices:  All Firestore invoices.
        multiplier:    Threshold multiplier (default 2.0 = double the average).

    Returns:
        List of AnomalyWarning dicts.
    """
    from collections import defaultdict

    supplier_amounts: Dict[str, List[float]] = defaultdict(list)
    for inv in all_invoices:
        sup = (inv.get("supplier_name") or "").lower()
        amt = float(inv.get("amount", 0) or 0)
        if sup and amt > 0:
            supplier_amounts[sup].append(amt)

    warnings: List[AnomalyWarning] = []
    for inv in new_invoices:
        sup    = (inv.get("supplier_name") or "").lower()
        amount = float(inv.get("amount", 0) or 0)
        hist   = supplier_amounts.get(sup, [])
        if len(hist) < 2 or amount == 0:
            continue
        avg = statistics.mean(hist)
        if amount > avg * multiplier:
            warnings.append(
                AnomalyWarning(
                    type="unusual_amount",
                    message=(
                        f"Unusually high amount for {inv.get('supplier_name')}: "
                        f"{amount} {inv.get('currency', '')} "
                        f"(historical avg: {avg:.2f})."
                    ),
                    details={
                        "supplier":    inv.get("supplier_name"),
                        "amount":      amount,
                        "average":     round(avg, 2),
                        "multiplier":  multiplier,
                    },
                )
            )
    return warnings


def check_anomalies(state: AgentState) -> AgentState:
    """
    Run all anomaly checks on freshly extracted invoices.

    Reads:
      state["user_id"]
      state["extracted_data"]
      state["month"], state["year"]

    Writes:
      state["anomaly_warnings"] — list of AnomalyWarning dicts
    """
    uid           = state.get("user_id", "")
    month         = state.get("month", "")
    year          = state.get("year", "")
    new_invoices: List[InvoiceData] = state.get("extracted_data", [])

    if not new_invoices:
        return {**state, "anomaly_warnings": []}

    try:
        all_invoices = get_all_invoices(uid)
    except Exception as exc:
        logger.error("check_anomalies: failed to load Firestore data: %s", exc)
        return {**state, "anomaly_warnings": []}

    # Exclude the just-processed invoices from the "existing" baseline
    existing_file_ids = {inv.get("drive_file_id") for inv in new_invoices}
    existing_invoices = [
        inv for inv in all_invoices
        if inv.get("drive_file_id") not in existing_file_ids
    ]

    warnings: List[AnomalyWarning] = []
    warnings += _find_duplicates(new_invoices, existing_invoices)
    warnings += _find_missing_recurring(uid, month, year, new_invoices, existing_invoices)
    warnings += _find_unusual_amounts(new_invoices, existing_invoices)

    logger.info("check_anomalies: %d warning(s) detected.", len(warnings))
    return {**state, "anomaly_warnings": warnings}
