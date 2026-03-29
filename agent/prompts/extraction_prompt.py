"""
Prompt templates for the invoice data extraction node.
"""

EXTRACTION_SYSTEM_PROMPT = """You are an expert invoice data extractor. \
Analyze this invoice image/document and extract the following information \
in JSON format only — no other text, no markdown, no code fences.

Return exactly this JSON structure:

{
  "supplier_name":   "exact company name as shown on the invoice",
  "invoice_number":  "invoice/receipt number or null",
  "invoice_date":    "YYYY-MM-DD format or null",
  "amount":          <numeric total amount without currency symbol>,
  "currency":        "3-letter ISO currency code (e.g. EUR, USD, GBP)",
  "tax_amount":      <numeric tax/VAT amount, or 0 if not applicable>,
  "tax_rate":        <numeric percentage e.g. 20 for 20%, or null if not shown>,
  "category":        "one of: Software, Travel, Office Supplies, Utilities, Marketing, Professional Services, Other",
  "description":     "brief 1-sentence description of what was purchased"
}

Rules:
- If any field cannot be determined, use null (except amount/tax_amount which must be numeric, default 0).
- amount and tax_amount must always be numeric (float or int), never strings.
- currency must be a valid 3-letter ISO code; default to "EUR" if not clearly visible.
- category must be exactly one of the listed values.
- Do NOT include any text outside the JSON object."""


EXTRACTION_USER_PROMPT = "Extract all invoice data from the attached document."


def build_extraction_messages(image_b64: str, mime_type: str = "image/jpeg") -> list:
    """
    Build the messages list for a GPT-4o vision call to extract invoice data.

    Args:
        image_b64: Base64-encoded image or PDF page.
        mime_type: MIME type of the image (image/jpeg, image/png, image/webp).

    Returns:
        List of message dicts ready for openai.chat.completions.create().
    """
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url":    f"data:{mime_type};base64,{image_b64}",
                        "detail": "high",
                    },
                },
                {"type": "text", "text": EXTRACTION_USER_PROMPT},
            ],
        },
    ]
