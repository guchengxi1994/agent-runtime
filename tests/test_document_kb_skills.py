from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def load_skill(path: str):
    source = Path(path)
    spec = importlib.util.spec_from_file_location(source.parent.name.replace("-", "_"), source)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def document_skills(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    workspace_id = "ws_document_test"
    raw = artifacts / "workspaces" / workspace_id / "document_knowledge" / "uploads_raw"
    raw.mkdir(parents=True)
    monkeypatch.setenv("AGENT_RUNTIME_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("AGENT_RUNTIME_WORKSPACE_ID", workspace_id)
    return {
        "artifacts": artifacts,
        "workspace_id": workspace_id,
        "raw": raw,
        "convert": load_skill("registry/skills/document-markdown-convert/skill.py"),
        "ingest": load_skill("registry/skills/document-kb-ingest/skill.py"),
        "read": load_skill("registry/skills/document-kb-read/skill.py"),
        "status": load_skill("registry/skills/document-kb-status/skill.py"),
        "upsert": load_skill("registry/skills/document-kb-upsert/skill.py"),
        "query": load_skill("registry/skills/document-kb-query/skill.py"),
    }


def test_markdown_conversion_then_resumable_knowledge_loop(document_skills):
    raw = document_skills["raw"] / "Steel Handbook.md"
    raw.write_text(
        "# Steelmaking\n\n<!-- source-anchor: page=1 -->\n\n## Oxygen practice\n\nOxygen flow affects decarburization rate.\n\n## Limits\n\nToo much flow can increase slopping.\n",
        encoding="utf-8",
    )
    converted = document_skills["convert"].execute({"source_path": "document_knowledge/uploads_raw/Steel Handbook.md"})
    assert converted["success"] is True
    assert converted["converter"] == "passthrough"
    assert converted["markdown_chars"] > 0
    assert "markdown" not in converted
    assert document_skills["convert"].execute({"source_path": "document_knowledge/uploads_raw/Steel Handbook.md"})["reused"] is True

    ingested = document_skills["ingest"].execute({"source_path": converted["markdown_path"], "document_id": converted["document_id"], "segment_target_chars": 1800})
    assert ingested["success"] is True
    assert ingested["segment_count"] == 1
    assert document_skills["ingest"].execute({"source_path": converted["markdown_path"], "document_id": converted["document_id"]})["reused"] is True

    read = document_skills["read"].execute({"document_id": converted["document_id"], "limit": 2})
    assert read["segments"][0]["source_page_start"] == 1
    segment_id = read["segments"][0]["segment_id"]
    upserted = document_skills["upsert"].execute(
        {
            "document_id": converted["document_id"],
            "processed_segment_ids": [segment_id],
            "nodes": [
                {
                    "type": "claim",
                    "title": "Oxygen flow and decarburization",
                    "statement": "Oxygen flow affects decarburization rate.",
                    "confidence": "explicit",
                    "evidence": [{"segment_id": segment_id, "quote": "Oxygen flow affects decarburization rate.", "page": 1}],
                }
            ],
        }
    )
    assert upserted["success"] is True
    assert upserted["upserted_node_count"] == 1
    status = document_skills["status"].execute({"document_id": converted["document_id"]})
    assert status["coverage"] == 1
    assert status["knowledge_node_count"] == 1
    query = document_skills["query"].execute({"document_id": converted["document_id"], "query": "decarburization"})
    assert query["nodes"][0]["evidence"][0]["segment_id"] == segment_id


def test_conversion_rejects_path_escape_and_changed_source(document_skills):
    raw = document_skills["raw"] / "note.txt"
    raw.write_text("initial", encoding="utf-8")
    converted = document_skills["convert"].execute({"source_path": "document_knowledge/uploads_raw/note.txt", "document_id": "note"})
    assert converted["success"] is True
    raw.write_text("changed", encoding="utf-8")
    changed = document_skills["convert"].execute({"source_path": "document_knowledge/uploads_raw/note.txt", "document_id": "note"})
    assert changed["error_type"] == "source_changed"
    with pytest.raises(ValueError, match="uploads_raw"):
        document_skills["convert"].execute({"source_path": "../note.txt"})


def test_mineru_conversion_persists_metadata_and_assets(document_skills, monkeypatch):
    raw = document_skills["raw"] / "scan.pdf"
    raw.write_bytes(b"%PDF-test")
    monkeypatch.setenv("MINERU_OCR_TOKEN", "test-token")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "markdown": "# Scan\n\n<!-- source-anchor: page=1 -->\n\nRecognized table.",
                "contentList": [{"page_idx": 0}],
                "archiveAssets": {"images/table.png": "data:image/png;base64,aGVsbG8="},
            }

    import requests

    captured = {}

    def post(*args, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(requests, "post", post)
    result = document_skills["convert"].execute({"source_path": "document_knowledge/uploads_raw/scan.pdf", "converter": "mineru", "mineru_url": "http://mineru.test"})
    assert result["success"] is True
    assert result["converter"] == "mineru"
    assert result["asset_count"] == 1
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    root = document_skills["artifacts"] / "workspaces" / document_skills["workspace_id"] / "document_knowledge" / result["document_id"]
    assert (root / "assets" / "images" / "table.png").read_bytes() == b"hello"
    assert json.loads((root / "conversion" / "content_list.json").read_text(encoding="utf-8"))[0]["page_idx"] == 0
