from __future__ import annotations

import importlib.util
import json
import sqlite3
import csv
from pathlib import Path

from agent_runtime.models import SkillExecutionContext


def load_skill_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_workspace_scoped_ontology_runtime_loop(tmp_path, monkeypatch):
    artifacts_dir = tmp_path / "artifacts"
    workspace_id = "ws_industrial"
    workspace_root = artifacts_dir / "workspaces" / workspace_id
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_RUNTIME_ARTIFACTS_DIR", str(artifacts_dir))
    monkeypatch.setenv("AGENT_RUNTIME_WORKSPACE_ID", workspace_id)

    upsert_module = load_skill_module(Path("registry/skills/ontology-registry-upsert/skill.py"))
    resolve_module = load_skill_module(Path("registry/skills/ontology-runtime-resolve/skill.py"))
    query_module = load_skill_module(Path("registry/skills/timeseries-query-sql/skill.py"))
    diagnose_module = load_skill_module(Path("registry/skills/energy-anomaly-diagnose/skill.py"))

    ontology = json.loads(Path("tests/fixtures/industrial_ontology/existing_ontology_fragment.json").read_text(encoding="utf-8"))
    schema_sql = Path("tests/fixtures/industrial_ontology/manufacturing_mes_schema.sql").read_text(encoding="utf-8")
    tag_csv = Path("tests/fixtures/industrial_ontology/line3_tags.csv").read_text(encoding="utf-8")
    timeseries_rows = load_csv_rows(Path("tests/fixtures/industrial_ontology/line3_power_timeseries.csv"))
    event_rows = load_csv_rows(Path("tests/fixtures/industrial_ontology/line3_alarm_events.csv"))

    upsert_result = upsert_module.execute(
        {
            "ontology": ontology,
            "schema_sql": schema_sql,
            "tag_csv": tag_csv,
            "timeseries_rows": timeseries_rows,
            "event_rows": event_rows,
        }
    )
    assert upsert_result["success"] is True
    db_path = Path(upsert_result["db_path"])
    assert db_path.is_file()

    conn = sqlite3.connect(db_path)
    try:
        object_count = conn.execute("SELECT COUNT(*) FROM ontology_object").fetchone()[0]
        instance_count = conn.execute("SELECT COUNT(*) FROM entity_instance").fetchone()[0]
        timeseries_count = conn.execute("SELECT COUNT(*) FROM timeseries_sample").fetchone()[0]
    finally:
        conn.close()
    assert object_count >= 3
    assert instance_count >= 3
    assert timeseries_count == 6

    resolve_result = resolve_module.execute({"entity_ref": "MachineA", "metric_name": "power_kw"})
    assert resolve_result["success"] is True
    assert resolve_result["best_match"]["entity_ref"] == "machine::machinea"
    assert resolve_result["resolved_mappings"]

    baseline_window = query_module.execute(
        {
            "entity_ref": "machine::machinea",
            "metric_name": "power_kw",
            "start_time": "2026-07-02T14:00:00+00:00",
            "end_time": "2026-07-02T16:59:59+00:00",
            "aggregate": "raw",
        }
    )
    current_window = query_module.execute(
        {
            "entity_ref": "machine::machinea",
            "metric_name": "power_kw",
            "start_time": "2026-07-03T14:00:00+00:00",
            "end_time": "2026-07-03T16:59:59+00:00",
            "aggregate": "raw",
            "include_events": True,
        }
    )
    assert baseline_window["success"] is True
    assert baseline_window["row_count"] == 3
    assert current_window["success"] is True
    assert current_window["row_count"] == 3
    assert len(current_window["events"]) == 1

    diagnosis = diagnose_module.execute(
        {
            "entity_ref": "machine::machinea",
            "metric_name": "power_kw",
            "current_window": current_window,
            "baseline_window": baseline_window,
            "context": resolve_result,
        }
    )
    assert diagnosis["success"] is True
    assert diagnosis["severity"] in {"medium", "high"}
    assert diagnosis["delta"]["avg_ratio"] > 1.3
    assert any(item["type"] == "event_correlation" for item in diagnosis["findings"])


def test_skill_execution_context_includes_workspace_id():
    context = SkillExecutionContext(
        agent_id="default",
        conversation_id="conv_test",
        workspace_id="ws_test",
        user_id="anonymous",
        run_id="run_test",
        tool_call_id="call_test",
    )

    assert context.model_dump()["workspace_id"] == "ws_test"
    assert context.model_dump()["tool_call_id"] == "call_test"


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
