definition = {
    "name": "timeseries-query-sql",
    "description": "Query workspace sample telemetry or SQLite timeseries data for industrial analysis.",
}

import json
import os
import re
import sqlite3


ALLOWED_AGGREGATES = {"raw", "avg", "sum", "max", "min", "latest"}


def execute(params):
    workspace_id = _resolve_workspace_id(params)
    registry_db_path = _resolve_registry_db_path(params, workspace_id)
    entity_ref = str(params.get("entity_ref") or "").strip()
    if not entity_ref:
        raise ValueError("entity_ref is required")
    metric_name = str(params.get("metric_name") or "").strip()
    start_time = str(params.get("start_time") or "").strip()
    end_time = str(params.get("end_time") or "").strip()
    aggregate = str(params.get("aggregate") or "raw").strip().lower() or "raw"
    if aggregate not in ALLOWED_AGGREGATES:
        raise ValueError(f"aggregate must be one of {sorted(ALLOWED_AGGREGATES)}")
    include_events = True if params.get("include_events") is None else bool(params.get("include_events"))
    limit = max(1, min(int(params.get("limit") or 200), 1000))
    sql_query = str(params.get("sql_query") or "").strip()
    source_db_path = str(params.get("source_db_path") or "").strip()

    registry_conn = sqlite3.connect(registry_db_path)
    registry_conn.row_factory = sqlite3.Row
    try:
        if sql_query:
            if not source_db_path:
                raise ValueError("source_db_path is required when sql_query is provided")
            rows = _query_external_sqlite(source_db_path, sql_query, limit)
            return {
                "success": True,
                "workspace_id": workspace_id,
                "registry_db_path": registry_db_path,
                "source_db_path": source_db_path,
                "query_mode": "external_sql",
                "rows": rows,
                "row_count": len(rows),
            }

        timeseries_rows = _query_registry_timeseries(registry_conn, entity_ref, metric_name, start_time, end_time, aggregate, limit)
        event_rows = _query_registry_events(registry_conn, entity_ref, start_time, end_time, limit) if include_events else []
        summary = _summarize_timeseries(timeseries_rows, aggregate)
        return {
            "success": True,
            "workspace_id": workspace_id,
            "registry_db_path": registry_db_path,
            "query_mode": "workspace_registry",
            "entity_ref": entity_ref,
            "metric_name": metric_name,
            "start_time": start_time,
            "end_time": end_time,
            "aggregate": aggregate,
            "row_count": len(timeseries_rows),
            "timeseries": timeseries_rows,
            "events": event_rows,
            "summary": summary,
        }
    finally:
        registry_conn.close()


def _resolve_workspace_id(params):
    value = str(params.get("workspace_id") or os.getenv("AGENT_RUNTIME_WORKSPACE_ID") or "").strip()
    if not value:
        raise ValueError("workspace_id is required through params or sandbox context")
    return _sanitize_id(value)


def _resolve_registry_db_path(params, workspace_id):
    raw = str(params.get("db_path") or "").strip()
    if raw:
        return raw
    artifacts_dir = str(os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR") or "").strip()
    if not artifacts_dir:
        raise ValueError("AGENT_RUNTIME_ARTIFACTS_DIR is not available in sandbox execution")
    return os.path.join(artifacts_dir, "workspaces", workspace_id, "ontology_runtime.db")


def _query_external_sqlite(db_path, sql_query, limit):
    lowered = sql_query.strip().lower()
    if not lowered.startswith("select"):
        raise ValueError("sql_query must be a read-only SELECT statement")
    if ";" in lowered[:-1]:
        raise ValueError("sql_query must contain only one statement")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(sql_query)
        rows = cursor.fetchmany(limit)
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _query_registry_timeseries(conn, entity_ref, metric_name, start_time, end_time, aggregate, limit):
    clauses = ["entity_ref = ?"]
    values = [entity_ref]
    if metric_name:
        clauses.append("metric_name = ?")
        values.append(metric_name)
    if start_time:
        clauses.append("ts >= ?")
        values.append(start_time)
    if end_time:
        clauses.append("ts <= ?")
        values.append(end_time)
    where_sql = " AND ".join(clauses)
    if aggregate == "raw":
        sql = f"""
            SELECT entity_ref, point_ref, metric_name, ts, value, unit, quality
            FROM timeseries_sample
            WHERE {where_sql}
            ORDER BY ts ASC
            LIMIT ?
        """
        values.append(limit)
        rows = conn.execute(sql, values).fetchall()
        return [dict(row) for row in rows]
    if aggregate == "latest":
        sql = f"""
            SELECT entity_ref, point_ref, metric_name, ts, value, unit, quality
            FROM timeseries_sample
            WHERE {where_sql}
            ORDER BY ts DESC
            LIMIT 1
        """
        rows = conn.execute(sql, values).fetchall()
        return [dict(row) for row in rows]
    sql_agg = {
        "avg": "AVG(value)",
        "sum": "SUM(value)",
        "max": "MAX(value)",
        "min": "MIN(value)",
    }[aggregate]
    sql = f"""
        SELECT entity_ref, metric_name, {sql_agg} AS value, MIN(ts) AS start_time, MAX(ts) AS end_time, MAX(unit) AS unit, COUNT(*) AS sample_count
        FROM timeseries_sample
        WHERE {where_sql}
        GROUP BY entity_ref, metric_name
    """
    rows = conn.execute(sql, values).fetchall()
    return [dict(row) for row in rows]


def _query_registry_events(conn, entity_ref, start_time, end_time, limit):
    clauses = ["entity_ref = ?"]
    values = [entity_ref]
    if start_time:
        clauses.append("(started_at >= ? OR ended_at >= ? OR ended_at = '')")
        values.extend([start_time, start_time])
    if end_time:
        clauses.append("started_at <= ?")
        values.append(end_time)
    sql = f"""
        SELECT entity_ref, event_type, event_code, severity, status, started_at, ended_at, details_json
        FROM event_sample
        WHERE {" AND ".join(clauses)}
        ORDER BY started_at ASC
        LIMIT ?
    """
    values.append(limit)
    rows = conn.execute(sql, values).fetchall()
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


def _summarize_timeseries(rows, aggregate):
    if not rows:
        return {"sample_count": 0, "value_min": None, "value_max": None, "value_avg": None}
    if aggregate != "raw":
        first = rows[0]
        return {
            "sample_count": int(first.get("sample_count") or len(rows)),
            "value_min": first.get("value") if aggregate == "min" else None,
            "value_max": first.get("value") if aggregate == "max" else None,
            "value_avg": first.get("value") if aggregate == "avg" else None,
            "value_sum": first.get("value") if aggregate == "sum" else None,
        }
    numeric_values = [float(row["value"]) for row in rows if row.get("value") is not None]
    if not numeric_values:
        return {"sample_count": len(rows), "value_min": None, "value_max": None, "value_avg": None}
    return {
        "sample_count": len(rows),
        "value_min": min(numeric_values),
        "value_max": max(numeric_values),
        "value_avg": sum(numeric_values) / len(numeric_values),
    }


def _load_json(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize_id(value):
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    normalized = normalized.strip("_")
    if not normalized:
        raise ValueError("workspace_id is empty after sanitization")
    return normalized[:96]
