from __future__ import annotations

import importlib.util
from pathlib import Path

from agent_runtime.registry import FileRegistry


def load_skill_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_enterprise_risk_report_registry_items_exist():
    registry = FileRegistry(Path("registry").resolve())
    registry.reload()

    assert "enterprise-risk-report" in registry.skills
    assert registry.skills["enterprise-risk-report"].executable is False
    assert "case-library-review" in registry.skills
    assert registry.skills["case-library-review"].executable is False
    assert "pg-case-search" in registry.skills
    assert registry.skills["pg-case-search"].executable is True
    assert "pg-report-query" in registry.skills
    assert registry.skills["pg-report-query"].executable is True
    assert "chart-spec-builder" in registry.skills
    assert registry.skills["chart-spec-builder"].executable is True

    agent = registry.get_agent("enterprise_risk_report_analyst")
    assert "enterprise-risk-report" in set(agent.skill_ids or [])
    assert "case-library-review" in set(agent.skill_ids or [])
    assert "pg-case-search" in set(agent.skill_ids or [])
    assert "pg-report-query" in set(agent.skill_ids or [])
    assert "chart-spec-builder" in set(agent.skill_ids or [])


def test_pg_report_query_returns_query_plan():
    module = load_skill_module(Path("registry/skills/pg-report-query/skill.py"))

    result = module.execute(
        {
            "report_question": "统计2025年以来按月份和风险类型分布的案件数量",
            "metrics": ["count(*) as case_count"],
            "dimensions": ["month", "risk_type"],
            "filters": {"region": "天宁区"},
            "table_hints": ["enterprise_risk_events"],
        }
    )

    assert result["success"] is True
    assert result["mode"] == "plan_only"
    assert "FROM enterprise_risk_events" in result["sql"]
    assert "GROUP BY month, risk_type" in result["sql"]


def test_pg_case_search_returns_retrieval_plan():
    module = load_skill_module(Path("registry/skills/pg-case-search/skill.py"))

    result = module.execute(
        {
            "query": "违规分包导致工程款回收风险",
            "tags": ["illegal_subcontracting", "payment_recovery"],
            "filters": {"region": "常州", "year": "2025"},
            "limit": 5,
        }
    )

    assert result["success"] is True
    assert result["mode"] == "plan_only"
    assert "FROM case_library" in result["sql"]
    assert "illegal_subcontracting" in result["sql"]
    assert any("违规分包导致工程款回收风险" in item for item in result["shortlist_guidance"])


def test_chart_spec_builder_returns_echarts_option():
    module = load_skill_module(Path("registry/skills/chart-spec-builder/skill.py"))

    result = module.execute(
        {
            "chart_type": "line",
            "title": "月度案件趋势",
            "x_field": "month",
            "y_fields": ["case_count"],
            "rows": [
                {"month": "2026-01", "case_count": 12},
                {"month": "2026-02", "case_count": 18},
                {"month": "2026-03", "case_count": 15},
            ],
            "series_name_map": {"case_count": "案件数"},
        }
    )

    assert result["success"] is True
    assert result["renderer"] == "echarts"
    assert result["option"]["xAxis"]["data"] == ["2026-01", "2026-02", "2026-03"]
    assert result["option"]["series"][0]["name"] == "案件数"
