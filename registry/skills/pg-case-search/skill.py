definition = {
    "name": "pg-case-search",
    "description": "规划或执行 PostgreSQL 案例检索，用于报告证据抽取。",
}

import os
import re
from datetime import date, datetime


DEFAULT_TABLE = "enterprise_risk_events"
DATASET_SCOPE_REGION_TERMS = (
    "常州市天宁区",
    "常州天宁区",
    "天宁区",
    "常州市",
    "常州",
)
OUTPUT_COLUMNS = [
    "event_source",
    "company_name",
    "case_title",
    "case_category",
    "case_type_sub1",
    "case_type_sub2",
    "region",
    "industry",
    "accepted_date",
    "source_file",
    "department_case_no",
]


def execute(params):
    query = str(params.get("query") or "").strip()
    tags = _string_list(params.get("tags"))
    filters = params.get("filters") if isinstance(params.get("filters"), dict) else {}
    sort_by = str(params.get("sort_by") or "relevance").strip().lower() or "relevance"
    include_sql = bool(params.get("include_sql", True))
    execute_flag = bool(params.get("execute", False))
    limit = max(1, min(int(params.get("limit") or _infer_limit_from_query(query) or 10), 50))
    if sort_by == "relevance" and _contains_any(query, ["最新", "最近", "近年"]):
        sort_by = "year_desc"

    if not query and not tags and not filters:
        raise ValueError("query, tags, or filters is required")

    normalized_filters = dict(filters)
    scope_assumptions = _normalize_scope_filters(normalized_filters)
    filters = normalized_filters

    sql, assumptions = _build_sql(query, tags, filters, sort_by, limit)
    assumptions = assumptions + scope_assumptions
    result = {
        "success": True,
        "mode": "plan_only",
        "query": query,
        "tags": tags,
        "filters": filters,
        "sort_by": sort_by,
        "limit": limit,
        "assumptions": assumptions,
        "recommended_schema": {
            "table": DEFAULT_TABLE,
            "columns": [
                "event_source",
                "company_name",
                "case_title",
                "case_category",
                "case_type_sub1",
                "case_type_sub2",
                "industry",
                "region",
                "accepted_date",
                "source_file",
            ],
            "suggested_indexes": [
                "BTREE(accepted_date)",
                "BTREE(region)",
                "BTREE(industry)",
                "BTREE(case_category)",
                "BTREE(event_source)",
            ],
        },
        "shortlist_guidance": _shortlist_guidance(query, tags, filters),
        "required_runtime_config": {
            "env": ["PG_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"],
            "bootstrap_skill": "pg-risk-dataset-sync",
        },
    }
    if include_sql:
        result["sql"] = sql

    if execute_flag:
        read_only_error = _validate_read_only_sql(sql)
        if read_only_error:
            return {
                "success": False,
                "mode": "invalid_sql",
                "error_type": "read_only_sql_required",
                "error": read_only_error,
                "sql": sql,
            }
        try:
            dataset_status = _dataset_status()
        except Exception as exc:
            return {
                "success": False,
                "mode": "postgres_unavailable",
                "error_type": "postgres_unavailable",
                "error": f"Failed to connect to PostgreSQL: {exc}",
                "sql": sql,
                "assumptions": assumptions,
            }
        if not dataset_status["table_exists"] or dataset_status["row_count"] <= 0:
            return {
                "success": False,
                "mode": "dataset_unavailable",
                "error_type": "missing_dataset",
                "error": "enterprise_risk_events is missing or empty. Call pg-risk-dataset-sync before case retrieval.",
                "sql": sql,
                "dataset_status": dataset_status,
            }
        filter_diagnostic = _validate_dynamic_filters(filters)
        if filter_diagnostic:
            return filter_diagnostic
        try:
            rows, columns = _execute_sql(sql)
        except Exception as exc:
            return {
                "success": False,
                "mode": "query_failed",
                "error_type": "query_failed",
                "error": f"PostgreSQL query failed: {exc}",
                "sql": sql,
                "dataset_status": dataset_status,
            }
        result["mode"] = "executed"
        result["rows"] = rows
        result["columns"] = columns
        result["row_count"] = len(rows)
        result["dataset_status"] = dataset_status
        result["summary"] = _summarize_case_rows(rows, query)
    return result


def _sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _build_sql(query, tags, filters, sort_by, limit):
    where_parts = []
    assumptions = [
        f"Assume a PostgreSQL table named {DEFAULT_TABLE} that stores structured enterprise legal/litigation cases and administrative penalty records.",
    ]

    if query:
        escaped = query.replace("'", "''")
        where_parts.append(
            "("
            "coalesce(case_title,'') ILIKE '%"
            + escaped
            + "%' "
            "OR coalesce(case_category,'') ILIKE '%"
            + escaped
            + "%' "
            "OR coalesce(case_type_sub1,'') ILIKE '%"
            + escaped
            + "%' "
            "OR coalesce(case_type_sub2,'') ILIKE '%"
            + escaped
            + "%' "
            "OR coalesce(company_name,'') ILIKE '%"
            + escaped
            + "%'"
            ")"
        )
    if tags:
        tag_parts = []
        for tag in tags:
            escaped_tag = tag.replace("'", "''")
            tag_parts.append(
                "("
                "coalesce(case_category,'') ILIKE '%"
                + escaped_tag
                + "%' OR coalesce(case_type_sub1,'') ILIKE '%"
                + escaped_tag
                + "%' OR coalesce(case_type_sub2,'') ILIKE '%"
                + escaped_tag
                + "%'"
                ")"
            )
        where_parts.append("(" + " OR ".join(tag_parts) + ")")
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
            "event_source": "event_source",
            "company_name": "company_name",
            "case_category": "case_category",
            "case_type_sub1": "case_type_sub1",
            "case_type_sub2": "case_type_sub2",
            "company_size": "company_size",
            "ownership_nature": "ownership_nature",
        }.get(key, key)
        if isinstance(value, list):
            where_parts.append(f"{column} IN ({', '.join(_sql_quote(item) for item in value)})")
        else:
            where_parts.append(f"{column} = {_sql_quote(value)}")

    order_sql = {
        "amount_desc": "ORDER BY accepted_date DESC NULLS LAST, company_name ASC",
        "year_desc": "ORDER BY accepted_date DESC NULLS LAST, company_name ASC",
        "risk_desc": "ORDER BY case_category ASC NULLS LAST, accepted_date DESC NULLS LAST",
    }.get(sort_by, "ORDER BY accepted_date DESC NULLS LAST, company_name ASC")
    if sort_by == "relevance" and query:
        order_sql = "ORDER BY accepted_date DESC NULLS LAST, company_name ASC"

    where_sql = f"\nWHERE {' AND '.join(where_parts)}" if where_parts else ""
    sql = (
        f"SELECT {', '.join(OUTPUT_COLUMNS)}\n"
        f"FROM {DEFAULT_TABLE}"
        f"{where_sql}\n"
        f"{order_sql}\n"
        f"LIMIT {limit};"
    )
    if not query:
        assumptions.append("No free-text query was provided; retrieval depends on tags and structured filters only.")
    if not tags:
        assumptions.append("No tags were provided; semantic retrieval relies on the query text or filters.")
    if not filters:
        assumptions.append("No structured filters were provided; the shortlist may mix streets/subdistricts, years, or industries.")
    return sql, assumptions


def _validate_dynamic_filters(filters):
    available_columns = _live_column_names()
    virtual_fields = {"date_from", "accepted_date_from", "date_to", "accepted_date_to", "year"}
    invalid_filters = []
    for field, value in filters.items():
        if value in (None, "", []):
            continue
        if field not in available_columns and field not in virtual_fields:
            invalid_filters.append(
                {
                    "field": field,
                    "value": value,
                    "reason": "unknown_column",
                }
            )
    if not invalid_filters:
        return None
    return {
        "success": False,
        "mode": "invalid_dynamic_filter",
        "error_type": "invalid_dynamic_filter",
        "error": "One or more filters do not match columns in the live PostgreSQL table.",
        "invalid_filters": invalid_filters,
        "allowed_filter_fields": sorted([*virtual_fields, *available_columns]),
        "recommended_next_skill": "pg-table-profile",
        "recommended_next_action": "Profile the table when the intended field or its valid values are unclear.",
        "retry_guidance": "Replace or remove each invalid filter field. Valid field names come from the live table, not a static filter schema.",
    }


def _normalize_scope_filters(filters):
    assumptions = []
    explicit_region = str(filters.get("region") or "").strip()
    if explicit_region in DATASET_SCOPE_REGION_TERMS:
        filters.pop("region", None)
        assumptions.append(
            f"Treat region={explicit_region} as dataset scope only; region values inside the dataset represent TianNing subdistricts/streets."
        )
    explicit_region_contains = str(filters.get("region_contains") or "").strip()
    if explicit_region_contains in DATASET_SCOPE_REGION_TERMS:
        filters.pop("region_contains", None)
        assumptions.append(
            f"Treat region_contains={explicit_region_contains} as dataset scope only; do not use it as an extra SQL filter."
        )
    return assumptions


def _shortlist_guidance(query, tags, filters):
    guidance = [
        "Prefer 1-3 representative cases in the final report, not an exhaustive dump.",
        "Prefer cases whose risk pattern directly supports the main report section.",
    ]
    if query:
        guidance.append(f"Current retrieval target: {query}")
    if tags:
        guidance.append(f"Prioritize tag overlap: {', '.join(tags)}")
    if filters:
        guidance.append("Keep the shortlist within the provided structured filters before broadening scope.")
    return guidance


def _summarize_case_rows(rows, query):
    if not rows:
        return "未检索到匹配案例。"
    first = rows[0]
    company = first.get("company_name") or "未命名企业"
    case_title = first.get("case_title") or first.get("case_category") or "未命名案件"
    accepted_date = first.get("accepted_date") or "未知日期"
    return f"共检索到 {len(rows)} 条候选记录；最新一条来自 {company}，案件为 {case_title}，时间 {accepted_date}。"


def _string_list(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _infer_limit_from_query(query):
    if not query:
        return None
    match = re.search(r"前\s*(\d+)", query)
    if match:
        return max(1, min(int(match.group(1)), 50))
    for text, value in {"前三": 3, "前五": 5, "前十": 10}.items():
        if text in query:
            return value
    match = re.search(r"top\s*(\d+)", query, flags=re.IGNORECASE)
    if match:
        return max(1, min(int(match.group(1)), 50))
    return None


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def _validate_read_only_sql(sql):
    normalized = sql.lstrip().lower()
    if not normalized.startswith("select"):
        return "Only read-only SELECT SQL is allowed."
    lowered = sql.lower()
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


def _live_column_names():
    with _connect_postgres() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT attribute.attname
                FROM pg_attribute AS attribute
                WHERE attribute.attrelid = to_regclass(%s)
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                ORDER BY attribute.attnum
                """,
                (DEFAULT_TABLE,),
            )
            return {str(row[0]) for row in cur.fetchall()}


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
