definition = {
    "name": "ontology-registry-upsert",
    "description": "Persist ontology fragments, mappings, instances, and sample industrial data into the workspace registry.",
}

import csv
import io
import json
import os
import re
import sqlite3
from datetime import datetime, timezone


DEFAULT_METRIC_DEFINITIONS = [
    {
        "metric_name": "power_kw",
        "display_name": "Instant Power",
        "unit": "kW",
        "aggregation_rule": "avg",
        "anomaly_rule": "spike_vs_baseline",
        "description": "Instantaneous electrical power.",
    },
    {
        "metric_name": "energy_kwh",
        "display_name": "Energy Consumption",
        "unit": "kWh",
        "aggregation_rule": "sum",
        "anomaly_rule": "sum_vs_baseline",
        "description": "Accumulated electrical energy.",
    },
    {
        "metric_name": "specific_energy_kwh_per_t",
        "display_name": "Specific Energy",
        "unit": "kWh/t",
        "aggregation_rule": "avg",
        "anomaly_rule": "higher_is_worse",
        "description": "Energy consumption normalized by production output.",
    },
]


OBJECT_SYNONYMS = {
    "production_line": "ProductionLine",
    "line": "ProductionLine",
    "machine": "Machine",
    "equipment": "Machine",
    "device": "Machine",
    "meter": "Meter",
    "sensor": "Sensor",
    "sensor_point": "Sensor",
    "measurement_point": "Sensor",
    "alarm": "Alarm",
    "alarm_event": "Alarm",
    "work_order": "WorkOrder",
    "maintenance_work_order": "WorkOrder",
}


def execute(params):
    workspace_id = _resolve_workspace_id(params)
    db_path = _resolve_db_path(params, workspace_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        counts = {
            "objects": 0,
            "properties": 0,
            "relations": 0,
            "mappings": 0,
            "behaviors": 0,
            "evidence": 0,
            "instances": 0,
            "instance_relations": 0,
            "metric_definitions": 0,
            "source_catalog": 0,
            "timeseries_rows": 0,
            "event_rows": 0,
        }
        inferred = {
            "objects": [],
            "relations": [],
            "instances": [],
            "mappings": [],
            "metric_definitions": [],
            "source_catalog": [],
        }
        notes = []

        ontology = params.get("ontology") if isinstance(params.get("ontology"), dict) else {}
        instances = _as_list(params.get("instances"))
        instance_relations = _as_list(params.get("instance_relations"))
        metric_definitions = _as_list(params.get("metric_definitions"))
        evidence = _as_list(params.get("evidence"))
        source_catalog = _as_list(params.get("source_catalog"))

        auto_infer_from_schema = True if params.get("auto_infer_from_schema") is None else bool(params.get("auto_infer_from_schema"))
        auto_infer_from_tags = True if params.get("auto_infer_from_tags") is None else bool(params.get("auto_infer_from_tags"))

        schema_sql = str(params.get("schema_sql") or "").strip()
        if schema_sql and auto_infer_from_schema:
            schema_inferred = _infer_from_schema_sql(schema_sql)
            _merge_inferred(inferred, schema_inferred)
            notes.append(f"inferred {len(schema_inferred['objects'])} object hint(s) and {len(schema_inferred['relations'])} relation hint(s) from schema_sql")

        tag_csv = str(params.get("tag_csv") or "").strip()
        if tag_csv and auto_infer_from_tags:
            tag_inferred = _infer_from_tag_csv(tag_csv)
            _merge_inferred(inferred, tag_inferred)
            notes.append(
                f"inferred {len(tag_inferred['instances'])} instance hint(s), {len(tag_inferred['mappings'])} mapping hint(s), and {len(tag_inferred['metric_definitions'])} metric hint(s) from tag_csv"
            )

        merged_ontology = _merge_ontology(ontology, inferred)
        merged_instances = _dedupe_instances(instances + inferred["instances"])
        merged_instance_relations = _dedupe_relations(instance_relations + inferred["relations"], level="instance")
        merged_metric_definitions = _dedupe_metric_definitions(DEFAULT_METRIC_DEFINITIONS + metric_definitions + inferred["metric_definitions"])
        merged_source_catalog = _dedupe_sources(source_catalog + inferred["source_catalog"])

        version_id = _new_id("ver")
        _insert_version(conn, version_id, workspace_id, params)
        counts["objects"] += _upsert_objects(conn, version_id, merged_ontology.get("objects") or [])
        counts["properties"] += _upsert_properties(conn, version_id, merged_ontology.get("objects") or [])
        counts["relations"] += _upsert_relations(conn, version_id, merged_ontology.get("relations") or [])
        counts["mappings"] += _upsert_mappings(conn, version_id, merged_ontology.get("mappings") or [])
        counts["behaviors"] += _upsert_behaviors(conn, version_id, merged_ontology.get("objects") or [])
        counts["evidence"] += _upsert_evidence(conn, version_id, evidence)
        counts["instances"] += _upsert_instances(conn, version_id, merged_instances)
        counts["instance_relations"] += _upsert_instance_relations(conn, version_id, merged_instance_relations)
        counts["metric_definitions"] += _upsert_metric_definitions(conn, version_id, merged_metric_definitions)
        counts["source_catalog"] += _upsert_sources(conn, version_id, merged_source_catalog)

        timeseries_rows = _collect_rows(params, "timeseries_rows", "timeseries_csv")
        if timeseries_rows:
            counts["timeseries_rows"] += _upsert_timeseries_rows(conn, version_id, timeseries_rows)
        event_rows = _collect_rows(params, "event_rows", "events_csv")
        if event_rows:
            counts["event_rows"] += _upsert_event_rows(conn, version_id, event_rows)

        conn.commit()
        return {
            "success": True,
            "workspace_id": workspace_id,
            "db_path": db_path,
            "version_id": version_id,
            "counts": counts,
            "object_names": [item.get("name") for item in merged_ontology.get("objects") or [] if isinstance(item, dict)],
            "instance_names": [item.get("entity_ref") for item in merged_instances if isinstance(item, dict)],
            "metric_names": [item.get("metric_name") for item in merged_metric_definitions if isinstance(item, dict)],
            "notes": notes,
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


def _sanitize_id(value):
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    normalized = normalized.strip("_")
    if not normalized:
        raise ValueError("workspace_id is empty after sanitization")
    return normalized[:96]


def _ensure_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ontology_version (
            version_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ontology_object (
            object_name TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ontology_property (
            object_name TEXT NOT NULL,
            property_name TEXT NOT NULL,
            version_id TEXT NOT NULL,
            data_type TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            enum_values_json TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (object_name, property_name)
        );
        CREATE TABLE IF NOT EXISTS ontology_relation (
            relation_key TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            predicate TEXT NOT NULL,
            target_name TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'class',
            evidence_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ontology_mapping (
            mapping_key TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            object_name TEXT NOT NULL,
            property_name TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_path TEXT NOT NULL,
            protocol TEXT NOT NULL DEFAULT '',
            table_name TEXT NOT NULL DEFAULT '',
            metric_name TEXT NOT NULL DEFAULT '',
            extra_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ontology_behavior (
            behavior_key TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            object_name TEXT NOT NULL,
            behavior_name TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            command_path TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ontology_evidence (
            evidence_id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            object_name TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            excerpt TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entity_instance (
            entity_ref TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            object_name TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            parent_ref TEXT NOT NULL DEFAULT '',
            attributes_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS instance_relation (
            relation_key TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            predicate TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            evidence_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metric_definition (
            metric_name TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            aggregation_rule TEXT NOT NULL DEFAULT '',
            anomaly_rule TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            expression TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS source_catalog (
            source_key TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            table_name TEXT NOT NULL DEFAULT '',
            metric_name TEXT NOT NULL DEFAULT '',
            entity_ref TEXT NOT NULL DEFAULT '',
            extra_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS timeseries_sample (
            row_key TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            entity_ref TEXT NOT NULL DEFAULT '',
            point_ref TEXT NOT NULL DEFAULT '',
            metric_name TEXT NOT NULL DEFAULT '',
            ts TEXT NOT NULL,
            value REAL,
            unit TEXT NOT NULL DEFAULT '',
            quality TEXT NOT NULL DEFAULT '',
            source_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_sample (
            row_key TEXT PRIMARY KEY,
            version_id TEXT NOT NULL,
            entity_ref TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL DEFAULT '',
            event_code TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL DEFAULT '',
            details_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_timeseries_entity_metric_ts ON timeseries_sample(entity_ref, metric_name, ts);
        CREATE INDEX IF NOT EXISTS idx_event_entity_time ON event_sample(entity_ref, started_at);
        """
    )


def _insert_version(conn, version_id, workspace_id, params):
    conn.execute(
        "INSERT INTO ontology_version (version_id, workspace_id, created_at, payload_json) VALUES (?, ?, ?, ?)",
        (
            version_id,
            workspace_id,
            _now_iso(),
            json.dumps(params, ensure_ascii=False),
        ),
    )


def _upsert_objects(conn, version_id, objects):
    count = 0
    for item in objects:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        aliases = [str(alias).strip() for alias in _as_list(item.get("aliases")) if str(alias).strip()]
        description = str(item.get("description") or "").strip()
        conn.execute(
            """
            INSERT INTO ontology_object (object_name, version_id, aliases_json, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(object_name) DO UPDATE SET
                version_id=excluded.version_id,
                aliases_json=excluded.aliases_json,
                description=excluded.description
            """,
            (name, version_id, json.dumps(aliases, ensure_ascii=False), description),
        )
        count += 1
    return count


def _upsert_properties(conn, version_id, objects):
    count = 0
    for item in objects:
        if not isinstance(item, dict):
            continue
        object_name = str(item.get("name") or "").strip()
        if not object_name:
            continue
        for prop in _as_list(item.get("properties")):
            if not isinstance(prop, dict):
                continue
            property_name = str(prop.get("name") or "").strip()
            if not property_name:
                continue
            data_type = str(prop.get("type") or "string").strip().lower() or "string"
            unit = str(prop.get("unit") or "").strip()
            enum_values = _as_list(prop.get("enum_values"))
            description = str(prop.get("description") or "").strip()
            conn.execute(
                """
                INSERT INTO ontology_property (object_name, property_name, version_id, data_type, unit, enum_values_json, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_name, property_name) DO UPDATE SET
                    version_id=excluded.version_id,
                    data_type=excluded.data_type,
                    unit=excluded.unit,
                    enum_values_json=excluded.enum_values_json,
                    description=excluded.description
                """,
                (object_name, property_name, version_id, data_type, unit, json.dumps(enum_values, ensure_ascii=False), description),
            )
            count += 1
    return count


def _upsert_relations(conn, version_id, relations):
    count = 0
    for item in _dedupe_relations(relations, level="class"):
        source_name = str(item.get("source") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        target_name = str(item.get("target") or "").strip()
        if not source_name or not predicate or not target_name:
            continue
        relation_key = f"class::{source_name}::{predicate}::{target_name}"
        conn.execute(
            """
            INSERT INTO ontology_relation (relation_key, version_id, source_name, predicate, target_name, level, evidence_json)
            VALUES (?, ?, ?, ?, ?, 'class', ?)
            ON CONFLICT(relation_key) DO UPDATE SET
                version_id=excluded.version_id,
                evidence_json=excluded.evidence_json
            """,
            (relation_key, version_id, source_name, predicate, target_name, json.dumps(item.get("evidence") or {}, ensure_ascii=False)),
        )
        count += 1
    return count


def _upsert_mappings(conn, version_id, mappings):
    count = 0
    for item in mappings:
        if not isinstance(item, dict):
            continue
        object_name = str(item.get("object") or "").strip()
        property_name = str(item.get("property") or "").strip()
        source_kind = str(item.get("source_kind") or "").strip()
        source_path = str(item.get("source_path") or "").strip()
        if not object_name or not property_name or not source_kind or not source_path:
            continue
        mapping_key = f"{object_name}::{property_name}::{source_kind}::{source_path}"
        conn.execute(
            """
            INSERT INTO ontology_mapping (mapping_key, version_id, object_name, property_name, source_kind, source_path, protocol, table_name, metric_name, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mapping_key) DO UPDATE SET
                version_id=excluded.version_id,
                protocol=excluded.protocol,
                table_name=excluded.table_name,
                metric_name=excluded.metric_name,
                extra_json=excluded.extra_json
            """,
            (
                mapping_key,
                version_id,
                object_name,
                property_name,
                source_kind,
                source_path,
                str(item.get("protocol") or "").strip(),
                str(item.get("table_name") or "").strip(),
                str(item.get("metric_name") or "").strip(),
                json.dumps(item, ensure_ascii=False),
            ),
        )
        count += 1
    return count


def _upsert_behaviors(conn, version_id, objects):
    count = 0
    for item in objects:
        if not isinstance(item, dict):
            continue
        object_name = str(item.get("name") or "").strip()
        if not object_name:
            continue
        for behavior in _as_list(item.get("behaviors")):
            if not isinstance(behavior, dict):
                continue
            behavior_name = str(behavior.get("name") or "").strip()
            if not behavior_name:
                continue
            behavior_key = f"{object_name}::{behavior_name}"
            conn.execute(
                """
                INSERT INTO ontology_behavior (behavior_key, version_id, object_name, behavior_name, parameters_json, command_path)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(behavior_key) DO UPDATE SET
                    version_id=excluded.version_id,
                    parameters_json=excluded.parameters_json,
                    command_path=excluded.command_path
                """,
                (
                    behavior_key,
                    version_id,
                    object_name,
                    behavior_name,
                    json.dumps(behavior.get("parameters") or [], ensure_ascii=False),
                    str(behavior.get("command_path") or "").strip(),
                ),
            )
            count += 1
    return count


def _upsert_evidence(conn, version_id, evidence_items):
    count = 0
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or _new_id("evi")).strip()
        conn.execute(
            """
            INSERT INTO ontology_evidence (evidence_id, version_id, object_name, source_type, source_ref, excerpt, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                version_id=excluded.version_id,
                object_name=excluded.object_name,
                source_type=excluded.source_type,
                source_ref=excluded.source_ref,
                excerpt=excluded.excerpt,
                metadata_json=excluded.metadata_json
            """,
            (
                evidence_id,
                version_id,
                str(item.get("object_name") or "").strip(),
                str(item.get("source_type") or "").strip(),
                str(item.get("source_ref") or "").strip(),
                str(item.get("excerpt") or "").strip(),
                json.dumps(item.get("metadata") or {}, ensure_ascii=False),
            ),
        )
        count += 1
    return count


def _upsert_instances(conn, version_id, instances):
    count = 0
    for item in instances:
        if not isinstance(item, dict):
            continue
        entity_ref = str(item.get("entity_ref") or "").strip()
        object_name = str(item.get("object_name") or "").strip()
        if not entity_ref or not object_name:
            continue
        conn.execute(
            """
            INSERT INTO entity_instance (entity_ref, version_id, object_name, display_name, parent_ref, attributes_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_ref) DO UPDATE SET
                version_id=excluded.version_id,
                object_name=excluded.object_name,
                display_name=excluded.display_name,
                parent_ref=excluded.parent_ref,
                attributes_json=excluded.attributes_json
            """,
            (
                entity_ref,
                version_id,
                object_name,
                str(item.get("display_name") or "").strip(),
                str(item.get("parent_ref") or "").strip(),
                json.dumps(item.get("attributes") or {}, ensure_ascii=False),
            ),
        )
        count += 1
    return count


def _upsert_instance_relations(conn, version_id, relations):
    count = 0
    for item in relations:
        if not isinstance(item, dict):
            continue
        source_ref = str(item.get("source") or item.get("source_ref") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        target_ref = str(item.get("target") or item.get("target_ref") or "").strip()
        if not source_ref or not predicate or not target_ref:
            continue
        relation_key = f"instance::{source_ref}::{predicate}::{target_ref}"
        conn.execute(
            """
            INSERT INTO instance_relation (relation_key, version_id, source_ref, predicate, target_ref, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(relation_key) DO UPDATE SET
                version_id=excluded.version_id,
                evidence_json=excluded.evidence_json
            """,
            (relation_key, version_id, source_ref, predicate, target_ref, json.dumps(item.get("evidence") or {}, ensure_ascii=False)),
        )
        count += 1
    return count


def _upsert_metric_definitions(conn, version_id, metric_definitions):
    count = 0
    for item in metric_definitions:
        if not isinstance(item, dict):
            continue
        metric_name = str(item.get("metric_name") or "").strip()
        if not metric_name:
            continue
        conn.execute(
            """
            INSERT INTO metric_definition (metric_name, version_id, display_name, unit, aggregation_rule, anomaly_rule, description, expression)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_name) DO UPDATE SET
                version_id=excluded.version_id,
                display_name=excluded.display_name,
                unit=excluded.unit,
                aggregation_rule=excluded.aggregation_rule,
                anomaly_rule=excluded.anomaly_rule,
                description=excluded.description,
                expression=excluded.expression
            """,
            (
                metric_name,
                version_id,
                str(item.get("display_name") or "").strip(),
                str(item.get("unit") or "").strip(),
                str(item.get("aggregation_rule") or "").strip(),
                str(item.get("anomaly_rule") or "").strip(),
                str(item.get("description") or "").strip(),
                str(item.get("expression") or "").strip(),
            ),
        )
        count += 1
    return count


def _upsert_sources(conn, version_id, sources):
    count = 0
    for item in sources:
        if not isinstance(item, dict):
            continue
        source_name = str(item.get("source_name") or "").strip()
        source_type = str(item.get("source_type") or "").strip()
        if not source_name or not source_type:
            continue
        source_key = f"{source_type}::{source_name}::{str(item.get('table_name') or '').strip()}::{str(item.get('metric_name') or '').strip()}"
        conn.execute(
            """
            INSERT INTO source_catalog (source_key, version_id, source_name, source_type, location, table_name, metric_name, entity_ref, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                version_id=excluded.version_id,
                location=excluded.location,
                table_name=excluded.table_name,
                metric_name=excluded.metric_name,
                entity_ref=excluded.entity_ref,
                extra_json=excluded.extra_json
            """,
            (
                source_key,
                version_id,
                source_name,
                source_type,
                str(item.get("location") or "").strip(),
                str(item.get("table_name") or "").strip(),
                str(item.get("metric_name") or "").strip(),
                str(item.get("entity_ref") or "").strip(),
                json.dumps(item, ensure_ascii=False),
            ),
        )
        count += 1
    return count


def _upsert_timeseries_rows(conn, version_id, rows):
    count = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        ts = _normalize_timestamp(item.get("ts") or item.get("timestamp") or item.get("collected_at"))
        if not ts:
            continue
        entity_ref = str(item.get("entity_ref") or item.get("machine_ref") or item.get("line_ref") or "").strip()
        point_ref = str(item.get("point_ref") or item.get("sensor_ref") or item.get("tag_name") or "").strip()
        metric_name = str(item.get("metric_name") or item.get("metric") or item.get("point_code") or "").strip()
        value = _to_float(item.get("value") or item.get("reading_value"))
        row_key = f"{entity_ref}::{point_ref}::{metric_name}::{ts}"
        conn.execute(
            """
            INSERT INTO timeseries_sample (row_key, version_id, entity_ref, point_ref, metric_name, ts, value, unit, quality, source_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_key) DO UPDATE SET
                version_id=excluded.version_id,
                value=excluded.value,
                unit=excluded.unit,
                quality=excluded.quality,
                source_json=excluded.source_json
            """,
            (
                row_key,
                version_id,
                entity_ref,
                point_ref,
                metric_name,
                ts,
                value,
                str(item.get("unit") or "").strip(),
                str(item.get("quality") or item.get("quality_code") or "").strip(),
                json.dumps(item, ensure_ascii=False),
            ),
        )
        count += 1
    return count


def _upsert_event_rows(conn, version_id, rows):
    count = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        started_at = _normalize_timestamp(item.get("started_at") or item.get("triggered_at") or item.get("timestamp"))
        if not started_at:
            continue
        entity_ref = str(item.get("entity_ref") or item.get("machine_ref") or item.get("line_ref") or "").strip()
        event_type = str(item.get("event_type") or "event").strip()
        event_code = str(item.get("event_code") or item.get("alarm_code") or "").strip()
        row_key = f"{entity_ref}::{event_type}::{event_code}::{started_at}"
        conn.execute(
            """
            INSERT INTO event_sample (row_key, version_id, entity_ref, event_type, event_code, severity, status, started_at, ended_at, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_key) DO UPDATE SET
                version_id=excluded.version_id,
                severity=excluded.severity,
                status=excluded.status,
                ended_at=excluded.ended_at,
                details_json=excluded.details_json
            """,
            (
                row_key,
                version_id,
                entity_ref,
                event_type,
                event_code,
                str(item.get("severity") or "").strip(),
                str(item.get("status") or "").strip(),
                started_at,
                _normalize_timestamp(item.get("ended_at") or item.get("cleared_at") or ""),
                json.dumps(item, ensure_ascii=False),
            ),
        )
        count += 1
    return count


def _collect_rows(params, rows_key, csv_key):
    rows = _as_list(params.get(rows_key))
    if rows:
        return [item for item in rows if isinstance(item, dict)]
    csv_text = str(params.get(csv_key) or "").strip()
    if not csv_text:
        return []
    reader = csv.DictReader(io.StringIO(csv_text))
    return [dict(row) for row in reader]


def _infer_from_schema_sql(schema_sql):
    result = {
        "objects": [],
        "relations": [],
        "instances": [],
        "mappings": [],
        "metric_definitions": [],
        "source_catalog": [],
    }
    table_blocks = re.findall(r"CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\);", schema_sql, flags=re.IGNORECASE | re.DOTALL)
    for table_name, body in table_blocks:
        canonical = OBJECT_SYNONYMS.get(table_name.lower(), _to_pascal(table_name))
        properties = []
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.upper().startswith("CONSTRAINT ") or line.upper().startswith("PRIMARY KEY"):
                continue
            if line.upper().startswith("FOREIGN KEY"):
                continue
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z0-9()_, ]+)", line)
            if not match:
                continue
            column_name = match.group(1)
            sql_type = match.group(2).strip().split()[0]
            properties.append(
                {
                    "name": column_name,
                    "type": _sql_type_to_property_type(sql_type),
                    "unit": _unit_from_column_name(column_name),
                }
            )
            if any(token in column_name.lower() for token in ("power", "energy", "temp", "speed", "pressure", "vibration")):
                result["mappings"].append(
                    {
                        "object": canonical,
                        "property": column_name,
                        "source_kind": "sql",
                        "source_path": f"{table_name}.{column_name}",
                        "table_name": table_name,
                        "metric_name": _metric_name_from_field(column_name, _unit_from_column_name(column_name)),
                    }
                )
        result["objects"].append(
            {
                "name": canonical,
                "aliases": [table_name],
                "properties": properties,
            }
        )
        result["source_catalog"].append(
            {
                "source_name": table_name,
                "source_type": "sql_table",
                "table_name": table_name,
                "location": "workspace_sql_schema",
            }
        )
    fk_blocks = re.findall(
        r"CONSTRAINT\s+([A-Za-z_][A-Za-z0-9_]*)\s+FOREIGN\s+KEY\s*\((.*?)\)\s+REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)",
        schema_sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    table_lookup = {alias.lower(): item["name"] for item in result["objects"] for alias in ([item["name"]] + _as_list(item.get("aliases")))}
    for _, _, target_table, _ in fk_blocks:
        source_match = re.search(
            r"CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((?:(?!CREATE\s+TABLE).)*?REFERENCES\s+%s\s*\(" % re.escape(target_table),
            schema_sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not source_match:
            continue
        source_table = source_match.group(1)
        source_name = table_lookup.get(source_table.lower(), _to_pascal(source_table))
        target_name = table_lookup.get(target_table.lower(), _to_pascal(target_table))
        predicate = _relation_predicate_for_tables(source_table, target_table)
        result["relations"].append({"source": target_name, "predicate": predicate, "target": source_name})
    return result


def _infer_from_tag_csv(tag_csv):
    result = {
        "objects": [
            {"name": "ProductionLine", "aliases": ["Line"], "properties": [{"name": "lineCode", "type": "string"}]},
            {"name": "Machine", "aliases": ["Device", "Equipment"], "properties": [{"name": "machineCode", "type": "string"}]},
            {"name": "Sensor", "aliases": ["Tag", "MeasurementPoint"], "properties": [{"name": "tagName", "type": "string"}, {"name": "address", "type": "string"}]},
            {"name": "Meter", "aliases": ["EnergyMeter"], "properties": [{"name": "metric_name", "type": "string"}, {"name": "unit", "type": "string"}]},
        ],
        "relations": [],
        "instances": [],
        "mappings": [],
        "metric_definitions": [],
        "source_catalog": [],
    }
    reader = csv.DictReader(io.StringIO(tag_csv))
    line_refs = {}
    machine_refs = {}
    for row in reader:
        if not isinstance(row, dict):
            continue
        tag_name = str(row.get("tag_name") or "").strip()
        protocol = str(row.get("protocol") or "").strip().lower()
        address = str(row.get("address") or "").strip()
        unit = str(row.get("unit") or "").strip()
        description = str(row.get("description") or "").strip()
        parsed = _parse_tag_identity(tag_name or address)
        line_code = parsed.get("line_code")
        machine_code = parsed.get("machine_code")
        metric_name = parsed.get("metric_name")
        if line_code:
            line_ref = f"line::{line_code.lower()}"
            if line_ref not in line_refs:
                result["instances"].append(
                    {"entity_ref": line_ref, "object_name": "ProductionLine", "display_name": line_code, "attributes": {"line_code": line_code}}
                )
                line_refs[line_ref] = True
        else:
            line_ref = ""
        if machine_code:
            machine_ref = f"machine::{machine_code.lower()}"
            if machine_ref not in machine_refs:
                result["instances"].append(
                    {
                        "entity_ref": machine_ref,
                        "object_name": "Machine",
                        "display_name": machine_code,
                        "parent_ref": line_ref,
                        "attributes": {"machine_code": machine_code, "line_code": line_code},
                    }
                )
                machine_refs[machine_ref] = True
            if line_ref:
                result["relations"].append({"source_ref": line_ref, "predicate": "contains", "target_ref": machine_ref})
        else:
            machine_ref = ""
        point_ref = f"point::{_slug(tag_name or address)}"
        result["instances"].append(
            {
                "entity_ref": point_ref,
                "object_name": "Sensor" if protocol in {"plc", "opcua", "mqtt"} else "Meter",
                "display_name": tag_name or address,
                "parent_ref": machine_ref or line_ref,
                "attributes": {
                    "tag_name": tag_name,
                    "protocol": protocol,
                    "address": address,
                    "unit": unit,
                    "description": description,
                },
            }
        )
        if machine_ref:
            result["relations"].append({"source_ref": machine_ref, "predicate": "measuredBy", "target_ref": point_ref})
        property_name = metric_name or "value"
        object_name = "Machine" if machine_ref else "ProductionLine"
        result["mappings"].append(
            {
                "object": object_name,
                "property": property_name,
                "source_kind": protocol or "tag",
                "source_path": address or tag_name,
                "protocol": protocol,
                "metric_name": _metric_name_from_field(property_name, unit),
            }
        )
        metric_key = _metric_name_from_field(property_name, unit)
        result["metric_definitions"].append(
            {
                "metric_name": metric_key,
                "display_name": property_name,
                "unit": unit,
                "aggregation_rule": "avg" if unit.lower() in {"kw", "rpm", "celsius", "mm/s"} else "sum",
                "anomaly_rule": "spike_vs_baseline",
                "description": description,
            }
        )
        result["source_catalog"].append(
            {
                "source_name": tag_name or address,
                "source_type": protocol or "tag",
                "location": address,
                "metric_name": metric_key,
                "entity_ref": machine_ref or line_ref,
                "extra": row,
            }
        )
    return result


def _merge_inferred(target, addition):
    for key in target:
        target[key].extend(addition.get(key) or [])


def _merge_ontology(explicit_ontology, inferred):
    result = {
        "objects": _merge_object_lists(_as_list(explicit_ontology.get("objects")), inferred["objects"]),
        "relations": _dedupe_relations(_as_list(explicit_ontology.get("relations")) + inferred["relations"], level="class"),
        "mappings": _dedupe_mappings(_as_list(explicit_ontology.get("mappings")) + inferred["mappings"]),
    }
    return result


def _merge_object_lists(primary, secondary):
    merged = {}
    for item in primary + secondary:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        existing = merged.get(name)
        if existing is None:
            merged[name] = {
                "name": name,
                "aliases": list(_as_list(item.get("aliases"))),
                "properties": list(_as_list(item.get("properties"))),
                "behaviors": list(_as_list(item.get("behaviors"))),
                "description": str(item.get("description") or "").strip(),
            }
            continue
        existing["aliases"] = _dedupe_strings(existing["aliases"] + _as_list(item.get("aliases")))
        existing["properties"] = _merge_properties(existing["properties"], _as_list(item.get("properties")))
        existing["behaviors"] = _merge_behaviors(existing["behaviors"], _as_list(item.get("behaviors")))
        if not existing["description"]:
            existing["description"] = str(item.get("description") or "").strip()
    return list(merged.values())


def _merge_properties(primary, secondary):
    merged = {}
    for item in primary + secondary:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        merged[name] = {
            "name": name,
            "type": str(item.get("type") or merged.get(name, {}).get("type") or "string").strip(),
            "unit": str(item.get("unit") or merged.get(name, {}).get("unit") or "").strip(),
            "enum_values": _as_list(item.get("enum_values")) or merged.get(name, {}).get("enum_values") or [],
            "description": str(item.get("description") or merged.get(name, {}).get("description") or "").strip(),
        }
    return list(merged.values())


def _merge_behaviors(primary, secondary):
    merged = {}
    for item in primary + secondary:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        merged[name] = item
    return list(merged.values())


def _dedupe_relations(relations, level):
    seen = {}
    for item in relations:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or item.get("source_ref") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        target = str(item.get("target") or item.get("target_ref") or "").strip()
        if not source or not predicate or not target:
            continue
        key = f"{level}::{source}::{predicate}::{target}"
        seen[key] = item
    return list(seen.values())


def _dedupe_mappings(mappings):
    seen = {}
    for item in mappings:
        if not isinstance(item, dict):
            continue
        key = "::".join(
            [
                str(item.get("object") or "").strip(),
                str(item.get("property") or "").strip(),
                str(item.get("source_kind") or "").strip(),
                str(item.get("source_path") or "").strip(),
            ]
        )
        if "::::" in key or not key.replace(":", "").strip():
            continue
        seen[key] = item
    return list(seen.values())


def _dedupe_instances(instances):
    seen = {}
    for item in instances:
        if not isinstance(item, dict):
            continue
        entity_ref = str(item.get("entity_ref") or "").strip()
        if not entity_ref:
            continue
        seen[entity_ref] = item
    return list(seen.values())


def _dedupe_metric_definitions(items):
    seen = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        metric_name = str(item.get("metric_name") or "").strip()
        if not metric_name:
            continue
        seen[metric_name] = item
    return list(seen.values())


def _dedupe_sources(items):
    seen = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = "::".join(
            [
                str(item.get("source_type") or "").strip(),
                str(item.get("source_name") or "").strip(),
                str(item.get("location") or "").strip(),
                str(item.get("table_name") or "").strip(),
                str(item.get("metric_name") or "").strip(),
            ]
        )
        if not key.replace(":", "").strip():
            continue
        seen[key] = item
    return list(seen.values())


def _sql_type_to_property_type(sql_type):
    normalized = sql_type.lower()
    if any(token in normalized for token in ("int", "decimal", "numeric", "real", "double", "float")):
        return "number"
    if "bool" in normalized:
        return "boolean"
    if "date" in normalized or "time" in normalized:
        return "datetime"
    return "string"


def _unit_from_column_name(column_name):
    normalized = column_name.lower()
    if normalized.endswith("_kw") or "power" in normalized:
        return "kW"
    if normalized.endswith("_kwh") or "energy" in normalized:
        return "kWh"
    if "temp" in normalized:
        return "celsius"
    if "speed" in normalized:
        return "rpm"
    if "vibration" in normalized:
        return "mm/s"
    return ""


def _metric_name_from_field(field_name, unit):
    normalized = _slug(str(field_name or "value"))
    unit_norm = str(unit or "").strip().lower()
    if "power" in normalized and unit_norm in {"kw", "kilowatt"}:
        return "power_kw"
    if "energy" in normalized and unit_norm in {"kwh", "kilowatt-hour"}:
        return "energy_kwh"
    return normalized


def _parse_tag_identity(value):
    text = str(value or "").strip()
    if not text:
        return {}
    normalized = text.replace("\\", "/")
    line_match = re.search(r"(line[_-]?\d+)", normalized, flags=re.IGNORECASE)
    machine_match = re.search(r"(machine[_-]?[A-Za-z0-9]+)", normalized, flags=re.IGNORECASE)
    if not machine_match:
        machine_match = re.search(r"/(machine[A-Za-z0-9]+)/", normalized, flags=re.IGNORECASE)
    metric_candidate = normalized.split(".")[-1].split("/")[-1].split(";")[-1]
    return {
        "line_code": line_match.group(1).replace("_", "").replace("-", "") if line_match else "",
        "machine_code": machine_match.group(1).replace("_", "").replace("-", "") if machine_match else "",
        "metric_name": _slug(metric_candidate),
    }


def _relation_predicate_for_tables(source_table, target_table):
    pair = f"{source_table.lower()}->{target_table.lower()}"
    if pair == "machine->production_line":
        return "contains"
    if pair == "sensor_point->machine":
        return "measuredBy"
    if pair == "alarm_event->machine":
        return "generatedBy"
    if pair == "work_order->machine":
        return "uses"
    return "relatedTo"


def _to_pascal(value):
    parts = re.split(r"[^A-Za-z0-9]+", str(value or "").strip())
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-") or "value"


def _dedupe_strings(values):
    result = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _normalize_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if "T" in text or text.endswith("Z"):
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _new_id(prefix):
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _as_list(value):
    return value if isinstance(value, list) else []
