"""
Chat with Expenses page.

Clean chat interface with conversation history, suggested question chips,
and source attribution for each answer.
"""

from __future__ import annotations

import uuid

import streamlit as st

from agent.graph import graph
from utils.session import (
    append_chat_message,
    clear_chat_history,
    get_chat_history,
    get_uid,
)

SUGGESTED_QUESTIONS = [
    "Total spend this month",
    "Top suppliers this year",
    "Category breakdown",
    "Find invoices over 500 EUR",
    "How much did I spend on AWS?",
    "Compare last two months",
]


def render() -> None:
    """Render the Chat with Expenses page."""
    st.title("💬 Chat with Expenses")
    uid = get_uid()

    # ---- Suggested questions ----
    st.markdown("##### Suggested questions")
    cols = st.columns(3)
    for i, question in enumerate(SUGGESTED_QUESTIONS):
        with cols[i % 3]:
            if st.button(question, key=f"chip_{i}", use_container_width=True):
                _run_chat(uid, question)

    st.markdown("---")

    # ---- Conversation history ----
    history = get_chat_history()
    for msg in history:
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        with st.chat_message(role):
            st.markdown(content)

    # ---- Input ----
    user_input = st.chat_input("Ask anything about your expenses…")
    if user_input:
        _run_chat(uid, user_input)

    # ---- Clear button ----
    if history:
        st.markdown("")
        if st.button("🗑️ Clear Conversation", use_container_width=False):
            clear_chat_history()
            st.rerun()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _run_chat(uid: str, query: str) -> None:
    """
    Run the chat action via the LangGraph compiled graph.

    Args:
        uid:   Firebase UID.
        query: User's question text.
    """
    history = get_chat_history()
    config  = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state   = {
        "user_id":      uid,
        "user_query":   query,
        "chat_history": history,
        "action":       "chat",
    }

    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            result = graph.invoke(state, config=config)

        answer = result.get("agent_response", "Sorry, I could not answer that.")
        st.markdown(answer)

    new_history = result.get("chat_history", history)
    st.session_state["chat_history"] = new_history

    if result.get("error"):
        st.error(result["error"])

    st.rerun()
