"""Compiled LangGraph workflow for the current equity-research pipeline."""

from langgraph.graph import END, START, StateGraph

from ..agents.aggregator import aggregator_node
from ..agents.competitive import competitive_analyst_node
from ..agents.qualitative import qualitative_analyst_node
from ..agents.quant import quant_analyst_node
from ..agents.post_quant_reviewer import (
    post_quant_reviewer_node,
    route_after_post_quant_review,
)
from ..agents.reviewer import valuation_assumption_reviewer_node
from ..agents.sensitivity import sensitivity_analyst_node
from ..agents.valuation_router import (
    route_valuation_method,
    unsupported_financial_node,
    valuation_router_node,
)
from ..agents.writer import lead_writer_node
from .state import EquityResearchState


def build_research_graph():
    """
    Build the current flow.

    Competitive and Qualitative run in parallel after aggregation. The aggregator
    harvests similar-stock candidates and 10-K bond ISINs on its own. Competitive,
    Qualitative, the assumption reviewer, and the writer must call OpenAI; there
    is no silent deterministic desk. Quant remains Python for WACC and FCFF. The
    reviewer only accepts or rejects bounded Python DCF candidates.
    """
    workflow = StateGraph(EquityResearchState)
    workflow.add_node("aggregator", aggregator_node)
    workflow.add_node("competitive_analyst", competitive_analyst_node)
    workflow.add_node("qualitative_analyst", qualitative_analyst_node)
    workflow.add_node("valuation_router", valuation_router_node)
    workflow.add_node(
        "valuation_assumption_reviewer",
        valuation_assumption_reviewer_node,
    )
    workflow.add_node("quant_analyst", quant_analyst_node)
    workflow.add_node("post_quant_reviewer", post_quant_reviewer_node)
    workflow.add_node("sensitivity_analyst", sensitivity_analyst_node)
    workflow.add_node("lead_writer", lead_writer_node)
    workflow.add_node("unsupported_financial", unsupported_financial_node)

    workflow.add_edge(START, "aggregator")
    workflow.add_edge("aggregator", "competitive_analyst")
    workflow.add_edge("aggregator", "qualitative_analyst")
    workflow.add_edge(
        ["competitive_analyst", "qualitative_analyst"],
        "valuation_router",
    )
    workflow.add_conditional_edges(
        "valuation_router",
        route_valuation_method,
        {
            "corporate_fcff": "valuation_assumption_reviewer",
            "unsupported_financial": "unsupported_financial",
        },
    )
    workflow.add_edge("valuation_assumption_reviewer", "quant_analyst")
    workflow.add_edge("quant_analyst", "post_quant_reviewer")
    workflow.add_conditional_edges(
        "post_quant_reviewer",
        route_after_post_quant_review,
        {
            "retry_quant": "quant_analyst",
            "continue": "sensitivity_analyst",
        },
    )
    workflow.add_edge("sensitivity_analyst", "lead_writer")
    workflow.add_edge("unsupported_financial", "lead_writer")
    workflow.add_edge("lead_writer", END)
    return workflow.compile()


graph = build_research_graph()
