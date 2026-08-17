definition = {"name": "document-kb-status", "description": "Report resumable document knowledge extraction progress."}

import json
import os
import re
import sqlite3
from pathlib import Path


def execute(params):
    root = _document_root(params["document_id"])
    manifest = _read_json(root / "manifest.json")
    progress = _read_json(root / "progress.json") or {}
    completed = set(progress.get("completed_segment_ids") or [])
    failed = progress.get("failed_segments") or []
    limit = max(1, min(int(params.get("pending_limit") or 20), 100))
    segment_ids = [row[0] for row in sqlite3.connect(root / "index.sqlite").execute("SELECT segment_id FROM segment ORDER BY ordinal")]
    pending = [segment_id for segment_id in segment_ids if segment_id not in completed][:limit]
    node_count = sqlite3.connect(root / "index.sqlite").execute("SELECT COUNT(*) FROM knowledge_node").fetchone()[0]
    return {
        "success": True,
        "document_id": manifest["document_id"],
        "title": manifest["title"],
        "segment_count": len(segment_ids),
        "completed_segment_count": len(completed),
        "pending_segment_count": max(0, len(segment_ids) - len(completed)),
        "pending_segment_ids": pending,
        "failed_segments": failed[-20:],
        "knowledge_node_count": node_count,
        "coverage": round(len(completed) / len(segment_ids), 4) if segment_ids else 0,
    }


def _document_root(raw_id):
    workspace_id = _sanitize(os.getenv("AGENT_RUNTIME_WORKSPACE_ID") or "")
    document_id = _sanitize(raw_id)
    artifacts = os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR")
    if not artifacts or not workspace_id:
        raise ValueError("workspace sandbox context is required")
    root = Path(artifacts).resolve() / "workspaces" / workspace_id / "document_knowledge" / document_id
    if not (root / "manifest.json").is_file():
        raise ValueError(f"Document knowledge base not found: {document_id}")
    return root


def _sanitize(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip()).strip("-_").lower()[:80]


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
