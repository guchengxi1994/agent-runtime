definition = {
    "name": "ontology-template-marketplace",
    "description": "Return reusable starter templates for industrial ontologies.",
}


BASE_TEMPLATE = {
    "template_name": "Generic Manufacturing Core",
    "objects": [
        {"name": "Factory", "aliases": ["Plant", "Site"], "properties": ["id", "name", "siteCode", "status"]},
        {"name": "Workshop", "aliases": ["Area", "Shop"], "properties": ["id", "name", "workshopCode", "status"]},
        {"name": "ProductionLine", "aliases": ["Line", "Cell"], "properties": ["id", "name", "lineCode", "status"]},
        {"name": "ProcessSegment", "aliases": ["Station", "Operation"], "properties": ["id", "name", "segmentCode", "status"]},
        {"name": "Machine", "aliases": ["Device", "Equipment"], "properties": ["id", "name", "model", "status", "temperature"]},
        {"name": "Sensor", "aliases": ["Tag", "MeasurementPoint"], "properties": ["id", "name", "value", "unit", "quality"]},
        {"name": "Alarm", "aliases": ["Event", "Fault"], "properties": ["id", "code", "severity", "status", "timestamp"]},
        {"name": "Product", "aliases": ["SKU", "Material"], "properties": ["id", "name", "specification", "version"]},
        {"name": "WorkOrder", "aliases": ["Order", "Job"], "properties": ["id", "orderNo", "status", "plannedStart", "plannedEnd"]},
        {"name": "MaterialLot", "aliases": ["Lot", "BatchLot"], "properties": ["id", "lotNo", "quantity", "unit", "status"]},
        {"name": "Operator", "aliases": ["Worker", "Technician"], "properties": ["id", "name", "role", "shiftCode"]},
        {"name": "Shift", "aliases": ["TeamShift"], "properties": ["id", "name", "startTime", "endTime", "status"]},
    ],
    "relations": [
        {"source": "Factory", "predicate": "contains", "target": "Workshop"},
        {"source": "Workshop", "predicate": "contains", "target": "ProductionLine"},
        {"source": "ProductionLine", "predicate": "contains", "target": "ProcessSegment"},
        {"source": "ProcessSegment", "predicate": "contains", "target": "Machine"},
        {"source": "Machine", "predicate": "measuredBy", "target": "Sensor"},
        {"source": "Alarm", "predicate": "generatedBy", "target": "Machine"},
        {"source": "WorkOrder", "predicate": "uses", "target": "Machine"},
        {"source": "WorkOrder", "predicate": "produces", "target": "Product"},
        {"source": "MaterialLot", "predicate": "usedBy", "target": "WorkOrder"},
        {"source": "Operator", "predicate": "operates", "target": "Machine"},
        {"source": "Operator", "predicate": "belongsTo", "target": "Shift"},
    ],
    "behaviors": [
        {"object": "Machine", "methods": ["start", "stop", "reset", "diagnose"]},
        {"object": "WorkOrder", "methods": ["create", "release", "close"]},
        {"object": "Alarm", "methods": ["acknowledge", "escalate", "clear"]},
    ],
    "mapping_targets": ["sql-table", "api-endpoint", "plc-register", "mqtt-topic", "opcua-node", "document-anchor"],
    "knowledge_anchor_types": ["manual", "sop", "maintenance-record", "alarm-history", "image", "video"],
    "governance_focus": ["alias normalization", "unit normalization", "machine-line hierarchy", "alarm-to-asset relation"],
}


INDUSTRY_EXTENSIONS = {
    "new-energy": {
        "template_name": "New Energy Core",
        "objects": [
            {"name": "EnergyStorageSystem", "aliases": ["ESS", "BatterySystem"], "properties": ["id", "name", "soc", "soh", "status"]},
            {"name": "Inverter", "aliases": ["PCS", "Converter"], "properties": ["id", "name", "power", "status", "temperature"]},
            {"name": "GridConnection", "aliases": ["PCC", "GridPoint"], "properties": ["id", "name", "voltage", "power", "status"]},
            {"name": "DispatchOrder", "aliases": ["DispatchCommand"], "properties": ["id", "commandType", "status", "effectiveTime"]},
        ],
        "relations": [
            {"source": "EnergyStorageSystem", "predicate": "controlledBy", "target": "Inverter"},
            {"source": "EnergyStorageSystem", "predicate": "connectedTo", "target": "GridConnection"},
            {"source": "DispatchOrder", "predicate": "controls", "target": "EnergyStorageSystem"},
        ],
        "behaviors": [
            {"object": "EnergyStorageSystem", "methods": ["charge", "discharge", "isolate"]},
            {"object": "Inverter", "methods": ["start", "stop", "setPowerLimit"]},
        ],
        "governance_focus": ["power and energy unit consistency", "dispatch behavior safety", "grid connection mapping"],
    },
    "battery": {
        "template_name": "Battery Manufacturing Core",
        "objects": [
            {"name": "Cell", "aliases": ["BatteryCell"], "properties": ["id", "serialNo", "chemistry", "voltage", "status"]},
            {"name": "Module", "aliases": ["BatteryModule"], "properties": ["id", "serialNo", "status", "temperature"]},
            {"name": "Pack", "aliases": ["BatteryPack"], "properties": ["id", "serialNo", "status", "soc"]},
            {"name": "FormationMachine", "aliases": ["FormationLine"], "properties": ["id", "name", "status", "channelCount"]},
            {"name": "DryRoom", "aliases": ["HumidityRoom"], "properties": ["id", "name", "dewPoint", "status"]},
            {"name": "QualitySample", "aliases": ["Sample"], "properties": ["id", "sampleType", "result", "timestamp"]},
        ],
        "relations": [
            {"source": "Cell", "predicate": "partOf", "target": "Module"},
            {"source": "Module", "predicate": "partOf", "target": "Pack"},
            {"source": "FormationMachine", "predicate": "produces", "target": "Cell"},
            {"source": "QualitySample", "predicate": "documents", "target": "Cell"},
        ],
        "behaviors": [
            {"object": "FormationMachine", "methods": ["start", "stop", "switchRecipe"]},
            {"object": "Pack", "methods": ["trace", "quarantine", "release"]},
        ],
        "governance_focus": ["cell-module-pack genealogy", "traceability", "quality sample linkage"],
    },
    "steel": {
        "template_name": "Steel Manufacturing Core",
        "objects": [
            {"name": "Heat", "aliases": ["Melt"], "properties": ["id", "heatNo", "grade", "status", "temperature"]},
            {"name": "Ladle", "aliases": ["TorpedoLadle"], "properties": ["id", "name", "status", "temperature"]},
            {"name": "Slab", "aliases": ["Billet", "Bloom"], "properties": ["id", "slabNo", "grade", "status", "temperature"]},
            {"name": "Coil", "aliases": ["HotRolledCoil"], "properties": ["id", "coilNo", "grade", "status", "weight"]},
            {"name": "Furnace", "aliases": ["BlastFurnace", "ReheatingFurnace"], "properties": ["id", "name", "status", "temperature"]},
            {"name": "RollingMill", "aliases": ["HotMill", "ColdMill"], "properties": ["id", "name", "status", "speed"]},
        ],
        "relations": [
            {"source": "Heat", "predicate": "containedBy", "target": "Ladle"},
            {"source": "Heat", "predicate": "produces", "target": "Slab"},
            {"source": "Slab", "predicate": "processedBy", "target": "RollingMill"},
            {"source": "RollingMill", "predicate": "produces", "target": "Coil"},
        ],
        "behaviors": [
            {"object": "Furnace", "methods": ["start", "stop", "adjustTemperature"]},
            {"object": "RollingMill", "methods": ["changeSchedule", "start", "stop"]},
        ],
        "governance_focus": ["heat-slab-coil lineage", "thermal unit consistency", "route-specific object hierarchy"],
    },
    "chemical": {
        "template_name": "Chemical Process Core",
        "objects": [
            {"name": "Batch", "aliases": ["CampaignBatch"], "properties": ["id", "batchNo", "status", "startTime", "endTime"]},
            {"name": "Reactor", "aliases": ["Kettle"], "properties": ["id", "name", "status", "temperature", "pressure"]},
            {"name": "Tank", "aliases": ["StorageTank"], "properties": ["id", "name", "level", "status"]},
            {"name": "Pipeline", "aliases": ["PipeSegment"], "properties": ["id", "name", "flow", "pressure", "status"]},
            {"name": "Formula", "aliases": ["Recipe"], "properties": ["id", "name", "version", "status"]},
            {"name": "SafetyInterlock", "aliases": ["SISRule"], "properties": ["id", "name", "status", "triggerCondition"]},
        ],
        "relations": [
            {"source": "Batch", "predicate": "uses", "target": "Formula"},
            {"source": "Batch", "predicate": "processedBy", "target": "Reactor"},
            {"source": "Reactor", "predicate": "connectedTo", "target": "Pipeline"},
            {"source": "Pipeline", "predicate": "connectedTo", "target": "Tank"},
            {"source": "SafetyInterlock", "predicate": "controls", "target": "Reactor"},
        ],
        "behaviors": [
            {"object": "Reactor", "methods": ["startBatch", "stopBatch", "cleanInPlace"]},
            {"object": "SafetyInterlock", "methods": ["trip", "reset", "bypassRequest"]},
        ],
        "governance_focus": ["safety interlock review", "pressure and temperature units", "batch genealogy"],
    },
    "food-beverage": {
        "template_name": "Food and Beverage Core",
        "objects": [
            {"name": "RecipeBatch", "aliases": ["Batch"], "properties": ["id", "batchNo", "status", "plannedVolume"]},
            {"name": "IngredientLot", "aliases": ["IngredientBatch"], "properties": ["id", "lotNo", "status", "expiryDate"]},
            {"name": "PackagingLine", "aliases": ["PackingLine"], "properties": ["id", "name", "status", "speed"]},
            {"name": "CIPCycle", "aliases": ["CleaningCycle"], "properties": ["id", "name", "status", "endTime"]},
            {"name": "QCCheck", "aliases": ["QualityCheck"], "properties": ["id", "checkType", "result", "timestamp"]},
        ],
        "relations": [
            {"source": "RecipeBatch", "predicate": "uses", "target": "IngredientLot"},
            {"source": "RecipeBatch", "predicate": "processedBy", "target": "PackagingLine"},
            {"source": "QCCheck", "predicate": "documents", "target": "RecipeBatch"},
            {"source": "CIPCycle", "predicate": "appliesTo", "target": "PackagingLine"},
        ],
        "behaviors": [
            {"object": "PackagingLine", "methods": ["start", "stop", "changeSku"]},
            {"object": "CIPCycle", "methods": ["start", "complete", "verify"]},
        ],
        "governance_focus": ["ingredient traceability", "cleaning validation", "quality release controls"],
    },
    "industrial-park": {
        "template_name": "Industrial Park Core",
        "objects": [
            {"name": "Building", "aliases": ["Facility"], "properties": ["id", "name", "buildingCode", "status"]},
            {"name": "Tenant", "aliases": ["EnterpriseTenant"], "properties": ["id", "name", "status", "industryType"]},
            {"name": "UtilityPlant", "aliases": ["EnergyCenter"], "properties": ["id", "name", "status", "capacity"]},
            {"name": "Meter", "aliases": ["SubMeter"], "properties": ["id", "name", "reading", "unit", "status"]},
            {"name": "Chiller", "aliases": ["CoolingUnit"], "properties": ["id", "name", "status", "load"]},
            {"name": "EmissionPoint", "aliases": ["Stack"], "properties": ["id", "name", "emissionType", "status"]},
        ],
        "relations": [
            {"source": "Factory", "predicate": "contains", "target": "Building"},
            {"source": "Building", "predicate": "occupiedBy", "target": "Tenant"},
            {"source": "UtilityPlant", "predicate": "supplies", "target": "Building"},
            {"source": "Building", "predicate": "measuredBy", "target": "Meter"},
            {"source": "EmissionPoint", "predicate": "belongsTo", "target": "Building"},
        ],
        "behaviors": [
            {"object": "UtilityPlant", "methods": ["dispatchLoad", "start", "stop"]},
            {"object": "Chiller", "methods": ["start", "stop", "setTemperature"]},
        ],
        "governance_focus": ["tenant-building-utility mapping", "meter normalization", "emission reporting lineage"],
    },
}


SCOPE_FOCUS = {
    "enterprise": ["Factory", "Workshop", "ProductionLine", "Product", "WorkOrder"],
    "plant": ["Factory", "Workshop", "ProductionLine", "Machine", "Sensor", "Alarm"],
    "line": ["ProductionLine", "ProcessSegment", "Machine", "Sensor", "Alarm", "WorkOrder"],
    "machine": ["Machine", "Sensor", "Alarm", "Operator", "WorkOrder"],
    "utility": ["UtilityPlant", "Meter", "EmissionPoint", "Chiller", "EnergyStorageSystem", "GridConnection"],
}


def execute(params):
    industry_input = str(params.get("industry") or "generic-manufacturing").strip().lower()
    scope = str(params.get("scope") or "plant").strip().lower()
    include_behaviors = True if params.get("include_behaviors") is None else bool(params.get("include_behaviors"))
    include_mappings = True if params.get("include_mappings") is None else bool(params.get("include_mappings"))

    template_key = _match_template(industry_input)
    template = _compose_template(template_key, include_behaviors, include_mappings)

    return {
        "industry_input": industry_input,
        "matched_template": template_key,
        "template_name": template["template_name"],
        "scope": scope,
        "focus_objects": _focus_objects(template["objects"], scope),
        "objects": template["objects"],
        "relations": template["relations"],
        "behaviors": template.get("behaviors", []),
        "mapping_targets": template.get("mapping_targets", []),
        "knowledge_anchor_types": template["knowledge_anchor_types"],
        "governance_focus": template["governance_focus"],
        "template_deltas_expected": [
            "source-specific aliases and naming conventions",
            "runtime mappings to actual data sources and command paths",
            "site-specific hierarchy or equipment variants",
            "validation and release policy adjustments",
        ],
        "suggested_next_skills": [
            "ontology-discovery for source evidence capture",
            "ontology-normalization for alias and unit consolidation",
            "ontology-mapping for runtime source binding",
            "ontology-validation before publish",
        ],
    }


def _match_template(industry_input):
    if not industry_input:
        return "generic-manufacturing"
    normalized = industry_input.replace("_", "-").strip().lower()
    aliases = {
        "generic": "generic-manufacturing",
        "manufacturing": "generic-manufacturing",
        "factory": "generic-manufacturing",
        "new energy": "new-energy",
        "新能源": "new-energy",
        "battery-manufacturing": "battery",
        "steelmaking": "steel",
        "food": "food-beverage",
        "food-and-beverage": "food-beverage",
        "park": "industrial-park",
    }
    if normalized in INDUSTRY_EXTENSIONS or normalized == "generic-manufacturing":
        return normalized
    return aliases.get(normalized, "generic-manufacturing")


def _compose_template(template_key, include_behaviors, include_mappings):
    template = {
        "template_name": BASE_TEMPLATE["template_name"],
        "objects": list(BASE_TEMPLATE["objects"]),
        "relations": list(BASE_TEMPLATE["relations"]),
        "behaviors": list(BASE_TEMPLATE["behaviors"]),
        "mapping_targets": list(BASE_TEMPLATE["mapping_targets"]),
        "knowledge_anchor_types": list(BASE_TEMPLATE["knowledge_anchor_types"]),
        "governance_focus": list(BASE_TEMPLATE["governance_focus"]),
    }
    if template_key != "generic-manufacturing":
        extension = INDUSTRY_EXTENSIONS[template_key]
        template["template_name"] = extension["template_name"]
        template["objects"].extend(extension["objects"])
        template["relations"].extend(extension["relations"])
        template["behaviors"].extend(extension["behaviors"])
        template["governance_focus"].extend(extension["governance_focus"])
    if not include_behaviors:
        template["behaviors"] = []
    if not include_mappings:
        template["mapping_targets"] = []
    return template


def _focus_objects(objects, scope):
    focus_names = SCOPE_FOCUS.get(scope, SCOPE_FOCUS["plant"])
    object_names = {item["name"] for item in objects}
    return [name for name in focus_names if name in object_names]
