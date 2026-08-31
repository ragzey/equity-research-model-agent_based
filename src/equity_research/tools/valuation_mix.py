"""Labeled DCF / relative mix. The LLM may pick a menu label, never a percentage."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .assumption_menus import is_mature_lifecycle, is_scale_up_lifecycle
from .firm_classifier import (
    SCALEUP_PS_THRESHOLD,
    classify_firm_and_adjust_assumptions,
    is_financial_services_firm,
)

# Initiation default remains 70/30. Other labels are evidence-gated.
# dcf_heavy is 90/10: a 15% weight on a near-zero trailing multiple still
# flips a Hold DCF to Sell on scale-ups, where EV/EBITDA is a poor descriptor.
MIX_WEIGHTS: Dict[str, Tuple[float, float]] = {
    "dcf_heavy": (0.90, 0.10),
    "base": (0.70, 0.30),
    "balanced": (0.55, 0.45),
    "not_applicable": (1.0, 0.0),
}
MIX_LABELS = ("dcf_heavy", "base", "balanced")
MIN_TIGHT_PEERS = 3
MIN_SAME_INDUSTRY = 2
PEER_FIT_VIEWS = {"tight", "mixed", "weak", "not_applicable", "insufficient"}
RELATIVE_ROLES = {
    "poor_descriptor",
    "cross_check",
    "industry_standard",
    "not_applicable",
    "insufficient",
}


def _view(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("view") or "").strip().lower()
    return str(block or "").strip().lower()


def _block(view: str, evidence: str) -> Dict[str, str]:
    return {"view": view, "evidence": evidence, "source": "ledger"}


def _na_block(reason: str) -> Dict[str, str]:
    return _block("not_applicable", reason)


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


def _norm_industry(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _industry_of(ticker: str, state: Dict[str, Any]) -> str:
    matrix = state.get("peer_comparison_matrix") or {}
    metrics = (matrix.get("metrics") or {}).get(ticker) or {}
    meta = (state.get("peer_metadata") or {}).get(ticker) or {}
    market = state.get("market_info") or {}
    return _norm_industry(
        metrics.get("industry") or meta.get("industry") or market.get("industry")
    )


def _sector_of(ticker: str, state: Dict[str, Any]) -> str:
    matrix = state.get("peer_comparison_matrix") or {}
    metrics = (matrix.get("metrics") or {}).get(ticker) or {}
    meta = (state.get("peer_metadata") or {}).get(ticker) or {}
    market = state.get("market_info") or {}
    return _norm_industry(
        metrics.get("sector") or meta.get("sector") or market.get("sector")
    )


def _selected_peers(state: Dict[str, Any]) -> List[str]:
    selection = state.get("peer_selection") or {}
    raw = selection.get("selected") or (
        (state.get("peer_comparison_matrix") or {}).get("competitors") or []
    )
    ticker = str(state.get("ticker") or "").strip().upper()
    peers: List[str] = []
    for item in raw:
        symbol = str(item or "").strip().upper()
        if symbol and symbol != ticker and symbol not in peers:
            peers.append(symbol)
    return peers


def _classify_firm(state: Dict[str, Any]) -> Dict[str, Any]:
    summary = ((state.get("valuation_summary") or {}).get("firm_classification")) or {}
    if summary.get("firm_type"):
        return dict(summary)
    income = state.get("income_statement") or {}
    if not income:
        return {}
    ticker = str(state.get("ticker") or "").strip().upper()
    meta = (state.get("peer_metadata") or {}).get(ticker) or {}
    market = state.get("market_info") or {}
    info = {**market, **meta}
    matrix = state.get("peer_comparison_matrix") or {}
    target = matrix.get("target") or ticker
    cap = ((matrix.get("metrics") or {}).get(target) or {}).get("market_cap")
    if cap is None:
        cap = meta.get("market_cap") or market.get("marketCap")
    cap_n = _finite(cap)
    if cap_n is None or cap_n <= 0:
        return {"firm_type": summary.get("firm_type")}
    try:
        return classify_firm_and_adjust_assumptions(cap_n, income, info)
    except (TypeError, ValueError):
        return {"firm_type": summary.get("firm_type")}


def mix_metrics(state: Dict[str, Any]) -> Dict[str, Any]:
    """Frozen Python facts the mix agent may cite. No DCF or TAM."""
    ticker = str(state.get("ticker") or "").strip().upper()
    classification = _classify_firm(state)
    firm_type = classification.get("firm_type")
    market = state.get("market_info") or {}
    peers = _selected_peers(state)
    target_industry = _industry_of(ticker, state)
    target_sector = _sector_of(ticker, state)
    same_industry = sum(1 for peer in peers if _industry_of(peer, state) == target_industry) if target_industry else 0
    same_sector = sum(1 for peer in peers if _sector_of(peer, state) == target_sector) if target_sector else 0
    matrix = state.get("peer_comparison_matrix") or {}
    medians = matrix.get("peer_medians") or {}
    target_metrics = (matrix.get("metrics") or {}).get(matrix.get("target") or ticker) or {}
    ebitda = _finite(target_metrics.get("ebitda"))
    peer_median = _finite(medians.get("ev_to_ebitda"))
    industry = state.get("industry_macro_packet") or {}
    growth_path = state.get("growth_path_packet") or {}
    ps = _finite(classification.get("price_to_sales"))
    if ps is None:
        ps = _finite((growth_path.get("metrics") or {}).get("price_to_sales"))
    return {
        "ticker": ticker,
        "firm_type": firm_type,
        "industry": target_industry or None,
        "sector": target_sector or None,
        "is_financial": bool(
            state.get("is_financial") or is_financial_services_firm(market)
        ),
        "is_scale_up": is_scale_up_lifecycle(firm_type)
        or _view((growth_path.get("scale_view") or {})) == "still_ramping",
        "is_mature": is_mature_lifecycle(firm_type),
        "price_to_sales": ps,
        "peer_count": len(peers),
        "selected_peers": peers,
        "same_industry_count": same_industry,
        "same_sector_count": same_sector,
        "has_peer_median": peer_median is not None,
        "peer_median_ev_ebitda": peer_median,
        "target_ebitda": ebitda,
        "category_growth": _view(industry.get("category_growth")),
        "cycle": _view(industry.get("cycle")),
        "demand_inflection": str(
            (industry.get("demand_inflection") or {}).get("direction") or ""
        ).strip().lower(),
        "scale_view": _view(growth_path.get("scale_view")),
    }


def mix_ledger(metrics: Dict[str, Any]) -> str:
    lines: List[str] = []
    firm = metrics.get("firm_type") or "Unclassified"
    lines.append(f"Python firm type is {firm}.")
    industry = metrics.get("industry")
    if industry:
        lines.append(f"Target industry is {industry}.")
    sector = metrics.get("sector")
    if sector:
        lines.append(f"Target sector is {sector}.")
    ps = metrics.get("price_to_sales")
    if ps is not None:
        lines.append(f"Price-to-sales is {float(ps):.1f} times last revenue.")
        lines.append(
            f"Scale-up price-to-sales threshold is {SCALEUP_PS_THRESHOLD:.0f} times."
        )
    lines.append(f"Selected peer count is {int(metrics.get('peer_count') or 0)}.")
    lines.append(
        f"Same-industry peer count is {int(metrics.get('same_industry_count') or 0)}."
    )
    lines.append(
        f"Same-sector peer count is {int(metrics.get('same_sector_count') or 0)}."
    )
    if metrics.get("has_peer_median"):
        median = metrics.get("peer_median_ev_ebitda")
        lines.append(f"Peer-median EV/EBITDA is {float(median):.1f} times.")
    else:
        lines.append("Peer-median EV/EBITDA is unavailable.")
    ebitda = metrics.get("target_ebitda")
    if ebitda is not None:
        lines.append(f"Target EBITDA is {float(ebitda):.3g}.")
    cycle = metrics.get("cycle")
    if cycle:
        lines.append(f"Industry cycle view is {cycle}.")
    category = metrics.get("category_growth")
    if category:
        lines.append(f"Category growth view is {category}.")
    inflection = metrics.get("demand_inflection")
    if inflection:
        lines.append(f"Demand inflection is {inflection}.")
    scale = metrics.get("scale_view")
    if scale:
        lines.append(f"Growth-path scale view is {scale}.")
    for label, (dcf_w, rel_w) in MIX_WEIGHTS.items():
        if label == "not_applicable":
            continue
        lines.append(
            f"Python mix label {label} is {dcf_w:.0%} DCF and {rel_w:.0%} peer EV/EBITDA."
        )
    return "\n".join(lines)


def _poor_relative_descriptor(metrics: Dict[str, Any]) -> bool:
    if metrics.get("is_scale_up"):
        return True
    ps = metrics.get("price_to_sales")
    if ps is not None and float(ps) >= SCALEUP_PS_THRESHOLD:
        return True
    ebitda = metrics.get("target_ebitda")
    if ebitda is not None and float(ebitda) <= 0:
        return True
    return False


def _tight_peer_fit(metrics: Dict[str, Any]) -> bool:
    return (
        int(metrics.get("peer_count") or 0) >= MIN_TIGHT_PEERS
        and int(metrics.get("same_industry_count") or 0) >= MIN_SAME_INDUSTRY
        and bool(metrics.get("has_peer_median"))
        and (
            metrics.get("target_ebitda") is None
            or float(metrics["target_ebitda"]) > 0
        )
    )


def _weak_peer_fit(metrics: Dict[str, Any]) -> bool:
    ebitda = metrics.get("target_ebitda")
    return (
        int(metrics.get("peer_count") or 0) < MIN_TIGHT_PEERS
        or not metrics.get("has_peer_median")
        or (ebitda is not None and float(ebitda) <= 0)
    )


def _hostile_industry(metrics: Dict[str, Any]) -> bool:
    return metrics.get("cycle") == "downswing" and (
        metrics.get("demand_inflection") == "negative"
        or metrics.get("category_growth") == "below_history"
    )


def ledger_mix_path(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Python default label, allow-list, and driver views. No LLM numbers."""
    if metrics.get("is_financial"):
        reason = "Valuation mix is out of scope for financial firms on this FCFF desk."
        na = _na_block(reason)
        return {
            "applicable": False,
            "default_label": "not_applicable",
            "allowed": [],
            "mix_view": na,
            "peer_fit": na,
            "relative_role": na,
        }

    poor = _poor_relative_descriptor(metrics)
    tight = _tight_peer_fit(metrics)
    weak = _weak_peer_fit(metrics)
    firm = metrics.get("firm_type") or "unclassified"
    peer_n = int(metrics.get("peer_count") or 0)
    same_n = int(metrics.get("same_industry_count") or 0)

    if weak:
        peer_fit = _block(
            "weak",
            (
                f"Selected peer count is {peer_n} and same-industry count is {same_n}"
                + (
                    "; peer-median EV/EBITDA is unavailable"
                    if not metrics.get("has_peer_median")
                    else ""
                )
                + (
                    f"; target EBITDA is {float(metrics['target_ebitda']):.3g}"
                    if metrics.get("target_ebitda") is not None
                    and float(metrics["target_ebitda"]) <= 0
                    else ""
                )
                + "."
            ).strip(),
        )
    elif tight:
        peer_fit = _block(
            "tight",
            (
                f"Selected peer count is {peer_n} with {same_n} same-industry comps "
                "and a peer-median EV/EBITDA."
            ),
        )
    else:
        peer_fit = _block(
            "mixed",
            (
                f"Selected peer count is {peer_n} with {same_n} same-industry comps; "
                "the set is not a tight same-industry screen."
            ),
        )

    if poor:
        relative_role = _block(
            "poor_descriptor",
            (
                f"Firm type {firm} "
                + (
                    f"and price-to-sales {float(metrics['price_to_sales']):.1f} times "
                    if metrics.get("price_to_sales") is not None
                    else ""
                )
                + "make trailing EV/EBITDA a poor descriptor of the business the DCF models."
            ).strip(),
        )
        default = "dcf_heavy"
        allowed = ["dcf_heavy"]
    elif metrics.get("is_mature") and tight and not _hostile_industry(metrics):
        relative_role = _block(
            "industry_standard",
            (
                f"Mature firm type {firm} with a tight same-industry peer set; "
                "EV/EBITDA is the industry cross-check language."
            ),
        )
        default = "balanced"
        allowed = ["dcf_heavy", "base", "balanced"]
    else:
        relative_role = _block(
            "cross_check",
            (
                f"Firm type {firm}; peer EV/EBITDA remains a 30% initiation cross-check "
                "unless the mix agent picks another allowed label."
            ),
        )
        default = "base"
        allowed = ["dcf_heavy", "base"]
        if tight and not poor:
            allowed.append("balanced")

    dcf_w, rel_w = MIX_WEIGHTS[default]
    mix_view = _block(
        default,
        (
            f"Python default mix is {default}: {dcf_w:.0%} DCF and {rel_w:.0%} "
            "peer EV/EBITDA from firm type, peer fit, and the industry packet."
        ),
    )
    return {
        "applicable": True,
        "default_label": default,
        "allowed": allowed,
        "mix_view": mix_view,
        "peer_fit": peer_fit,
        "relative_role": relative_role,
    }


def mix_weights_for_label(label: Optional[str]) -> Tuple[float, float]:
    key = str(label or "base").strip().lower()
    return MIX_WEIGHTS.get(key, MIX_WEIGHTS["base"])


def overlay_ledger_valuation_mix(
    packet: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill views from the Python ledger. LLM may only keep an allowed mix label."""
    filled = ledger_mix_path(metrics)
    updated = dict(packet or {})
    updated["applicable"] = filled["applicable"]
    updated["metrics"] = metrics
    updated["allowed"] = list(filled["allowed"])
    updated["default_label"] = filled["default_label"]
    updated["peer_fit"] = filled["peer_fit"]
    updated["relative_role"] = filled["relative_role"]

    allowed = set(filled["allowed"])
    llm_label = _view(packet.get("mix_view") if packet else None)
    if filled["applicable"] and llm_label in allowed:
        dcf_w, rel_w = mix_weights_for_label(llm_label)
        evidence = str((packet.get("mix_view") or {}).get("evidence") or "").strip()
        if not evidence:
            evidence = str(filled["mix_view"].get("evidence") or "")
        updated["mix_view"] = _block(
            llm_label,
            evidence
            or (
                f"Mix agent chose {llm_label}: {dcf_w:.0%} DCF and {rel_w:.0%} "
                "peer EV/EBITDA, which is on the Python allow-list."
            ),
        )
    else:
        updated["mix_view"] = filled["mix_view"]

    label = _view(updated.get("mix_view")) or filled["default_label"]
    dcf_w, rel_w = mix_weights_for_label(label)
    updated["label"] = label
    updated["dcf_weight"] = dcf_w
    updated["relative_weight"] = rel_w
    if not str(updated.get("narrative") or "").strip():
        bits = [
            str((updated.get(key) or {}).get("evidence") or "").strip()
            for key in ("mix_view", "peer_fit", "relative_role")
        ]
        updated["narrative"] = " ".join(bit for bit in bits if bit)
    return updated


def mix_weights_from_state(state: Dict[str, Any]) -> Tuple[float, float]:
    """Weights for blend_fair_value. Label maps to the Python menu; never LLM floats."""
    packet = state.get("valuation_mix_packet") or {}
    label = str(packet.get("label") or _view(packet.get("mix_view")) or "").strip().lower()
    if label in MIX_WEIGHTS and label != "not_applicable":
        return mix_weights_for_label(label)
    filled = overlay_ledger_valuation_mix(packet, mix_metrics(state))
    return float(filled["dcf_weight"]), float(filled["relative_weight"])
