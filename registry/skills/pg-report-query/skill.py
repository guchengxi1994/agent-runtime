definition = {
    "name": "pg-report-query",
    "description": "规划或执行 PostgreSQL 报表查询，用于生成结构化案件分析报告指标。",
}

import os
import re
from datetime import date, datetime


DEFAULT_TABLE = "enterprise_risk_events"
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
DATASET_SCOPE_REGION_TERMS = (
    "常州市天宁区",
    "常州天宁区",
    "天宁区",
    "常州市",
    "常州",
)

DIMENSION_SPECS = {
    "month": {
        "select": "to_char(date_trunc('month', accepted_date), 'YYYY-MM') AS month",
        "alias": "month",
        "label": "月份",
        "kind": "time",
    },
    "year": {
        "select": "EXTRACT(YEAR FROM accepted_date)::int AS year",
        "alias": "year",
        "label": "年份",
        "kind": "time",
    },
    "risk_type": {
        "select": "COALESCE(NULLIF(case_type_sub2, ''), NULLIF(case_type_sub1, ''), case_category) AS risk_type",
        "alias": "risk_type",
        "label": "案件事项类型",
        "kind": "category",
    },
    "event_source": {
        "select": "event_source",
        "alias": "event_source",
        "label": "来源类型",
        "kind": "category",
    },
    "region": {"select": "region", "alias": "region", "label": "区域", "kind": "category"},
    "industry": {"select": "industry", "alias": "industry", "label": "行业", "kind": "category"},
    "company_name": {"select": "company_name", "alias": "company_name", "label": "企业名称", "kind": "category"},
    "company_size": {"select": "company_size", "alias": "company_size", "label": "企业规模", "kind": "category"},
    "ownership_nature": {
        "select": "ownership_nature",
        "alias": "ownership_nature",
        "label": "所有权性质",
        "kind": "category",
    },
    "org_form": {"select": "org_form", "alias": "org_form", "label": "组织形式", "kind": "category"},
    "case_category": {
        "select": "case_category",
        "alias": "case_category",
        "label": "案件大类",
        "kind": "category",
    },
    "case_type_sub1": {
        "select": "case_type_sub1",
        "alias": "case_type_sub1",
        "label": "案件子类1",
        "kind": "category",
    },
    "case_type_sub2": {
        "select": "case_type_sub2",
        "alias": "case_type_sub2",
        "label": "案件子类2",
        "kind": "category",
    },
    "litigation_role_major": {
        "select": "litigation_role_major",
        "alias": "litigation_role_major",
        "label": "诉讼地位大类",
        "kind": "category",
    },
    "litigation_role": {
        "select": "litigation_role",
        "alias": "litigation_role",
        "label": "诉讼地位",
        "kind": "category",
    },
    "tech_enterprise": {
        "select": "tech_enterprise",
        "alias": "tech_enterprise",
        "label": "科技企业",
        "kind": "category",
    },
    "source_file": {
        "select": "source_file",
        "alias": "source_file",
        "label": "来源文件",
        "kind": "category",
    },
}

METRIC_SPECS = {
    "case_count": {"select": "COUNT(*) AS case_count", "alias": "case_count", "label": "案件数"},
    "record_count": {"select": "COUNT(*) AS record_count", "alias": "record_count", "label": "记录数"},
    "company_count": {
        "select": "COUNT(DISTINCT company_name) AS company_count",
        "alias": "company_count",
        "label": "企业数",
    },
    "judicial_case_count": {
        "select": "COUNT(*) FILTER (WHERE event_source = 'judicial_case') AS judicial_case_count",
        "alias": "judicial_case_count",
        "label": "司法案件数",
    },
    "administrative_penalty_count": {
        "select": "COUNT(*) FILTER (WHERE event_source = 'administrative_penalty') AS administrative_penalty_count",
        "alias": "administrative_penalty_count",
        "label": "行政处罚数",
    },
    "share_percent": {
        "select": "ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 2) AS share_percent",
        "alias": "share_percent",
        "label": "占比(%)",
    },
}


def execute(params):
    report_question = str(params.get("report_question") or "").strip()
    metrics = _string_list(params.get("metrics"))
    dimensions = _string_list(params.get("dimensions"))
    filters = params.get("filters") if isinstance(params.get("filters"), dict) else {}
    table_hints = _string_list(params.get("table_hints"))
    raw_sql = str(params.get("sql") or "").strip()
    execute_flag = bool(params.get("execute", False))
    limit = max(1, min(int(params.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
    top_n = _positive_int_or_none(params.get("top_n"))
    order_by = str(params.get("order_by") or "").strip()
    order_direction = _normalize_order_direction(params.get("order_direction"))
    include_share = _optional_bool(params.get("include_share"))
    include_chart = _optional_bool(params.get("include_chart"))
    chart_type = str(params.get("chart_type") or "").strip().lower() or None

    if not report_question and not raw_sql:
        raise ValueError("report_question or sql is required")

    resolved = _resolve_query_request(
        report_question=report_question,
        metrics=metrics,
        dimensions=dimensions,
        filters=filters,
        top_n=top_n,
        order_by=order_by,
        order_direction=order_direction,
        include_share=include_share,
        include_chart=include_chart,
        chart_type=chart_type,
        limit=limit,
    )

    planned_sql = raw_sql or _build_sql(
        report_question=report_question,
        resolved_dimensions=resolved["dimensions"],
        resolved_metrics=resolved["metrics"],
        filters=resolved["filters"],
        table_hints=table_hints,
        limit=resolved["sql_limit"],
        order_by=resolved["order_by"],
        order_direction=resolved["order_direction"],
    )
    assumptions = _build_assumptions(
        metrics=resolved["metrics"],
        dimensions=resolved["dimensions"],
        filters=resolved["filters"],
        table_hints=table_hints,
        planning_notes=resolved["planning_notes"],
    )
    runtime_config = {
        "env": ["PG_DSN", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"],
        "default_table": DEFAULT_TABLE,
        "bootstrap_skill": "pg-risk-dataset-sync",
        "schema_skill": "pg-table-profile",
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
            rows, columns, truncated = _execute_sql(planned_sql, max_rows=limit)
        except Exception as exc:
            error_text = str(exc)
            if _looks_like_missing_column(error_text):
                return {
                    "success": False,
                    "mode": "schema_mismatch",
                    "error_type": "schema_mismatch",
                    "error": f"PostgreSQL query failed because the SQL referenced a missing column: {exc}",
                    "sql": planned_sql,
                    "dataset_status": dataset_status,
                    "recommended_next_skill": "pg-table-profile",
                    "recommended_next_action": "Inspect the live table schema and enum-like values, then rewrite the SQL with real column names.",
                    "required_runtime_config": runtime_config,
                }
            return {
                "success": False,
                "mode": "query_failed",
                "error_type": "query_failed",
                "error": f"PostgreSQL query failed: {exc}",
                "sql": planned_sql,
                "dataset_status": dataset_status,
            }

        chart_spec = None
        if resolved["chart_plan"].get("enabled"):
            chart_spec = _build_chart_spec(
                rows=rows,
                dimension_specs=resolved["dimension_specs"],
                metric_specs=resolved["metric_specs"],
                chart_plan=resolved["chart_plan"],
            )

        return {
            "success": True,
            "mode": "executed",
            "report_question": report_question,
            "sql": planned_sql,
            "metrics": resolved["metrics"],
            "dimensions": resolved["dimensions"],
            "filters": resolved["filters"],
            "table_hints": table_hints,
            "assumptions": assumptions,
            "row_count": len(rows),
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "requested_limit": limit,
            "dataset_status": dataset_status,
            "top_n": resolved["top_n"],
            "order_by": resolved["order_by"],
            "order_direction": resolved["order_direction"],
            "summary": _summarize_rows(
                rows=rows,
                dimension_specs=resolved["dimension_specs"],
                metric_specs=resolved["metric_specs"],
                report_question=report_question,
                truncated=truncated,
                limit=limit,
            ),
            "chart_plan": resolved["chart_plan"],
            "chart_spec": chart_spec,
            "planning_notes": resolved["planning_notes"],
        }

    return {
        "success": True,
        "mode": "plan_only",
        "report_question": report_question,
        "sql": planned_sql,
        "metrics": resolved["metrics"],
        "dimensions": resolved["dimensions"],
        "filters": resolved["filters"],
        "table_hints": table_hints,
        "assumptions": assumptions,
        "required_runtime_config": runtime_config,
        "next_inputs": _next_inputs(resolved["metrics"], resolved["dimensions"], table_hints),
        "top_n": resolved["top_n"],
        "order_by": resolved["order_by"],
        "order_direction": resolved["order_direction"],
        "chart_plan": resolved["chart_plan"],
        "planning_notes": resolved["planning_notes"],
        "recommended_preflight_skill": "pg-table-profile",
    }


def _resolve_query_request(
    *,
    report_question,
    metrics,
    dimensions,
    filters,
    top_n,
    order_by,
    order_direction,
    include_share,
    include_chart,
    chart_type,
    limit,
):
    question = report_question or ""
    planning_notes = []
    resolved_filters = dict(filters)

    _infer_filters(question, resolved_filters, planning_notes)
    _normalize_scope_filters(resolved_filters, planning_notes)

    resolved_dimensions = list(dimensions)
    if not resolved_dimensions:
        resolved_dimensions = _infer_dimensions(question)
        if resolved_dimensions:
            planning_notes.append(f"inferred dimensions from question: {', '.join(resolved_dimensions)}")
    if resolved_filters.get("event_source") and "event_source" in resolved_dimensions and len(resolved_dimensions) > 1:
        resolved_dimensions = [item for item in resolved_dimensions if item != "event_source"]
        planning_notes.append("removed event_source from dimensions because the question already fixes it as a filter")

    resolved_metrics = list(metrics)
    if include_share is None:
        include_share = _contains_any(question, ["占比", "比例", "构成"])
    if not resolved_metrics:
        resolved_metrics = _infer_metrics(question, resolved_dimensions)
        if resolved_metrics:
            planning_notes.append(f"inferred metrics from question: {', '.join(resolved_metrics)}")
    if not resolved_metrics:
        resolved_metrics = ["case_count"]
        planning_notes.append("defaulted metrics to case_count")
    if include_share and resolved_dimensions and "share_percent" not in resolved_metrics:
        resolved_metrics.append("share_percent")
        planning_notes.append("added share_percent because the question implies a composition view")

    if top_n is None:
        top_n = _infer_top_n(question)
        if top_n:
            planning_notes.append(f"inferred top_n={top_n} from ranking language")

    if not order_by:
        order_by = _infer_order_by(question, resolved_dimensions, resolved_metrics, top_n)
        if order_by:
            planning_notes.append(f"inferred order_by={order_by}")
    if not order_direction:
        order_direction = _infer_order_direction(question, top_n)
    if order_by and not order_direction:
        order_direction = "desc"

    chart_plan = _infer_chart_plan(
        question=question,
        dimensions=resolved_dimensions,
        metrics=resolved_metrics,
        include_share=bool(include_share),
        include_chart=include_chart,
        chart_type=chart_type,
        top_n=top_n,
    )
    dimension_specs = _resolve_dimension_specs(resolved_dimensions)
    metric_specs = _resolve_metric_specs(resolved_metrics)
    sql_limit = min(limit, top_n) if top_n else limit

    return {
        "dimensions": resolved_dimensions,
        "metrics": resolved_metrics,
        "filters": resolved_filters,
        "top_n": top_n,
        "order_by": order_by,
        "order_direction": order_direction or "desc",
        "chart_plan": chart_plan,
        "dimension_specs": dimension_specs,
        "metric_specs": metric_specs,
        "planning_notes": planning_notes,
        "sql_limit": sql_limit,
    }


def _infer_dimensions(question):
    inferred = []
    rules = [
        ("month", ["按月", "每月", "月度", "月份", "趋势"]),
        ("year", ["按年", "每年", "年度", "年份"]),
        ("industry", ["行业", "各行业"]),
        ("region", ["区域", "地区", "属地", "街道", "镇", "乡", "板块", "园区"]),
        ("ownership_nature", ["所有权性质"]),
        ("company_size", ["企业规模", "规模"]),
        ("org_form", ["组织形式"]),
        ("event_source", ["来源", "司法案件", "行政处罚", "司法与行政"]),
        ("risk_type", ["风险类型", "案件类型", "事项类型", "子类"]),
        ("case_category", ["案件大类"]),
        ("litigation_role_major", ["诉讼地位大类"]),
        ("litigation_role", ["诉讼地位"]),
        ("tech_enterprise", ["科技企业"]),
        ("source_file", ["来源文件"]),
        ("company_name", ["企业名单", "企业名称"]),
    ]
    for key, keywords in rules:
        if _contains_any(question, keywords):
            inferred.append(key)

    if "month" in inferred and "year" in inferred:
        inferred.remove("year")
    if "risk_type" in inferred and "case_category" in inferred:
        inferred.remove("case_category")
    return inferred[:2]


def _infer_metrics(question, resolved_dimensions):
    if _contains_any(question, ["企业数", "企业数量", "多少企业"]):
        return ["company_count"]
    if _contains_any(question, ["行政处罚数"]) and not resolved_dimensions:
        return ["administrative_penalty_count"]
    if _contains_any(question, ["司法案件数", "诉讼案件数"]) and not resolved_dimensions:
        return ["judicial_case_count"]
    if resolved_dimensions:
        return ["case_count"]
    if _contains_any(question, ["多少", "总数", "总量", "总共有", "有几条", "数量"]):
        return ["case_count"]
    return ["case_count"]


def _infer_filters(question, filters, planning_notes):
    if "event_source" not in filters:
        if _contains_any(question, ["行政处罚"]):
            filters["event_source"] = "administrative_penalty"
            planning_notes.append("inferred filter event_source=administrative_penalty")
        elif _contains_any(question, ["司法案件", "涉诉", "诉讼案件", "司法文书"]):
            filters["event_source"] = "judicial_case"
            planning_notes.append("inferred filter event_source=judicial_case")

    years = [int(item) for item in re.findall(r"(20\d{2})年", question)]
    if years:
        if "accepted_date_from" not in filters and "date_from" not in filters and "以来" in question:
            filters["accepted_date_from"] = f"{years[0]}-01-01"
            planning_notes.append(f"inferred date_from={years[0]}-01-01")
        elif len(years) == 1 and "year" not in filters:
            filters["year"] = years[0]
            planning_notes.append(f"inferred year={years[0]}")
        elif len(years) >= 2 and "date_from" not in filters and "accepted_date_to" not in filters:
            start_year = min(years[0], years[1])
            end_year = max(years[0], years[1])
            filters["accepted_date_from"] = f"{start_year}-01-01"
            filters["accepted_date_to"] = f"{end_year}-12-31"
            planning_notes.append(f"inferred date range {start_year}-01-01 to {end_year}-12-31")

    for candidate in DATASET_SCOPE_REGION_TERMS:
        if candidate in question:
            planning_notes.append(
                f"treated {candidate} as dataset scope rather than a region filter; aggregate region as subdistrict/street"
            )
            break


def _normalize_scope_filters(filters, planning_notes):
    explicit_region = str(filters.get("region") or "").strip()
    if explicit_region in DATASET_SCOPE_REGION_TERMS:
        filters.pop("region", None)
        planning_notes.append(
            f"removed region={explicit_region} because the dataset is already scoped to TianNing and region is used for street-level aggregation"
        )

    explicit_region_contains = str(filters.get("region_contains") or "").strip()
    if explicit_region_contains in DATASET_SCOPE_REGION_TERMS:
        filters.pop("region_contains", None)
        planning_notes.append(
            f"removed region_contains={explicit_region_contains} because the dataset scope is already TianNing"
        )


def _infer_top_n(question):
    match = re.search(r"前\s*(\d+)", question)
    if match:
        return max(1, min(int(match.group(1)), 100))
    chinese_map = {"前三": 3, "前五": 5, "前十": 10}
    for text, value in chinese_map.items():
        if text in question:
            return value
    match = re.search(r"top\s*(\d+)", question, flags=re.IGNORECASE)
    if match:
        return max(1, min(int(match.group(1)), 100))
    return None


def _infer_order_by(question, dimensions, metrics, top_n):
    if metrics and (_contains_any(question, ["最多", "最高", "排行", "排名", "前"]) or top_n):
        return _metric_alias(metrics[0])
    if dimensions and _contains_any(question, ["按月", "每月", "月度", "趋势", "年度", "年份"]):
        return _dimension_alias(dimensions[0])
    if dimensions:
        return _dimension_alias(dimensions[0])
    if metrics:
        return _metric_alias(metrics[0])
    return ""


def _infer_order_direction(question, top_n):
    if _contains_any(question, ["最少", "最低"]):
        return "asc"
    if _contains_any(question, ["最新"]) and not top_n:
        return "desc"
    if _contains_any(question, ["最多", "最高", "排行", "排名", "前"]) or top_n:
        return "desc"
    return "asc" if _contains_any(question, ["按月", "每月", "月度", "趋势", "按年", "每年"]) else ""


def _infer_chart_plan(*, question, dimensions, metrics, include_share, include_chart, chart_type, top_n):
    enabled = include_chart if isinstance(include_chart, bool) else _contains_any(
        question,
        ["图", "图表", "趋势", "分布", "占比", "构成", "排名", "柱状图", "折线图", "饼图"],
    )
    if not dimensions or not metrics:
        enabled = False if include_chart is not True else enabled
    inferred_type = chart_type or ""
    if not inferred_type:
        if include_share and len(dimensions) == 1:
            inferred_type = "pie"
        elif dimensions and DIMENSION_SPECS.get(dimensions[0], {}).get("kind") == "time":
            inferred_type = "line"
        elif len(dimensions) >= 2:
            inferred_type = "stacked_bar"
        elif top_n or _contains_any(question, ["最多", "最高", "排名", "排行", "前"]):
            inferred_type = "bar"
        else:
            inferred_type = "bar"
    return {
        "enabled": bool(enabled and dimensions and metrics),
        "chart_type": inferred_type,
        "title": (question[:60] if question else "查询图表"),
    }


def _build_sql(
    *,
    report_question,
    resolved_dimensions,
    resolved_metrics,
    filters,
    table_hints,
    limit,
    order_by,
    order_direction,
):
    dimension_specs = _resolve_dimension_specs(resolved_dimensions)
    metric_specs = _resolve_metric_specs(resolved_metrics)
    select_specs = dimension_specs + metric_specs
    select_parts = [item["select"] for item in select_specs] or ["*"]
    select_prefix = "SELECT DISTINCT" if dimension_specs and not metric_specs else "SELECT"
    source = table_hints[0] if table_hints else DEFAULT_TABLE
    where_parts = _build_where_parts(filters)
    where_sql = f"\nWHERE {' AND '.join(where_parts)}" if where_parts else ""
    group_sql = (
        f"\nGROUP BY {', '.join(str(index + 1) for index in range(len(dimension_specs)))}"
        if dimension_specs and metric_specs
        else ""
    )
    order_sql = _build_order_sql(select_specs, dimension_specs, metric_specs, order_by, order_direction)
    comment = f"-- report_question: {report_question}" if report_question else "-- generated report query plan"
    return (
        f"{comment}\n"
        f"{select_prefix} {', '.join(select_parts)}\n"
        f"FROM {source}"
        f"{where_sql}"
        f"{group_sql}"
        f"{order_sql}\n"
        f"LIMIT {max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))};"
    )


def _build_order_sql(select_specs, dimension_specs, metric_specs, order_by, order_direction):
    if not select_specs:
        return ""
    order_direction = order_direction or "asc"
    alias_positions = {spec["alias"]: str(index + 1) for index, spec in enumerate(select_specs)}
    if order_by:
        order_target = alias_positions.get(order_by) or order_by
        return f"\nORDER BY {order_target} {order_direction.upper()}"
    if metric_specs:
        target = alias_positions.get(metric_specs[0]["alias"], str(len(dimension_specs) + 1))
        return f"\nORDER BY {target} DESC"
    if dimension_specs:
        target = alias_positions.get(dimension_specs[0]["alias"], "1")
        return f"\nORDER BY {target} ASC"
    return ""


def _build_assumptions(metrics, dimensions, filters, table_hints, planning_notes):
    assumptions = []
    if not table_hints:
        assumptions.append(f"No table_hints were provided; the SQL uses the default source table {DEFAULT_TABLE}.")
    if not metrics:
        assumptions.append("No metrics were provided; the query plan is schema-oriented rather than KPI-oriented.")
    if not dimensions:
        assumptions.append("No dimensions were provided; the query plan is not grouped.")
    if not filters:
        assumptions.append("No filters were provided; the plan is broad and may need time-range or category constraints.")
    assumptions.extend(planning_notes[:8])
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


def _resolve_dimension_specs(dimensions):
    specs = []
    for key in dimensions:
        spec = DIMENSION_SPECS.get(key)
        if spec:
            specs.append(dict(spec, key=key))
        else:
            alias = _extract_alias(key, key)
            specs.append({"select": key, "alias": alias, "label": alias, "kind": "category", "key": key})
    return specs


def _resolve_metric_specs(metrics):
    specs = []
    for key in metrics:
        spec = METRIC_SPECS.get(key)
        if spec:
            specs.append(dict(spec, key=key))
        else:
            alias = _extract_alias(key, key)
            specs.append({"select": key, "alias": alias, "label": alias, "key": key})
    return specs


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
        if key.endswith("_contains"):
            base_key = key[: -len("_contains")]
            column = {
                "region": "region",
                "industry": "industry",
                "company_name": "company_name",
                "case_category": "case_category",
                "case_type_sub1": "case_type_sub1",
                "case_type_sub2": "case_type_sub2",
            }.get(base_key, base_key)
            escaped_value = str(value).replace("'", "''")
            where_parts.append(f"{column} ILIKE '%{escaped_value}%'")
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


def _build_chart_spec(rows, dimension_specs, metric_specs, chart_plan):
    if not rows or not dimension_specs or not metric_specs:
        return None
    metric_alias = metric_specs[-1]["alias"] if metric_specs[-1]["alias"] == "share_percent" else metric_specs[0]["alias"]
    metric_label = next((item["label"] for item in metric_specs if item["alias"] == metric_alias), metric_alias)
    chart_type = chart_plan.get("chart_type") or "bar"
    title = chart_plan.get("title") or "查询图表"
    x_spec = dimension_specs[0]
    x_field = x_spec["alias"]

    if chart_type == "pie":
        option = {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0},
            "series": [
                {
                    "name": metric_label,
                    "type": "pie",
                    "radius": "58%",
                    "data": [
                        {"name": str(row.get(x_field) or ""), "value": row.get(metric_alias)}
                        for row in rows
                    ],
                }
            ],
        }
        return {
            "renderer": "echarts",
            "chart_type": chart_type,
            "title": title,
            "option": option,
            "frontend_hint": "Render option directly with ECharts on the frontend.",
        }

    if len(dimension_specs) >= 2:
        series_field = dimension_specs[1]["alias"]
        pivot_rows, series_names = _pivot_rows(rows, x_field=x_field, series_field=series_field, value_field=metric_alias)
        if not pivot_rows or not series_names or len(series_names) > 8:
            return None
        categories = [str(row.get(x_field) or "") for row in pivot_rows]
        series = []
        for series_name in series_names:
            series.append(
                {
                    "name": str(series_name),
                    "type": "line" if chart_type == "line" else "bar",
                    "stack": "total" if chart_type == "stacked_bar" else None,
                    "data": [row.get(series_name, 0) for row in pivot_rows],
                }
            )
        for item in series:
            if item.get("stack") is None:
                item.pop("stack", None)
        option = {
            "title": {"text": title},
            "tooltip": {"trigger": "axis"},
            "legend": {"top": 30},
            "xAxis": {"type": "category", "data": categories},
            "yAxis": {"type": "value"},
            "series": series,
        }
        return {
            "renderer": "echarts",
            "chart_type": chart_type,
            "title": title,
            "option": option,
            "frontend_hint": "Render option directly with ECharts on the frontend.",
        }

    categories = [str(row.get(x_field) or "") for row in rows]
    option = {
        "title": {"text": title},
        "tooltip": {"trigger": "axis"},
        "legend": {"top": 30},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": [
            {
                "name": metric_label,
                "type": "line" if chart_type == "line" else "bar",
                "data": [row.get(metric_alias) for row in rows],
            }
        ],
    }
    return {
        "renderer": "echarts",
        "chart_type": chart_type,
        "title": title,
        "option": option,
        "frontend_hint": "Render option directly with ECharts on the frontend.",
    }


def _pivot_rows(rows, *, x_field, series_field, value_field):
    buckets = {}
    x_order = []
    series_order = []
    for row in rows:
        x_value = str(row.get(x_field) or "")
        series_value = str(row.get(series_field) or "")
        if x_value not in buckets:
            buckets[x_value] = {x_field: x_value}
            x_order.append(x_value)
        if series_value and series_value not in series_order:
            series_order.append(series_value)
        if series_value:
            buckets[x_value][series_value] = row.get(value_field)
    return [buckets[item] for item in x_order], series_order


def _summarize_rows(rows, dimension_specs, metric_specs, report_question, truncated=False, limit=None):
    if not rows:
        return "未查询到匹配数据。"
    primary_metric = metric_specs[0] if metric_specs else {"alias": "value", "label": "数值"}
    metric_alias = primary_metric["alias"]
    metric_label = primary_metric["label"]
    truncation_note = f" 结果已截断为前 {int(limit or len(rows))} 行预览。" if truncated else ""
    if not dimension_specs:
        return f"{report_question or '查询结果'}：{metric_label}为 {rows[0].get(metric_alias)}。{truncation_note}".strip()
    first_dimension = dimension_specs[0]
    if len(dimension_specs) == 1:
        leader = rows[0]
        return (
            f"共返回 {len(rows)} 个分组；排名第一的{first_dimension['label']}是 "
            f"{leader.get(first_dimension['alias']) or '未命名'}，{metric_label}为 {leader.get(metric_alias)}。{truncation_note}"
        )
    leader = rows[0]
    second_dimension = dimension_specs[1]
    return (
        f"共返回 {len(rows)} 行交叉结果；首行对应 {first_dimension['label']}={leader.get(first_dimension['alias'])}，"
        f"{second_dimension['label']}={leader.get(second_dimension['alias'])}，{metric_label}为 {leader.get(metric_alias)}。{truncation_note}"
    )


def _sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


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


def _looks_like_missing_column(error_text):
    normalized = str(error_text or "").lower()
    return 'column "' in normalized and "does not exist" in normalized


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


def _execute_sql(sql, max_rows=DEFAULT_LIMIT):
    capped = max(1, min(int(max_rows or DEFAULT_LIMIT), MAX_LIMIT))
    with _connect_postgres() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [item.name for item in cur.description or []]
            fetched = cur.fetchmany(capped + 1)
            truncated = len(fetched) > capped
            rows = [_json_safe_row(dict(zip(columns, row))) for row in fetched[:capped]]
            return rows, columns, truncated


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


def _string_list(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _extract_alias(expression, fallback):
    match = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", str(expression), flags=re.IGNORECASE)
    return match.group(1) if match else fallback


def _metric_alias(metric_key):
    return METRIC_SPECS.get(metric_key, {}).get("alias") or _extract_alias(metric_key, metric_key)


def _dimension_alias(dimension_key):
    return DIMENSION_SPECS.get(dimension_key, {}).get("alias") or _extract_alias(dimension_key, dimension_key)


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def _positive_int_or_none(value):
    if value in (None, "", 0):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(number, 100))


def _optional_bool(value):
    return value if isinstance(value, bool) else None


def _normalize_order_direction(value):
    text = str(value or "").strip().lower()
    return text if text in {"asc", "desc"} else ""
