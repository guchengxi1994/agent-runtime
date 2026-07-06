definition = {
    "name": "ontology-structure-validator",
    "description": "Validate industrial ontology fragments for common structural issues.",
}


ALLOWED_PROPERTY_TYPES = {
    "string",
    "integer",
    "number",
    "boolean",
    "datetime",
    "date",
    "enum",
    "object",
    "array",
    "quantity",
}
HIERARCHY_PREDICATES = {"contains", "belongsTo", "partOf", "parentOf", "childOf"}
UNIT_HINT_TOKENS = ("temp", "temperature", "pressure", "voltage", "current", "speed", "flow", "weight", "energy")


def execute(params):
    ontology = params.get("ontology")
    if not isinstance(ontology, dict):
        raise ValueError("ontology must be an object")

    strict = bool(params.get("strict", False))
    objects = ontology.get("objects")
    relations = ontology.get("relations") or []
    mappings = ontology.get("mappings") or []

    if not isinstance(objects, list) or not objects:
        raise ValueError("ontology.objects must be a non-empty array")
    if not isinstance(relations, list):
        raise ValueError("ontology.relations must be an array when provided")
    if not isinstance(mappings, list):
        raise ValueError("ontology.mappings must be an array when provided")

    errors = []
    warnings = []
    infos = []

    object_names = {}
    alias_index = {}
    object_properties = {}

    for index, item in enumerate(objects, start=1):
        if not isinstance(item, dict):
            errors.append(_finding("error", "object.invalid", f"Object #{index} must be an object."))
            continue

        name = str(item.get("name") or "").strip()
        if not name:
            errors.append(_finding("error", "object.missing_name", f"Object #{index} is missing a name."))
            continue

        normalized_name = _normalize(name)
        if normalized_name in object_names:
            errors.append(_finding("error", "object.duplicate", f"Duplicate object name: {name}."))
            continue
        object_names[normalized_name] = name

        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            errors.append(_finding("error", "object.invalid_aliases", f"{name}.aliases must be an array."))
            aliases = []
        for raw_alias in aliases:
            alias = str(raw_alias or "").strip()
            if not alias:
                continue
            normalized_alias = _normalize(alias)
            existing = alias_index.get(normalized_alias)
            if existing and existing != name:
                warnings.append(
                    _finding("warning", "alias.collision", f"Alias '{alias}' is shared by objects {existing} and {name}.")
                )
            else:
                alias_index[normalized_alias] = name

        properties = item.get("properties") or []
        if not isinstance(properties, list):
            errors.append(_finding("error", "property.invalid_list", f"{name}.properties must be an array."))
            properties = []
        object_properties[normalized_name] = set()
        for prop in properties:
            _validate_property(name, prop, object_properties[normalized_name], strict, errors, warnings)

        behaviors = item.get("behaviors") or []
        if not isinstance(behaviors, list):
            errors.append(_finding("error", "behavior.invalid_list", f"{name}.behaviors must be an array."))
            behaviors = []
        seen_behaviors = set()
        for behavior in behaviors:
            if not isinstance(behavior, dict):
                errors.append(_finding("error", "behavior.invalid", f"{name} has a non-object behavior entry."))
                continue
            behavior_name = str(behavior.get("name") or "").strip()
            if not behavior_name:
                errors.append(_finding("error", "behavior.missing_name", f"{name} has a behavior without a name."))
                continue
            normalized_behavior = _normalize(behavior_name)
            if normalized_behavior in seen_behaviors:
                warnings.append(_finding("warning", "behavior.duplicate", f"{name} has duplicate behavior {behavior_name}."))
            else:
                seen_behaviors.add(normalized_behavior)

    relation_keys = set()
    for relation in relations:
        if not isinstance(relation, dict):
            errors.append(_finding("error", "relation.invalid", "Relation entries must be objects."))
            continue
        source = str(relation.get("source") or "").strip()
        predicate = str(relation.get("predicate") or "").strip()
        target = str(relation.get("target") or "").strip()
        if not source or not predicate or not target:
            errors.append(_finding("error", "relation.missing_fields", "Relation entries require source, predicate, and target."))
            continue
        source_key = _normalize(source)
        target_key = _normalize(target)
        if source_key not in object_names:
            errors.append(_finding("error", "relation.unknown_source", f"Relation source object not found: {source}."))
        if target_key not in object_names:
            errors.append(_finding("error", "relation.unknown_target", f"Relation target object not found: {target}."))
        relation_key = (source_key, _normalize(predicate), target_key)
        if relation_key in relation_keys:
            warnings.append(_finding("warning", "relation.duplicate", f"Duplicate relation: {source} {predicate} {target}."))
        else:
            relation_keys.add(relation_key)
        if source_key == target_key:
            code = "relation.self_loop_hierarchy" if predicate in HIERARCHY_PREDICATES else "relation.self_loop"
            severity = "error" if predicate in HIERARCHY_PREDICATES else "warning"
            message = f"Self-referential relation detected: {source} {predicate} {target}."
            _bucket(severity, errors, warnings).append(_finding(severity, code, message))

    mapping_keys = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            errors.append(_finding("error", "mapping.invalid", "Mapping entries must be objects."))
            continue
        object_name = str(mapping.get("object") or "").strip()
        property_name = str(mapping.get("property") or "").strip()
        source_kind = str(mapping.get("source_kind") or "").strip()
        source_path = str(mapping.get("source_path") or "").strip()
        if not object_name or not property_name or not source_kind or not source_path:
            errors.append(_finding("error", "mapping.missing_fields", "Mappings require object, property, source_kind, and source_path."))
            continue
        object_key = _normalize(object_name)
        property_key = _normalize(property_name)
        if object_key not in object_names:
            errors.append(_finding("error", "mapping.unknown_object", f"Mapping object not found: {object_name}."))
        elif property_key not in object_properties.get(object_key, set()):
            warnings.append(
                _finding("warning", "mapping.unknown_property", f"Mapping property {object_name}.{property_name} was not found in the object definition.")
            )
        mapping_key = (object_key, property_key, _normalize(source_kind), source_path)
        if mapping_key in mapping_keys:
            warnings.append(
                _finding("warning", "mapping.duplicate", f"Duplicate mapping for {object_name}.{property_name} -> {source_kind}:{source_path}.")
            )
        else:
            mapping_keys.add(mapping_key)

    if not errors and not warnings:
        infos.append(_finding("info", "ontology.clean", "No structural issues were detected in the submitted ontology fragment."))

    release_recommendation = "block" if errors else "review" if warnings else "pass"
    return {
        "object_count": len(object_names),
        "relation_count": len([item for item in relations if isinstance(item, dict)]),
        "mapping_count": len([item for item in mappings if isinstance(item, dict)]),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "info_count": len(infos),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "release_recommendation": release_recommendation,
    }


def _validate_property(object_name, prop, seen, strict, errors, warnings):
    if not isinstance(prop, dict):
        errors.append(_finding("error", "property.invalid", f"{object_name} has a non-object property entry."))
        return
    property_name = str(prop.get("name") or "").strip()
    if not property_name:
        errors.append(_finding("error", "property.missing_name", f"{object_name} has a property without a name."))
        return
    property_key = _normalize(property_name)
    if property_key in seen:
        warnings.append(_finding("warning", "property.duplicate", f"{object_name} has duplicate property {property_name}."))
    else:
        seen.add(property_key)

    property_type = str(prop.get("type") or "string").strip().lower()
    if property_type not in ALLOWED_PROPERTY_TYPES:
        errors.append(
            _finding(
                "error",
                "property.invalid_type",
                f"{object_name}.{property_name} uses unsupported type {property_type}.",
            )
        )

    enum_values = prop.get("enum_values")
    if property_type == "enum" and (not isinstance(enum_values, list) or not enum_values):
        errors.append(_finding("error", "property.enum_missing_values", f"{object_name}.{property_name} is enum but has no enum_values."))

    unit = str(prop.get("unit") or "").strip()
    should_have_unit = property_type in {"number", "quantity"} and any(token in property_key for token in UNIT_HINT_TOKENS)
    if should_have_unit and not unit:
        severity = "error" if strict else "warning"
        message = f"{object_name}.{property_name} looks like an engineering quantity but has no unit."
        _bucket(severity, errors, warnings).append(_finding(severity, "property.missing_unit", message))


def _bucket(severity, errors, warnings):
    return errors if severity == "error" else warnings


def _finding(severity, code, message):
    return {"severity": severity, "code": code, "message": message}


def _normalize(value):
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())
