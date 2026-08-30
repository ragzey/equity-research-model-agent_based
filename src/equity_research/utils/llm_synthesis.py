"""Optional LLM synthesis with deterministic fallback for industry outlook."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .llm_client import chat_text, llm_configured

logger = logging.getLogger("LLMSynthesis")


def _deterministic_industry_outlook(
    target: str,
    matrix: Dict[str, Any],
    filing_excerpt: Optional[str] = None,
) -> str:
    """Rule-based peer positioning summary when no LLM API key is configured."""
    target_metrics = matrix.get("metrics", {}).get(target, {})
    medians = matrix.get("peer_medians", {})
    competitors: List[str] = matrix.get("competitors", [])

    def _fmt(value: Any, suffix: str = "") -> str:
        if value is None:
            return "N/A"
        if isinstance(value, float):
            return f"{value:.2f}{suffix}"
        return f"{value}{suffix}"

    lines = [
        f"Industry & Competitive Positioning — {target}",
        "",
        f"Peer group ({len(competitors)}): {', '.join(competitors) if competitors else 'none specified'}.",
        "",
        "Relative valuation vs peer medians:",
        f"- Trailing P/E: {_fmt(target_metrics.get('trailing_pe'))} vs median {_fmt(medians.get('trailing_pe'))}",
        f"- Forward P/E: {_fmt(target_metrics.get('forward_pe'))} vs median {_fmt(medians.get('forward_pe'))}",
        f"- EV/EBITDA: {_fmt(target_metrics.get('ev_to_ebitda'))}x vs median {_fmt(medians.get('ev_to_ebitda'))}x",
        f"- Operating margin: {_fmt(target_metrics.get('operating_margin_pct'), '%')} vs median {_fmt(medians.get('operating_margin_pct'), '%')}",
        f"- Revenue growth (YoY): {_fmt(target_metrics.get('revenue_growth_yoy_pct'), '%')} vs median {_fmt(medians.get('revenue_growth_yoy_pct'), '%')}",
        "",
        "Competitive assessment (deterministic):",
    ]

    fwd_pe = target_metrics.get("forward_pe")
    med_fwd = medians.get("forward_pe")
    if fwd_pe is not None and med_fwd is not None:
        if fwd_pe > med_fwd * 1.15:
            lines.append("- Valuation: trades at a premium to peers on forward P/E — market may be pricing superior growth or moat.")
        elif fwd_pe < med_fwd * 0.85:
            lines.append("- Valuation: trades at a discount to peers on forward P/E — potential value or structural concerns.")
        else:
            lines.append("- Valuation: broadly in line with peer forward P/E multiples.")

    op_margin = target_metrics.get("operating_margin_pct")
    med_margin = medians.get("operating_margin_pct")
    if op_margin is not None and med_margin is not None:
        if op_margin > med_margin + 5:
            lines.append("- Profitability: operating margin above peer median — possible cost advantage or pricing power.")
        elif op_margin < med_margin - 5:
            lines.append("- Profitability: operating margin below peer median — competitive pressure or scale disadvantage.")

    if filing_excerpt:
        lines.extend(
            [
                "",
                "Qualitative context: SEC filing risk-factor excerpt available on ledger for deeper moat / barrier analysis.",
            ]
        )

    return "\n".join(lines)


def synthesize_industry_outlook(
    target: str,
    matrix: Dict[str, Any],
    filing_excerpt: Optional[str] = None,
) -> str:
    """
    LLM industry outlook when OPENAI_API_KEY is set; otherwise deterministic summary.
    """
    if not llm_configured():
        logger.info("OPENAI_API_KEY not set; using deterministic industry outlook.")
        return _deterministic_industry_outlook(target, matrix, filing_excerpt)

    competitors: List[str] = matrix.get("competitors", [])
    metrics_json = json.dumps(matrix.get("metrics", {}), indent=2, default=str)
    filing_snippet = (filing_excerpt or "")[:4000]

    system_prompt = (
        "You are an evidence-grounded equity research analyst. Use only the supplied "
        "peer metrics and SEC excerpt. Multiples and margins do not by themselves prove "
        "market share, barriers to entry, moat durability, saturation, or price erosion. "
        "State when evidence is insufficient; do not use model memory or outside facts."
    )
    user_prompt = f"""Target company: {target}
Peer group: {', '.join(competitors)}

Peer comparison metrics (JSON):
{metrics_json}

Optional SEC filing excerpt:
{filing_snippet}

Write a concise analysis (250-400 words) under:
1. OBSERVED PEER POSITIONING
2. INDUSTRY EVIDENCE AND LIMITATIONS

Compare only observed metrics. Discuss barriers, market share, saturation,
price wars, price erosion, secular decline, or obsolescence only when the SEC
excerpt explicitly supports that claim. Use those exact phrases only when
supported, because downstream rules may alter valuation assumptions."""

    content = chat_text(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=60,
    )
    if not content:
        return _deterministic_industry_outlook(target, matrix, filing_excerpt)
    logger.info("LLM industry outlook generated for %s (%d chars).", target, len(content))
    return content
