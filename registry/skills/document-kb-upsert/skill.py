definition = {"name": "document-kb-upsert", "description": "Persist evidence-backed document knowledge nodes and extraction progress."}

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


VALID_TYPES = {"definition", "claim", "method", "procedure", "formula", "result", "limitation", "example", "warning", "citation", "argument", "relation"}
VALID_CONFIDENCE = {"explicit", "inferred", "working"}
MAX_NODES = 80
MAX_QUOTE_CHARS = 1000


def execute(params):
    root = _document_root(params["document_id"])
    processed = _string_list(params.get("processed_segment_ids"), "processed_segment_ids")
    nodes = params.get("nodes")
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        raise ValueError(f"nodes must be an array with at most {MAX_NODES} items")
    progress = _read_json(root / "progress.json") or {"completed_segment_ids": [], "failed_segments": []}
    conn = sqlite3.connect(root / "index.sqlite")
    try:
        known = {row[0] for row in conn.execute("SELECT segment_id FROM segment")}
        unknown = sorted(set(processed) - known)
        if unknown:
            raise ValueError(f"processed_segment_ids contain unknown segments: {unknown}")
        normalized = [_normalize_node(item, known) for item in nodes]
        conn.execute("BEGIN")
        for node in normalized:
            _upsert_node(conn, node)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    completed = list(dict.fromkeys([*(progress.get("completed_segment_ids") or []), *processed]))
    progress.update({"completed_segment_ids": completed, "updated_at": _now()})
    _write_json_atomic(root / "progress.json", progress)
    _write_projection(root)
    return {
        "success": True,
        "document_id": _sanitize(params["document_id"]),
        "upserted_node_count": len(normalized),
        "processed_segment_count": len(processed),
        "completed_segment_count": len(completed),
        "node_ids": [item["node_id"] for item in normalized],
        "next_action": "Call document-kb-status for the next pending batch, or document-kb-query for an evidence-backed question.",
    }


def _normalize_node(raw, known_segments):
    if not isinstance(raw, dict):
        raise ValueError("each node must be an object")
    node_type = str(raw.get("type") or "").strip().lower()
    if node_type not in VALID_TYPES:
        raise ValueError(f"node type must be one of {sorted(VALID_TYPES)}")
    title = _required(raw, "title", 240)
    statement = _required(raw, "statement", 6000)
    confidence = str(raw.get("confidence") or "explicit").strip().lower()
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"confidence must be one of {sorted(VALID_CONFIDENCE)}")
    evidence_raw = raw.get("evidence")
    if not isinstance(evidence_raw, list) or not evidence_raw:
        raise ValueError("each node requires at least one evidence item")
    evidence = []
    for item in evidence_raw:
        if not isinstance(item, dict):
            raise ValueError("each evidence item must be an object")
        segment_id = str(item.get("segment_id") or "").strip()
        if segment_id not in known_segments:
            raise ValueError(f"evidence references unknown segment_id: {segment_id}")
        quote = str(item.get("quote") or "").strip()
        locator = str(item.get("locator") or "").strip()
        if not quote and not locator:
            raise ValueError("each evidence item requires quote or locator")
        if len(quote) > MAX_QUOTE_CHARS:
            raise ValueError(f"evidence quote exceeds {MAX_QUOTE_CHARS} characters")
        page = item.get("page")
        evidence.append({"segment_id": segment_id, "quote": quote, "locator": locator[:240], "page": int(page) if page is not None else None})
    scope = str(raw.get("scope") or "").strip()[:1000]
    aliases = _optional_strings(raw.get("aliases"), "aliases", 80, 240)
    relations = raw.get("relations") if isinstance(raw.get("relations"), list) else []
    node_id = _sanitize(raw.get("node_id") or "") or _stable_node_id(node_type, title, statement)
    return {"node_id": node_id, "node_type": node_type, "title": title, "statement": statement, "scope": scope, "aliases": aliases, "relations": relations, "confidence": confidence, "evidence": evidence}


def _upsert_node(conn, node):
    payload = {key: value for key, value in node.items() if key not in {"node_id", "node_type", "title", "statement", "scope", "aliases", "relations", "confidence", "evidence"}}
    conn.execute(
        """INSERT INTO knowledge_node(node_id, node_type, title, statement, scope, aliases_json, relations_json, confidence, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(node_id) DO UPDATE SET node_type=excluded.node_type, title=excluded.title, statement=excluded.statement,
             scope=excluded.scope, aliases_json=excluded.aliases_json, relations_json=excluded.relations_json,
             confidence=excluded.confidence, payload_json=excluded.payload_json""",
        (node["node_id"], node["node_type"], node["title"], node["statement"], node["scope"], json.dumps(node["aliases"], ensure_ascii=False), json.dumps(node["relations"], ensure_ascii=False), node["confidence"], json.dumps(payload, ensure_ascii=False)),
    )
    conn.execute("DELETE FROM knowledge_fts WHERE node_id = ?", (node["node_id"],))
    conn.execute("INSERT INTO knowledge_fts(node_id, title, aliases, statement) VALUES (?, ?, ?, ?)", (node["node_id"], node["title"], " ".join(node["aliases"]), node["statement"]))
    for evidence in node["evidence"]:
        conn.execute("INSERT OR IGNORE INTO knowledge_evidence(node_id, segment_id, quote, page, locator) VALUES (?, ?, ?, ?, ?)", (node["node_id"], evidence["segment_id"], evidence["quote"], evidence["page"], evidence["locator"]))


def _write_projection(root):
    conn = sqlite3.connect(root / "index.sqlite")
    try:
        rows = conn.execute("SELECT node_id, node_type, title, statement, confidence FROM knowledge_node ORDER BY node_type, title LIMIT 5000").fetchall()
    finally:
        conn.close()
    lines = ["# Extracted Knowledge", ""]
    for node_id, node_type, title, statement, confidence in rows:
        lines.extend([f"## {title}", f"- id: `{node_id}`", f"- type: {node_type}", f"- confidence: {confidence}", "", statement, ""])
    (root / "knowledge.md").write_text("\n".join(lines), encoding="utf-8")


def _document_root(document_id):
    workspace_id = _sanitize(os.getenv("AGENT_RUNTIME_WORKSPACE_ID") or "")
    artifacts = os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR")
    document_id = _sanitize(document_id)
    if not artifacts or not workspace_id or not document_id:
        raise ValueError("workspace sandbox context and document_id are required")
    root = Path(artifacts).resolve() / "workspaces" / workspace_id / "document_knowledge" / document_id
    if not (root / "index.sqlite").is_file():
        raise ValueError(f"Document knowledge base not found: {document_id}")
    return root


def _required(raw, key, limit):
    value = str(raw.get(key) or "").strip()
    if not value or len(value) > limit:
        raise ValueError(f"{key} is required and must not exceed {limit} characters")
    return value


def _string_list(value, key):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty array")
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _optional_strings(value, key, maximum, length):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{key} must be an array with at most {maximum} items")
    return list(dict.fromkeys(str(item).strip()[:length] for item in value if str(item).strip()))


def _stable_node_id(node_type, title, statement):
    value = "\n".join([node_type, title.casefold(), statement.casefold()])
    return f"node_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]}"


def _sanitize(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip()).strip("-_").lower()[:80]


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path, payload):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    temp.replace(path)


def _now():
    return datetime.now(timezone.utc).isoformat()
