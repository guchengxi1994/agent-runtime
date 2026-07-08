definition = {
    "name": "pg-table-profile",
    "description": "Inspect a PostgreSQL table schema and low-cardinality enum-like values before writing SQL.",
}

import os
import re
from datetime import date, datetime


DEFAULT_TABLE = "enterprise_risk_events"
DEFAULT_SCHEMA = "public"
TEXT_TYPES = {"text", "varchar", "bpchar", "char", "name"}
DATE_TYPES = {"date", "timestamp", "timestamptz", "timestamp without time zone", "timestamp with time zone"}
NUMERIC_TYPES = {"int2", "int4", "int8", "numeric", "float4", "float8", "decimal"}
EXCLUDED_DIMENSION_COLUMNS = {"raw_json", "row_fingerprint", "event_id"}


def execute(params):
    table_name = str(params.get("table_name") or DEFAULT_TABLE).strip() or DEFAULT_TABLE
    table_schema = str(params.get("table_schema") or "").strip() or None
    focus_columns = _string_list(params.get("focus_columns"))
    max_enum_values = max(3, min(int(params.get("max_enum_values") or 12), 30))
    max_top_values = max(3, min(int(params.get("max_top_values") or 8), 20))

    try:
        profile = _profile_table(
            table_name=table_name,
            table_schema=table_schema,
            focus_columns=focus_columns,
            max_enum_values=max_enum_values,
            max_top_values=max_top_values,
        )
    except Exception as exc:
        return {
            "success": False,
            "mode": "postgres_unavailable",
            "error_type": "postgres_unavailable",
            "error": f"Failed to inspect PostgreSQL table: {exc}",
            "required_runtime_config": {
                "env": ["PG_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"],
                "bootstrap_skill": "pg-risk-dataset-sync",
            },
        }

    if not profile["table_exists"]:
        return {
            "success": False,
            "mode": "table_missing",
            "error_type": "table_missing",
            "error": f"Table not found: {profile['requested_table']}",
            "required_runtime_config": {
                "env": ["PG_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"],
                "bootstrap_skill": "pg-risk-dataset-sync",
            },
        }

    return {
        "success": True,
        "mode": "profiled",
        "table_name": profile["table_name"],
        "table_schema": profile["table_schema"],
        "qualified_table": profile["qualified_table"],
        "row_count": profile["row_count"],
        "column_count": len(profile["column_profiles"]),
        "dimension_candidates": profile["dimension_candidates"],
        "time_candidates": profile["time_candidates"],
        "filterable_enums": profile["filterable_enums"],
        "column_profiles": profile["column_profiles"],
        "columns": ["column_name", "data_type", "nullable", "distinct_count", "enum_values_preview", "notes"],
        "rows": [_profile_row(item) for item in profile["column_profiles"]],
        "summary": _build_summary(profile),
        "schema_overview": _build_schema_overview(profile),
        "llm_context": _build_llm_context(profile),
        "required_runtime_config": {
            "env": ["PG_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"],
            "bootstrap_skill": "pg-risk-dataset-sync",
        },
    }


def _profile_table(*, table_name, table_schema, focus_columns, max_enum_values, max_top_values):
    requested_table = f"{table_schema}.{table_name}" if table_schema else table_name
    with _connect_postgres() as conn:
        with conn.cursor() as cur:
            resolved_schema, resolved_table = _resolve_table(cur, table_name=table_name, table_schema=table_schema)
            if not resolved_table:
                return {
                    "table_exists": False,
                    "requested_table": requested_table,
                }
            row_count = _get_row_count(cur, resolved_schema, resolved_table)
            column_meta = _load_column_metadata(cur, resolved_schema, resolved_table)
            profiles = [
                _profile_column(
                    cur,
                    resolved_schema,
                    resolved_table,
                    meta,
                    focus_columns=focus_columns,
                    max_enum_values=max_enum_values,
                    max_top_values=max_top_values,
                )
                for meta in column_meta
            ]

    filterable_enums = {
        item["column_name"]: item["enum_values"]
        for item in profiles
        if isinstance(item.get("enum_values"), list) and item["enum_values"]
    }
    dimension_candidates = [item["column_name"] for item in profiles if item.get("dimension_candidate")]
    time_candidates = [item["column_name"] for item in profiles if item.get("time_candidate")]
    return {
        "table_exists": True,
        "requested_table": requested_table,
        "table_name": resolved_table,
        "table_schema": resolved_schema,
        "qualified_table": f"{resolved_schema}.{resolved_table}",
        "row_count": row_count,
        "column_profiles": profiles,
        "filterable_enums": filterable_enums,
        "dimension_candidates": dimension_candidates,
        "time_candidates": time_candidates,
    }


def _resolve_table(cur, *, table_name, table_schema):
    if "." in table_name and not table_schema:
        parts = table_name.split(".", 1)
        table_schema, table_name = parts[0].strip(), parts[1].strip()
    if table_schema:
        cur.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            LIMIT 1
            """,
            (table_schema, table_name),
        )
        row = cur.fetchone()
        if row:
            return row[0], row[1]
        return None, None

    cur.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_name = %s
        ORDER BY CASE WHEN table_schema = 'public' THEN 0 ELSE 1 END, table_schema
        LIMIT 1
        """,
        (table_name,),
    )
    row = cur.fetchone()
    if row:
        return row[0], row[1]
    return None, None


def _get_row_count(cur, schema_name, table_name):
    identifier = _qualified_identifier(schema_name, table_name)
    cur.execute(f"SELECT COUNT(*) FROM {identifier}")
    row = cur.fetchone()
    return int(row[0] or 0)


def _load_column_metadata(cur, schema_name, table_name):
    cur.execute(
        """
        SELECT
            column_name,
            data_type,
            udt_name,
            is_nullable,
            ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema_name, table_name),
    )
    rows = cur.fetchall()
    return [
        {
            "column_name": row[0],
            "data_type": row[1],
            "udt_name": row[2],
            "nullable": row[3] == "YES",
            "ordinal_position": int(row[4] or 0),
        }
        for row in rows
    ]


def _profile_column(cur, schema_name, table_name, meta, *, focus_columns, max_enum_values, max_top_values):
    column_name = meta["column_name"]
    data_type = str(meta.get("data_type") or "")
    udt_name = str(meta.get("udt_name") or "")
    normalized_type = udt_name or data_type
    identifier = _qualified_identifier(schema_name, table_name)
    column_identifier = _identifier(column_name)

    non_null_count = None
    distinct_count = None
    enum_values = []
    top_values = []
    min_value = None
    max_value = None
    notes = []

    if _is_text_like(data_type, udt_name) or normalized_type == "bool":
        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE {column_identifier} IS NOT NULL AND NULLIF(BTRIM({column_identifier}::text), '') IS NOT NULL) AS non_null_count,
                COUNT(DISTINCT {column_identifier}::text) FILTER (WHERE {column_identifier} IS NOT NULL AND NULLIF(BTRIM({column_identifier}::text), '') IS NOT NULL) AS distinct_count
            FROM {identifier}
            """
        )
        row = cur.fetchone() or (0, 0)
        non_null_count = int(row[0] or 0)
        distinct_count = int(row[1] or 0)
        if non_null_count > 0:
            cur.execute(
                f"""
                SELECT {column_identifier}::text AS value, COUNT(*) AS sample_count
                FROM {identifier}
                WHERE {column_identifier} IS NOT NULL AND NULLIF(BTRIM({column_identifier}::text), '') IS NOT NULL
                GROUP BY 1
                ORDER BY 2 DESC, 1 ASC
                LIMIT %s
                """,
                (max(distinct_count if distinct_count and distinct_count <= max_enum_values else max_top_values, 1),),
            )
            pairs = [(str(item[0]), int(item[1] or 0)) for item in cur.fetchall()]
            if distinct_count and distinct_count <= max_enum_values:
                enum_values = [item[0] for item in pairs]
                notes.append("low_cardinality_enum")
            else:
                top_values = [{"value": item[0], "count": item[1]} for item in pairs[:max_top_values]]
                if distinct_count:
                    notes.append("high_cardinality_text")

    elif _is_date_like(data_type, udt_name):
        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE {column_identifier} IS NOT NULL),
                MIN({column_identifier}),
                MAX({column_identifier})
            FROM {identifier}
            """
        )
        row = cur.fetchone() or (0, None, None)
        non_null_count = int(row[0] or 0)
        min_value = _json_safe_value(row[1])
        max_value = _json_safe_value(row[2])
        if non_null_count:
            notes.append("time_range_available")

    elif _is_numeric_like(data_type, udt_name):
        cur.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE {column_identifier} IS NOT NULL),
                MIN({column_identifier}),
                MAX({column_identifier})
            FROM {identifier}
            """
        )
        row = cur.fetchone() or (0, None, None)
        non_null_count = int(row[0] or 0)
        min_value = _json_safe_value(row[1])
        max_value = _json_safe_value(row[2])

    focus_requested = column_name in focus_columns
    dimension_candidate = _is_dimension_candidate(
        column_name=column_name,
        data_type=data_type,
        udt_name=udt_name,
        distinct_count=distinct_count,
        focus_requested=focus_requested,
    )
    time_candidate = _is_date_like(data_type, udt_name)
    if dimension_candidate and "dimension_candidate" not in notes:
        notes.append("dimension_candidate")
    if time_candidate and "time_candidate" not in notes:
        notes.append("time_candidate")

    return {
        "column_name": column_name,
        "data_type": data_type,
        "udt_name": udt_name,
        "nullable": bool(meta.get("nullable")),
        "non_null_count": non_null_count,
        "distinct_count": distinct_count,
        "enum_values": enum_values,
        "top_values": top_values,
        "min_value": min_value,
        "max_value": max_value,
        "dimension_candidate": dimension_candidate,
        "time_candidate": time_candidate,
        "notes": notes,
    }


def _is_dimension_candidate(*, column_name, data_type, udt_name, distinct_count, focus_requested):
    if column_name in EXCLUDED_DIMENSION_COLUMNS:
        return False
    if focus_requested:
        return True
    if _is_date_like(data_type, udt_name):
        return True
    if _is_text_like(data_type, udt_name):
        if any(token in column_name for token in ("title", "address", "fingerprint")):
            return False
        if distinct_count is None:
            return True
        return distinct_count <= 80
    if udt_name == "bool":
        return True
    return False


def _build_summary(profile):
    enum_columns = sorted(profile["filterable_enums"].keys())
    dimension_text = "、".join(profile["dimension_candidates"][:6]) if profile["dimension_candidates"] else "无明显维度候选"
    enum_text = "、".join(enum_columns[:5]) if enum_columns else "无低基数字段预览"
    return (
        f"表 {profile['qualified_table']} 共 {profile['row_count']} 行、{len(profile['column_profiles'])} 个字段；"
        f"可优先用于分组的字段包括 {dimension_text}；"
        f"已提取枚举/低基数预览的字段包括 {enum_text}。"
    )


def _build_schema_overview(profile):
    lines = [
        f"表名：{profile['qualified_table']}",
        f"总行数：{profile['row_count']}",
        f"字段数：{len(profile['column_profiles'])}",
        f"推荐维度：{', '.join(profile['dimension_candidates']) if profile['dimension_candidates'] else '无'}",
    ]
    for item in profile["column_profiles"]:
        if item["enum_values"]:
            lines.append(f"{item['column_name']} = {', '.join(item['enum_values'])}")
        elif item["top_values"]:
            lines.append(
                f"{item['column_name']} 高频取值 = {', '.join(entry['value'] for entry in item['top_values'][:5])}"
            )
        elif item["time_candidate"] and (item["min_value"] or item["max_value"]):
            lines.append(f"{item['column_name']} 范围 = {item['min_value']} ~ {item['max_value']}")
    return "\n".join(lines[:18])


def _build_llm_context(profile):
    lines = [
        f"Use only real columns from {profile['qualified_table']}.",
        f"Row count: {profile['row_count']}.",
        f"Dimension candidates: {', '.join(profile['dimension_candidates']) if profile['dimension_candidates'] else 'none'}.",
    ]
    for item in profile["column_profiles"]:
        if item["enum_values"]:
            lines.append(
                f"- {item['column_name']} ({item['data_type']}): enum values = {', '.join(item['enum_values'])}"
            )
        elif item["top_values"]:
            lines.append(
                f"- {item['column_name']} ({item['data_type']}): top values = "
                + ", ".join(entry["value"] for entry in item["top_values"][:5])
            )
        elif item["time_candidate"] and (item["min_value"] or item["max_value"]):
            lines.append(
                f"- {item['column_name']} ({item['data_type']}): range = {item['min_value']} to {item['max_value']}"
            )
    lines.append("Do not invent columns that are not listed above.")
    return "\n".join(lines[:24])


def _profile_row(item):
    enum_preview = ", ".join(item["enum_values"]) if item["enum_values"] else ""
    if not enum_preview and item["top_values"]:
        enum_preview = ", ".join(entry["value"] for entry in item["top_values"][:5])
    return {
        "column_name": item["column_name"],
        "data_type": item["data_type"],
        "nullable": "YES" if item["nullable"] else "NO",
        "distinct_count": item["distinct_count"],
        "enum_values_preview": enum_preview,
        "notes": ", ".join(item["notes"]),
    }


def _identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _qualified_identifier(schema_name, table_name):
    return f'{_identifier(schema_name)}.{_identifier(table_name)}'


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


def _is_text_like(data_type, udt_name):
    normalized = str(udt_name or data_type or "").lower()
    return normalized in TEXT_TYPES


def _is_date_like(data_type, udt_name):
    normalized_data = str(data_type or "").lower()
    normalized_udt = str(udt_name or "").lower()
    return normalized_data in DATE_TYPES or normalized_udt in {"date", "timestamp", "timestamptz"}


def _is_numeric_like(data_type, udt_name):
    normalized = str(udt_name or data_type or "").lower()
    return normalized in NUMERIC_TYPES


def _json_safe_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _string_list(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result
