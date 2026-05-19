"""
services/query_router.py

Classifies a user chat message into one of three intents:
  "preview"  — user wants to see / open a specific invoice file
  "search"   — user asks about purchased items or specific invoice content
  "general"  — aggregation question answered from full context

Uses a single cheap gpt-4o-mini call (max_tokens=15).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PREVIEW_KEYWORDS = (
    "show me",
    "open invoice",
    "open the invoice",
    "display invoice",
    "display the invoice",
    "view invoice",
    "view the invoice",
    "see the invoice",
    "see invoice",
    "preview invoice",
    "preview the invoice",
    "show invoice",
    "show the invoice",
    "latest invoice",
    "show latest",
)

_ROUTER_PROMPT = """Classify this expense chat message into exactly one word.

Message: "{question}"

Rules:
- Reply "preview" if the user wants to SEE, OPEN, SHOW, VIEW, or DISPLAY a specific invoice file or PDF
- Reply "search" if the user asks WHAT WAS PURCHASED, WHAT PRODUCTS, WHICH ITEMS, or searches for specific content inside invoices
- Reply "general" for all other expense questions (totals, summaries, comparisons, supplier lists)

Reply with exactly one word: search, preview, or general"""


def _get_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("OPENAI_API_KEY", "")
        except Exception:
            pass
    return key


def classify_intent(question: str) -> str:
    """
    Returns one of: "search", "preview", "general".
    Keyword pre-match runs before the LLM to catch obvious preview requests.
    Defaults to "search" on any error.
    """
    q_lower = question.lower()
    if any(kw in q_lower for kw in _PREVIEW_KEYWORDS):
        return "preview"

    try:
        from openai import OpenAI
        client   = OpenAI(api_key=_get_api_key())
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": _ROUTER_PROMPT.format(question=question)}],
            max_tokens=15,
            temperature=0,
        )
        intent = (response.choices[0].message.content or "").strip().lower()
        if intent not in ("search", "preview", "general"):
            intent = "search"
        return intent
    except Exception as exc:
        logger.warning("classify_intent failed: %s", exc)
        return "search"
