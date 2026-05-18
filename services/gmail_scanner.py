"""
Gmail scanner service for Invoxa.

Scans the user's Gmail inbox for unread emails containing invoice attachments,
classifies them with GPT-4o-mini, downloads attachments, and labels processed emails.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, List, Tuple

import streamlit as st
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

INVOICE_ATTACHMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

GMAIL_LABEL_NAME = "invoxa-processed"


# ---------------------------------------------------------------------------
# Service builder
# ---------------------------------------------------------------------------

def get_gmail_service(google_credentials):
    """Build and return an authenticated Gmail API v1 service client."""
    return build("gmail", "v1", credentials=google_credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Email listing
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(HttpError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def list_candidate_emails(service) -> List[Dict[str, Any]]:
    """
    Fetch unread emails with attachments not labelled invoxa-processed.
    Returns up to 30 messages with attachment metadata (no download).
    """
    query = "is:unread has:attachment -label:invoxa-processed"
    try:
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=30,
        ).execute()
    except HttpError as exc:
        logger.error("list_candidate_emails failed: %s", exc)
        raise

    messages = result.get("messages", [])
    candidates: List[Dict[str, Any]] = []

    for stub in messages:
        msg_id = stub["id"]
        try:
            msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full",
            ).execute()
        except HttpError as exc:
            logger.warning("Could not fetch message %s: %s", msg_id, exc)
            continue

        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        subject = headers.get("subject", "(no subject)")
        sender  = headers.get("from", "")
        date    = headers.get("date", "")

        attachments = _extract_attachments(msg.get("payload", {}))
        if not attachments:
            continue

        candidates.append({
            "msg_id":      msg_id,
            "subject":     subject,
            "sender":      sender,
            "date":        date,
            "attachments": attachments,
        })

    return candidates


def _extract_attachments(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Recursively extract invoice-type attachments from a Gmail message payload."""
    found: List[Dict[str, Any]] = []

    for part in payload.get("parts", []):
        mime_type = part.get("mimeType", "")
        filename  = part.get("filename", "")
        body      = part.get("body", {})
        att_id    = body.get("attachmentId")
        size      = body.get("size", 0)

        if att_id and filename and mime_type in INVOICE_ATTACHMENT_TYPES:
            found.append({
                "attachment_id": att_id,
                "filename":      filename,
                "mime_type":     mime_type,
                "size_bytes":    size,
            })

        if part.get("parts"):
            found.extend(_extract_attachments(part))

    return found


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_email_as_invoice(
    subject: str,
    sender: str,
    filenames: List[str],
) -> Tuple[bool, float]:
    """
    Use GPT-4o-mini to decide whether an email likely contains an invoice.
    Returns (is_invoice, confidence 0.0–1.0).
    Only email metadata is used — no attachment download at this stage.
    """
    client        = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    filenames_str = ", ".join(filenames) if filenames else "(none)"

    prompt = (
        "You classify whether an email contains an invoice, receipt, quote, or purchase order.\n\n"
        f"Subject: {subject}\n"
        f"From: {sender}\n"
        f"Attachment filenames: {filenames_str}\n\n"
        "Reply ONLY with valid JSON, no explanation:\n"
        '{"is_invoice": true, "confidence": 0.95}\n\n'
        "Confidence 1.0 = certain. Classify conservatively — prefer false positives over missing real invoices."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        return bool(data.get("is_invoice", False)), float(data.get("confidence", 0.0))
    except Exception as exc:
        logger.warning("classify_email_as_invoice failed: %s", exc)
        return False, 0.0


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(HttpError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
def download_attachment(service, msg_id: str, attachment_id: str) -> bytes:
    """Download a single Gmail attachment and return raw bytes."""
    attachment = service.users().messages().attachments().get(
        userId="me",
        messageId=msg_id,
        id=attachment_id,
    ).execute()
    data = attachment.get("data", "")
    # Gmail uses URL-safe base64 without padding
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


# ---------------------------------------------------------------------------
# Label management
# ---------------------------------------------------------------------------

def label_email_processed(service, msg_id: str) -> None:
    """Apply the invoxa-processed label to a Gmail message."""
    try:
        label_id = get_or_create_label(service, GMAIL_LABEL_NAME)
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": [label_id]},
        ).execute()
    except Exception as exc:
        logger.error("label_email_processed(%s) failed: %s", msg_id, exc)


def get_or_create_label(service, label_name: str) -> str:
    """Return the ID of label_name, creating it if it does not exist."""
    result = service.users().labels().list(userId="me").execute()
    for label in result.get("labels", []):
        if label.get("name") == label_name:
            return label["id"]

    created = service.users().labels().create(
        userId="me",
        body={
            "name":                  label_name,
            "labelListVisibility":   "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return created["id"]


def remove_label_from_all(service) -> None:
    """Remove invoxa-processed label from every labelled message (used by clear import history)."""
    try:
        label_id = get_or_create_label(service, GMAIL_LABEL_NAME)
        result   = service.users().messages().list(
            userId="me",
            labelIds=[label_id],
            maxResults=500,
        ).execute()
        for msg in result.get("messages", []):
            try:
                service.users().messages().modify(
                    userId="me",
                    id=msg["id"],
                    body={"removeLabelIds": [label_id]},
                ).execute()
            except Exception as exc:
                logger.warning("Failed to remove label from %s: %s", msg["id"], exc)
    except Exception as exc:
        logger.error("remove_label_from_all failed: %s", exc)
