definition = {
    "name": "pg-case-search",
    "description": "规划或执行 PostgreSQL 案例检索，用于报告证据抽取。",
}

import os
from datetime import date, datetime


DEFAULT_TABLE = "enterprise_risk_events"
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
    limit = max(1, min(int(params.get("limit") or 10), 50))

    if not query and not tags and not filters:
        raise ValueError("query, tags, or filters is required")

    sql, assumptions = _build_sql(query, tags, filters, sort_by, limit)
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
    return result


def _sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _build_sql(query, tags, filters, sort_by, limit):
    where_parts = []
    assumptions = [
        f"Assume a PostgreSQL table named {DEFAULT_TABLE} that stores structured enterprise risk events.",
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
        assumptions.append("No structured filters were provided; the shortlist may mix regions, years, or industries.")
    return sql, assumptions


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


def _string_list(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


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
