"""
Agent utility: classify_email_as_invoice

Uses GPT-4o-mini to decide whether an email likely contains an invoice.
Called directly from the Gmail scan UI — not a LangGraph state-machine node,
but follows the same pattern: prompt module, OpenAI client, retry, cost logging.
"""

from __future__ import annotations

import json
import logging
from typing import Tuple

import streamlit as st
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.prompts.email_classification_prompt import build_classification_messages
from services.firestore import calc_ai_cost, log_ai_usage

logger = logging.getLogger(__name__)

CLASSIFICATION_MODEL = "gpt-4o-mini"


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    stop=stop_after_attempt(2),
    reraise=True,
)
def _call_openai_classification(
    client: OpenAI,
    subject: str,
    sender: str,
    filenames: list[str],
) -> Tuple[bool, float, object]:
    """
    Make the GPT-4o-mini API call and parse the JSON response.

    Returns:
        (is_invoice, confidence, usage)
    """
    messages = build_classification_messages(subject, sender, filenames)
    response = client.chat.completions.create(
        model=CLASSIFICATION_MODEL,
        temperature=0,
        max_tokens=60,
        messages=messages,  # type: ignore[arg-type]
    )
    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text)
    return bool(data.get("is_invoice", False)), float(data.get("confidence", 0.0)), response.usage


def classify_email_as_invoice(
    subject: str,
    sender: str,
    filenames: list[str],
    uid: str = "",
) -> Tuple[bool, float]:
    """
    Use GPT-4o-mini to decide whether an email likely contains an invoice.

    Args:
        subject:   Email subject line.
        sender:    Sender address / display name.
        filenames: Attachment filenames (used as classification signals).
        uid:       Firebase user ID — used to log AI cost. Pass empty string to skip logging.

    Returns:
        (is_invoice, confidence) where confidence is 0.0-1.0.
    """
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    try:
        is_invoice, confidence, usage = _call_openai_classification(
            client, subject, sender, filenames
        )
        if usage and uid:
            cost = calc_ai_cost(
                CLASSIFICATION_MODEL,
                usage.prompt_tokens,
                usage.completion_tokens,
            )
            log_ai_usage(
                uid,
                CLASSIFICATION_MODEL,
                "classify_email",
                usage.prompt_tokens,
                usage.completion_tokens,
                cost,
            )
        return is_invoice, confidence
    except Exception as exc:
        logger.warning("classify_email_as_invoice failed: %s", exc)
        return False, 0.0
