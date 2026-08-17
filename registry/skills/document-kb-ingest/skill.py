definition = {
    "name": "document-kb-ingest",
    "description": "Create stable Markdown segments and a workspace-scoped full-text index for a document knowledge base.",
}

import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MAX_SOURCE_CHARS = 20_000_000


def execute(params):
    workspace_id = _workspace_id()
    source = _resolve_source(params["source_path"], workspace_id)
    raw = source.read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError("source_path contains no Markdown content")
    if len(raw) > MAX_SOURCE_CHARS:
        raise ValueError(f"source Markdown exceeds {MAX_SOURCE_CHARS} characters")
    source_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    document_id = _document_id(params.get("document_id") or source.stem)
    root = _documents_root(workspace_id) / document_id
    manifest_path = root / "manifest.json"
    force_rebuild = bool(params.get("force_rebuild"))
    existing = _read_json(manifest_path)
    if existing and existing.get("source_hash") == source_hash and not force_rebuild:
        return _result(existing, reused=True)
    if existing and existing.get("source_hash") != source_hash and not force_rebuild:
        return {
            "success": False,
            "error_type": "source_changed",
            "error": "The Markdown source changed after this document was indexed.",
            "document_id": document_id,
            "retry_guidance": "Ask the user to confirm a rebuild, then call again with force_rebuild=true. This replaces prior extracted knowledge.",
        }

    target = max(1800, min(int(params.get("segment_target_chars") or 7000), 18000))
    segments = _segment_markdown(raw, target)
    if not segments:
        raise ValueError("No readable segments were created from Markdown")
    if root.exists() and force_rebuild:
        shutil.rmtree(root)
    (root / "source").mkdir(parents=True, exist_ok=True)
    (root / "source" / "document.md").write_text(raw, encoding="utf-8")
    _write_jsonl(root / "segments.jsonl", segments)
    _init_index(root / "index.sqlite", segments)
    manifest = {
        "schema_version": 1,
        "document_id": document_id,
        "workspace_id": workspace_id,
        "title": str(params.get("title") or source.stem).strip() or source.stem,
        "parser": str(params.get("parser") or "unknown").strip() or "unknown",
        "source_path": str(source.relative_to(_workspace_root(workspace_id))),
        "source_hash": source_hash,
        "source_chars": len(raw),
        "segment_target_chars": target,
        "segment_count": len(segments),
        "created_at": _now(),
        "updated_at": _now(),
    }
    _write_json(manifest_path, manifest)
    _write_json(root / "progress.json", {"completed_segment_ids": [], "failed_segments": [], "updated_at": _now()})
    _write_overview(root, manifest, segments)
    return _result(manifest, reused=False)


def _segment_markdown(markdown, target):
    blocks = _markdown_blocks(markdown)
    segments, buffer, heading_path, page_start, page_end, start = [], [], [], None, None, 0
    size = 0
    for block in blocks:
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", block["text"].strip())
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            heading_path = heading_path[: level - 1] + [title]
        page = _page_anchor(block["text"])
        if page is not None:
            page_start = page if page_start is None else page_start
            page_end = page
        block_size = len(block["text"])
        if buffer and size + block_size > target and (heading or size >= target * 0.7):
            segments.append(_make_segment(len(segments) + 1, buffer, start, block["start"], heading_path, page_start, page_end))
            buffer, size, start, page_start, page_end = [], 0, block["start"], page, page
        if not buffer:
            start = block["start"]
        buffer.append(block["text"])
        size += block_size
    if buffer:
        segments.append(_make_segment(len(segments) + 1, buffer, start, blocks[-1]["end"], heading_path, page_start, page_end))
    return segments


def _markdown_blocks(markdown):
    blocks, current, start, position, fenced = [], [], 0, 0, False
    for line in markdown.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
        if not fenced and not stripped and current:
            text = "".join(current).strip()
            if text:
                blocks.append({"text": text + "\n\n", "start": start, "end": position + len(line)})
            current, start = [], position + len(line)
        elif not current:
            start = position
            current.append(line)
        else:
            current.append(line)
        position += len(line)
    if current:
        text = "".join(current).strip()
        if text:
            blocks.append({"text": text + "\n", "start": start, "end": position})
    return blocks


def _make_segment(number, blocks, start, end, heading_path, page_start, page_end):
    content = "".join(blocks).strip()
    return {
        "segment_id": f"seg_{number:05d}",
        "ordinal": number,
        "heading_path": list(heading_path),
        "source_page_start": page_start,
        "source_page_end": page_end,
        "markdown_start": start,
        "markdown_end": end,
        "char_count": len(content),
        "content": content,
    }


def _page_anchor(text):
    match = re.search(r"<!--\s*source-anchor:\s*page\s*=\s*(\d+)\s*-->", text, re.I)
    return int(match.group(1)) if match else None


def _init_index(path, segments):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE segment (segment_id TEXT PRIMARY KEY, ordinal INTEGER, heading_path TEXT, page_start INTEGER, page_end INTEGER, content TEXT);
            CREATE VIRTUAL TABLE segment_fts USING fts5(segment_id UNINDEXED, heading_path, content, tokenize='unicode61');
            CREATE TABLE knowledge_node (node_id TEXT PRIMARY KEY, node_type TEXT, title TEXT, statement TEXT, scope TEXT, aliases_json TEXT, relations_json TEXT, confidence TEXT, payload_json TEXT);
            CREATE TABLE knowledge_evidence (node_id TEXT, segment_id TEXT, quote TEXT, page INTEGER, locator TEXT, PRIMARY KEY (node_id, segment_id, quote));
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(node_id UNINDEXED, title, aliases, statement, tokenize='unicode61');
            """
        )
        for item in segments:
            heading = " > ".join(item["heading_path"])
            conn.execute("INSERT INTO segment VALUES (?, ?, ?, ?, ?, ?)", (item["segment_id"], item["ordinal"], heading, item["source_page_start"], item["source_page_end"], item["content"]))
            conn.execute("INSERT INTO segment_fts VALUES (?, ?, ?)", (item["segment_id"], heading, item["content"]))
        conn.commit()
    finally:
        conn.close()


def _result(manifest, reused):
    return {
        "success": True,
        "document_id": manifest["document_id"],
        "title": manifest["title"],
        "source_hash": manifest["source_hash"],
        "source_chars": manifest["source_chars"],
        "segment_count": manifest["segment_count"],
        "pending_segment_ids": [f"seg_{index:05d}" for index in range(1, min(manifest["segment_count"], 8) + 1)],
        "reused": reused,
        "next_action": "Read a small segment batch with document-kb-read; extract evidence-backed nodes; then call document-kb-upsert.",
    }


def _write_overview(root, manifest, segments):
    headings = [" > ".join(item["heading_path"]) for item in segments if item["heading_path"]]
    lines = [f"# {manifest['title']}", "", "## Corpus", f"- document_id: `{manifest['document_id']}`", f"- segments: {len(segments)}", f"- source chars: {manifest['source_chars']}", "", "## Structure"]
    lines.extend(f"- {heading}" for heading in dict.fromkeys(headings).keys())
    (root / "overview.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _workspace_id():
    value = str(os.getenv("AGENT_RUNTIME_WORKSPACE_ID") or "").strip()
    if not value:
        raise ValueError("AGENT_RUNTIME_WORKSPACE_ID is required")
    return _document_id(value)


def _documents_root(workspace_id):
    return _workspace_root(workspace_id) / "document_knowledge"


def _workspace_root(workspace_id):
    artifacts = os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR")
    if not artifacts:
        raise ValueError("AGENT_RUNTIME_ARTIFACTS_DIR is not available in sandbox execution")
    return Path(artifacts).resolve() / "workspaces" / workspace_id


def _resolve_source(raw_path, workspace_id):
    workspace = _workspace_root(workspace_id).resolve()
    path = (workspace / str(raw_path).strip()).resolve()
    uploads = (workspace / "document_knowledge" / "uploads").resolve()
    try:
        path.relative_to(uploads)
    except ValueError as exc:
        raise ValueError("source_path must be workspace-relative and located under document_knowledge/uploads/") from exc
    if path.suffix.lower() not in {".md", ".markdown"} or not path.is_file():
        raise ValueError("source_path must point to an existing .md or .markdown file")
    return path


def _document_id(value):
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip()).strip("-_").lower()
    if not cleaned:
        raise ValueError("document_id is empty after normalization")
    return cleaned[:80]


def _read_json(path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _now():
    return datetime.now(timezone.utc).isoformat()
