"""
Prompt templates for the chat-with-expenses node.
"""

CHAT_SYSTEM_PROMPT = """You are Invoxa, an intelligent expense management assistant. \
You have access to the user's complete invoice and expense history stored in Firestore.

Your role:
- Answer questions about the user's expenses clearly and concisely.
- Perform calculations (totals, averages, comparisons) accurately.
- Identify trends, top suppliers, category breakdowns, and anomalies.
- Always ground answers in the data provided — do not make up figures.
- When referencing specific invoices, mention the supplier name, date, and amount.
- Format monetary amounts as: {amount} {currency} (e.g. 150.00 EUR).
- If the question cannot be answered from available data, say so clearly.

Context about the user's expenses will be injected below."""


def build_chat_system_message(expense_context: str) -> dict:
    """
    Build the system message including the injected expense context.

    Args:
        expense_context: A formatted string summary of relevant invoices/stats.

    Returns:
        A message dict with role="system".
    """
    content = CHAT_SYSTEM_PROMPT
    if expense_context:
        content += f"\n\n--- Expense Data ---\n{expense_context}"
    return {"role": "system", "content": content}


def format_expense_context(invoices: list[dict], suppliers: list[dict]) -> str:
    """
    Format invoice and supplier records into a compact text context for the LLM.

    Args:
        invoices:  List of invoice dicts from Firestore.
        suppliers: List of supplier summary dicts from Firestore.

    Returns:
        A formatted multi-line string.
    """
    lines: list[str] = []

    if invoices:
        lines.append("INVOICES:")
        for inv in invoices:
            date     = inv.get("invoice_date", "unknown date")
            supplier = inv.get("supplier_name", "Unknown")
            amount   = inv.get("amount", 0)
            currency = inv.get("currency", "EUR")
            category = inv.get("category", "Other")
            month    = inv.get("month", "")
            year     = inv.get("year", "")
            lines.append(
                f"  - {supplier} | {date} | {amount} {currency} | {category} | {month} {year}"
            )

    if suppliers:
        lines.append("\nSUPPLIERS (lifetime summary):")
        for sup in suppliers:
            name     = sup.get("name", "Unknown")
            total    = sup.get("total_spend", 0)
            count    = sup.get("invoice_count", 0)
            category = sup.get("category", "Other")
            lines.append(f"  - {name} | {category} | total: {total} | invoices: {count}")

    return "\n".join(lines) if lines else "No expense data available yet."
