definition = {
    "name": "ontology-runtime-resolve",
    "description": "Resolve industrial business references into workspace runtime mappings, instances, and related evidence.",
}

import json
import os
import re
import sqlite3


def execute(params):
    workspace_id = _resolve_workspace_id(params)
    db_path = _resolve_db_path(params, workspace_id)
    entity_ref = str(params.get("entity_ref") or "").strip()
    if not entity_ref:
        raise ValueError("entity_ref is required")
    metric_name = str(params.get("metric_name") or "").strip()
    analysis_goal = str(params.get("analysis_goal") or "").strip() or "general"
    include_related = True if params.get("include_related") is None else bool(params.get("include_related"))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        candidates = _resolve_candidates(conn, entity_ref)
        if not candidates:
            return {
                "success": False,
                "workspace_id": workspace_id,
                "db_path": db_path,
                "entity_ref": entity_ref,
                "error_type": "entity_not_found",
                "error": f"No entity matched '{entity_ref}' in the workspace ontology registry.",
            }
        best = candidates[0]
        mappings = _resolve_mappings(conn, best, metric_name)
        source_catalog = _resolve_sources(conn, best, metric_name)
        metric_definitions = _resolve_metric_definitions(conn, metric_name, mappings)
        related_entities = _resolve_related_entities(conn, best["entity_ref"]) if include_related else []
        recent_events = _resolve_recent_events(conn, best["entity_ref"]) if include_related else []
        recent_timeseries = _resolve_timeseries_preview(conn, best["entity_ref"], metric_name, mappings) if include_related else []
        return {
            "success": True,
            "workspace_id": workspace_id,
            "db_path": db_path,
            "analysis_goal": analysis_goal,
            "query": {"entity_ref": entity_ref, "metric_name": metric_name},
            "best_match": best,
            "candidates": candidates[:5],
            "metric_name": metric_name or _best_metric_name(metric_definitions, mappings),
            "metric_definitions": metric_definitions,
            "resolved_mappings": mappings,
            "source_catalog": source_catalog,
            "related_entities": related_entities,
            "recent_events": recent_events,
            "recent_timeseries_preview": recent_timeseries,
            "notes": _build_notes(best, mappings, source_catalog, recent_events),
        }
    finally:
        conn.close()


def _resolve_workspace_id(params):
    value = str(params.get("workspace_id") or os.getenv("AGENT_RUNTIME_WORKSPACE_ID") or "").strip()
    if not value:
        raise ValueError("workspace_id is required through params or sandbox context")
    return _sanitize_id(value)


def _resolve_db_path(params, workspace_id):
    raw = str(params.get("db_path") or "").strip()
    if raw:
        return raw
    artifacts_dir = str(os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR") or "").strip()
    if not artifacts_dir:
        raise ValueError("AGENT_RUNTIME_ARTIFACTS_DIR is not available in sandbox execution")
    return os.path.join(artifacts_dir, "workspaces", workspace_id, "ontology_runtime.db")


def _resolve_candidates(conn, query):
    normalized = _normalize(query)
    rows = conn.execute("SELECT entity_ref, object_name, display_name, parent_ref, attributes_json FROM entity_instance").fetchall()
    scored = []
    for row in rows:
        attributes = _load_json(row["attributes_json"])
        score = _match_score(normalized, [row["entity_ref"], row["display_name"], attributes.get("line_code"), attributes.get("machine_code"), attributes.get("tag_name")])
        if score <= 0:
            continue
        scored.append(
            {
                "entity_ref": row["entity_ref"],
                "object_name": row["object_name"],
                "display_name": row["display_name"],
                "parent_ref": row["parent_ref"],
                "attributes": attributes,
                "match_score": score,
            }
        )
    scored.sort(key=lambda item: (-item["match_score"], item["entity_ref"]))
    return scored


def _resolve_mappings(conn, best_match, metric_name):
    entity_object = best_match["object_name"]
    attributes = best_match.get("attributes") or {}
    possible_properties = []
    if metric_name:
        possible_properties.append(metric_name)
        possible_properties.append(_property_from_metric(metric_name))
    if "metric_name" in attributes:
        possible_properties.append(str(attributes.get("metric_name") or ""))
    rows = conn.execute(
        "SELECT object_name, property_name, source_kind, source_path, protocol, table_name, metric_name, extra_json FROM ontology_mapping WHERE object_name IN (?, 'ProductionLine', 'Machine', 'Sensor', 'Meter')",
        (entity_object,),
    ).fetchall()
    resolved = []
    for row in rows:
        row_metric = str(row["metric_name"] or "").strip()
        property_name = str(row["property_name"] or "").strip()
        if metric_name and not _mapping_matches_metric(metric_name, row_metric, property_name):
            continue
        resolved.append(
            {
                "object_name": row["object_name"],
                "property_name": property_name,
                "source_kind": row["source_kind"],
                "source_path": row["source_path"],
                "protocol": row["protocol"],
                "table_name": row["table_name"],
                "metric_name": row_metric,
                "extra": _load_json(row["extra_json"]),
            }
        )
    resolved.sort(key=lambda item: (0 if item["object_name"] == entity_object else 1, item["source_kind"], item["source_path"]))
    return resolved


def _resolve_sources(conn, best_match, metric_name):
    rows = conn.execute(
        "SELECT source_name, source_type, location, table_name, metric_name, entity_ref, extra_json FROM source_catalog"
    ).fetchall()
    results = []
    for row in rows:
        row_entity = str(row["entity_ref"] or "").strip()
        row_metric = str(row["metric_name"] or "").strip()
        extra = _load_json(row["extra_json"])
        if row_entity and row_entity != best_match["entity_ref"] and row_entity != best_match.get("parent_ref"):
            continue
        if metric_name and row_metric and not _normalize(metric_name) in {_normalize(row_metric), _normalize(_property_from_metric(row_metric))}:
            continue
        results.append(
            {
                "source_name": row["source_name"],
                "source_type": row["source_type"],
                "location": row["location"],
                "table_name": row["table_name"],
                "metric_name": row_metric,
                "entity_ref": row_entity,
                "extra": extra,
            }
        )
    return results[:10]


def _resolve_metric_definitions(conn, metric_name, mappings):
    candidates = []
    if metric_name:
        rows = conn.execute(
            "SELECT metric_name, display_name, unit, aggregation_rule, anomaly_rule, description, expression FROM metric_definition WHERE metric_name = ?",
            (metric_name,),
        ).fetchall()
        candidates.extend(rows)
    for mapping in mappings:
        if mapping.get("metric_name"):
            rows = conn.execute(
                "SELECT metric_name, display_name, unit, aggregation_rule, anomaly_rule, description, expression FROM metric_definition WHERE metric_name = ?",
                (mapping["metric_name"],),
            ).fetchall()
            candidates.extend(rows)
    deduped = {}
    for row in candidates:
        deduped[row["metric_name"]] = {
            "metric_name": row["metric_name"],
            "display_name": row["display_name"],
            "unit": row["unit"],
            "aggregation_rule": row["aggregation_rule"],
            "anomaly_rule": row["anomaly_rule"],
            "description": row["description"],
            "expression": row["expression"],
        }
    return list(deduped.values())


def _resolve_related_entities(conn, entity_ref):
    results = []
    rows = conn.execute(
        "SELECT source_ref, predicate, target_ref FROM instance_relation WHERE source_ref = ? OR target_ref = ?",
        (entity_ref, entity_ref),
    ).fetchall()
    for row in rows:
        neighbor = row["target_ref"] if row["source_ref"] == entity_ref else row["source_ref"]
        entity = conn.execute(
            "SELECT entity_ref, object_name, display_name, parent_ref, attributes_json FROM entity_instance WHERE entity_ref = ?",
            (neighbor,),
        ).fetchone()
        if not entity:
            continue
        results.append(
            {
                "relation": {
                    "source_ref": row["source_ref"],
                    "predicate": row["predicate"],
                    "target_ref": row["target_ref"],
                },
                "entity": {
                    "entity_ref": entity["entity_ref"],
                    "object_name": entity["object_name"],
                    "display_name": entity["display_name"],
                    "parent_ref": entity["parent_ref"],
                    "attributes": _load_json(entity["attributes_json"]),
                },
            }
        )
    return results[:12]


def _resolve_recent_events(conn, entity_ref):
    rows = conn.execute(
        """
        SELECT entity_ref, event_type, event_code, severity, status, started_at, ended_at, details_json
        FROM event_sample
        WHERE entity_ref = ?
        ORDER BY started_at DESC
        LIMIT 8
        """,
        (entity_ref,),
    ).fetchall()
    return [
        {
            "entity_ref": row["entity_ref"],
            "event_type": row["event_type"],
            "event_code": row["event_code"],
            "severity": row["severity"],
            "status": row["status"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "details": _load_json(row["details_json"]),
        }
        for row in rows
    ]


def _resolve_timeseries_preview(conn, entity_ref, metric_name, mappings):
    metric_candidates = []
    if metric_name:
        metric_candidates.append(metric_name)
    for mapping in mappings:
        if mapping.get("metric_name"):
            metric_candidates.append(mapping["metric_name"])
        if mapping.get("property_name"):
            metric_candidates.append(mapping["property_name"])
    metric_candidates = [item for item in metric_candidates if item]
    if not metric_candidates:
        rows = conn.execute(
            """
            SELECT entity_ref, point_ref, metric_name, ts, value, unit, quality
            FROM timeseries_sample
            WHERE entity_ref = ?
            ORDER BY ts DESC
            LIMIT 8
            """,
            (entity_ref,),
        ).fetchall()
    else:
        placeholders = ",".join(["?"] * len(metric_candidates))
        rows = conn.execute(
            f"""
            SELECT entity_ref, point_ref, metric_name, ts, value, unit, quality
            FROM timeseries_sample
            WHERE entity_ref = ? AND metric_name IN ({placeholders})
            ORDER BY ts DESC
            LIMIT 12
            """,
            [entity_ref, *metric_candidates],
        ).fetchall()
    return [
        {
            "entity_ref": row["entity_ref"],
            "point_ref": row["point_ref"],
            "metric_name": row["metric_name"],
            "ts": row["ts"],
            "value": row["value"],
            "unit": row["unit"],
            "quality": row["quality"],
        }
        for row in rows
    ]


def _best_metric_name(metric_definitions, mappings):
    if metric_definitions:
        return metric_definitions[0]["metric_name"]
    for mapping in mappings:
        if mapping.get("metric_name"):
            return mapping["metric_name"]
    return ""


def _property_from_metric(metric_name):
    normalized = _normalize(metric_name)
    if normalized.endswith("kw") and "power" in normalized:
        return "power"
    if normalized.endswith("kwh") and "energy" in normalized:
        return "energy"
    return metric_name


def _mapping_matches_metric(metric_name, row_metric, property_name):
    normalized = _normalize(metric_name)
    candidates = {_normalize(row_metric), _normalize(property_name), _normalize(_property_from_metric(row_metric))}
    return normalized in candidates


def _match_score(query, candidates):
    best = 0
    for candidate in candidates:
        text = _normalize(candidate)
        if not text:
            continue
        if text == query:
            best = max(best, 100)
        elif text.endswith(query) or query.endswith(text):
            best = max(best, 80)
        elif query in text or text in query:
            best = max(best, 60)
        else:
            overlap = len(set(re.findall(r"[a-z0-9]+", text)) & set(re.findall(r"[a-z0-9]+", query)))
            if overlap:
                best = max(best, 20 + overlap * 10)
    return best


def _build_notes(best_match, mappings, source_catalog, recent_events):
    notes = []
    notes.append(f"best match resolved to {best_match['object_name']}::{best_match['entity_ref']}")
    if mappings:
        notes.append(f"found {len(mappings)} mapping candidate(s)")
    if source_catalog:
        notes.append(f"found {len(source_catalog)} source catalog entry(ies)")
    if recent_events:
        notes.append(f"found {len(recent_events)} recent event(s) tied to the entity")
    return notes


def _load_json(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize(value):
    text = str(value or "").strip().lower()
    text = text.replace("号产线", "")
    text = text.replace("产线", "line")
    text = text.replace("主轧机", "machine")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _sanitize_id(value):
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    normalized = normalized.strip("_")
    if not normalized:
        raise ValueError("workspace_id is empty after sanitization")
    return normalized[:96]
