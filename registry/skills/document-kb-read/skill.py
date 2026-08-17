definition = {"name": "document-kb-read", "description": "Read a bounded batch of stable Markdown source segments."}

import os
import re
import sqlite3
from pathlib import Path


def execute(params):
    root = _document_root(params["document_id"])
    requested = [str(item).strip() for item in params.get("segment_ids") or [] if str(item).strip()]
    limit = max(1, min(int(params.get("limit") or 2), 8))
    conn = sqlite3.connect(root / "index.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        if requested:
            placeholders = ",".join("?" for _ in requested[:limit])
            rows = conn.execute(f"SELECT * FROM segment WHERE segment_id IN ({placeholders}) ORDER BY ordinal", requested[:limit]).fetchall()
            missing = sorted(set(requested[:limit]) - {row["segment_id"] for row in rows})
        else:
            after = str(params.get("after_segment_id") or "").strip()
            ordinal = conn.execute("SELECT ordinal FROM segment WHERE segment_id = ?", (after,)).fetchone() if after else None
            rows = conn.execute("SELECT * FROM segment WHERE ordinal > ? ORDER BY ordinal LIMIT ?", (ordinal[0] if ordinal else 0, limit)).fetchall()
            missing = []
        segments = [
            {
                "segment_id": row["segment_id"],
                "ordinal": row["ordinal"],
                "heading_path": [item.strip() for item in row["heading_path"].split(" > ") if item.strip()],
                "source_page_start": row["page_start"],
                "source_page_end": row["page_end"],
                "content": row["content"],
            }
            for row in rows
        ]
        return {"success": True, "document_id": str(params["document_id"]), "segments": segments, "missing_segment_ids": missing, "next_after_segment_id": segments[-1]["segment_id"] if segments else None}
    finally:
        conn.close()


def _document_root(raw_id):
    workspace_id = _sanitize(os.getenv("AGENT_RUNTIME_WORKSPACE_ID") or "")
    document_id = _sanitize(raw_id)
    artifacts = os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR")
    if not artifacts or not workspace_id:
        raise ValueError("workspace sandbox context is required")
    root = Path(artifacts).resolve() / "workspaces" / workspace_id / "document_knowledge" / document_id
    if not (root / "index.sqlite").is_file():
        raise ValueError(f"Document knowledge base not found: {document_id}")
    return root


def _sanitize(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip()).strip("-_").lower()[:80]
