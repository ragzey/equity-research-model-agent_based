"""Command-line entry point for the compiled equity-research LangGraph."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow `python main.py` from the repository root without requiring installation.
PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import build_research_graph
from equity_research.tools.sec_api import resolve_listed_symbol
from equity_research.utils.llm_client import llm_session, require_llm

logger = logging.getLogger("ResearchPipelineCLI")


def _normalize_symbols(values: Optional[List[str]]) -> List[str]:
    symbols: List[str] = []
    for value in values or []:
        clean = value.strip().upper()
        if clean and clean not in symbols:
            symbols.append(clean)
    return symbols


def run_pipeline(
    ticker: str,
    target_year: str,
    peer_tickers: Optional[List[str]] = None,
    target_bonds: Optional[List[str]] = None,
    openai_api_key: Optional[str] = None,
    openai_model: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Initialize state, invoke the compiled graph, and return final state."""
    listed = resolve_listed_symbol(ticker) or ticker.strip().upper()
    clean_ticker = listed.strip().upper()
    if not clean_ticker:
        raise ValueError("Ticker cannot be empty.")
    peers = [
        symbol
        for symbol in _normalize_symbols(peer_tickers)
        if symbol != clean_ticker
    ]
    bonds = _normalize_symbols(target_bonds)

    logger.info(
        "Starting equity-research pipeline | ticker=%s | peers=%s | bonds=%d",
        clean_ticker,
        peers or "none",
        len(bonds),
    )
    starting_state = initial_state(
        ticker=clean_ticker,
        target_year=target_year,
        target_bonds=bonds or None,
        competitor_tickers=peers or None,
    )
    with llm_session(
        api_key=openai_api_key,
        model=openai_model,
        provider=llm_provider,
    ):
        require_llm()
        final_state = build_research_graph().invoke(starting_state)
    logger.info("Pipeline completed successfully for %s", clean_ticker)
    return dict(final_state)


def print_summary(ticker: str, final_state: Dict[str, Any]) -> None:
    """Print a compact, auditable CLI result summary."""
    valuation = final_state.get("valuation_summary") or {}
    classification = valuation.get("firm_classification") or {}
    dcf = valuation.get("dcf") or {}
    overrides = final_state.get("dcf_overrides") or {}
    wacc = final_state.get("discount_rate")
    intrinsic_value = final_state.get("calculated_dcf_value")
    valuation_method = valuation.get("valuation_method") or final_state.get(
        "valuation_method"
    ) or "corporate_fcff"

    print("\n==========================================")
    print(f"VALUATION MODEL SUMMARY: {ticker.strip().upper()}")
    print("==========================================")
    print(f"Valuation method    : {valuation_method}")
    print(f"Firm classification : {classification.get('firm_type', 'N/A')}")
    pack = valuation.get("report_pack") or {}
    if pack.get("model_rating"):
        upside = pack.get("upside_to_pt")
        upside_text = f"{upside:+.1%}" if upside is not None else "n/a"
        print(
            f"Model band          : {pack['model_rating']} "
            f"({upside_text} vs last; ±15% convention, not a recommendation)"
        )
    if pack.get("fair_value") is not None:
        print(f"Blended fair value  : ${pack['fair_value']:,.2f} per share")
    if pack.get("price_target_12m") is not None:
        print(f"12-month PT         : ${pack['price_target_12m']:,.2f} per share")
    print(f"Discount rate       : {wacc:.2%}" if wacc is not None else "Discount rate       : N/A")
    print(
        f"Illustrative DCF    : ${intrinsic_value:,.2f} per share"
        if intrinsic_value is not None
        else "Illustrative DCF    : N/A"
    )
    terminal_share = dcf.get("terminal_value_share_of_enterprise_value")
    if terminal_share is not None:
        print(f"Terminal value / EV : {terminal_share:.1%}")
    print(
        f"Arithmetic review    : {'VERIFIED' if final_state.get('is_math_verified') else 'NOT VERIFIED'}"
    )
    selection = final_state.get("peer_selection") or {}
    selected = selection.get("selected") or final_state.get("competitor_tickers") or []
    if selected:
        print(
            f"Peer set             : {', '.join(selected)} "
            f"({selection.get('mode') or 'auto'})"
        )
    harvested = final_state.get("discovered_bond_isins") or []
    if harvested:
        print(f"Harvested ISINs      : {', '.join(harvested)}")
    print(
        f"Memo path            : {final_state.get('final_equity_memo_path') or 'N/A'}"
    )
    print(
        f"PDF path             : {final_state.get('final_equity_memo_pdf_path') or 'N/A'}"
    )

    print("------------------------------------------")
    print("Research desk accept/reject:")
    decisions = overrides.get("decisions") or []
    if decisions:
        print(f" Desk mode            : {overrides.get('desk_mode', 'n/a')}")
        for row in decisions:
            print(
                f" - {row.get('key')}: {row.get('action')} ({row.get('reason', '')})"
            )
    else:
        print(" - No assumption-reviewer decisions were recorded.")
    messages = final_state.get("agent_messages") or []
    if messages:
        print(f" Desk handoffs        : {len(messages)}")
    print("------------------------------------------")
    print("Reviewed DCF override rationales:")
    rationales = overrides.get("rationales") or {}
    if rationales:
        for key, rationale in rationales.items():
            print(f" - {key.replace('_', ' ').title()}: {rationale}")
    else:
        print(" - No qualitative/competitive overrides were available.")
    print("------------------------------------------")
    print("Model output only; not an investment recommendation.")
    print("==========================================\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the multi-agent equity-research LangGraph."
    )
    parser.add_argument(
        "--ticker",
        required=True,
        help="Target stock ticker, for example MSFT.",
    )
    parser.add_argument(
        "--target-year",
        default=str(date.today().year),
        help="Research horizon label (default: current year).",
    )
    parser.add_argument(
        "--peers",
        nargs="*",
        default=[],
        help="Optional override. If omitted, the competitive analyst harvests comps.",
    )
    parser.add_argument(
        "--target-bonds",
        nargs="*",
        default=[],
        help="Optional TRACE ISIN override. If omitted, ISINs are harvested from the 10-K.",
    )
    parser.add_argument(
        "--openai-api-key",
        default=None,
        help="OpenAI or Gemini key for this run. Prefer .env or the GUI.",
    )
    parser.add_argument(
        "--openai-model",
        default=None,
        help="Chat model (gpt-4o-mini or gemini-2.5-flash, etc.).",
    )
    parser.add_argument(
        "--llm-provider",
        choices=("auto", "openai", "gemini"),
        default="auto",
        help="Which API to call. auto infers from the key (sk- vs AIza).",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    try:
        result = run_pipeline(
            ticker=args.ticker,
            target_year=args.target_year,
            peer_tickers=args.peers,
            target_bonds=args.target_bonds,
            openai_api_key=args.openai_api_key,
            openai_model=args.openai_model,
            llm_provider=None if args.llm_provider == "auto" else args.llm_provider,
        )
        print_summary(args.ticker, result)
        return 0
    except Exception:
        logger.exception("Pipeline failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
