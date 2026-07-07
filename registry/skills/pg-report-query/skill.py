definition = {
    "name": "pg-report-query",
    "description": "规划或执行 PostgreSQL 报表查询，用于生成结构化报告指标。",
}

import os


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
    dsn = str(os.getenv("PG_DSN") or "").strip()

    if execute_flag and dsn:
        return {
            "success": False,
            "mode": "not_implemented_execute",
            "error_type": "execution_not_implemented",
            "error": "PG execution is intentionally deferred in this stage; use the returned SQL plan and wire execution later.",
            "sql": planned_sql,
            "assumptions": assumptions,
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
        "required_runtime_config": {
            "env": ["PG_DSN"],
            "future_support": "When runtime DB execution is wired in, this skill can switch from plan_only to execute mode.",
        },
        "next_inputs": _next_inputs(metrics, dimensions, table_hints),
    }


def _sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _build_sql(report_question, metrics, dimensions, filters, table_hints, limit):
    select_parts = []
    if dimensions:
        select_parts.extend(dimensions)
    if metrics:
        select_parts.extend([f"/* metric */ {metric}" for metric in metrics])
    if not select_parts:
        select_parts.append("*")
    source = table_hints[0] if table_hints else "your_pg_table"
    where_parts = []
    for key, value in filters.items():
        where_parts.append(f"{key} = {_sql_quote(value)}")
    where_sql = f"\nWHERE {' AND '.join(where_parts)}" if where_parts else ""
    group_sql = f"\nGROUP BY {', '.join(dimensions)}" if dimensions and metrics else ""
    order_sql = f"\nORDER BY {', '.join(dimensions)}" if dimensions else ""
    comment = f"-- report_question: {report_question}" if report_question else "-- generated report query plan"
    return (
        f"{comment}\n"
        f"SELECT {', '.join(select_parts)}\n"
        f"FROM {source}"
        f"{where_sql}"
        f"{group_sql}"
        f"{order_sql}\n"
        f"LIMIT {limit};"
    )


def _build_assumptions(metrics, dimensions, filters, table_hints):
    assumptions = []
    if not table_hints:
        assumptions.append("No table_hints were provided; the SQL uses a placeholder source table.")
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
        needed.append("Provide source table hints or a data mart name.")
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
