"""Bounded DCF assumption menus. The LLM may pick a labeled choice, not a number."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .consensus import blend_high_growth_rate
from .firm_classifier import (
    MATURE_TERMINAL_MARGIN,
    MAX_HIGH_GROWTH_YEARS,
    SCALE_TERMINAL_MARGIN,
    SCALEUP_STRETCH_RATE,
    classify_firm_and_adjust_assumptions,
    extract_operating_baseline,
)
from .operating_cycle import clip_sales_to_capital, measure_operating_cycle
from .qual_to_quant import generate_valuation_overrides
from ..utils.grounding import contains_web_link

TERMINAL_GROWTH_FLOOR = 0.015
TERMINAL_GROWTH_HARD_CAP = 0.05
# Kept for older imports/tests that still mention the mature 2.5% default.
TERMINAL_GROWTH_CAP = 0.025

GROWTH_CHOICES = ("low", "base", "high")
YEAR_CHOICES = ("compress", "base", "extend")
TERMINAL_GROWTH_CHOICES = ("low", "base", "high")
MARGIN_CHOICES = ("baseline", "proposed")
CSRP_CHOICES = ("none", "proposed")
STC_CHOICES = ("heavy", "base", "light", "fade", "harvest")
# Missing allow-list → never reopen stretch labels from live packets.
CONSERVATIVE_LABELS: Dict[str, Tuple[str, ...]] = {
    "high_growth_rate": ("low", "base"),
    "high_growth_years": ("compress", "base"),
    "terminal_growth_rate": ("low", "base"),
    "terminal_margin": ("baseline",),
    "company_specific_risk_premium": ("none",),
    "sales_to_capital": ("base",),
}
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def is_high_growth_lifecycle(firm_type: Optional[str]) -> bool:
    text = str(firm_type or "").strip().lower()
    return "high-growth" in text or text == "mid-cap growth"


def is_scale_up_lifecycle(firm_type: Optional[str]) -> bool:
    return "scale-up" in str(firm_type or "").strip().lower()


def is_mature_lifecycle(firm_type: Optional[str]) -> bool:
    return "mature" in str(firm_type or "").strip().lower()


def economy_terminal_cap(risk_free_rate: Optional[float]) -> float:
    """Stable g cannot exceed expected nominal growth. Rf is the economy proxy."""
    if risk_free_rate is None:
        return TERMINAL_GROWTH_CAP
    return min(
        TERMINAL_GROWTH_HARD_CAP,
        max(TERMINAL_GROWTH_FLOOR, float(risk_free_rate) - 0.005),
    )


def _rf_spread_for_firm(firm_type: Optional[str]) -> float:
    """How far below Rf the base perpetuity sits. High-growth firms run closer to the economy."""
    if is_high_growth_lifecycle(firm_type):
        return 0.010
    return 0.015


def policy_terminal_growth(
    risk_free_rate: Optional[float],
    *,
    firm_type: Optional[str] = None,
    packet: Optional[Dict[str, Any]] = None,
) -> float:
    """Base perpetuity g from firm lifecycle, Rf (economy), and the industry/macro packet."""
    cap = economy_terminal_cap(risk_free_rate)
    floor = TERMINAL_GROWTH_FLOOR
    if risk_free_rate is None:
        base = min(cap, TERMINAL_GROWTH_CAP)
    else:
        base = min(
            cap,
            max(floor, float(risk_free_rate) - _rf_spread_for_firm(firm_type)),
        )
    if _hostile_macro(packet):
        conservative = (
            min(cap, max(floor, float(risk_free_rate) - 0.020))
            if risk_free_rate is not None
            else floor
        )
        base = min(base, conservative)
    elif _constructive_macro(packet) and not is_high_growth_lifecycle(firm_type):
        base = min(cap, (base + cap) / 2.0)
    return base


def clip_terminal_growth(
    value: Any,
    risk_free_rate: Optional[float],
    *,
    firm_type: Optional[str] = None,
    packet: Optional[Dict[str, Any]] = None,
) -> float:
    """Safety rail: keep g in [1.5%, min(5%, Rf − 50bp)]. Do not flatten high-growth cases to 2.5%."""
    cap = economy_terminal_cap(risk_free_rate)
    if value is None or value == "":
        return policy_terminal_growth(
            risk_free_rate, firm_type=firm_type, packet=packet
        )
    try:
        number = float(value)
    except (TypeError, ValueError):
        return policy_terminal_growth(
            risk_free_rate, firm_type=firm_type, packet=packet
        )
    return min(cap, max(TERMINAL_GROWTH_FLOOR, number))


def _has_evidence(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    return bool(str(block.get("evidence") or "").strip())


def _view(block: Any) -> str:
    if not isinstance(block, dict):
        return "insufficient"
    return str(block.get("view") or block.get("direction") or "insufficient").strip().lower()


def _hostile_macro(packet: Optional[Dict[str, Any]]) -> bool:
    """A recession case needs a downswing plus weak demand, not one web snippet."""
    packet = packet or {}
    cycle_down = _view(packet.get("cycle")) == "downswing"
    inflect_neg = _view(packet.get("demand_inflection")) == "negative"
    below = _view(packet.get("category_growth")) == "below_history"
    return cycle_down and (inflect_neg or below)


def _constructive_macro(packet: Optional[Dict[str, Any]]) -> bool:
    packet = packet or {}
    growth = packet.get("category_growth") or {}
    inflection = packet.get("demand_inflection") or {}
    return (
        (_view(growth) == "above_history" and _has_evidence(growth))
        or (_view(inflection) == "positive" and _has_evidence(inflection))
    )


def allowed_growth_choices(
    packet: Optional[Dict[str, Any]],
    *,
    firm_type: Optional[str] = None,
) -> List[str]:
    """High-band growth requires evidenced above-history, or a scale-up lifecycle.

    Growth / scale-up names stay on the classifier base rate unless the
    industry packet shows a hostile cycle or below-history demand. Mature
    names still have a low label.
    """
    allowed = ["base"]
    growth = (packet or {}).get("category_growth") or {}
    below = _view(growth) == "below_history" and _has_evidence(growth)
    unclassified = not str(firm_type or "").strip()
    if unclassified or _hostile_macro(packet) or below:
        allowed = ["low", "base"]
    if is_scale_up_lifecycle(firm_type) or (
        _view(growth) == "above_history" and _has_evidence(growth)
    ):
        allowed.append("high")
    return allowed


def allowed_year_choices(
    packet: Optional[Dict[str, Any]],
    *,
    firm_type: Optional[str] = None,
) -> List[str]:
    """Mature names stay on the classifier horizon unless the cycle is hostile.

    High-growth and scale-up names get extend without a constructive industry
    packet. Compress needs a ledger downswing plus weak demand.
    """
    if _hostile_macro(packet):
        return ["compress", "base"]
    allowed = ["base"]
    if (
        is_high_growth_lifecycle(firm_type)
        or is_scale_up_lifecycle(firm_type)
        or _constructive_macro(packet)
    ):
        allowed.append("extend")
    return allowed


def allowed_terminal_growth_choices(
    packet: Optional[Dict[str, Any]],
    menus: Dict[str, Dict[str, float]],
    *,
    firm_type: Optional[str] = None,
) -> List[str]:
    """High stable-g is for high-growth firms or an evidenced constructive category."""
    menu = menus.get("terminal_growth_rate") or {}
    below = _view((packet or {}).get("category_growth")) == "below_history"
    allowed = [choice for choice in ("base",) if choice in menu]
    if "low" in menu and (_hostile_macro(packet) or below):
        allowed.insert(0, "low")
    unlocked = is_high_growth_lifecycle(firm_type) or _constructive_macro(packet)
    if "high" in menu and unlocked and not _hostile_macro(packet):
        allowed.append("high")
    return allowed


def _growth_path_view(packet: Optional[Dict[str, Any]], key: str) -> str:
    return _view((packet or {}).get(key))


def growth_path_is_scale_up(packet: Optional[Dict[str, Any]]) -> bool:
    return _growth_path_view(packet, "scale_view") in {"still_ramping", "stretched"}


def allowed_stc_choices(
    operations_packet: Optional[Dict[str, Any]],
    *,
    growth_path_packet: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Heavy/light from operations; fade/harvest from the growth-path packet."""
    allowed = ["base"]
    packet = operations_packet or {}
    wc = packet.get("working_capital") or {}
    ccc = packet.get("cash_conversion") or {}
    reinvest = packet.get("reinvestment") or {}
    # One year of inventory build with a stable CCC is not a heavy-reinvestment path.
    absorbing = _view(ccc) == "lengthening" or _view(reinvest) == "heavy"
    releasing = (
        _view(wc) == "releasing"
        or _view(ccc) == "shortening"
        or _view(reinvest) == "asset_light"
    )
    evidenced = _has_evidence(wc) or _has_evidence(ccc) or _has_evidence(reinvest)
    if absorbing and evidenced:
        allowed.append("heavy")
    if releasing and evidenced and not absorbing:
        allowed.append("light")
    path = _growth_path_view(growth_path_packet, "reinvestment_path")
    if path == "fade":
        allowed.append("fade")
    if path == "harvest":
        allowed.append("harvest")
    return allowed


def _reason_numbers_in_ledger(reason: str, ledger: str) -> bool:
    """Invented figures in stretch-label reasons cannot unlock the label."""
    if not ledger:
        return True
    haystack = ledger.replace(",", "")
    for token in _NUMBER_RE.findall((reason or "").replace(",", "")):
        pattern = rf"(?<![\d.]){re.escape(token)}(?![\d.])"
        if not re.search(pattern, haystack):
            return False
    return True


def stable_sales_to_capital_for_label(
    label: str,
    high_growth_stc: float,
    baseline_stable: Optional[float] = None,
) -> float:
    """Map a reinvestment label onto stable sales-to-capital. Same rule as the architect."""
    stc = float(high_growth_stc)
    stable = clip_sales_to_capital(
        max(stc, float(baseline_stable if baseline_stable is not None else stc)),
        stc,
    )
    if label == "heavy":
        return clip_sales_to_capital(stc * 1.10, stc)
    if label == "light":
        return clip_sales_to_capital(stc * 1.05, stc)
    return stable


def resolve_labeled_choice(
    raw: Any,
    allowed: Sequence[str],
    default: str = "base",
) -> str:
    """Map LLM output to a menu label. Numeric values and unknown labels fall back."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return default if default in allowed else (allowed[0] if allowed else default)
    label = str(raw or "").strip().lower()
    if label in allowed:
        return label
    return default if default in allowed else (allowed[0] if allowed else default)


def _target_margin_and_cap(state: Dict[str, Any]) -> Tuple[float, float, Dict[str, Any]]:
    income_statement = state.get("income_statement") or {}
    if not income_statement:
        raise ValueError("Income statement is required before assumption review.")

    peer_matrix = state.get("peer_comparison_matrix")
    market_cap = None
    if peer_matrix:
        target = peer_matrix.get("target")
        target_metrics = (peer_matrix.get("metrics") or {}).get(target, {})
        market_cap = target_metrics.get("market_cap")

    if market_cap is None:
        ticker = str(state.get("ticker") or "").strip().upper()
        market_cap = (
            (state.get("peer_metadata") or {}).get(ticker, {}).get("market_cap")
        )
    if market_cap is None:
        raise ValueError(
            "Market cap is required for baseline assumptions; run Aggregator with peer metadata."
        )

    info = dict(
        (state.get("peer_metadata") or {}).get(
            str(state.get("ticker") or "").strip().upper(), {}
        )
        or {}
    )
    if not info.get("sector"):
        market = state.get("market_info") or {}
        info = {**market, **info}
    baseline = classify_firm_and_adjust_assumptions(
        float(market_cap), income_statement, info
    )
    base_revenue, base_ebit = extract_operating_baseline(income_statement)
    statement_margin = base_ebit / base_revenue
    # Moat overlay must use the same EBIT/revenue as the DCF P&L, not Yahoo
    # trailing operatingMargins, which can disagree with the annual statements.
    target_margin = statement_margin
    return float(target_margin), float(market_cap), baseline


def build_assumption_bundle(
    state: Dict[str, Any],
    *,
    risk_free_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Classifier baseline plus phrase/consensus candidates. No LLM numbers."""
    target_margin, _market_cap, baseline = _target_margin_and_cap(state)
    filing_evidence = state.get("qualitative_evidence") or []
    qualitative_risk_input = " ".join(
        item.get("excerpt", "") for item in filing_evidence if isinstance(item, dict)
    )
    proposed = generate_valuation_overrides(
        target_margin=target_margin,
        peer_comparison_matrix=state.get("peer_comparison_matrix"),
        qualitative_summary=qualitative_risk_input,
        industry_outlook=None,
        default_terminal_margin=baseline["terminal_margin"],
        default_high_growth_years=baseline["high_growth_years"],
    )
    proposed["baseline_firm_type"] = baseline["firm_type"]
    growth_rate, growth_rationale = blend_high_growth_rate(
        baseline["high_growth_rate"],
        tuple(baseline["high_growth_rate_bounds"]),
        state.get("consensus_growth"),
    )
    proposed["high_growth_rate"] = growth_rate
    proposed.setdefault("rationales", {})["high_growth_rate"] = growth_rationale
    cycle = measure_operating_cycle(
        state.get("income_statement"),
        state.get("balance_sheet"),
        classifier_sales_to_capital=baseline.get("sales_to_capital"),
    )
    baseline["operating_cycle"] = cycle
    ops_observed = ((state.get("operations_packet") or {}).get("metrics") or {}).get(
        "observed_sales_to_capital"
    )
    if ops_observed is not None:
        baseline["sales_to_capital"] = clip_sales_to_capital(
            ops_observed, baseline.get("sales_to_capital")
        )
        baseline["sales_to_capital_source"] = "operations_packet"
    elif cycle.get("observed_sales_to_capital") is not None:
        baseline["sales_to_capital"] = cycle["observed_sales_to_capital"]
        baseline["sales_to_capital_source"] = cycle.get("source")
    proposed["sales_to_capital"] = clip_sales_to_capital(
        baseline.get("sales_to_capital"), 1.8
    )
    proposed["stable_sales_to_capital"] = clip_sales_to_capital(
        max(
            float(proposed["sales_to_capital"]),
            float(baseline.get("stable_sales_to_capital") or proposed["sales_to_capital"]),
        ),
        proposed["sales_to_capital"],
    )
    proposed.setdefault("rationales", {})["sales_to_capital"] = (
        f"Sales-to-capital {proposed['sales_to_capital']:.2f} from "
        f"{baseline.get('sales_to_capital_source') or 'firm-type default'}. "
        "FCFF reinvestment = ΔRevenue / sales-to-capital (working capital + net PPE)."
    )
    packet = state.get("industry_macro_packet") or {}
    firm_type = baseline.get("firm_type")
    terminal_g = policy_terminal_growth(
        risk_free_rate, firm_type=firm_type, packet=packet
    )
    cap = economy_terminal_cap(risk_free_rate)
    rf_text = f"{float(risk_free_rate):.2%}" if risk_free_rate is not None else "n/a"
    proposed["terminal_growth_rate"] = terminal_g
    proposed.setdefault("rationales", {})["terminal_growth_rate"] = (
        f"Perpetuity growth {terminal_g:.1%} from firm type {firm_type} and the "
        f"economy (Rf {rf_text}). Stable g tracks nominal growth (Rf minus a "
        f"firm-type spread), not a 2.5% cap. Hard ceiling {cap:.1%} "
        f"(min of 5% and Rf − 50bp)."
    )
    growth_path = state.get("growth_path_packet") or {}
    margin_path = _growth_path_view(growth_path, "margin_path")
    if margin_path == "scale":
        proposed["terminal_margin"] = max(
            float(proposed.get("terminal_margin") or 0.0),
            SCALE_TERMINAL_MARGIN,
        )
        proposed.setdefault("rationales", {})["terminal_margin"] = (
            f"Growth-path margin_path is scale; terminal margin is at least "
            f"{SCALE_TERMINAL_MARGIN:.0%} rather than last year's print."
        )
    elif margin_path == "mature":
        proposed["terminal_margin"] = max(
            float(proposed.get("terminal_margin") or 0.0),
            MATURE_TERMINAL_MARGIN,
        )
        proposed.setdefault("rationales", {})["terminal_margin"] = (
            f"Growth-path margin_path is mature; terminal margin is "
            f"{MATURE_TERMINAL_MARGIN:.0%}."
        )
    return {"baseline": baseline, "proposed": proposed}


def build_choice_menus(
    bundle: Dict[str, Any],
    packet: Optional[Dict[str, Any]] = None,
    *,
    risk_free_rate: Optional[float] = None,
    operations_packet: Optional[Dict[str, Any]] = None,
    growth_path_packet: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Discrete labeled candidates. High/extend are omitted when evidence is thin."""
    baseline = bundle["baseline"]
    proposed = bundle["proposed"]
    low, high = baseline["high_growth_rate_bounds"]
    years_base = int(baseline["high_growth_years"])
    years_compress = int(proposed.get("high_growth_years") or years_base)
    years_compress = min(years_compress, max(2, years_base - 2))
    years_extend = min(MAX_HIGH_GROWTH_YEARS, years_base + 2)

    firm_type = baseline.get("firm_type")
    policy_g = policy_terminal_growth(
        risk_free_rate, firm_type=firm_type, packet=packet
    )
    cap = economy_terminal_cap(risk_free_rate)
    terminal_menu: Dict[str, float] = {
        "low": TERMINAL_GROWTH_FLOOR,
        "base": policy_g,
    }
    if cap > policy_g + 1e-9:
        terminal_menu["high"] = cap

    stretch_high = float(high)
    if (
        is_scale_up_lifecycle(firm_type)
        and _growth_path_view(growth_path_packet, "scale_view") == "still_ramping"
    ):
        stretch_high = SCALEUP_STRETCH_RATE
    growth_menu = {
        "low": float(low),
        "base": float(proposed["high_growth_rate"]),
        "high": stretch_high,
    }
    year_menu = {
        "compress": years_compress,
        "base": years_base,
        "extend": years_extend,
    }
    margin_menu = {
        "baseline": float(baseline["terminal_margin"]),
        "proposed": float(proposed["terminal_margin"]),
    }
    csrp_menu = {
        "none": 0.0,
        "proposed": float(proposed.get("company_specific_risk_premium") or 0.0),
    }
    base_stc = clip_sales_to_capital(proposed.get("sales_to_capital"), 1.8)
    stable_stc = clip_sales_to_capital(
        baseline.get("stable_sales_to_capital") or proposed.get("stable_sales_to_capital"),
        2.0,
    )
    fade_stc = clip_sales_to_capital((base_stc + stable_stc) / 2.0, base_stc)
    stc_menu = {
        "heavy": clip_sales_to_capital(base_stc * 0.75, base_stc),
        "base": base_stc,
        "light": clip_sales_to_capital(base_stc * 1.25, base_stc),
        "fade": fade_stc,
        "harvest": stable_stc,
    }
    if abs(stc_menu["heavy"] - stc_menu["base"]) < 1e-9:
        stc_menu.pop("heavy", None)
    if abs(stc_menu["light"] - stc_menu["base"]) < 1e-9:
        stc_menu.pop("light", None)
    if abs(stc_menu["fade"] - stc_menu["base"]) < 1e-9:
        stc_menu.pop("fade", None)
    if abs(stc_menu["harvest"] - stc_menu["base"]) < 1e-9:
        stc_menu.pop("harvest", None)

    allowed = {
        "high_growth_rate": [
            key for key in GROWTH_CHOICES if key in growth_menu
        ],
        "high_growth_years": [
            key for key in YEAR_CHOICES if key in year_menu
        ],
        "terminal_growth_rate": list(terminal_menu.keys()),
        "terminal_margin": list(MARGIN_CHOICES),
        "company_specific_risk_premium": list(CSRP_CHOICES),
        "sales_to_capital": [key for key in STC_CHOICES if key in stc_menu],
    }
    allowed["high_growth_rate"] = [
        key
        for key in allowed["high_growth_rate"]
        if key in allowed_growth_choices(packet, firm_type=firm_type)
    ]
    allowed["high_growth_years"] = [
        key
        for key in allowed["high_growth_years"]
        if key in allowed_year_choices(packet, firm_type=firm_type)
    ]
    allowed["terminal_growth_rate"] = allowed_terminal_growth_choices(
        packet,
        {"terminal_growth_rate": terminal_menu},
        firm_type=firm_type,
    )
    allowed["sales_to_capital"] = [
        key
        for key in allowed["sales_to_capital"]
        if key in allowed_stc_choices(
            operations_packet, growth_path_packet=growth_path_packet
        )
    ]
    if abs(margin_menu["baseline"] - margin_menu["proposed"]) < 1e-12:
        allowed["terminal_margin"] = ["baseline"]
    if csrp_menu["proposed"] <= 0:
        allowed["company_specific_risk_premium"] = ["none"]

    return {
        "high_growth_rate": growth_menu,
        "high_growth_years": year_menu,
        "terminal_growth_rate": terminal_menu,
        "terminal_margin": margin_menu,
        "company_specific_risk_premium": csrp_menu,
        "sales_to_capital": stc_menu,
        "allowed": allowed,
    }


def apply_architect_choices(
    bundle: Dict[str, Any],
    menus: Dict[str, Any],
    raw_choices: Optional[Dict[str, Any]],
    *,
    reasons: Optional[Dict[str, Any]] = None,
    ledger_text: str = "",
) -> Dict[str, Any]:
    """Translate labeled choices into clipped numbers. Ignore any LLM floats."""
    proposed = dict(bundle["proposed"])
    baseline = bundle["baseline"]
    allowed = menus.get("allowed") or {}
    choices = raw_choices if isinstance(raw_choices, dict) else {}
    notes = reasons if isinstance(reasons, dict) else {}
    rationales = dict(proposed.get("rationales") or {})
    applied_labels: Dict[str, str] = {}

    specs: List[Tuple[str, str, str]] = [
        ("high_growth_rate", "high_growth_rate", "base"),
        ("high_growth_years", "high_growth_years", "base"),
        ("terminal_growth_rate", "terminal_growth_rate", "base"),
        ("terminal_margin", "terminal_margin", "baseline"),
        ("company_specific_risk_premium", "company_specific_risk_premium", "none"),
        ("sales_to_capital", "sales_to_capital", "base"),
    ]
    needs_reason = {"high", "extend", "heavy", "light", "fade", "harvest", "proposed"}
    for key, menu_name, default in specs:
        menu = menus.get(menu_name) or {}
        if not menu:
            continue
        if key in allowed:
            permitted = [
                str(item) for item in (allowed.get(key) or []) if str(item) in menu
            ]
        else:
            permitted = [
                label
                for label in CONSERVATIVE_LABELS.get(key, (default,))
                if label in menu
            ]
        if not permitted:
            permitted = [default] if default in menu else list(menu.keys())[:1]
        label = resolve_labeled_choice(choices.get(key), list(permitted), default=default)
        reason = str(notes.get(key) or "").strip()
        if contains_web_link(reason):
            reason = ""
        if label in needs_reason and (
            not reason or not _reason_numbers_in_ledger(reason, ledger_text)
        ):
            label = default if default in menu else next(iter(menu), default)
            reason = (
                "Fell back to the base label because the architect did not cite "
                "ledger evidence."
            )
        if label not in menu:
            label = default if default in menu else next(iter(menu), default)
        applied_labels[key] = label
        proposed[key] = menu[label]
        rationales[key] = (
            f"Assumption architect chose '{label}' → {proposed[key]}. "
            f"Allowed labels: {', '.join(permitted)}. "
            f"{reason}"
        ).strip()
        if key == "high_growth_years":
            rationales["high_growth_horizon"] = rationales[key]
        if key == "sales_to_capital":
            stable = stable_sales_to_capital_for_label(
                label,
                float(proposed[key]),
                baseline.get("stable_sales_to_capital"),
            )
            proposed["stable_sales_to_capital"] = stable
            rationales["stable_sales_to_capital"] = (
                f"Stable sales-to-capital {stable:.2f} follows the '{label}' "
                "reinvestment choice."
            )

    proposed["rationales"] = rationales
    proposed["architect_choices"] = applied_labels
    proposed["architect_menus"] = {
        key: menus[key]
        for key in (
            "high_growth_rate",
            "high_growth_years",
            "terminal_growth_rate",
            "terminal_margin",
            "company_specific_risk_premium",
            "sales_to_capital",
        )
        if key in menus
    }
    proposed["architect_allowed"] = allowed
    proposed["baseline_firm_type"] = baseline.get("firm_type")
    proposed["desk_mode"] = "architect"
    return proposed
