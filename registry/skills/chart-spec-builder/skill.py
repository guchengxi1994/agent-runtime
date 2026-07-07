definition = {
    "name": "chart-spec-builder",
    "description": "从结构化数据行生成面向 ECharts 的图表规格。",
}


def execute(params):
    chart_type = str(params.get("chart_type") or "").strip().lower()
    title = str(params.get("title") or "").strip()
    subtitle = str(params.get("subtitle") or "").strip()
    x_field = str(params.get("x_field") or "").strip()
    y_fields = _string_list(params.get("y_fields"))
    rows = params.get("rows")
    if not chart_type:
        raise ValueError("chart_type is required")
    if not title:
        raise ValueError("title is required")
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows must be a non-empty array")

    if chart_type not in {"line", "bar", "stacked_bar", "pie", "heatmap"}:
        raise ValueError("unsupported chart_type")

    if chart_type in {"line", "bar", "stacked_bar", "heatmap"} and not x_field:
        raise ValueError("x_field is required for this chart_type")
    if chart_type != "pie" and not y_fields:
        raise ValueError("y_fields is required for this chart_type")

    option = _build_option(chart_type, title, subtitle, x_field, y_fields, rows, params.get("series_name_map"))
    return {
        "success": True,
        "renderer": "echarts",
        "chart_type": chart_type,
        "title": title,
        "option": option,
        "data_preview": rows[:10],
        "frontend_hint": "Render option directly with ECharts on the frontend.",
    }


def _build_option(chart_type, title, subtitle, x_field, y_fields, rows, series_name_map):
    series_name_map = series_name_map if isinstance(series_name_map, dict) else {}
    if chart_type == "pie":
        value_field = y_fields[0] if y_fields else "value"
        return {
            "title": {"text": title, "subtext": subtitle, "left": "center"},
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0},
            "series": [
                {
                    "name": title,
                    "type": "pie",
                    "radius": "55%",
                    "data": [
                        {"name": str(row.get(x_field or "name") or ""), "value": row.get(value_field)}
                        for row in rows
                    ],
                }
            ],
        }

    if chart_type == "heatmap":
        y_field = y_fields[0]
        categories_x = [str(row.get(x_field) or "") for row in rows]
        categories_y = [field_name(series_name_map, y_field)]
        return {
            "title": {"text": title, "subtext": subtitle},
            "tooltip": {"position": "top"},
            "xAxis": {"type": "category", "data": categories_x},
            "yAxis": {"type": "category", "data": categories_y},
            "visualMap": {
                "min": 0,
                "max": max(float(row.get(y_field) or 0) for row in rows),
                "calculable": True,
                "orient": "horizontal",
                "left": "center",
                "bottom": 0,
            },
            "series": [
                {
                    "name": field_name(series_name_map, y_field),
                    "type": "heatmap",
                    "data": [[index, 0, row.get(y_field)] for index, row in enumerate(rows)],
                }
            ],
        }

    categories = [str(row.get(x_field) or "") for row in rows]
    series = []
    for field in y_fields:
        series.append(
            {
                "name": field_name(series_name_map, field),
                "type": "line" if chart_type == "line" else "bar",
                "stack": "total" if chart_type == "stacked_bar" else None,
                "data": [row.get(field) for row in rows],
            }
        )
    for item in series:
        if item.get("stack") is None:
            item.pop("stack", None)
    return {
        "title": {"text": title, "subtext": subtitle},
        "tooltip": {"trigger": "axis"},
        "legend": {"top": 30},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": series,
    }


def field_name(series_name_map, field):
    return str(series_name_map.get(field) or field)


def _string_list(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result
