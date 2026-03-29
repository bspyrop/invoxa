"""
LangGraph agent graph definition for Invoxa.

Routing:
  START
    └─ route_action
         ├─ "process_invoices" → list_invoices → extract_invoice_data
         │                       → suggest_filename → [HITL interrupt]
         │                       → rename_and_organize → check_anomalies → END
         ├─ "generate_report"  → generate_report → END
         └─ "chat"             → chat_with_expenses → END
"""

from __future__ import annotations

import logging
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from agent.state import AgentState
from agent.nodes.list_invoices   import list_invoices
from agent.nodes.extract_data    import extract_invoice_data
from agent.nodes.suggest_filename import suggest_filename
from agent.nodes.rename_organize  import rename_and_organize
from agent.nodes.check_anomalies  import check_anomalies
from agent.nodes.generate_report  import generate_report
from agent.nodes.chat             import chat_with_expenses

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def route_action(
    state: AgentState,
) -> Literal["list_invoices", "generate_report", "chat_with_expenses"]:
    """
    Inspect state["action"] and route to the appropriate first node.

    Args:
        state: Current graph state.

    Returns:
        Name of the next node to execute.
    """
    action = state.get("action", "chat")
    if action == "process_invoices":
        return "list_invoices"
    if action == "generate_report":
        return "generate_report"
    return "chat_with_expenses"


def should_continue_processing(
    state: AgentState,
) -> Literal["suggest_filename", "check_anomalies"]:
    """
    After rename_and_organize, decide whether more invoices remain.

    Returns:
      "suggest_filename"  — if current_file_index is still within the list
      "check_anomalies"   — once all invoices have been processed
    """
    idx       = state.get("current_file_index", 0)
    extracted = state.get("extracted_data", [])
    if idx < len(extracted):
        return "suggest_filename"
    return "check_anomalies"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """
    Construct and compile the LangGraph agent graph.

    Returns:
        A compiled StateGraph with a MemorySaver checkpointer (for HITL support).
    """
    builder = StateGraph(AgentState)

    # Register nodes
    builder.add_node("list_invoices",       list_invoices)
    builder.add_node("extract_invoice_data", extract_invoice_data)
    builder.add_node("suggest_filename",    suggest_filename)
    builder.add_node("rename_and_organize", rename_and_organize)
    builder.add_node("check_anomalies",     check_anomalies)
    builder.add_node("generate_report",     generate_report)
    builder.add_node("chat_with_expenses",  chat_with_expenses)

    # Entry point: conditional routing based on action
    builder.add_conditional_edges(START, route_action)

    # Process-invoices path
    builder.add_edge("list_invoices",        "extract_invoice_data")
    builder.add_edge("extract_invoice_data", "suggest_filename")
    # suggest_filename → interrupt (HITL) is handled by the Streamlit UI
    # After user approves, the UI calls rename_and_organize
    builder.add_edge("suggest_filename",     "rename_and_organize")
    builder.add_conditional_edges(
        "rename_and_organize",
        should_continue_processing,
    )
    builder.add_edge("check_anomalies",      END)

    # Report path
    builder.add_edge("generate_report",      END)

    # Chat path
    builder.add_edge("chat_with_expenses",   END)

    checkpointer = MemorySaver()
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["rename_and_organize"],  # pause for HITL
    )


# Module-level compiled graph instance (imported by pages)
graph = build_graph()
