definition = {
    "name": "steel-savings-prioritizer",
    "description": "Rank steel energy-saving measures by impact, economics, feasibility, and risk.",
}


EVIDENCE_SCORE = {"high": 1.0, "medium": 0.7, "low": 0.4, "unknown": 0.5}
RISK_SCORE = {"low": 1.0, "medium": 0.65, "high": 0.25, "unknown": 0.5}


def execute(params):
    measures = params.get("measures")
    if not isinstance(measures, list) or not measures:
        raise ValueError("measures must be a non-empty array")

    baseline = params.get("baseline_energy_gj_per_year")
    baseline = float(baseline) if baseline is not None else None
    energy_price = float(params.get("energy_price_per_gj") or 0)
    objective = str(params.get("objective") or "balanced").strip().lower()

    evaluated = []
    max_saving = 0.0
    for index, measure in enumerate(measures, start=1):
        if not isinstance(measure, dict):
            raise ValueError(f"measure #{index} must be an object")
        item = _evaluate_measure(measure, baseline, energy_price)
        max_saving = max(max_saving, item["annual_energy_saving_gj"])
        evaluated.append(item)

    for item in evaluated:
        item["priority_score"] = _score(item, max_saving, objective)
        item["priority_band"] = _band(item["priority_score"])

    evaluated.sort(key=lambda item: item["priority_score"], reverse=True)

    return {
        "objective": objective,
        "baseline_energy_gj_per_year": baseline,
        "energy_price_per_gj": energy_price,
        "ranked_measures": evaluated,
        "quick_wins": [
            item for item in evaluated
            if item["implementation_months"] <= 6 and item["risk_level"] in {"low", "unknown"} and item["annual_energy_saving_gj"] > 0
        ][:5],
        "warnings": _warnings(evaluated, baseline, energy_price),
        "interpretation_notes": [
            "Score is for screening only; verify savings with metered baseline and process constraints.",
            "Energy saving from percentage requires baseline_energy_gj_per_year.",
            "Payback uses simple capex divided by annual net cash saving.",
        ],
    }


def _evaluate_measure(measure, baseline, energy_price):
    name = str(measure.get("name") or "").strip()
    if not name:
        raise ValueError("each measure requires a name")

    saving = measure.get("annual_energy_saving_gj")
    saving_percent = measure.get("saving_percent")
    if saving is None and saving_percent is not None and baseline is not None:
        saving = baseline * float(saving_percent) / 100.0
    annual_energy_saving_gj = max(0.0, float(saving or 0))

    capex = max(0.0, float(measure.get("capex") or 0))
    annual_opex_saving = float(measure.get("annual_opex_saving") or 0)
    annual_energy_cost_saving = annual_energy_saving_gj * energy_price
    annual_net_cash_saving = annual_energy_cost_saving + annual_opex_saving
    payback_years = capex / annual_net_cash_saving if annual_net_cash_saving > 0 and capex > 0 else None
    implementation_months = max(0.0, float(measure.get("implementation_months") or 6))
    evidence_level = _normalize_level(measure.get("evidence_level"), EVIDENCE_SCORE)
    risk_level = _normalize_level(measure.get("risk_level"), RISK_SCORE)
    dependencies = measure.get("dependencies")
    dependencies = dependencies if isinstance(dependencies, list) else []

    return {
        "name": name,
        "process_stage": str(measure.get("process_stage") or "unspecified"),
        "annual_energy_saving_gj": annual_energy_saving_gj,
        "saving_percent": float(saving_percent) if saving_percent is not None else None,
        "annual_energy_cost_saving": annual_energy_cost_saving,
        "annual_opex_saving": annual_opex_saving,
        "annual_net_cash_saving": annual_net_cash_saving,
        "capex": capex,
        "simple_payback_years": payback_years,
        "implementation_months": implementation_months,
        "evidence_level": evidence_level,
        "risk_level": risk_level,
        "dependencies": [str(item) for item in dependencies],
    }


def _score(item, max_saving, objective):
    saving_score = item["annual_energy_saving_gj"] / max_saving if max_saving > 0 else 0.0
    payback = item["simple_payback_years"]
    if payback is None:
        payback_score = 0.45 if item["capex"] == 0 and item["annual_energy_saving_gj"] > 0 else 0.2
    elif payback <= 1:
        payback_score = 1.0
    elif payback <= 2:
        payback_score = 0.85
    elif payback <= 3:
        payback_score = 0.65
    elif payback <= 5:
        payback_score = 0.4
    else:
        payback_score = 0.2

    speed_score = 1.0 if item["implementation_months"] <= 3 else 0.8 if item["implementation_months"] <= 6 else 0.55 if item["implementation_months"] <= 12 else 0.3
    evidence_score = EVIDENCE_SCORE[item["evidence_level"]]
    risk_score = RISK_SCORE[item["risk_level"]]

    weights = {
        "quick wins": (0.2, 0.3, 0.25, 0.1, 0.15),
        "quick-wins": (0.2, 0.3, 0.25, 0.1, 0.15),
        "maximum savings": (0.5, 0.15, 0.1, 0.15, 0.1),
        "low risk": (0.2, 0.2, 0.15, 0.15, 0.3),
        "balanced": (0.32, 0.24, 0.16, 0.14, 0.14),
    }.get(objective, (0.32, 0.24, 0.16, 0.14, 0.14))

    score = (
        weights[0] * saving_score
        + weights[1] * payback_score
        + weights[2] * speed_score
        + weights[3] * evidence_score
        + weights[4] * risk_score
    )
    return round(score * 100, 2)


def _band(score):
    if score >= 75:
        return "A"
    if score >= 55:
        return "B"
    if score >= 35:
        return "C"
    return "D"


def _normalize_level(value, allowed):
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in allowed else "unknown"


def _warnings(evaluated, baseline, energy_price):
    warnings = []
    if baseline is None and any(item["saving_percent"] is not None and item["annual_energy_saving_gj"] == 0 for item in evaluated):
        warnings.append("Some measures used saving_percent but no baseline_energy_gj_per_year was provided.")
    if energy_price <= 0:
        warnings.append("energy_price_per_gj is zero or missing; economic ranking excludes energy cost savings.")
    if any(item["risk_level"] == "high" for item in evaluated):
        warnings.append("High-risk measures require engineering, quality, safety, and production-capacity review.")
    return warnings
