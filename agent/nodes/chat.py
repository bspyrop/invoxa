"""
LangGraph node: chat_with_expenses

Handles natural-language queries about the user's expense data.
Uses GPT-4o-mini with full conversation history and Firestore context.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import streamlit as st
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.state import AgentState
from agent.prompts.chat_prompt import build_chat_system_message, format_expense_context
from services.firestore import calc_ai_cost, get_all_invoices, get_all_suppliers, log_ai_usage, log_error

logger = logging.getLogger(__name__)

CHAT_MODEL = "gpt-4o-mini"
MAX_HISTORY_TURNS = 20   # keep last N turns to stay within context window


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_openai_chat(
    client: OpenAI,
    system_message: dict,
    history: List[Dict[str, str]],
    user_query: str,
) -> Tuple[str, Any]:
    """
    Send a chat request to GPT-4o-mini and return (reply, usage).
    """
    messages: List[Dict[str, Any]] = [system_message]

    trimmed_history = history[-(MAX_HISTORY_TURNS * 2):]
    messages.extend(trimmed_history)
    messages.append({"role": "user", "content": user_query})

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1024,
        temperature=0.3,
    )
    return response.choices[0].message.content or "", response.usage


def chat_with_expenses(state: AgentState) -> AgentState:
    """
    Answer a user's natural-language expense query.

    Reads:
      state["user_id"]
      state["user_query"]    — the user's question
      state["chat_history"]  — prior conversation turns

    Writes:
      state["agent_response"] — the assistant's answer
      state["chat_history"]   — updated with new turn appended
      state["error"]          — error string on failure
    """
    uid         = state.get("user_id", "")
    user_query  = state.get("user_query", "")
    chat_history: List[Dict[str, str]] = list(state.get("chat_history", []))

    if not user_query:
        return {**state, "agent_response": "Please ask a question.", "error": None}

    api_key = st.secrets.get("OPENAI_API_KEY", "")
    client  = OpenAI(api_key=api_key)

    # Load expense context from Firestore
    try:
        invoices  = get_all_invoices(uid)
        suppliers = get_all_suppliers(uid)
    except Exception as exc:
        logger.error("chat node: failed to load Firestore data: %s", exc)
        log_error(uid, "chat_with_expenses", str(exc))
        invoices  = []
        suppliers = []

    expense_context = format_expense_context(invoices, suppliers)
    system_message  = build_chat_system_message(expense_context)

    try:
        answer, usage = _call_openai_chat(client, system_message, chat_history, user_query)
        if usage:
            cost = calc_ai_cost(CHAT_MODEL, usage.prompt_tokens, usage.completion_tokens)
            log_ai_usage(uid, CHAT_MODEL, "chat", usage.prompt_tokens, usage.completion_tokens, cost)
    except Exception as exc:
        msg = f"Chat request failed: {exc}"
        logger.error(msg)
        log_error(uid, "chat_with_expenses", str(exc))
        return {**state, "agent_response": "Sorry, I encountered an error. Please try again.", "error": msg}

    # Append new turn to history
    chat_history.append({"role": "user",      "content": user_query})
    chat_history.append({"role": "assistant", "content": answer})

    return {
        **state,
        "agent_response": answer,
        "chat_history":   chat_history,
        "error":          None,
    }
