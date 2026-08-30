"""Model versus Street snapshot. Numbers come from Yahoo and the DCF, not the LLM."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

PT_GAP_BAND = 0.05
EPS_GAP_BAND = 0.05
GROWTH_GAP_BAND = 0.01


def _finite(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _positive(value: Any) -> Optional[float]:
    number = _finite(value)
    if number is None or number <= 0:
        return None
    return number


def _gap(model: Optional[float], street: Optional[float]) -> Optional[float]:
    if model is None or street is None:
        return None
    denom = abs(street)
    if denom < 1e-12:
        return None
    return (model - street) / denom


def _stance(gap: Optional[float], band: float) -> Optional[str]:
    if gap is None:
        return None
    if gap >= band:
        return "above"
    if gap <= -band:
        return "below"
    return "in_line"


def _is_forward_growth_source(source: Optional[str]) -> bool:
    text = str(source or "").lower()
    if not text:
        return False
    if "trailing" in text:
        return False
    return "estimate" in text or "consensus" in text or "+1y" in text


def extract_street_snapshot(
    info: Optional[Dict[str, Any]] = None,
    consensus: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read labeled Yahoo analyst fields already on the ledger. No invented quotes."""
    info = info or {}
    consensus = consensus or {}
    revenue_source = str(consensus.get("source") or "")
    revenue_growth = _finite(consensus.get("growth"))
    forward_growth = revenue_growth if _is_forward_growth_source(revenue_source) else None
    forward_eps = _finite(consensus.get("forward_eps")) or _finite(
        info.get("forwardEps") or info.get("epsForward")
    )
    return {
        "target_mean": _positive(info.get("targetMeanPrice")),
        "target_median": _positive(info.get("targetMedianPrice")),
        "target_high": _positive(info.get("targetHighPrice")),
        "target_low": _positive(info.get("targetLowPrice")),
        "n_analysts": _finite(info.get("numberOfAnalystOpinions")),
        "recommendation_mean": _finite(info.get("recommendationMean")),
        "recommendation_key": str(info.get("recommendationKey") or "").strip() or None,
        "forward_eps": forward_eps,
        "trailing_eps": _finite(info.get("trailingEps") or info.get("epsTrailingTwelveMonths")),
        "forward_pe": _finite(info.get("forwardPE")),
        "trailing_pe": _finite(info.get("trailingPE")),
        "eps_growth": _finite(consensus.get("eps_growth"))
        or _finite(info.get("earningsGrowth")),
        "revenue_growth": revenue_growth,
        "revenue_growth_source": revenue_source or None,
        "revenue_growth_label": consensus.get("label"),
        "forward_revenue_growth": forward_growth,
        "source": "Yahoo Finance analyst estimates / quote",
    }


def build_street_comparison(
    *,
    snapshot: Optional[Dict[str, Any]],
    model_price_target: Optional[float],
    model_year1_eps: Optional[float],
    model_growth: Optional[float],
    share_price: Optional[float] = None,
) -> Dict[str, Any]:
    """Compare accepted model outputs with Yahoo Street fields. Python only."""
    street = dict(snapshot or {})
    street_pt = street.get("target_mean") or street.get("target_median")
    street_eps = street.get("forward_eps")
    street_growth = street.get("forward_revenue_growth")
    pt_gap = _gap(_finite(model_price_target), _positive(street_pt))
    eps_gap = _gap(_finite(model_year1_eps), _finite(street_eps))
    growth_gap = None
    model_g = _finite(model_growth)
    street_g = _finite(street_growth)
    if model_g is not None and street_g is not None:
        growth_gap = model_g - street_g
    rows = [
        {
            "item": "12-month price target",
            "model": _finite(model_price_target),
            "street": _positive(street_pt),
            "gap": pt_gap,
            "kind": "usd",
            "tests": "Whether the DCF/relative blend is inside the published target range.",
        },
        {
            "item": "Year-1 EPS",
            "model": _finite(model_year1_eps),
            "street": _finite(street_eps),
            "gap": eps_gap,
            "kind": "usd",
            "tests": "Model EPS is NI / shares on the accepted P&L, not Street EPS.",
        },
        {
            "item": "Near-term sales growth",
            "model": model_g,
            "street": street_g,
            "gap": growth_gap,
            "kind": "percent",
            "tests": "Accepted high-growth rate versus labeled Yahoo +1y revenue growth.",
        },
    ]
    headline = _stance(pt_gap, PT_GAP_BAND)
    if headline is None:
        headline = _stance(eps_gap, EPS_GAP_BAND)
    if headline is None:
        headline = _stance(growth_gap, GROWTH_GAP_BAND)
    return {
        "source": street.get("source") or "Yahoo Finance",
        "n_analysts": street.get("n_analysts"),
        "target_mean": street.get("target_mean"),
        "target_median": street.get("target_median"),
        "target_high": street.get("target_high"),
        "target_low": street.get("target_low"),
        "recommendation_key": street.get("recommendation_key"),
        "recommendation_mean": street.get("recommendation_mean"),
        "forward_pe": street.get("forward_pe"),
        "share_price": _positive(share_price),
        "rows": rows,
        "pt_gap": pt_gap,
        "eps_gap": eps_gap,
        "growth_gap": growth_gap,
        "headline": headline,
        "has_street": any(
            row.get("street") is not None for row in rows
        ),
    }


def build_thesis_spine(comparison: Dict[str, Any]) -> str:
    """Deterministic thesis numbers. The writer may add why, not new figures."""
    rows = {row["item"]: row for row in (comparison.get("rows") or [])}
    pt = rows.get("12-month price target") or {}
    eps = rows.get("Year-1 EPS") or {}
    growth = rows.get("Near-term sales growth") or {}
    n_analysts = comparison.get("n_analysts")
    parts: List[str] = []
    if pt.get("street") is not None and pt.get("model") is not None:
        count = (
            f" ({int(n_analysts)} analysts)"
            if n_analysts is not None
            else ""
        )
        gap = pt.get("gap")
        gap_text = f" ({gap:+.1%})" if gap is not None else ""
        parts.append(
            f"The Street mean 12-month target is ${pt['street']:,.2f}{count} "
            f"versus this model's ${pt['model']:,.2f}{gap_text}."
        )
    elif pt.get("model") is not None:
        parts.append(
            f"Yahoo did not supply a usable analyst target. The model's "
            f"12-month price target is ${pt['model']:,.2f}."
        )
    if eps.get("street") is not None and eps.get("model") is not None:
        gap = eps.get("gap")
        gap_text = f" ({gap:+.1%})" if gap is not None else ""
        parts.append(
            f"Yahoo forward EPS is ${eps['street']:,.2f}; model Year-1 EPS is "
            f"${eps['model']:,.2f}{gap_text}."
        )
    elif eps.get("model") is not None:
        parts.append(
            f"Model Year-1 EPS is ${eps['model']:,.2f}; Street forward EPS was not on the ledger."
        )
    if growth.get("street") is not None and growth.get("model") is not None:
        parts.append(
            f"Labeled Street +1y sales growth is {growth['street']:.1%} versus the "
            f"accepted DCF high-growth rate of {growth['model']:.1%} "
            f"({(growth.get('gap') or 0):+.1%} points)."
        )
    elif growth.get("model") is not None:
        parts.append(
            f"The accepted DCF high-growth rate is {growth['model']:.1%}; a forward "
            f"Street sales-growth estimate was not on the ledger."
        )
    headline = comparison.get("headline")
    if headline == "above":
        parts.append(
            "The model is above Street on the headline comparison. That gap is the "
            "thesis the 10-K evidence has to support."
        )
    elif headline == "below":
        parts.append(
            "The model is below Street on the headline comparison. That gap is the "
            "thesis the 10-K evidence has to support."
        )
    elif headline == "in_line":
        parts.append(
            "The model is in line with Street on the headline comparison "
            "(within a 5% price/EPS band or 100bp of sales growth)."
        )
    if not parts:
        return (
            "Street consensus was not on the ledger for this run. The thesis is the "
            "accepted DCF path only."
        )
    parts.append("This is a model view, not an investment recommendation.")
    return " ".join(parts)


def build_thesis_pack(
    state: Dict[str, Any],
    *,
    model_price_target: Optional[float],
    model_year1_eps: Optional[float],
    model_growth: Optional[float],
    share_price: Optional[float] = None,
) -> Dict[str, Any]:
    snapshot = state.get("street_snapshot")
    if not snapshot:
        snapshot = extract_street_snapshot(
            state.get("market_info") or {},
            state.get("consensus_growth") or {},
        )
    comparison = build_street_comparison(
        snapshot=snapshot,
        model_price_target=model_price_target,
        model_year1_eps=model_year1_eps,
        model_growth=model_growth,
        share_price=share_price,
    )
    return {
        "snapshot": snapshot,
        "comparison": comparison,
        "spine": build_thesis_spine(comparison),
    }
