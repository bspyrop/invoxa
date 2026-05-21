"""
Chat with Expenses page.

Clean chat interface with conversation history, suggested question chips,
invoice preview cards, and cold-start ChromaDB index rebuild.
"""

from __future__ import annotations

import uuid

import streamlit as st

from agent.graph import graph
from styles.apply_theme import apply_theme
from utils.session import (
    clear_chat_history,
    get_chat_history,
    get_uid,
)

SUGGESTED_QUESTIONS = [
    # Aggregation / summary
    "Total spend this month",
    "Top suppliers this year",
    "Category breakdown",
    "Find invoices over 500 EUR",
    # Product-level (powered by line items + RAG)
    "What did I buy from Amazon?",
    "Which invoice had an EC2 charge?",
    # Preview
    "Show me the latest invoice",
]


def render() -> None:
    """Render the Chat with Expenses page."""
    apply_theme()
    uid = get_uid()

    # ---- Header ----
    st.markdown("## Chat with Expenses")
    st.markdown(
        '<p style="color:#6B7A99;font-size:13px;margin-top:-10px">'
        "Ask questions about your expenses in plain language."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<hr style="border:none;border-top:0.5px solid #E2E6EF;margin:12px 0 20px">',
        unsafe_allow_html=True,
    )

    # ---- Cold-start: rebuild ChromaDB index if empty ----
    _ensure_index(uid)

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
        if msg.get("preview"):
            _render_preview_card(msg["preview"])

    # ---- Input ----
    user_input = st.chat_input("Ask anything about your expenses…")
    if user_input:
        _run_chat(uid, user_input)

    # ---- Clear button ----
    if history:
        st.markdown("")
        if st.button("Clear Conversation", use_container_width=False, type="secondary"):
            clear_chat_history()
            st.rerun()


# ---------------------------------------------------------------------------
# Cold-start helper
# ---------------------------------------------------------------------------

def _ensure_index(uid: str) -> None:
    """Show a progress bar while rebuilding ChromaDB from Firestore if empty."""
    try:
        from services.chroma_service import get_collection_count, RAG_THRESHOLD
        from services.firestore import get_all_invoices

        count         = get_collection_count(uid)
        invoice_count = len(get_all_invoices(uid))

        if count == 0 and invoice_count > 0:
            progress_bar = st.progress(0, text="Initialising search index…")

            def _on_progress(current: int, total: int) -> None:
                pct = int((current / total) * 100)
                progress_bar.progress(pct / 100, text=f"Indexing invoices… {current}/{total}")

            from services.chroma_service import rebuild_if_empty
            rebuild_if_empty(uid, progress_callback=_on_progress)
            progress_bar.empty()

        # Mode indicator
        if invoice_count > RAG_THRESHOLD:
            st.caption(f"Smart search active — {invoice_count} invoices indexed")
        else:
            st.caption(
                f"{invoice_count} invoice(s) loaded · "
                f"Smart search activates at {RAG_THRESHOLD}"
            )
    except Exception:
        pass  # non-fatal — chat still works without the indicator


# ---------------------------------------------------------------------------
# Preview card renderer
# ---------------------------------------------------------------------------

def _render_preview_card(preview: dict | None) -> None:
    """Render an invoice preview card with Drive thumbnail and links."""
    if not preview:
        st.warning(
            "Could not find that invoice. Try being more specific — "
            "include the supplier name or date."
        )
        return

    with st.container(border=True):
        st.markdown(
            f"**{preview['supplier']}** — "
            f"{preview['invoice_date']} — "
            f"{float(preview['amount']):.2f} {preview['currency']}"
        )

        # Fetch thumbnail via Drive API (returns a direct image URL, no auth needed to fetch)
        creds = st.session_state.get("google_credentials")
        if creds:
            try:
                from services.google_drive import _drive_service
                import requests as _req
                service = _drive_service(creds)
                meta = service.files().get(
                    fileId=preview["drive_file_id"],
                    fields="thumbnailLink",
                ).execute()
                thumb_url = meta.get("thumbnailLink", "")
                # Bump resolution — Drive thumbnail URLs end with =s<size>
                import re as _re
                thumb_url = _re.sub(r"=s\d+$", "=s1600", thumb_url)
                if thumb_url:
                    resp = _req.get(thumb_url, timeout=10)
                    if resp.status_code == 200:
                        caption = f"Invoice {preview.get('invoice_number', '')}".strip()
                        st.image(resp.content, caption=caption or None, use_container_width=True)
                    else:
                        st.caption("Thumbnail not available")
                else:
                    st.caption("No thumbnail generated by Google Drive yet")
            except Exception as _e:
                st.caption(f"Thumbnail error: {_e}")
        else:
            st.caption("Sign in to Google to see thumbnail")

        st.link_button("Open in Google Drive", preview["view_url"], use_container_width=True)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_PREVIEW_TRIGGERS = ("show me", "show the", "preview", "open", "display", "view the", "see the", "latest invoice", "show latest")
_INVOICE_TERMS    = ("invoice", "receipt", "document")


def _is_preview_query(query: str) -> bool:
    q = query.lower()
    for trigger in _PREVIEW_TRIGGERS:
        if trigger in q:
            # Multi-word triggers are strong enough alone
            if " " in trigger:
                return True
            # Single-word triggers need an invoice-related word in the query too
            if any(term in q for term in _INVOICE_TERMS):
                return True
    return False


def _run_chat(uid: str, query: str) -> None:
    """Run the chat action via LangGraph and render the response."""
    history = get_chat_history()

    # ── Preview shortcut (bypass graph entirely) ──────────────────
    if _is_preview_query(query):
        with st.spinner("Finding invoice…"):
            try:
                from services.invoice_preview import find_invoice_for_preview
                preview = find_invoice_for_preview(uid, query)
            except Exception:
                preview = None

        if preview:
            answer = (
                f"Showing preview for **{preview['supplier']}** "
                f"— {preview['invoice_date']} — "
                f"{float(preview['amount']):.2f} {preview['currency']}"
            )
            assistant_msg = {"role": "assistant", "content": answer, "preview": preview}
        else:
            answer = "Could not find that invoice. Try including the supplier name or date."
            assistant_msg = {"role": "assistant", "content": answer}

        new_history = history + [
            {"role": "user", "content": query},
            assistant_msg,
        ]
        st.session_state["chat_history"] = new_history
        st.rerun()
        return

    # ── Normal graph path ─────────────────────────────────────────
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state  = {
        "user_id":      uid,
        "user_query":   query,
        "chat_history": history,
        "action":       "chat",
    }

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            result = graph.invoke(state, config=config)

        answer = result.get("agent_response", "Sorry, I could not answer that.")
        st.markdown(answer)

    preview     = result.get("preview_result")
    new_history = result.get("chat_history", history)

    if preview is not None and new_history and new_history[-1].get("role") == "assistant":
        last = dict(new_history[-1])
        last["preview"] = preview
        new_history = new_history[:-1] + [last]

    st.session_state["chat_history"] = new_history

    if result.get("error"):
        st.error(result["error"])

    st.rerun()
