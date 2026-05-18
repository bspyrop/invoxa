"""
Prompt template for the email-to-invoice classification node.
"""

EMAIL_CLASSIFICATION_SYSTEM_PROMPT = (
    "You classify whether an email contains an invoice, receipt, quote, or purchase order. "
    "Reply ONLY with valid JSON and no other text: "
    '{\"is_invoice\": true, \"confidence\": 0.95}. '
    "Confidence 1.0 = certain invoice. "
    "Classify conservatively — prefer false positives over missing real invoices."
)


def build_classification_messages(
    subject: str,
    sender: str,
    filenames: list[str],
) -> list:
    """
    Build the messages list for a GPT-4o-mini classification call.

    Args:
        subject:   Email subject line.
        sender:    Sender address.
        filenames: List of attachment filenames.

    Returns:
        List of message dicts ready for openai.chat.completions.create().
    """
    filenames_str = ", ".join(filenames) if filenames else "(none)"
    user_content = (
        f"Subject: {subject}\n"
        f"From: {sender}\n"
        f"Attachment filenames: {filenames_str}"
    )
    return [
        {"role": "system", "content": EMAIL_CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]
