from __future__ import annotations

import json
from pathlib import Path

import yaml


FIXTURE_DIR = Path("tests/fixtures/industrial_ontology")


def test_industrial_ontology_fixture_files_exist():
    expected = {
        "manufacturing_mes_schema.sql",
        "device_service_openapi.yaml",
        "line3_tags.csv",
        "maintenance_sop_excerpt.md",
        "existing_ontology_fragment.json",
    }
    found = {path.name for path in FIXTURE_DIR.iterdir() if path.is_file()}

    assert expected.issubset(found)


def test_industrial_ontology_fixture_content_is_parseable():
    sql_text = FIXTURE_DIR.joinpath("manufacturing_mes_schema.sql").read_text(encoding="utf-8")
    api_doc = yaml.safe_load(FIXTURE_DIR.joinpath("device_service_openapi.yaml").read_text(encoding="utf-8"))
    ontology = json.loads(FIXTURE_DIR.joinpath("existing_ontology_fragment.json").read_text(encoding="utf-8"))
    tags_csv = FIXTURE_DIR.joinpath("line3_tags.csv").read_text(encoding="utf-8")

    assert "CREATE TABLE machine" in sql_text
    assert "/api/v1/machines/{machineId}/start" in api_doc["paths"]
    assert any(obj["name"] == "Machine" for obj in ontology["objects"])
    assert "Line3.MachineA.Temp" in tags_csv
