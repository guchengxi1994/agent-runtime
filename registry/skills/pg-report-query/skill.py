definition = {
    "name": "pg-report-query",
    "description": "规划或执行 PostgreSQL 报表查询，用于生成结构化报告指标。",
}

import os
from datetime import date, datetime


DEFAULT_TABLE = "enterprise_risk_events"

DIMENSION_MAP = {
    "month": "to_char(date_trunc('month', accepted_date), 'YYYY-MM') AS month",
    "year": "EXTRACT(YEAR FROM accepted_date)::int AS year",
    "risk_type": "COALESCE(NULLIF(case_type_sub2, ''), NULLIF(case_type_sub1, ''), case_category) AS risk_type",
    "event_source": "event_source",
    "region": "region",
    "industry": "industry",
    "company_name": "company_name",
    "company_size": "company_size",
    "ownership_nature": "ownership_nature",
    "org_form": "org_form",
    "case_category": "case_category",
    "case_type_sub1": "case_type_sub1",
    "case_type_sub2": "case_type_sub2",
    "litigation_role_major": "litigation_role_major",
    "litigation_role": "litigation_role",
    "tech_enterprise": "tech_enterprise",
    "source_file": "source_file",
}

METRIC_MAP = {
    "case_count": "COUNT(*) AS case_count",
    "record_count": "COUNT(*) AS record_count",
    "company_count": "COUNT(DISTINCT company_name) AS company_count",
    "judicial_case_count": "COUNT(*) FILTER (WHERE event_source = 'judicial_case') AS judicial_case_count",
    "administrative_penalty_count": "COUNT(*) FILTER (WHERE event_source = 'administrative_penalty') AS administrative_penalty_count",
}


def execute(params):
    report_question = str(params.get("report_question") or "").strip()
    metrics = _string_list(params.get("metrics"))
    dimensions = _string_list(params.get("dimensions"))
    filters = params.get("filters") if isinstance(params.get("filters"), dict) else {}
    table_hints = _string_list(params.get("table_hints"))
    raw_sql = str(params.get("sql") or "").strip()
    execute_flag = bool(params.get("execute", False))
    limit = max(1, min(int(params.get("limit") or 200), 1000))

    if not report_question and not raw_sql:
        raise ValueError("report_question or sql is required")

    planned_sql = raw_sql or _build_sql(report_question, metrics, dimensions, filters, table_hints, limit)
    assumptions = _build_assumptions(metrics, dimensions, filters, table_hints)
    runtime_config = {
        "env": ["PG_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"],
        "default_table": DEFAULT_TABLE,
        "bootstrap_skill": "pg-risk-dataset-sync",
    }

    if execute_flag:
        read_only_error = _validate_read_only_sql(planned_sql)
        if read_only_error:
            return {
                "success": False,
                "mode": "invalid_sql",
                "error_type": "read_only_sql_required",
                "error": read_only_error,
                "sql": planned_sql,
            }
        try:
            dataset_status = _dataset_status()
        except Exception as exc:
            return {
                "success": False,
                "mode": "postgres_unavailable",
                "error_type": "postgres_unavailable",
                "error": f"Failed to connect to PostgreSQL: {exc}",
                "sql": planned_sql,
                "assumptions": assumptions,
                "required_runtime_config": runtime_config,
            }
        if not dataset_status["table_exists"] or dataset_status["row_count"] <= 0:
            return {
                "success": False,
                "mode": "dataset_unavailable",
                "error_type": "missing_dataset",
                "error": "enterprise_risk_events is missing or empty. Call pg-risk-dataset-sync before executing report queries.",
                "sql": planned_sql,
                "dataset_status": dataset_status,
                "required_runtime_config": runtime_config,
            }
        try:
            rows, columns = _execute_sql(planned_sql)
        except Exception as exc:
            return {
                "success": False,
                "mode": "query_failed",
                "error_type": "query_failed",
                "error": f"PostgreSQL query failed: {exc}",
                "sql": planned_sql,
                "dataset_status": dataset_status,
            }
        return {
            "success": True,
            "mode": "executed",
            "report_question": report_question,
            "sql": planned_sql,
            "metrics": metrics,
            "dimensions": dimensions,
            "filters": filters,
            "table_hints": table_hints,
            "assumptions": assumptions,
            "row_count": len(rows),
            "columns": columns,
            "rows": rows,
            "dataset_status": dataset_status,
        }

    return {
        "success": True,
        "mode": "plan_only",
        "report_question": report_question,
        "sql": planned_sql,
        "metrics": metrics,
        "dimensions": dimensions,
        "filters": filters,
        "table_hints": table_hints,
        "assumptions": assumptions,
        "required_runtime_config": runtime_config,
        "next_inputs": _next_inputs(metrics, dimensions, table_hints),
    }


def _sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _build_sql(report_question, metrics, dimensions, filters, table_hints, limit):
    select_parts = []
    if dimensions:
        select_parts.extend(_resolve_dimensions(dimensions))
    if metrics:
        select_parts.extend(_resolve_metrics(metrics))
    if not select_parts:
        select_parts.append("*")
    select_prefix = "SELECT DISTINCT" if dimensions and not metrics else "SELECT"
    source = table_hints[0] if table_hints else DEFAULT_TABLE
    where_parts = _build_where_parts(filters)
    where_sql = f"\nWHERE {' AND '.join(where_parts)}" if where_parts else ""
    group_sql = f"\nGROUP BY {', '.join(str(index + 1) for index in range(len(dimensions)))}" if dimensions and metrics else ""
    order_sql = f"\nORDER BY {', '.join(str(index + 1) for index in range(len(dimensions)))}" if dimensions else ""
    comment = f"-- report_question: {report_question}" if report_question else "-- generated report query plan"
    return (
        f"{comment}\n"
        f"{select_prefix} {', '.join(select_parts)}\n"
        f"FROM {source}"
        f"{where_sql}"
        f"{group_sql}"
        f"{order_sql}\n"
        f"LIMIT {limit};"
    )


def _build_assumptions(metrics, dimensions, filters, table_hints):
    assumptions = []
    if not table_hints:
        assumptions.append(f"No table_hints were provided; the SQL uses the default source table {DEFAULT_TABLE}.")
    if not metrics:
        assumptions.append("No metrics were provided; the query plan is schema-oriented rather than KPI-oriented.")
    if not dimensions:
        assumptions.append("No dimensions were provided; the query plan is not grouped.")
    if not filters:
        assumptions.append("No filters were provided; the plan is broad and may need time-range or region constraints.")
    return assumptions


def _next_inputs(metrics, dimensions, table_hints):
    needed = []
    if not table_hints:
        needed.append(f"Optional: confirm whether {DEFAULT_TABLE} is the intended source table.")
    if not metrics:
        needed.append("Provide KPI names such as case_count, complaint_count, or accepted_items_count.")
    if not dimensions:
        needed.append("Provide report dimensions such as month, risk_type, or industry.")
    return needed


def _string_list(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _resolve_dimensions(dimensions):
    resolved = []
    for dimension in dimensions:
        resolved.append(DIMENSION_MAP.get(dimension, dimension))
    return resolved


def _resolve_metrics(metrics):
    resolved = []
    for metric in metrics:
        if metric in METRIC_MAP:
            resolved.append(METRIC_MAP[metric])
        else:
            resolved.append(metric)
    return resolved


def _build_where_parts(filters):
    where_parts = []
    for key, value in filters.items():
        if value in (None, "", []):
            continue
        if key in {"date_from", "accepted_date_from"}:
            where_parts.append(f"accepted_date >= {_sql_quote(value)}")
            continue
        if key in {"date_to", "accepted_date_to"}:
            where_parts.append(f"accepted_date <= {_sql_quote(value)}")
            continue
        if key == "year":
            where_parts.append(f"EXTRACT(YEAR FROM accepted_date) = {int(value)}")
            continue
        column = {
            "region": "region",
            "industry": "industry",
            "company_name": "company_name",
            "company_size": "company_size",
            "ownership_nature": "ownership_nature",
            "event_source": "event_source",
            "case_category": "case_category",
            "case_type_sub1": "case_type_sub1",
            "case_type_sub2": "case_type_sub2",
            "litigation_role_major": "litigation_role_major",
            "litigation_role": "litigation_role",
            "org_form": "org_form",
            "tech_enterprise": "tech_enterprise",
            "source_file": "source_file",
        }.get(key, key)
        if isinstance(value, list):
            where_parts.append(f"{column} IN ({', '.join(_sql_quote(item) for item in value)})")
        else:
            where_parts.append(f"{column} = {_sql_quote(value)}")
    return where_parts


def _validate_read_only_sql(sql):
    normalized = sql.lstrip().lower()
    if not normalized.startswith(("select", "with", "--")):
        return "Only read-only SELECT/WITH SQL is allowed."
    stripped_lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    statement = "\n".join(stripped_lines).strip().rstrip(";")
    lowered = statement.lower()
    if not lowered.startswith(("select", "with")):
        return "Only read-only SELECT/WITH SQL is allowed."
    banned = (" insert ", " update ", " delete ", " drop ", " alter ", " create ", " truncate ")
    padded = f" {lowered} "
    if any(token in padded for token in banned):
        return "The provided SQL contains write or DDL operations."
    return None


def _dataset_status():
    with _connect_postgres() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (DEFAULT_TABLE,))
            row = cur.fetchone()
            table_exists = bool(row and row[0])
            if not table_exists:
                return {"table_exists": False, "row_count": 0, "table_name": DEFAULT_TABLE}
            cur.execute(f"SELECT COUNT(*) FROM {DEFAULT_TABLE}")
            row = cur.fetchone()
            return {"table_exists": True, "row_count": int(row[0] or 0), "table_name": DEFAULT_TABLE}


def _execute_sql(sql):
    with _connect_postgres() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [item.name for item in cur.description or []]
            rows = [_json_safe_row(dict(zip(columns, row))) for row in cur.fetchall()]
            return rows, columns


def _connect_postgres():
    import psycopg

    dsn = str(os.getenv("PG_DSN") or "").strip()
    if dsn:
        return psycopg.connect(dsn, connect_timeout=5)
    return psycopg.connect(
        host=os.getenv("PGHOST") or None,
        port=os.getenv("PGPORT") or None,
        dbname=os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB") or None,
        user=os.getenv("PGUSER") or os.getenv("POSTGRES_USER") or None,
        password=os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD") or None,
        connect_timeout=5,
    )


def _json_safe_row(row):
    safe = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            safe[key] = value.isoformat()
        elif isinstance(value, date):
            safe[key] = value.isoformat()
        else:
            safe[key] = value
    return safe
