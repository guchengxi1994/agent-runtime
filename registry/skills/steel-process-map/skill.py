definition = {
    "name": "steel-process-map",
    "description": "Build a process-stage map for steel energy-control analysis.",
}


ROUTES = {
    "integrated-bf-bof": {
        "aliases": ["integrated", "bf-bof", "blast-furnace-basic-oxygen-furnace", "long-process"],
        "stages": [
            "raw material preparation",
            "coking",
            "sintering or pelletizing",
            "blast furnace ironmaking",
            "hot metal pretreatment",
            "BOF steelmaking",
            "secondary metallurgy",
            "continuous casting",
            "rolling and finishing",
            "oxygen, power, steam, gas, and water utilities",
        ],
        "energy_carriers": [
            "coke",
            "pulverized coal",
            "blast furnace gas",
            "coke oven gas",
            "converter gas",
            "oxygen",
            "electricity",
            "steam",
            "natural gas or fuel oil",
        ],
        "drivers": [
            "coke rate and pulverized coal injection balance",
            "hot metal silicon and temperature control",
            "sinter quality and burden permeability",
            "gas recovery rate and gas network dispatch",
            "hot charging or direct rolling ratio",
            "yield loss and rework",
            "oxygen plant and compressed-air efficiency",
        ],
        "levers": [
            "stabilize burden quality and blast furnace permeability",
            "optimize PCI, oxygen enrichment, and top pressure recovery",
            "increase LDG/BFG/COG recovery and prioritized cascade use",
            "raise hot-charge ratio and reduce slab temperature loss",
            "tighten reheating furnace air-fuel ratio and furnace pressure",
        ],
    },
    "eaf": {
        "aliases": ["electric-arc-furnace", "short-process"],
        "stages": [
            "scrap or DRI/HBI preparation",
            "scrap preheating if available",
            "EAF melting and refining",
            "ladle furnace",
            "continuous casting",
            "rolling and finishing",
            "off-gas, power, oxygen, water, and dust collection systems",
        ],
        "energy_carriers": ["electricity", "oxygen", "natural gas", "carbon", "chemical heat", "steam", "compressed air"],
        "drivers": [
            "metallic charge quality",
            "tap-to-tap time",
            "foamy slag stability",
            "oxygen and burner practice",
            "transformer utilization",
            "tap temperature and waiting time",
            "off-gas sensible heat recovery",
        ],
        "levers": [
            "improve scrap mix and charge density",
            "optimize burner, oxygen, carbon, and slag practice",
            "reduce power-off time and waiting time",
            "stabilize endpoint temperature and chemistry",
            "recover off-gas heat where equipment and dust constraints allow",
        ],
    },
    "hot-rolling": {
        "aliases": ["hot rolling", "hot-strip-mill", "plate-mill", "bar-rolling", "wire-rod"],
        "stages": [
            "slab or billet yard",
            "reheating furnace",
            "descaling",
            "roughing mill",
            "finishing mill",
            "cooling",
            "coiling or cutting",
            "hydraulic, lubrication, water, and compressed-air systems",
        ],
        "energy_carriers": ["fuel gas", "natural gas", "electricity", "steam", "compressed air", "cooling water"],
        "drivers": [
            "charge temperature and hot charging ratio",
            "furnace residence time",
            "air-fuel ratio and oxygen trim",
            "furnace pressure and door opening losses",
            "scale loss",
            "mill motor load and idle running",
            "rolling schedule and delays",
        ],
        "levers": [
            "increase hot charging or direct rolling",
            "optimize reheating curve by grade and thickness",
            "reduce furnace excess air and leakage",
            "minimize delays between furnace and mill",
            "shut down idle auxiliaries and repair compressed-air leaks",
        ],
    },
    "cold-rolling": {
        "aliases": ["cold rolling", "crm", "pickling", "annealing", "galvanizing"],
        "stages": [
            "pickling",
            "cold rolling mill",
            "degreasing",
            "annealing",
            "skin pass",
            "galvanizing or coating if applicable",
            "finishing and inspection",
            "hydrogen, nitrogen, steam, compressed-air, water, and power systems",
        ],
        "energy_carriers": ["electricity", "steam", "natural gas", "hydrogen", "nitrogen", "compressed air", "cooling water"],
        "drivers": [
            "product mix and annealing cycle",
            "strip thickness reduction",
            "line speed and downtime",
            "furnace atmosphere and temperature control",
            "motor and drive efficiency",
            "steam trap and condensate recovery",
        ],
        "levers": [
            "optimize annealing recipes by grade",
            "reduce line stops and furnace idling",
            "recover waste heat from furnace exhaust",
            "improve steam trap and condensate management",
            "optimize motor, pump, fan, and compressed-air systems",
        ],
    },
    "reheating-furnace": {
        "aliases": ["reheating", "walking-beam-furnace", "pusher-furnace"],
        "stages": ["charging", "preheating zone", "heating zone", "soaking zone", "discharging", "flue gas and recuperator"],
        "energy_carriers": ["blast furnace gas", "coke oven gas", "converter gas", "natural gas", "electricity", "combustion air"],
        "drivers": [
            "charge temperature",
            "residence time",
            "target discharge temperature",
            "excess air",
            "furnace pressure",
            "skid and wall losses",
            "recuperator performance",
            "production interruptions",
        ],
        "levers": [
            "model-based temperature setpoints",
            "oxygen trim and air-fuel ratio control",
            "furnace pressure control",
            "recuperator maintenance",
            "reduce door opening and waiting losses",
            "increase hot charging",
        ],
    },
    "utilities": {
        "aliases": ["utility", "power-steam-gas", "gas-network", "energy-center"],
        "stages": ["gas recovery", "gas holder and mixing", "boilers or CHP", "steam network", "power distribution", "oxygen plant", "compressed air", "water systems"],
        "energy_carriers": ["BFG", "COG", "LDG", "steam", "electricity", "oxygen", "nitrogen", "compressed air", "water"],
        "drivers": [
            "gas recovery rate",
            "gas holder pressure and dispatch priority",
            "boiler or CHP efficiency",
            "steam pressure cascade",
            "oxygen plant load curve",
            "compressed-air leakage",
            "pump and fan throttling losses",
        ],
        "levers": [
            "prioritize high-value by-product gas use",
            "reduce gas diffusion and flaring",
            "optimize steam pressure levels",
            "schedule oxygen plant and large motors against power tariff",
            "repair compressed-air leaks and replace throttling with VFD control",
        ],
    },
}


GENERIC = {
    "stages": ["material input", "thermal processing", "mechanical processing", "quality control", "utilities", "emissions and by-products"],
    "energy_carriers": ["electricity", "fuel gas", "steam", "oxygen", "compressed air", "water"],
    "drivers": ["product mix", "equipment utilization", "temperature targets", "yield", "idle time", "utility efficiency"],
    "levers": ["define boundary", "measure major carriers", "separate product mix", "identify top losses", "rank controllable measures"],
}


def execute(params):
    process_route = str(params.get("process_route") or "unknown").strip().lower()
    product_or_grade = str(params.get("product_or_grade") or "unknown").strip()
    boundary = str(params.get("boundary") or "unknown").strip()
    objective = str(params.get("objective") or "baseline diagnosis").strip()

    route_key = _match_route(process_route)
    route = ROUTES.get(route_key, GENERIC)

    grade_notes = _grade_notes(product_or_grade)
    kpis = _kpis(route_key)
    required_data = _required_data(route_key)

    return {
        "process_route_input": process_route,
        "matched_route": route_key or "unknown",
        "product_or_grade": product_or_grade,
        "boundary": boundary,
        "objective": objective,
        "stages": route["stages"],
        "energy_carriers": route["energy_carriers"],
        "controllable_drivers": route["drivers"],
        "candidate_energy_control_levers": route["levers"],
        "recommended_kpis": kpis,
        "required_measurement_data": required_data,
        "grade_or_product_notes": grade_notes,
        "next_skill_suggestions": _next_skills(objective),
        "warnings": [
            "Do not compare energy intensity across different process routes or product mixes without normalization.",
            "Separate purchased energy, recovered by-product gas, exported energy, and final versus primary energy.",
        ],
    }


def _match_route(value):
    if not value or value == "unknown":
        return None
    normalized = value.replace("_", "-").strip().lower()
    for key, route in ROUTES.items():
        if normalized == key or normalized in route["aliases"]:
            return key
    if "blast" in normalized or "bof" in normalized or "converter" in normalized:
        return "integrated-bf-bof"
    if "electric" in normalized or normalized == "eaf":
        return "eaf"
    if "hot" in normalized and "roll" in normalized:
        return "hot-rolling"
    if "cold" in normalized and "roll" in normalized:
        return "cold-rolling"
    if "reheat" in normalized or "furnace" in normalized:
        return "reheating-furnace"
    if "util" in normalized or "gas" in normalized or "steam" in normalized:
        return "utilities"
    return None


def _kpis(route_key):
    base = [
        "GJ per tonne on the selected product basis",
        "kgce per tonne on the selected product basis",
        "electricity kWh per tonne",
        "fuel GJ per tonne",
        "yield percent",
        "operating rate and idle time",
    ]
    route_specific = {
        "integrated-bf-bof": ["kg coke per tonne hot metal", "kg coal injection per tonne hot metal", "BFG/COG/LDG recovery rate", "hot charge ratio"],
        "eaf": ["kWh per tonne liquid steel", "Nm3 oxygen per tonne", "tap-to-tap time", "power-on time ratio"],
        "hot-rolling": ["reheating fuel GJ per tonne", "mill electricity kWh per tonne", "hot charge ratio", "scale loss percent"],
        "cold-rolling": ["electricity kWh per tonne", "annealing fuel or steam per tonne", "line speed", "downtime ratio"],
        "reheating-furnace": ["fuel GJ per tonne", "discharge temperature deviation", "excess oxygen percent", "furnace residence time"],
        "utilities": ["gas recovery percent", "gas diffusion or flaring rate", "steam generation efficiency", "compressed-air leakage estimate"],
    }
    return base + route_specific.get(route_key or "", [])


def _required_data(route_key):
    data = [
        "production quantity and product basis",
        "time period",
        "energy stream name, amount, unit, and whether it is input, recovered, exported, or loss",
        "major product or grade mix",
        "main operating constraints and downtime",
    ]
    if route_key in {"integrated-bf-bof", "utilities"}:
        data.extend(["BFG/COG/LDG generation, recovery, holder, use, and diffusion", "coke, coal, oxygen, steam, and power balances"])
    if route_key in {"hot-rolling", "reheating-furnace"}:
        data.extend(["charge and discharge temperature", "furnace residence time", "fuel composition or LHV", "rolling delays"])
    if route_key == "eaf":
        data.extend(["scrap or DRI mix", "power-on time", "oxygen and burner use", "tap temperature"])
    return data


def _grade_notes(product_or_grade):
    value = product_or_grade.lower()
    notes = []
    if any(word in value for word in ["stainless", "silicon", "bearing", "alloy", "electrical"]):
        notes.append("High-alloy or specialty grades may need higher refining, annealing, atmosphere control, or finishing energy.")
    if any(word in value for word in ["rebar", "wire", "bar"]):
        notes.append("Long products are often sensitive to billet reheating practice, rolling delays, and mill utilization.")
    if any(word in value for word in ["hot rolled", "hrc", "plate", "coil"]):
        notes.append("Flat hot-rolled products are sensitive to slab temperature, furnace schedule, thickness mix, and laminar cooling constraints.")
    if any(word in value for word in ["cold", "galvan", "anneal"]):
        notes.append("Cold-rolled or coated products can shift energy from hot rolling to annealing, coating, steam, and atmosphere systems.")
    return notes or ["No grade-specific adjustment identified from the provided product description."]


def _next_skills(objective):
    value = objective.lower()
    suggestions = ["steel-energy-balance if numeric energy streams are available"]
    if any(word in value for word in ["save", "reduce", "control", "plan", "measure", "priority"]):
        suggestions.append("steel-savings-prioritizer for ranking actions")
    if any(word in value for word in ["benchmark", "policy", "current", "source", "citation", "standard"]):
        suggestions.append("web-search and web-fetch for external evidence")
    return suggestions
