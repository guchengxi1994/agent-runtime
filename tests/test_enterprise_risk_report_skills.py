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
    assert "enterprise-risk-report-writer" in registry.skills
    assert registry.skills["enterprise-risk-report-writer"].executable is False
    assert "pg-table-profile" in registry.skills
    assert registry.skills["pg-table-profile"].executable is True
    assert "pg-risk-dataset-sync" in registry.skills
    assert registry.skills["pg-risk-dataset-sync"].executable is True
    assert any(resource.path.endswith(".xlsx") for resource in registry.skills["pg-risk-dataset-sync"].resources)
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
    assert "enterprise-risk-report-writer" in set(agent.skill_ids or [])
    assert "pg-table-profile" in set(agent.skill_ids or [])
    assert "pg-risk-dataset-sync" in set(agent.skill_ids or [])
    assert "case-library-review" in set(agent.skill_ids or [])
    assert "pg-case-search" in set(agent.skill_ids or [])
    assert "pg-report-query" in set(agent.skill_ids or [])
    assert "chart-spec-builder" in set(agent.skill_ids or [])


def test_enterprise_risk_report_writer_has_style_reference():
    skill_path = Path("registry/skills/enterprise-risk-report-writer/SKILL.md")
    reference_path = Path("registry/skills/enterprise-risk-report-writer/references/style-profile.md")

    assert skill_path.is_file()
    assert reference_path.is_file()
    writer_text = skill_path.read_text(encoding="utf-8")
    assert "style-profile.md" in writer_text
    assert "不要在开头追问目标企业" in writer_text


def test_enterprise_risk_report_defaults_to_district_wide_scope():
    skill_text = Path("registry/skills/enterprise-risk-report/SKILL.md").read_text(encoding="utf-8")

    assert "默认将任务理解为**天宁区全区企业涉法涉诉案件分析报告**" in skill_text
    assert "不要追问目标企业" in skill_text


def test_pg_skills_ship_env_examples():
    assert Path("registry/skills/pg-risk-dataset-sync/.env.example").is_file()
    assert Path("registry/skills/pg-report-query/.env.example").is_file()
    assert Path("registry/skills/pg-case-search/.env.example").is_file()
    assert Path("registry/skills/pg-table-profile/.env.example").is_file()


def test_pg_report_query_returns_query_plan():
    module = load_skill_module(Path("registry/skills/pg-report-query/skill.py"))

    result = module.execute(
        {
            "report_question": "统计2025年以来按月份和案件事项类型分布的案件数量",
            "metrics": ["count(*) as case_count"],
            "dimensions": ["month", "risk_type"],
            "filters": {"region": "天宁区"},
            "table_hints": ["enterprise_risk_events"],
        }
    )

    assert result["success"] is True
    assert result["mode"] == "plan_only"
    assert "FROM enterprise_risk_events" in result["sql"]
    assert "GROUP BY 1, 2" in result["sql"]
    assert "region = '天宁区'" not in result["sql"]
    assert result["filters"] == {"accepted_date_from": "2025-01-01"}


def test_pg_report_query_treats_tianning_as_dataset_scope_and_aggregates_region():
    module = load_skill_module(Path("registry/skills/pg-report-query/skill.py"))

    result = module.execute(
        {
            "report_question": "统计2025年天宁区各街道行政处罚数量并生成柱状图",
        }
    )

    assert result["success"] is True
    assert result["mode"] == "plan_only"
    assert result["dimensions"] == ["region"]
    assert result["filters"]["event_source"] == "administrative_penalty"
    assert result["filters"]["year"] == 2025
    assert "region ILIKE" not in result["sql"]
    assert "region = '天宁区'" not in result["sql"]
    assert "GROUP BY 1" in result["sql"]
    assert any("dataset scope" in note for note in result["planning_notes"])


def test_pg_report_query_infers_aggregation_and_chart_plan_from_question():
    module = load_skill_module(Path("registry/skills/pg-report-query/skill.py"))

    result = module.execute(
        {
            "report_question": "统计2025年行政处罚最多的行业前三名，并生成柱状图",
        }
    )

    assert result["success"] is True
    assert result["mode"] == "plan_only"
    assert result["dimensions"] == ["industry"]
    assert result["metrics"][0] == "case_count"
    assert result["filters"]["event_source"] == "administrative_penalty"
    assert result["filters"]["year"] == 2025
    assert result["top_n"] == 3
    assert result["order_by"] == "case_count"
    assert result["chart_plan"]["enabled"] is True
    assert result["chart_plan"]["chart_type"] == "bar"
    assert "event_source = 'administrative_penalty'" in result["sql"]
    assert "EXTRACT(YEAR FROM accepted_date) = 2025" in result["sql"]
    assert "LIMIT 3;" in result["sql"]
    assert result["recommended_preflight_skill"] == "pg-table-profile"


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
    assert "FROM enterprise_risk_events" in result["sql"]
    assert "illegal_subcontracting" in result["sql"]
    assert "region = '常州'" not in result["sql"]
    assert result["filters"] == {"year": "2025"}
    assert any("违规分包导致工程款回收风险" in item for item in result["shortlist_guidance"])


def test_pg_case_search_infers_limit_from_question():
    module = load_skill_module(Path("registry/skills/pg-case-search/skill.py"))

    result = module.execute({"query": "前3个最新的行政处罚案例"})

    assert result["success"] is True
    assert result["limit"] == 3


def test_pg_table_profile_returns_schema_summary(monkeypatch):
    module = load_skill_module(Path("registry/skills/pg-table-profile/skill.py"))

    monkeypatch.setattr(
        module,
        "_profile_table",
        lambda **_: {
            "table_exists": True,
            "table_name": "enterprise_risk_events",
            "table_schema": "public",
            "qualified_table": "public.enterprise_risk_events",
            "row_count": 11107,
            "dimension_candidates": ["event_source", "region", "industry", "accepted_date"],
            "time_candidates": ["accepted_date"],
            "filterable_enums": {
                "event_source": ["judicial_case", "administrative_penalty"],
                "region": ["天宁街道", "雕庄街道", "青龙街道"],
            },
            "column_profiles": [
                {
                    "column_name": "event_source",
                    "data_type": "text",
                    "udt_name": "text",
                    "nullable": False,
                    "non_null_count": 11107,
                    "distinct_count": 2,
                    "enum_values": ["judicial_case", "administrative_penalty"],
                    "top_values": [],
                    "min_value": None,
                    "max_value": None,
                    "dimension_candidate": True,
                    "time_candidate": False,
                    "notes": ["low_cardinality_enum", "dimension_candidate"],
                },
                {
                    "column_name": "accepted_date",
                    "data_type": "date",
                    "udt_name": "date",
                    "nullable": True,
                    "non_null_count": 11107,
                    "distinct_count": None,
                    "enum_values": [],
                    "top_values": [],
                    "min_value": "2025-01-02",
                    "max_value": "2025-12-30",
                    "dimension_candidate": True,
                    "time_candidate": True,
                    "notes": ["dimension_candidate", "time_candidate"],
                },
            ],
        },
    )

    result = module.execute({"table_name": "enterprise_risk_events"})

    assert result["success"] is True
    assert result["mode"] == "profiled"
    assert result["table_name"] == "enterprise_risk_events"
    assert result["dimension_candidates"][:2] == ["event_source", "region"]
    assert result["filterable_enums"]["event_source"] == ["judicial_case", "administrative_penalty"]
    assert "Do not invent columns" in result["llm_context"]
    assert result["rows"][0]["column_name"] == "event_source"


def test_pg_report_query_returns_schema_mismatch_for_missing_columns(monkeypatch):
    module = load_skill_module(Path("registry/skills/pg-report-query/skill.py"))

    monkeypatch.setattr(module, "_dataset_status", lambda: {"table_exists": True, "row_count": 100, "table_name": "enterprise_risk_events"})
    monkeypatch.setattr(
        module,
        "_execute_sql",
        lambda _sql, max_rows=None: (_ for _ in ()).throw(Exception('column "event_category" does not exist')),
    )

    result = module.execute(
        {
            "report_question": "测试查询",
            "sql": "SELECT event_category, COUNT(*) FROM enterprise_risk_events GROUP BY 1;",
            "execute": True,
        }
    )

    assert result["success"] is False
    assert result["mode"] == "schema_mismatch"
    assert result["error_type"] == "schema_mismatch"
    assert result["recommended_next_skill"] == "pg-table-profile"


def test_pg_report_query_marks_truncated_results(monkeypatch):
    module = load_skill_module(Path("registry/skills/pg-report-query/skill.py"))

    monkeypatch.setattr(module, "_dataset_status", lambda: {"table_exists": True, "row_count": 1000, "table_name": "enterprise_risk_events"})
    monkeypatch.setattr(
        module,
        "_execute_sql",
        lambda _sql, max_rows=None: (
            [{"month": "2025-01", "case_count": 10}, {"month": "2025-02", "case_count": 12}],
            ["month", "case_count"],
            True,
        ),
    )

    result = module.execute(
        {
            "report_question": "统计2025年按月案件数量",
            "dimensions": ["month"],
            "metrics": ["case_count"],
            "execute": True,
            "limit": 2,
        }
    )

    assert result["success"] is True
    assert result["mode"] == "executed"
    assert result["truncated"] is True
    assert result["requested_limit"] == 2
    assert "结果已截断为前 2 行预览" in result["summary"]


def test_pg_risk_dataset_sync_dry_run_reads_bundled_excel_files(monkeypatch):
    module = load_skill_module(Path("registry/skills/pg-risk-dataset-sync/skill.py"))
    monkeypatch.delenv("PG_RISK_DATA_DIR", raising=False)

    result = module.execute({"dry_run": True})

    assert result["success"] is True
    assert result["mode"] == "dry_run"
    assert result["files_seen"] == 4
    assert result["rows_read"] == 11108
    assert result["distinct_rows"] == 11107
    assert result["duplicate_rows_in_input"] == 1
    assert any(item["event_source"] == "judicial_case" for item in result["file_summaries"])
    assert any(item["event_source"] == "administrative_penalty" for item in result["file_summaries"])


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
