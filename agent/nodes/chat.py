"""
LangGraph node: chat_with_expenses

Hybrid RAG + full-context chat with invoice preview support.

Strategy:
  - intent "preview"         → return Drive thumbnail + links, skip LLM
  - invoice_count > RAG_THRESHOLD → semantic retrieval (ChromaDB)
  - invoice_count ≤ RAG_THRESHOLD → full context injection (existing behaviour)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import streamlit as st
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.state import AgentState
from agent.prompts.chat_prompt import format_expense_context
from services.firestore import (
    calc_ai_cost, get_all_invoices, get_all_suppliers,
    get_line_items, log_ai_usage, log_error,
)
from services.chroma_service import (
    RAG_THRESHOLD, rebuild_if_empty, search,
)
from services.query_router import classify_intent
from services.invoice_preview import find_invoice_for_preview

logger = logging.getLogger(__name__)

CHAT_MODEL        = "gpt-4o-mini"
MAX_HISTORY_TURNS = 20

_RAG_SYSTEM = """You are Invoxa, a clear and data-grounded expense assistant.

Answer ONLY based on the invoice data below. If the answer is not in the data, say so clearly.
Never fabricate amounts, dates, or supplier names. Be concise and specific.
Format monetary amounts as: {{amount}} {{currency}} (e.g. 150.00 EUR).

INVOICE DATA (most relevant to the question):
{context}"""

_FULL_SYSTEM = """You are Invoxa, an intelligent expense management assistant.
You have access to the user's complete invoice and expense history.

Answer based on the data below. Be concise and specific.
Never fabricate amounts, dates, or supplier names.
Format monetary amounts as: {{amount}} {{currency}} (e.g. 150.00 EUR).
If the question cannot be answered from available data, say so clearly.

{context}"""


# ---------------------------------------------------------------------------
# OpenAI call (with retry)
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_openai(
    client: OpenAI,
    system_content: str,
    history: List[Dict[str, str]],
    user_query: str,
) -> Tuple[str, Any]:
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
    messages.extend(history[-(MAX_HISTORY_TURNS * 2):])
    messages.append({"role": "user", "content": user_query})
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1024,
        temperature=0.3,
    )
    return response.choices[0].message.content or "", response.usage


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------

def _build_rag_context(uid: str, question: str) -> str:
    results = search(uid=uid, query=question, top_k=8)
    if not results:
        return "No relevant invoices found."

    # Deduplicate: keep best chunk per invoice_id
    seen: Dict[str, dict] = {}
    for r in results:
        inv_id = r["invoice_id"]
        if inv_id not in seen or r["distance"] < seen[inv_id]["distance"]:
            seen[inv_id] = r

    return "\n\n".join(f"--- Invoice ---\n{r['document']}" for r in seen.values())


def _build_full_context(uid: str, invoices: list, suppliers: list) -> str:
    line_items_map: Dict[str, list] = {}
    for inv in invoices:
        if inv.get("line_items_count", 0) > 0:
            fid = inv.get("drive_file_id", "")
            if fid:
                line_items_map[fid] = get_line_items(uid, fid)

    # Base context from existing prompt helper
    base = format_expense_context(invoices, suppliers)

    # Append line items where present
    extra_lines = []
    for inv in invoices:
        fid   = inv.get("drive_file_id", "")
        items = line_items_map.get(fid, [])
        if items:
            item_strs = [
                f"{i.get('description')} x{i.get('quantity')} "
                f"@ {i.get('unit_price')} = {i.get('line_total')}"
                for i in items
            ]
            supplier = inv.get("supplier_name", "")
            date     = inv.get("invoice_date", "")
            extra_lines.append(
                f"  Line items for {supplier} {date}: " + "; ".join(item_strs)
            )

    if extra_lines:
        return base + "\n\nLINE ITEMS:\n" + "\n".join(extra_lines)
    return base


# ---------------------------------------------------------------------------
# Node entry point
# ---------------------------------------------------------------------------

def chat_with_expenses(state: AgentState) -> AgentState:
    """
    Answer a natural-language expense query with RAG + preview support.

    Reads:  state["user_id"], state["user_query"], state["chat_history"]
    Writes: state["agent_response"], state["chat_history"], state["error"]
    Extra:  state["preview_result"] — set when intent is "preview"
    """
    uid          = state.get("user_id", "")
    user_query   = state.get("user_query", "")
    chat_history: List[Dict[str, str]] = list(state.get("chat_history", []))

    if not user_query:
        return {**state, "agent_response": "Please ask a question.", "error": None}

    api_key = st.secrets.get("OPENAI_API_KEY", "")
    client  = OpenAI(api_key=api_key)

    # ── Cold-start guard ─────────────────────────────────────────
    try:
        rebuild_if_empty(uid)
    except Exception as exc:
        logger.warning("ChromaDB rebuild failed (non-fatal): %s", exc)

    # ── Intent classification ─────────────────────────────────────
    _PREVIEW_KW = (
        "show me", "show invoice", "show the invoice",
        "open invoice", "open the invoice",
        "preview invoice", "preview the invoice",
        "view invoice", "view the invoice",
        "display invoice", "display the invoice",
        "see the invoice", "see invoice",
        "latest invoice", "show latest",
    )
    q_lower = user_query.lower()
    if any(kw in q_lower for kw in _PREVIEW_KW):
        intent = "preview"
    else:
        try:
            intent = classify_intent(user_query)
        except Exception:
            intent = "general"

    # ── Preview intent ─────────────────────────────────────────────
    if intent == "preview":
        preview = find_invoice_for_preview(uid, user_query)
        if preview:
            answer = (
                f"Showing preview for **{preview['supplier']}** "
                f"— {preview['invoice_date']} — "
                f"{preview['amount']:.2f} {preview['currency']}"
            )
        else:
            answer = "Could not find that invoice. Try including the supplier name or date."

        new_history = chat_history + [
            {"role": "user",      "content": user_query},
            {"role": "assistant", "content": answer},
        ]
        return {
            **state,
            "agent_response":  answer,
            "preview_result":  preview,
            "chat_history":    new_history,
            "error":           None,
        }

    # ── Decide RAG vs full context ─────────────────────────────────
    try:
        invoices  = get_all_invoices(uid)
        suppliers = get_all_suppliers(uid)
    except Exception as exc:
        logger.error("chat node: Firestore load failed: %s", exc)
        log_error(uid, "chat_with_expenses", str(exc))
        invoices, suppliers = [], []

    use_rag = len(invoices) > RAG_THRESHOLD

    if use_rag:
        context        = _build_rag_context(uid, user_query)
        system_content = _RAG_SYSTEM.format(context=context)
    else:
        context        = _build_full_context(uid, invoices, suppliers)
        system_content = _FULL_SYSTEM.format(context=context)

    # ── Generate answer ───────────────────────────────────────────
    try:
        answer, usage = _call_openai(client, system_content, chat_history, user_query)
        if usage:
            cost = calc_ai_cost(CHAT_MODEL, usage.prompt_tokens, usage.completion_tokens)
            log_ai_usage(uid, CHAT_MODEL, "chat", usage.prompt_tokens, usage.completion_tokens, cost)
    except Exception as exc:
        msg = f"Chat request failed: {exc}"
        logger.error(msg)
        log_error(uid, "chat_with_expenses", str(exc))
        return {**state, "agent_response": "Sorry, I encountered an error. Please try again.", "error": msg}

    new_history = chat_history + [
        {"role": "user",      "content": user_query},
        {"role": "assistant", "content": answer},
    ]
    return {
        **state,
        "agent_response":  answer,
        "preview_result":  None,
        "chat_history":    new_history,
        "error":           None,
    }
