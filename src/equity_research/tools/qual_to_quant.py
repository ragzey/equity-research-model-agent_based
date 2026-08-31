"""Translate peer and qualitative evidence into bounded, auditable DCF overrides."""

from __future__ import annotations

import logging
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("QualToQuant")

REGULATORY_SIGNALS = (
    "regulatory crackdown",
    "antitrust investigation",
    "anti-trust investigation",
    "sec investigation",
    "ftc investigation",
    "compliance fine",
    "material litigation",
)
OPERATIONAL_SIGNALS = (
    "supply chain disruption",
    "patent expiration",
    "patent cliff",
    "material shortage",
    "labor strike",
)
SATURATION_SIGNALS = (
    "market saturation",
    "intense price war",
    "price erosion",
    "secular decline",
    "technological obsolescence",
)

MAX_TERMINAL_MARGIN = 0.30
MAX_TERMINAL_MARGIN_UPLIFT = 0.03
MAX_COMPANY_RISK_PREMIUM = 0.0125


def _as_margin(value: Any) -> Optional[float]:
    """Normalize peer margins stored as percentage points to decimal form."""
    if value is None:
        return None
    try:
        margin = float(value)
    except (TypeError, ValueError):
        return None
    if margin != margin:
        return None
    return margin / 100.0 if abs(margin) > 1 else margin


def _peer_margins(
    peer_comparison_matrix: Optional[Dict[str, Any]],
) -> List[float]:
    """Read the actual peer_analysis.py matrix shape, excluding the target."""
    if not peer_comparison_matrix:
        return []
    target = peer_comparison_matrix.get("target")
    metrics = peer_comparison_matrix.get("metrics") or {}
    margins: List[float] = []
    for ticker, values in metrics.items():
        if ticker == target or not isinstance(values, dict):
            continue
        parsed = _as_margin(values.get("operating_margin_pct"))
        if parsed is not None:
            margins.append(parsed)
    return margins


def analyze_competitive_moat(
    target_margin: float,
    peer_comparison_matrix: Optional[Dict[str, Any]] = None,
    default_terminal_margin: float = 0.15,
    superiority_threshold: float = 0.03,
) -> Tuple[float, str]:
    """
    Raise terminal margin only when current margin exceeds the peer median by
    at least three percentage points. This is evidence of profitability, not
    proof of a durable moat.
    """
    target = float(target_margin)
    default = float(default_terminal_margin)
    peer_margins = _peer_margins(peer_comparison_matrix)
    if not peer_margins:
        return default, "No valid peer margins; retained classifier terminal margin."

    peer_median = median(peer_margins)
    spread = target - peer_median
    if spread < superiority_threshold:
        return (
            default,
            (
                f"Target operating margin ({target:.1%}) does not exceed peer "
                f"median ({peer_median:.1%}) by the {superiority_threshold:.1%} threshold; "
                "retained classifier terminal margin."
            ),
        )

    midpoint = (target + peer_median) / 2.0
    adjusted = min(
        max(default, midpoint),
        default + MAX_TERMINAL_MARGIN_UPLIFT,
        MAX_TERMINAL_MARGIN,
    )
    # Cap at current profitability only when the firm is already above the
    # lifecycle floor. Loss-making or sub-floor names fade up to the classifier
    # default; they must not drag terminal margin down to today's losses.
    if target >= default:
        adjusted = min(adjusted, target)
    adjusted = max(adjusted, default)
    rationale = (
        f"Profitability advantage: target margin {target:.1%} vs peer median "
        f"{peer_median:.1%}. Terminal margin bounded at {adjusted:.1%}; "
        "this is a valuation policy adjustment, not standalone proof of a moat."
    )
    if target < default:
        rationale += (
            f" Current margin is below the classifier floor {default:.1%}, "
            "so terminal margin was not pulled down to today's profitability."
        )
    logger.info(rationale)
    return round(adjusted, 4), rationale


def assess_qualitative_risks(
    qualitative_summary: Optional[str] = None,
    industry_outlook: Optional[str] = None,
    base_equity_risk_premium: float = 0.05,
) -> Tuple[float, float, str]:
    """
    Convert explicit risk phrases from **filing excerpts** into a bounded
    company-specific premium. LLM industry outlook is not scanned here.

    The premium is added directly to cost of equity; it is not multiplied by
    beta as it would be if incorrectly embedded inside the market ERP.
    """
    text = (qualitative_summary or "").lower()
    regulatory = sorted({signal for signal in REGULATORY_SIGNALS if signal in text})
    operational = sorted({signal for signal in OPERATIONAL_SIGNALS if signal in text})

    premium = 0.0
    reasons: List[str] = []
    if regulatory:
        premium += 0.0075
        reasons.append(f"regulatory: {', '.join(regulatory)}")
    if operational:
        premium += 0.005
        reasons.append(f"operational: {', '.join(operational)}")
    premium = min(premium, MAX_COMPANY_RISK_PREMIUM)

    if reasons:
        rationale = (
            f"Added bounded company-specific risk premium of {premium:.2%} "
            f"for phrase matches ({'; '.join(reasons)}). Requires reviewer judgment."
        )
    else:
        rationale = "No configured high-priority risk phrases matched; no premium added."
    logger.info(rationale)
    return float(base_equity_risk_premium), premium, rationale


def evaluate_growth_horizon(
    source_text: Optional[str] = None,
    default_high_growth_years: int = 5,
) -> Tuple[int, str]:
    """Compress, but never extend, the classifier's growth horizon on filing headwinds."""
    years = int(default_high_growth_years)
    if not source_text:
        return years, "No filing saturation evidence; retained classifier growth horizon."
    text = source_text.lower()
    signals = sorted({signal for signal in SATURATION_SIGNALS if signal in text})
    if not signals:
        return years, "No configured saturation phrases matched; retained growth horizon."
    adjusted = max(2, years - 2)
    rationale = (
        f"Compressed high-growth horizon from {years} to {adjusted} years "
        f"for saturation signals: {', '.join(signals)}."
    )
    logger.info(rationale)
    return adjusted, rationale


def generate_valuation_overrides(
    target_margin: float,
    peer_comparison_matrix: Optional[Dict[str, Any]] = None,
    qualitative_summary: Optional[str] = None,
    industry_outlook: Optional[str] = None,
    default_terminal_margin: float = 0.15,
    base_equity_risk_premium: float = 0.05,
    default_high_growth_years: int = 5,
) -> Dict[str, Any]:
    """Generate bounded overrides and preserve every rationale for audit."""
    terminal_margin, margin_rationale = analyze_competitive_moat(
        target_margin,
        peer_comparison_matrix,
        default_terminal_margin,
    )
    market_erp, company_risk_premium, risk_rationale = assess_qualitative_risks(
        qualitative_summary,
        industry_outlook,
        base_equity_risk_premium,
    )
    high_growth_years, horizon_rationale = evaluate_growth_horizon(
        qualitative_summary,
        default_high_growth_years,
    )
    return {
        "terminal_margin": terminal_margin,
        "market_equity_risk_premium": market_erp,
        "company_specific_risk_premium": company_risk_premium,
        "high_growth_years": high_growth_years,
        "rationales": {
            "terminal_margin": margin_rationale,
            "company_specific_risk_premium": risk_rationale,
            "high_growth_horizon": horizon_rationale,
        },
        "methodology_note": (
            "Rule-based, bounded overrides. Phrase matches are decision support "
            "and require human review; they are not independent risk estimates."
        ),
    }
