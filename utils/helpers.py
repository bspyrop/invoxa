"""
Utility helpers used across the Invoxa Streamlit app.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Month / year utilities
# ---------------------------------------------------------------------------

MONTHS = list(calendar.month_name)[1:]   # ["January", "February", ..., "December"]


def current_month_year() -> Tuple[str, str]:
    """Return the current month name and four-digit year string."""
    now = datetime.now(timezone.utc)
    return calendar.month_name[now.month], str(now.year)


def month_to_number(month_name: str) -> int:
    """Convert a full month name to its integer (1–12)."""
    return list(calendar.month_name).index(month_name)


def year_range(start: int = 2020) -> List[str]:
    """Return a list of year strings from `start` to current year."""
    current = datetime.now(timezone.utc).year
    return [str(y) for y in range(current, start - 1, -1)]


# ---------------------------------------------------------------------------
# Currency / amount formatting
# ---------------------------------------------------------------------------

CURRENCY_SYMBOLS: Dict[str, str] = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "JPY": "¥",
    "CHF": "CHF",
}


def format_amount(amount: float, currency: str = "EUR") -> str:
    """
    Format a monetary amount with its currency symbol.

    Args:
        amount:   Numeric amount.
        currency: ISO 4217 currency code.

    Returns:
        Formatted string, e.g. "€ 1,234.56" or "USD 1,234.56".
    """
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())
    return f"{symbol} {amount:,.2f}"


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

CATEGORIES = [
    "Software",
    "Travel",
    "Office Supplies",
    "Utilities",
    "Marketing",
    "Professional Services",
    "Other",
]

CATEGORY_COLORS: Dict[str, str] = {
    "Software":             "#4285F4",
    "Travel":               "#34A853",
    "Office Supplies":      "#FBBC05",
    "Utilities":            "#EA4335",
    "Marketing":            "#AB47BC",
    "Professional Services": "#00ACC1",
    "Other":                "#9E9E9E",
}


def get_category_color(category: str) -> str:
    """Return the hex colour associated with a category."""
    return CATEGORY_COLORS.get(category, "#9E9E9E")


# ---------------------------------------------------------------------------
# Invoice summary helpers
# ---------------------------------------------------------------------------

def compute_monthly_stats(invoices: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute summary statistics for a list of invoices.

    Args:
        invoices: List of invoice dicts from Firestore.

    Returns:
        Dict with keys: total_amount, total_tax, invoice_count,
        supplier_count, category_breakdown, supplier_breakdown.
    """
    total_amount = sum(float(i.get("amount", 0) or 0) for i in invoices)
    total_tax    = sum(float(i.get("tax_amount", 0) or 0) for i in invoices)
    suppliers    = {i.get("supplier_name", "") for i in invoices if i.get("supplier_name")}

    category_breakdown: Dict[str, float] = {}
    supplier_breakdown: Dict[str, float] = {}

    for inv in invoices:
        cat = inv.get("category", "Other")
        sup = inv.get("supplier_name", "Unknown")
        amt = float(inv.get("amount", 0) or 0)
        category_breakdown[cat] = category_breakdown.get(cat, 0) + amt
        supplier_breakdown[sup] = supplier_breakdown.get(sup, 0) + amt

    return {
        "total_amount":       total_amount,
        "total_tax":          total_tax,
        "invoice_count":      len(invoices),
        "supplier_count":     len(suppliers),
        "category_breakdown": dict(sorted(category_breakdown.items(), key=lambda x: x[1], reverse=True)),
        "supplier_breakdown": dict(sorted(supplier_breakdown.items(), key=lambda x: x[1], reverse=True)),
    }


# ---------------------------------------------------------------------------
# MIME type helpers
# ---------------------------------------------------------------------------

MIME_TO_EXT: Dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg":      ".jpg",
    "image/png":       ".png",
    "image/webp":      ".webp",
    "image/tiff":      ".tiff",
}


def mime_to_ext(mime_type: str) -> str:
    """Return the file extension (with dot) for a MIME type."""
    return MIME_TO_EXT.get(mime_type, ".pdf")


def is_image_mime(mime_type: str) -> bool:
    """Return True if the MIME type is an image (not PDF)."""
    return mime_type.startswith("image/")


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------

def truncate(text: str, max_length: int = 60, suffix: str = "…") -> str:
    """Truncate a string to max_length, appending suffix if truncated."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
