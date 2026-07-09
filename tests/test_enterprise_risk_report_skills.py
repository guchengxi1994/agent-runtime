from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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
    assert "qwen-web-analysis" in registry.skills
    assert registry.skills["qwen-web-analysis"].executable is True

    agent = registry.get_agent("enterprise_risk_report_analyst")
    assert "enterprise-risk-report" in set(agent.skill_ids or [])
    assert "enterprise-risk-report-writer" in set(agent.skill_ids or [])
    assert "pg-table-profile" in set(agent.skill_ids or [])
    assert "pg-risk-dataset-sync" in set(agent.skill_ids or [])
    assert "case-library-review" in set(agent.skill_ids or [])
    assert "pg-case-search" in set(agent.skill_ids or [])
    assert "pg-report-query" in set(agent.skill_ids or [])
    assert "chart-spec-builder" in set(agent.skill_ids or [])
    assert "qwen-web-analysis" in set(agent.skill_ids or [])
    assert "web-search" not in set(agent.skill_ids or [])
    assert "web-fetch" not in set(agent.skill_ids or [])


def test_enterprise_risk_report_writer_has_style_reference():
    skill_path = Path("registry/skills/enterprise-risk-report-writer/SKILL.md")
    reference_path = Path("registry/skills/enterprise-risk-report-writer/references/style-profile.md")

    assert skill_path.is_file()
    assert reference_path.is_file()
    writer_text = skill_path.read_text(encoding="utf-8")
    assert "style-profile.md" in writer_text
    assert "不要在开头追问目标企业" in writer_text
    assert "联网分析只是补强项，不是必需项" in writer_text
    assert "正文不写未纳入清单" in writer_text
    assert "不要单列“数据局限说明”" in writer_text
    assert "风险成因深层分析" in writer_text
    assert "治理意见建议" in writer_text
    assert "检察机关/检察院视角" in writer_text
    assert "党委政府主责" in writer_text
    assert "检察履职协同" in writer_text


def test_enterprise_risk_report_defaults_to_district_wide_scope():
    skill_text = Path("registry/skills/enterprise-risk-report/SKILL.md").read_text(encoding="utf-8")

    assert "默认将任务理解为**天宁区全区企业涉法涉诉案件分析报告**" in skill_text
    assert "不要追问目标企业" in skill_text


def test_pg_skills_ship_env_examples():
    assert Path("registry/skills/pg-risk-dataset-sync/.env.example").is_file()
    assert Path("registry/skills/pg-report-query/.env.example").is_file()
    assert Path("registry/skills/pg-case-search/.env.example").is_file()
    assert Path("registry/skills/pg-table-profile/.env.example").is_file()
    assert Path("registry/skills/qwen-web-analysis/.env.example").is_file()


def test_enterprise_risk_report_limits_network_analysis_usage():
    skill_text = Path("registry/skills/enterprise-risk-report/SKILL.md").read_text(encoding="utf-8")

    assert "整份报告通常最多调用 2 次" in skill_text
    assert "每次都必须显式指定模式" in skill_text
    assert "不要再串联 `web-search`、`web-fetch`" in skill_text
    assert "直接忽略，不要阻塞报告成稿" in skill_text
    assert "只有同时满足以下条件时才应触发" in skill_text
    assert "出现以下情况时不应触发" in skill_text
    assert "mode=recommend" in skill_text
    assert "如果最终成稿保留 `风险成因深层分析` 章节" in skill_text
    assert "如果最终成稿保留 `治理意见建议` 章节" in skill_text


def test_enterprise_risk_report_avoids_limitations_and_planning_sections():
    harness_text = Path("registry/skills/enterprise-risk-report/SKILL.md").read_text(encoding="utf-8")
    writer_text = Path("registry/skills/enterprise-risk-report-writer/SKILL.md").read_text(encoding="utf-8")
    reference_text = Path("registry/skills/enterprise-risk-report/references/report-structure.md").read_text(encoding="utf-8")

    assert "不要单列“数据局限说明”" in harness_text
    assert "不要单列“数据局限说明”" in writer_text
    assert "不要默认单列“数据局限说明”" in reference_text
    assert "后续规划" in writer_text
    assert "风险成因深层分析" in reference_text
    assert "治理意见建议" in reference_text


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


def test_chart_spec_builder_infers_fields_for_pie_rows():
    module = load_skill_module(Path("registry/skills/chart-spec-builder/skill.py"))

    result = module.execute(
        {
            "chart_type": "pie",
            "title": "企业规模分布",
            "rows": [
                {"company_size": "微型", "cnt": 3984},
                {"company_size": "小型", "cnt": 3195},
                {"company_size": "中型", "cnt": 1645},
            ],
        }
    )

    assert result["success"] is True
    assert result["resolved_fields"]["x_field"] == "company_size"
    assert result["resolved_fields"]["y_fields"] == ["cnt"]
    assert result["option"]["series"][0]["data"][0]["value"] == 3984


def test_chart_spec_builder_reports_missing_numeric_field_for_pie():
    module = load_skill_module(Path("registry/skills/chart-spec-builder/skill.py"))

    try:
        module.execute(
            {
                "chart_type": "pie",
                "title": "无效饼图",
                "rows": [
                    {"industry": "批发业", "label": "高"},
                    {"industry": "房地产业", "label": "中"},
                ],
            }
        )
    except ValueError as exc:
        assert "y_fields is required for pie charts" in str(exc)
    else:
        raise AssertionError("expected pie chart field inference to fail when no numeric column exists")


def test_qwen_web_analysis_requires_model_env(monkeypatch):
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        module.execute({"question": "为什么某类行业更集中"})


def test_qwen_web_analysis_prompt_constrains_to_one_core_question():
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))

    prompt = module.build_analysis_prompt(
        mode="explain",
        question="为什么某类行业更集中，这对治理资源配置意味着什么？",
        evidence_summary="行业前五合计占比超过六成，头部行业明显集中。",
        recommendation_focus="",
        institution_perspective="procuratorate",
        analysis_scope="天宁区企业涉法涉诉案件",
        region="天宁区",
        time_range="2025年",
        answer_language="zh-CN",
    )

    assert "本次只围绕一个核心问题展开" in prompt
    assert "不要拆成多个并列搜索任务" in prompt
    assert "不要把已经给出的数字、排序、占比重新上网核对一遍" in prompt
    assert "每个部分尽量写成 1-2 个完整自然段" in prompt


def test_qwen_web_analysis_recommend_mode_prompt_is_explicit():
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))

    prompt = module.build_analysis_prompt(
        mode="recommend",
        question="针对天宁区涉企案件最集中的商贸服务业，下一步有哪些可借鉴治理做法？",
        evidence_summary="商贸服务业涉企案件数量居首，头部集中度明显。",
        recommendation_focus="商贸服务业高频涉诉治理",
        institution_perspective="procuratorate",
        analysis_scope="天宁区企业涉法涉诉案件",
        region="天宁区",
        time_range="2025年",
        answer_language="zh-CN",
    )

    assert "分析模式：recommend" in prompt
    assert "党委政府主责" in prompt
    assert "检察履职协同" in prompt
    assert "建议聚焦：商贸服务业高频涉诉治理" in prompt
    assert "机构口径：procuratorate" in prompt


def test_qwen_web_analysis_uses_faster_default_timeout(monkeypatch):
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_RUNTIME_MODEL", "qwen-plus")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.delenv("QWEN_WEB_ANALYSIS_MODEL_TIMEOUT_SECONDS", raising=False)

    config = module.load_model_config()

    assert config["timeout_seconds"] == 75


def test_qwen_web_analysis_rejects_invalid_mode():
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))

    with pytest.raises(ValueError, match="mode must be one of"):
        module.execute({"mode": "auto", "question": "test"})


def test_qwen_web_analysis_rejects_invalid_institution_perspective():
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))

    with pytest.raises(ValueError, match="institution_perspective must be one of"):
        module.execute({"mode": "recommend", "question": "test", "institution_perspective": "custom"})


def test_qwen_web_analysis_truncates_long_inputs():
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))

    question, question_truncated = module.truncate_text("q" * 700, module.MAX_QUESTION_CHARS)
    evidence, evidence_truncated = module.truncate_text("e" * 5000, module.MAX_EVIDENCE_CHARS)

    assert question_truncated is True
    assert evidence_truncated is True
    assert len(question) == module.MAX_QUESTION_CHARS
    assert len(evidence) == module.MAX_EVIDENCE_CHARS
    assert question.endswith("…")
    assert evidence.endswith("…")


def test_qwen_web_analysis_executes_with_mocked_search_and_model(monkeypatch):
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_RUNTIME_MODEL", "qwen-plus")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")

    monkeypatch.setattr(
        module,
        "call_dashscope_native_search",
        lambda *args, **kwargs: {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "## 核心判断\n天宁街道案件压力更高，主要与企业密度、商事活动强度和纠纷暴露渠道集中有关。[1]\n\n## 治理启示\n应优先向高负荷板块倾斜商事调解和法治服务资源。[1]",
                        "annotations": [
                            {
                                "title": "天宁区营商环境观察",
                                "url": "https://example.com/a",
                                "description": "核心商圈企业密集、商事活动频繁，纠纷更易集中。",
                            }
                        ],
                    },
                }
            ]
        },
    )

    result = module.execute(
        {
            "mode": "explain",
            "question": "为什么天宁街道案件压力更高，这对治理资源配置意味着什么？",
            "evidence_summary": "天宁街道案件 4066 条，占比 36.6%，显著高于其他板块。",
            "analysis_scope": "天宁区企业涉法涉诉案件",
            "region": "天宁区",
            "time_range": "2020-12 至 2026-01",
        }
    )

    assert result["success"] is True
    assert result["mode"] == "dashscope_native_search"
    assert result["analysis_mode"] == "explain"
    assert result["search_enabled"] is True
    assert "核心判断" in result["answer_markdown"]
    assert "参考来源" in result["answer_markdown"]
    assert result["sources"][0]["url"] == "https://example.com/a"
    assert result["timings_ms"]["total"] >= 0
    assert result["finish_reason"] == "stop"


def test_qwen_web_analysis_uses_dashscope_native_search_flag(monkeypatch):
    module = load_skill_module(Path("registry/skills/qwen-web-analysis/skill.py"))

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    payload = module.call_dashscope_native_search(
        {
            "api_key": "test-key",
            "model": "qwen-plus",
            "base_url": "https://example.com/v1",
            "timeout_seconds": 90,
        },
        messages=[{"role": "user", "content": "test"}],
        temperature=0.3,
        max_tokens=1000,
    )

    assert payload["choices"][0]["message"]["content"] == "ok"
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["json"]["enable_search"] is True
