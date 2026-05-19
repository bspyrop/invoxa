"""
services/chroma_service.py

ChromaDB vector store service for Invoxa RAG.
Per-user collections keyed by Firebase UID.
Embedding model: text-embedding-3-small (OpenAI).
All other modules interact with ChromaDB only through this file.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHROMA_PATH       = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_db")
EMBEDDING_MODEL   = "text-embedding-3-small"
CHUNK_TYPE_HEADER = "header"    # invoice-level summary
CHUNK_TYPE_ITEMS  = "line_items"  # product descriptions
CHUNK_TYPE_PTR    = "pointer"   # lightweight pointer for preview lookup
RAG_THRESHOLD     = 50          # switch from full-context to RAG above this
TOP_K             = 8           # chunks to retrieve per query


# ---------------------------------------------------------------------------
# Client singletons
# ---------------------------------------------------------------------------

_chroma_client = None
_openai_client = None


def _get_api_key() -> str:
    """Read OpenAI API key from env var first, then st.secrets."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("OPENAI_API_KEY", "")
        except Exception:
            pass
    return key


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        from chromadb.config import Settings
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=CHROMA_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
    return _chroma_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=_get_api_key())
    return _openai_client


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def get_collection(uid: str):
    """Get or create the per-user ChromaDB collection."""
    return get_chroma_client().get_or_create_collection(
        name=f"invoices_{uid}",
        metadata={"hnsw:space": "cosine"},
    )


def get_collection_count(uid: str) -> int:
    """Return total number of chunks indexed for this user."""
    try:
        return get_collection(uid).count()
    except Exception:
        return 0


def is_indexed(uid: str, invoice_id: str) -> bool:
    """Check if an invoice header chunk is already in the index."""
    try:
        result = get_collection(uid).get(ids=[f"{invoice_id}_{CHUNK_TYPE_HEADER}"])
        return len(result["ids"]) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def embed(texts: List[str]) -> List[List[float]]:
    """Embed a list of texts with text-embedding-3-small. Batches ≤100 texts."""
    client   = _get_openai_client()
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def index_invoice(uid: str, invoice: Dict[str, Any], line_items: List[Dict[str, Any]]) -> int:
    """
    Index one invoice into ChromaDB as 2–3 chunks (header + optional items + pointer).
    Uses upsert — safe to call multiple times on the same invoice.
    Returns number of chunks written.
    """
    invoice_id = invoice.get("drive_file_id") or str(uuid.uuid4())

    # shared metadata subset
    shared_meta = {
        "invoice_id":    invoice_id,
        "supplier":      invoice.get("supplier_name", ""),
        "invoice_date":  invoice.get("invoice_date", ""),
        "amount":        float(invoice.get("amount") or 0),
        "currency":      invoice.get("currency", "EUR"),
        "category":      invoice.get("category", ""),
        "drive_file_id": invoice.get("drive_file_id", ""),
    }

    documents: List[str] = []
    metadatas: List[dict] = []
    ids:       List[str] = []

    # ── Chunk 1: Header ──────────────────────────────────────────
    header_text = (
        f"Supplier: {invoice.get('supplier_name', '')}. "
        f"Date: {invoice.get('invoice_date', '')}. "
        f"Amount: {invoice.get('amount', 0)} {invoice.get('currency', '')}. "
        f"Category: {invoice.get('category', '')}. "
        f"Description: {invoice.get('description', '')}. "
        f"Tax: {invoice.get('tax_amount', 0)} ({invoice.get('tax_rate', 0)}%). "
        f"Invoice number: {invoice.get('invoice_number', '')}."
    )
    documents.append(header_text)
    metadatas.append({**shared_meta, "chunk_type": CHUNK_TYPE_HEADER,
                      "source": invoice.get("source", "manual")})
    ids.append(f"{invoice_id}_{CHUNK_TYPE_HEADER}")

    # ── Chunk 2: Line items (only if present) ────────────────────
    if line_items:
        parts = []
        for item in line_items:
            parts.append(
                f"{item.get('description', '')} "
                f"x{item.get('quantity', 1)} {item.get('unit', '')} "
                f"@ {item.get('unit_price', 0)} = {item.get('line_total', 0)}"
                .strip()
            )
        items_text = (
            f"Supplier: {invoice.get('supplier_name', '')}. "
            f"Date: {invoice.get('invoice_date', '')}. "
            f"Products and services: " + "; ".join(parts) + "."
        )
        documents.append(items_text)
        metadatas.append({**shared_meta, "chunk_type": CHUNK_TYPE_ITEMS,
                          "source": invoice.get("source", "manual")})
        ids.append(f"{invoice_id}_{CHUNK_TYPE_ITEMS}")

    # ── Chunk 3: Pointer (lightweight, for preview lookup) ───────
    ptr_text = (
        f"Invoice from {invoice.get('supplier_name', '')} "
        f"dated {invoice.get('invoice_date', '')} "
        f"number {invoice.get('invoice_number', '')} "
        f"amount {invoice.get('amount', 0)} {invoice.get('currency', '')}."
    )
    documents.append(ptr_text)
    metadatas.append({**shared_meta, "chunk_type": CHUNK_TYPE_PTR,
                      "invoice_number": invoice.get("invoice_number", "")})
    ids.append(f"{invoice_id}_{CHUNK_TYPE_PTR}")

    vectors = embed(documents)
    get_collection(uid).upsert(
        documents=documents,
        embeddings=vectors,
        metadatas=metadatas,
        ids=ids,
    )
    return len(documents)


def delete_invoice_chunks(uid: str, invoice_id: str) -> None:
    """Remove all chunks for a given invoice from ChromaDB."""
    collection = get_collection(uid)
    for chunk_type in (CHUNK_TYPE_HEADER, CHUNK_TYPE_ITEMS, CHUNK_TYPE_PTR):
        try:
            collection.delete(ids=[f"{invoice_id}_{chunk_type}"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

def search(
    uid: str,
    query: str,
    top_k: int = TOP_K,
    chunk_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Semantic search over the user's invoice chunks.
    Returns list of {document, metadata, distance, invoice_id}, sorted by relevance.
    """
    collection = get_collection(uid)
    if collection.count() == 0:
        return []

    query_vector = embed([query])[0]

    where = None
    if chunk_types:
        if len(chunk_types) == 1:
            where = {"chunk_type": {"$eq": chunk_types[0]}}
        else:
            where = {"chunk_type": {"$in": chunk_types}}

    kwargs: Dict[str, Any] = dict(
        query_embeddings=[query_vector],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    output = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append({"document": doc, "metadata": meta,
                       "distance": dist, "invoice_id": meta.get("invoice_id", "")})
    return output


# ---------------------------------------------------------------------------
# Cold-start rebuild
# ---------------------------------------------------------------------------

def rebuild_if_empty(uid: str, progress_callback: Optional[Callable] = None) -> None:
    """
    Rebuild the ChromaDB collection from Firestore if it is empty.
    Call this at the top of the chat page on every Streamlit restart.
    """
    if get_collection_count(uid) > 0:
        return

    from services.firestore import get_all_invoices, get_line_items

    invoices = get_all_invoices(uid)
    total    = len(invoices)
    if not total:
        return

    for i, invoice in enumerate(invoices):
        file_id    = invoice.get("drive_file_id", "")
        line_items = get_line_items(uid, file_id) if invoice.get("line_items_count", 0) > 0 else []
        try:
            index_invoice(uid, invoice, line_items)
        except Exception as exc:
            logger.warning("rebuild_if_empty: failed to index %s: %s", file_id, exc)
        if progress_callback:
            progress_callback(i + 1, total)
