definition = {
    "name": "steel-energy-balance",
    "description": "Calculate steel energy balance and energy intensity from stream data.",
}


KGCE_GJ = 0.0293076

UNIT_FACTORS_GJ = {
    "gj": 1.0,
    "mj": 0.001,
    "mwh": 3.6,
    "kwh": 0.0036,
    "tce": 29.3076,
    "tonne-coal-equivalent": 29.3076,
    "kgce": KGCE_GJ,
    "tonne-coke": 28.5,
    "t-coke": 28.5,
    "kg-coke": 0.0285,
    "1000nm3-natural-gas": 35.8,
    "nm3-natural-gas": 0.0358,
    "1000nm3-bfg": 3.2,
    "nm3-bfg": 0.0032,
    "1000nm3-cog": 17.6,
    "nm3-cog": 0.0176,
    "1000nm3-ldg": 8.4,
    "1000nm3-bofg": 8.4,
    "nm3-ldg": 0.0084,
    "nm3-bofg": 0.0084,
}

SUBTRACT_ROLES = {"recovered", "exported", "output", "credit"}
ADD_ROLES = {"input", "loss"}


def execute(params):
    production_tonnes = float(params.get("production_tonnes") or 0)
    if production_tonnes <= 0:
        raise ValueError("production_tonnes must be greater than zero")
    streams = params.get("energy_streams")
    if not isinstance(streams, list) or not streams:
        raise ValueError("energy_streams must be a non-empty array")

    product_basis = str(params.get("product_basis") or "selected product")
    subtract_recovered = params.get("subtract_recovered_from_net")
    subtract_recovered = True if subtract_recovered is None else bool(subtract_recovered)
    electricity_primary_factor = params.get("electricity_primary_factor")
    electricity_primary_factor = float(electricity_primary_factor) if electricity_primary_factor is not None else None

    normalized = []
    gross_input_gj = 0.0
    recovered_or_exported_gj = 0.0
    electricity_gj = 0.0
    primary_adjusted_extra_gj = 0.0
    category_totals = {}
    warnings = []

    for index, stream in enumerate(streams, start=1):
        if not isinstance(stream, dict):
            raise ValueError(f"energy stream #{index} must be an object")
        name = str(stream.get("name") or f"stream_{index}").strip()
        amount = float(stream.get("amount") or 0)
        unit = str(stream.get("unit") or "").strip()
        role = str(stream.get("role") or "input").strip().lower()
        category = str(stream.get("category") or "other").strip().lower()
        lhv = stream.get("lhv_gj_per_unit")
        factor = float(lhv) if lhv is not None else _unit_factor(unit)
        gj = amount * factor

        if role in SUBTRACT_ROLES:
            recovered_or_exported_gj += gj
        else:
            if role not in ADD_ROLES:
                warnings.append(f"Unknown role '{role}' for {name}; treated as input.")
            gross_input_gj += gj

        if category == "electricity" or unit.lower() in {"kwh", "mwh"}:
            electricity_gj += gj
            if electricity_primary_factor is not None:
                primary_adjusted_extra_gj += gj * (electricity_primary_factor - 1.0)

        category_totals[category] = category_totals.get(category, 0.0) + gj
        normalized.append(
            {
                "name": name,
                "amount": amount,
                "unit": unit,
                "role": role,
                "category": category,
                "conversion_factor_gj_per_unit": factor,
                "energy_gj": gj,
                "energy_kgce": gj / KGCE_GJ,
            }
        )

    net_input_gj = gross_input_gj - recovered_or_exported_gj if subtract_recovered else gross_input_gj
    primary_adjusted_net_gj = net_input_gj + primary_adjusted_extra_gj
    intensity_gj_per_t = net_input_gj / production_tonnes
    intensity_kgce_per_t = intensity_gj_per_t / KGCE_GJ
    primary_adjusted_gj_per_t = primary_adjusted_net_gj / production_tonnes
    electricity_share = electricity_gj / gross_input_gj if gross_input_gj > 0 else 0.0

    if net_input_gj < 0:
        warnings.append("Net input is negative after subtracting recovered/exported streams; check accounting boundary.")
    if recovered_or_exported_gj > gross_input_gj * 0.8 and gross_input_gj > 0:
        warnings.append("Recovered/exported energy is very large relative to input; verify roles and units.")

    return {
        "product_basis": product_basis,
        "production_tonnes": production_tonnes,
        "stream_count": len(normalized),
        "gross_input_gj": gross_input_gj,
        "recovered_exported_credit_gj": recovered_or_exported_gj,
        "net_input_gj": net_input_gj,
        "primary_adjusted_net_gj": primary_adjusted_net_gj,
        "intensity_gj_per_t": intensity_gj_per_t,
        "intensity_kgce_per_t": intensity_kgce_per_t,
        "primary_adjusted_gj_per_t": primary_adjusted_gj_per_t,
        "electricity_gj": electricity_gj,
        "electricity_share_of_gross_input": electricity_share,
        "category_totals_gj": category_totals,
        "normalized_streams": normalized,
        "warnings": warnings,
        "conversion_notes": [
            "Electricity is final energy unless electricity_primary_factor is provided.",
            "Default by-product gas LHV values are approximate; use lhv_gj_per_unit for site data.",
            "1 kgce = 0.0293076 GJ.",
        ],
    }


def _unit_factor(unit):
    normalized = unit.strip().lower().replace(" ", "-").replace("_", "-")
    if normalized in UNIT_FACTORS_GJ:
        return UNIT_FACTORS_GJ[normalized]
    raise ValueError(f"unsupported unit: {unit}")
