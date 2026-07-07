definition = {
    "name": "pg-case-search",
    "description": "规划或执行 PostgreSQL 案例检索，用于报告证据抽取。",
}

import os


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
    dsn = str(os.getenv("PG_DSN") or "").strip()
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
            "table": "case_library",
            "columns": [
                "case_id",
                "title",
                "summary",
                "industry",
                "risk_type",
                "region",
                "year",
                "amount_band",
                "tags",
                "keywords",
                "source_path",
            ],
            "suggested_indexes": [
                "GIN(tags)",
                "GIN(to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(summary,'')))",
                "BTREE(year)",
                "BTREE(risk_type)",
            ],
        },
        "shortlist_guidance": _shortlist_guidance(query, tags, filters),
        "required_runtime_config": {
            "env": ["PG_DSN"],
            "future_support": "When PostgreSQL execution is wired in, this skill can switch from plan_only to execute mode.",
        },
    }
    if include_sql:
        result["sql"] = sql

    if execute_flag and dsn:
        return {
            "success": False,
            "mode": "not_implemented_execute",
            "error_type": "execution_not_implemented",
            "error": "PG execution is intentionally deferred in this stage; use the returned retrieval plan and wire execution later.",
            "sql": sql,
            "assumptions": assumptions,
        }
    return result


def _sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _build_sql(query, tags, filters, sort_by, limit):
    where_parts = []
    assumptions = [
        "Assume a PostgreSQL table named case_library with title, summary, tags, and structured metadata columns.",
    ]

    if query:
        escaped = query.replace("'", "''")
        where_parts.append(
            "("
            "to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(summary,'')) @@ plainto_tsquery('simple', '"
            + escaped
            + "') "
            "OR title ILIKE '%"
            + escaped
            + "%' "
            "OR summary ILIKE '%"
            + escaped
            + "%'"
            ")"
        )
    if tags:
        quoted_tags = ", ".join(_sql_quote(tag) for tag in tags)
        where_parts.append(f"tags && ARRAY[{quoted_tags}]::text[]")
    for key, value in filters.items():
        where_parts.append(f"{key} = {_sql_quote(value)}")

    order_sql = {
        "amount_desc": "ORDER BY amount_band DESC NULLS LAST, year DESC NULLS LAST",
        "year_desc": "ORDER BY year DESC NULLS LAST",
        "risk_desc": "ORDER BY risk_type ASC, year DESC NULLS LAST",
    }.get(sort_by, "ORDER BY year DESC NULLS LAST, case_id ASC")
    if sort_by == "relevance" and query:
        order_sql = (
            "ORDER BY ts_rank("
            "to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(summary,'')), "
            "plainto_tsquery('simple', '"
            + query.replace("'", "''")
            + "')"
            ") DESC, year DESC NULLS LAST"
        )

    where_sql = f"\nWHERE {' AND '.join(where_parts)}" if where_parts else ""
    sql = (
        "SELECT case_id, title, summary, industry, risk_type, region, year, amount_band, tags, source_path\n"
        "FROM case_library"
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
