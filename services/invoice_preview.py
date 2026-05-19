"""
services/invoice_preview.py

Finds a specific invoice in ChromaDB and returns Google Drive preview URLs.
Used when the chat intent is "preview" (user wants to see an invoice file).
"""

from __future__ import annotations

from typing import Optional

from services.chroma_service import CHUNK_TYPE_PTR, search

DRIVE_THUMBNAIL_URL = "https://drive.google.com/thumbnail?id={file_id}&sz=w800"
DRIVE_PREVIEW_URL   = "https://drive.google.com/file/d/{file_id}/preview"
DRIVE_VIEW_URL      = "https://drive.google.com/file/d/{file_id}/view"


def find_invoice_for_preview(uid: str, query: str) -> Optional[dict]:
    """
    Semantic search over pointer chunks to find the best-matching invoice.
    Returns a dict with preview URLs, or None if nothing found.
    """
    results = search(uid=uid, query=query, top_k=3, chunk_types=[CHUNK_TYPE_PTR])
    if not results:
        return None

    best          = results[0]
    meta          = best["metadata"]
    drive_file_id = meta.get("drive_file_id", "")
    if not drive_file_id:
        return None

    return {
        "supplier":       meta.get("supplier", "Unknown"),
        "invoice_date":   meta.get("invoice_date", ""),
        "invoice_number": meta.get("invoice_number", ""),
        "amount":         meta.get("amount", 0),
        "currency":       meta.get("currency", "EUR"),
        "drive_file_id":  drive_file_id,
        "thumbnail_url":  DRIVE_THUMBNAIL_URL.format(file_id=drive_file_id),
        "preview_url":    DRIVE_PREVIEW_URL.format(file_id=drive_file_id),
        "view_url":       DRIVE_VIEW_URL.format(file_id=drive_file_id),
        "distance":       best["distance"],
    }
