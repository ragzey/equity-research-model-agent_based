"""Compiled LangGraph workflow for the current equity-research pipeline."""

from langgraph.graph import END, START, StateGraph

from ..agents.aggregator import aggregator_node
from ..agents.assumption_architect import assumption_architect_node
from ..agents.competitive import competitive_analyst_node
from ..agents.company_products import company_products_node
from ..agents.growth_path import growth_path_node
from ..agents.independent_auditor import independent_auditor_node
from ..agents.industry_macro import industry_macro_node
from ..agents.operations import operations_node
from ..agents.post_quant_reviewer import (
    post_quant_reviewer_node,
    route_after_post_quant_review,
)
from ..agents.qualitative import qualitative_analyst_node
from ..agents.quant import quant_analyst_node
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

    Competitive and Qualitative run in parallel after aggregation. Industry/macro,
    company/products, and operations then run in parallel: demand/cycle versus
    products/mix versus CCC. Growth-path runs after those packets on scale-up
    names and writes the horizon, reinvestment-fade, and margin-path labels.
    On the FCFF path the assumption architect picks bounded
    menu labels; the reviewer only accepts or rejects. Quant remains Python for
    WACC, the operating P&L, and FCFF.     Sensitivity adds operational bear/base/bull
    from the same menus. The writer puts a Python thesis and Street table on
    the memo. The auditor may correct narrative and clip invented
    tickers; it may not rewrite DCF or WACC.
    """
    workflow = StateGraph(EquityResearchState)
    workflow.add_node("aggregator", aggregator_node)
    workflow.add_node("competitive_analyst", competitive_analyst_node)
    workflow.add_node("qualitative_analyst", qualitative_analyst_node)
    workflow.add_node("industry_macro", industry_macro_node)
    workflow.add_node("company_products", company_products_node)
    workflow.add_node("operations", operations_node)
    workflow.add_node("growth_path", growth_path_node)
    workflow.add_node("valuation_router", valuation_router_node)
    workflow.add_node("assumption_architect", assumption_architect_node)
    workflow.add_node(
        "valuation_assumption_reviewer",
        valuation_assumption_reviewer_node,
    )
    workflow.add_node("quant_analyst", quant_analyst_node)
    workflow.add_node("post_quant_reviewer", post_quant_reviewer_node)
    workflow.add_node("sensitivity_analyst", sensitivity_analyst_node)
    workflow.add_node("lead_writer", lead_writer_node)
    workflow.add_node("independent_auditor", independent_auditor_node)
    workflow.add_node("unsupported_financial", unsupported_financial_node)

    workflow.add_edge(START, "aggregator")
    workflow.add_edge("aggregator", "competitive_analyst")
    workflow.add_edge("aggregator", "qualitative_analyst")
    workflow.add_edge(
        ["competitive_analyst", "qualitative_analyst"],
        "industry_macro",
    )
    workflow.add_edge(
        ["competitive_analyst", "qualitative_analyst"],
        "company_products",
    )
    workflow.add_edge(
        ["competitive_analyst", "qualitative_analyst"],
        "operations",
    )
    workflow.add_edge(
        ["industry_macro", "company_products", "operations"],
        "growth_path",
    )
    workflow.add_edge("growth_path", "valuation_router")
    workflow.add_conditional_edges(
        "valuation_router",
        route_valuation_method,
        {
            "corporate_fcff": "assumption_architect",
            "unsupported_financial": "unsupported_financial",
        },
    )
    workflow.add_edge("assumption_architect", "valuation_assumption_reviewer")
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
    workflow.add_edge("lead_writer", "independent_auditor")
    workflow.add_edge("independent_auditor", END)
    return workflow.compile()


graph = build_research_graph()
