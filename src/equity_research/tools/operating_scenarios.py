"""Operational bull / base / bear cases from the same labeled menus. Python only."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .assumption_menus import (
    CONSERVATIVE_LABELS,
    build_assumption_bundle,
    build_choice_menus,
    clip_terminal_growth,
    stable_sales_to_capital_for_label,
)
from .valuation import perform_3stage_dcf_valuation

BULL_ORDER: Dict[str, Tuple[str, ...]] = {
    "high_growth_rate": ("high", "base", "low"),
    "high_growth_years": ("extend", "base", "compress"),
    "terminal_growth_rate": ("high", "base", "low"),
    "terminal_margin": ("proposed", "baseline"),
    "sales_to_capital": ("light", "base", "heavy"),
}
BEAR_ORDER: Dict[str, Tuple[str, ...]] = {
    "high_growth_rate": ("low", "base", "high"),
    "high_growth_years": ("compress", "base", "extend"),
    "terminal_growth_rate": ("low", "base", "high"),
    "terminal_margin": ("baseline", "proposed"),
    "sales_to_capital": ("heavy", "base", "light"),
}

_MENU_KEYS = (
    "high_growth_rate",
    "high_growth_years",
    "terminal_growth_rate",
    "terminal_margin",
    "sales_to_capital",
)

# When an allow-list is missing, never reopen stretch labels from live packets.

_DEFAULT_LABELS: Dict[str, str] = {
    "high_growth_rate": "base",
    "high_growth_years": "base",
    "terminal_growth_rate": "base",
    "terminal_margin": "baseline",
    "sales_to_capital": "base",
}

_METHODOLOGY = (
    "Bear / base / bull change only operating menu labels: high-growth "
    "rate, high-growth years, terminal margin, sales-to-capital, and "
    "perpetuity g. WACC, beta, size premium, the labeled DCF/relative mix, "
    "and the peer EV/EBITDA cross-check stay on the accepted base case. The published rating "
    "uses the reviewer-accepted base path. Bull and bear are the most "
    "optimistic and pessimistic combinations still inside the "
    "evidence-gated allow-list, not the architect's unpublished pick. "
    "If a stretch label is not on that allow-list, it is not used."
)


def _permitted_labels(
    key: str,
    allowed: Dict[str, Any],
    menu: Dict[str, Any],
) -> List[str]:
    menu_keys = [str(item) for item in (menu or {}) if item != "allowed"]
    if key in allowed:
        return [
            str(item)
            for item in (allowed.get(key) or [])
            if str(item) in menu_keys
        ]
    conservative = CONSERVATIVE_LABELS.get(key, ("base",))
    return [label for label in conservative if label in menu_keys]


def _pick_label(
    key: str,
    preference: Sequence[str],
    allowed: Dict[str, Any],
    menu: Dict[str, Any],
) -> Optional[str]:
    permitted = _permitted_labels(key, allowed, menu)
    for label in preference:
        if label in permitted:
            return label
    return permitted[0] if permitted else None


def _label_for_applied(menu: Dict[str, Any], value: Any, fallback: str) -> str:
    if value is None or not menu:
        return fallback
    try:
        target = float(value)
    except (TypeError, ValueError):
        return fallback
    best: Optional[str] = None
    best_dist: Optional[float] = None
    for label, stored in menu.items():
        if label == "allowed":
            continue
        try:
            numeric = float(stored)
        except (TypeError, ValueError):
            continue
        dist = abs(numeric - target)
        if best_dist is None or dist < best_dist - 1e-12:
            best_dist = dist
            best = str(label)
        elif best is not None and abs(dist - best_dist) <= 1e-12 and str(label) == fallback:
            best = str(label)
    return best or fallback


def _base_labels(menus: Dict[str, Any], applied: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, str]:
    architect = dict((state.get("dcf_overrides") or {}).get("architect_choices") or {})
    labels: Dict[str, str] = {}
    for key, fallback in _DEFAULT_LABELS.items():
        hint = str(architect.get(key) or fallback)
        labels[key] = _label_for_applied(menus.get(key) or {}, applied.get(key), hint)
    return labels


def _menus_from_state(state: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    overrides = state.get("dcf_overrides") or {}
    menus = dict(overrides.get("architect_menus") or {})
    allowed = dict(overrides.get("architect_allowed") or {})
    menus.pop("allowed", None)
    if menus:
        # Stored menus win even if the allow-list is empty. Rebuilding from
        # live packets would re-unlock stretch labels the reviewer never saw.
        return menus, allowed
    summary = state.get("valuation_summary") or {}
    inputs = summary.get("valuation_date_inputs") or {}
    bundle = build_assumption_bundle(
        state,
        risk_free_rate=inputs.get("risk_free_rate"),
    )
    built = build_choice_menus(
        bundle,
        state.get("industry_macro_packet") or {},
        risk_free_rate=inputs.get("risk_free_rate"),
        operations_packet=state.get("operations_packet") or {},
        growth_path_packet=state.get("growth_path_packet") or {},
    )
    rebuilt_allowed = dict(built.get("allowed") or {})
    rebuilt_menus = {key: value for key, value in built.items() if key != "allowed"}
    return rebuilt_menus, rebuilt_allowed


def _relative_price(state: Dict[str, Any], inputs: Dict[str, Any]) -> Optional[float]:
    matrix = state.get("peer_comparison_matrix") or {}
    ticker = str(state.get("ticker") or "").strip().upper()
    competitors = matrix.get("competitors") or []
    medians = matrix.get("peer_medians") or {}
    metrics = (matrix.get("metrics") or {}).get(ticker) or {}
    if not competitors:
        return None
    from .report_pack import implied_price_from_ev_ebitda

    result = implied_price_from_ev_ebitda(
        peer_median_ev_ebitda=medians.get("ev_to_ebitda"),
        target_ev_ebitda=metrics.get("ev_to_ebitda"),
        target_ebitda=metrics.get("ebitda"),
        market_cap=inputs.get("market_cap"),
        total_debt=float(inputs.get("total_debt") or 0.0),
        cash=float(inputs.get("cash_and_equivalents") or 0.0),
        shares=float(inputs.get("shares_outstanding") or 0.0),
    )
    if not result:
        return None
    try:
        return float(result["implied_price"])
    except (TypeError, ValueError, KeyError):
        return None


def _case_assumptions(
    *,
    name: str,
    labels: Dict[str, str],
    menus: Dict[str, Any],
    applied: Dict[str, Any],
    risk_free_rate: Optional[float],
    firm_type: Optional[str],
    packet: Optional[Dict[str, Any]],
    baseline_stable: Optional[float],
) -> Dict[str, Any]:
    growth = float(
        menus.get("high_growth_rate", {}).get(
            labels.get("high_growth_rate"),
            applied["high_growth_rate"],
        )
    )
    years = int(
        menus.get("high_growth_years", {}).get(
            labels.get("high_growth_years"),
            applied["high_growth_years"],
        )
    )
    margin = float(
        menus.get("terminal_margin", {}).get(
            labels.get("terminal_margin"),
            applied["terminal_margin"],
        )
    )
    stc = float(
        menus.get("sales_to_capital", {}).get(
            labels.get("sales_to_capital"),
            applied["sales_to_capital"],
        )
    )
    raw_g = menus.get("terminal_growth_rate", {}).get(labels.get("terminal_growth_rate"))
    terminal_g = clip_terminal_growth(
        raw_g if raw_g is not None else applied.get("terminal_growth_rate"),
        risk_free_rate,
        firm_type=firm_type,
        packet=packet,
    )
    stable = stable_sales_to_capital_for_label(
        labels.get("sales_to_capital") or "base",
        stc,
        baseline_stable if baseline_stable is not None else applied.get("stable_sales_to_capital"),
    )
    return {
        "name": name,
        "labels": labels,
        "high_growth_rate": growth,
        "high_growth_years": years,
        "transition_years": int(applied["transition_years"]),
        "terminal_margin": margin,
        "sales_to_capital": stc,
        "stable_sales_to_capital": stable,
        "terminal_growth_rate": terminal_g,
    }


def _run_case(
    assumptions: Dict[str, Any],
    *,
    applied: Dict[str, Any],
    inputs: Dict[str, Any],
    dcf: Dict[str, Any],
    relative_value: Optional[float],
    ke: Optional[float],
    dividend: Optional[float],
    mix_dcf: float = 0.70,
    mix_rel: float = 0.30,
) -> Dict[str, Any]:
    wacc = float(dcf["wacc_applied"])
    terminal_wacc = float(dcf["terminal_wacc_applied"])
    g = min(float(assumptions["terminal_growth_rate"]), terminal_wacc - 0.02)
    result = perform_3stage_dcf_valuation(
        base_revenue=float(applied["base_revenue"]),
        base_ebit=float(applied["base_ebit"]),
        sales_to_capital=float(assumptions["sales_to_capital"]),
        high_growth_rate=float(assumptions["high_growth_rate"]),
        wacc=wacc,
        terminal_wacc=terminal_wacc,
        shares_outstanding=float(inputs["shares_outstanding"]),
        total_debt=float(inputs["total_debt"]),
        cash_and_equivalents=float(inputs["cash_and_equivalents"]),
        high_growth_years=int(assumptions["high_growth_years"]),
        transition_years=int(assumptions["transition_years"]),
        terminal_growth_rate=g,
        terminal_margin=float(assumptions["terminal_margin"]),
        stable_sales_to_capital=float(assumptions["stable_sales_to_capital"]),
        interest_expense=float(applied.get("interest_expense") or 0.0),
    )
    from .report_pack import blend_fair_value, price_target_12m

    dcf_ps = max(float(result["intrinsic_value_per_share"]), 0.0)
    fair, dcf_w, rel_w = blend_fair_value(dcf_ps, relative_value, mix_dcf, mix_rel)
    target = price_target_12m(fair, ke, dividend)
    if target is not None:
        target = max(target, 0.0)
    year1 = (result.get("projections") or [{}])[0]
    return {
        **assumptions,
        "terminal_growth_rate": g,
        "dcf_per_share": dcf_ps,
        "fair_value": fair,
        "price_target_12m": target,
        "dcf_weight": dcf_w,
        "relative_weight": rel_w,
        "year1_revenue": year1.get("revenue"),
        "year1_ebit": year1.get("ebit"),
        "year1_eps": year1.get("eps"),
        "year1_fcff": year1.get("fcff"),
    }


def build_operating_scenarios(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Three DCFs that change operating labels only. WACC, beta, and the peer
    multiple stay on the base case.
    """
    summary = state.get("valuation_summary") or {}
    applied = dict(summary.get("applied_dcf_assumptions") or {})
    inputs = summary.get("valuation_date_inputs") or {}
    dcf = summary.get("dcf") or {}
    required = (
        "base_revenue",
        "base_ebit",
        "sales_to_capital",
        "high_growth_rate",
        "high_growth_years",
        "transition_years",
        "terminal_margin",
        "stable_sales_to_capital",
        "terminal_growth_rate",
    )
    if any(applied.get(key) is None for key in required):
        return None
    if any(
        inputs.get(key) is None
        for key in ("shares_outstanding", "total_debt", "cash_and_equivalents")
    ):
        return None
    if dcf.get("wacc_applied") is None or dcf.get("terminal_wacc_applied") is None:
        return None

    menus, allowed = _menus_from_state(state)
    classification = summary.get("firm_classification") or {}
    packet = state.get("industry_macro_packet") or {}
    rf = (summary.get("valuation_date_inputs") or {}).get("risk_free_rate")
    relative = _relative_price(state, inputs)
    ke = summary.get("cost_of_equity")
    dividend = inputs.get("indicated_dividend")
    from .valuation_mix import mix_weights_from_state

    mix_dcf, mix_rel = mix_weights_from_state(state)
    baseline_stable = (classification or {}).get("stable_sales_to_capital")
    base_labels = _base_labels(menus, applied, state)

    def labels_for(order: Dict[str, Tuple[str, ...]]) -> Dict[str, str]:
        picked: Dict[str, str] = {}
        for key in _MENU_KEYS:
            label = _pick_label(
                key,
                order[key],
                allowed,
                menus.get(key) or {},
            )
            if label:
                picked[key] = label
        return picked

    cases: Dict[str, Any] = {}
    base_case = {
        "name": "base",
        "labels": {key: base_labels.get(key) for key in _MENU_KEYS if base_labels.get(key)},
        "high_growth_rate": float(applied["high_growth_rate"]),
        "high_growth_years": int(applied["high_growth_years"]),
        "transition_years": int(applied["transition_years"]),
        "terminal_margin": float(applied["terminal_margin"]),
        "sales_to_capital": float(applied["sales_to_capital"]),
        "stable_sales_to_capital": float(applied["stable_sales_to_capital"]),
        "terminal_growth_rate": float(applied["terminal_growth_rate"]),
    }
    try:
        cases["base"] = _run_case(
            base_case,
            applied=applied,
            inputs=inputs,
            dcf=dcf,
            relative_value=relative,
            ke=ke,
            dividend=dividend,
            mix_dcf=mix_dcf,
            mix_rel=mix_rel,
        )
    except (TypeError, ValueError):
        return None

    for name, labels in (("bear", labels_for(BEAR_ORDER)), ("bull", labels_for(BULL_ORDER))):
        assumptions = _case_assumptions(
            name=name,
            labels=labels,
            menus=menus,
            applied=applied,
            risk_free_rate=rf,
            firm_type=classification.get("firm_type"),
            packet=packet,
            baseline_stable=baseline_stable,
        )
        try:
            cases[name] = _run_case(
                assumptions,
                applied=applied,
                inputs=inputs,
                dcf=dcf,
                relative_value=relative,
                ke=ke,
                dividend=dividend,
                mix_dcf=mix_dcf,
                mix_rel=mix_rel,
            )
        except (TypeError, ValueError):
            continue

    if "bear" not in cases or "bull" not in cases:
        return {
            "methodology": _METHODOLOGY,
            "wacc_held": dcf.get("wacc_applied"),
            "cases": cases,
            "note": "A bull or bear case could not be solved under the terminal WACC − g rail.",
        }

    identical = (
        abs(cases["bear"]["dcf_per_share"] - cases["bull"]["dcf_per_share"]) < 1e-6
        and abs(cases["bear"]["dcf_per_share"] - cases["base"]["dcf_per_share"]) < 1e-6
    )
    return {
        "methodology": _METHODOLOGY,
        "wacc_held": dcf.get("wacc_applied"),
        "relative_held": relative,
        "identical_to_base": identical,
        "cases": cases,
    }


def _case_iterable(cases: Any) -> List[Dict[str, Any]]:
    if isinstance(cases, dict):
        return [case for case in cases.values() if isinstance(case, dict)]
    if isinstance(cases, list):
        return [case for case in cases if isinstance(case, dict)]
    return []


def scenario_dcf_range(
    scenarios: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float]]:
    if not scenarios:
        return None, None
    values: List[float] = []
    for case in _case_iterable(scenarios.get("cases")):
        value = case.get("dcf_per_share")
        if value is None:
            continue
        try:
            values.append(max(float(value), 0.0))
        except (TypeError, ValueError):
            continue
    if not values:
        return None, None
    return min(values), max(values)
