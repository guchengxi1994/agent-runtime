definition = {"name": "document-kb-query", "description": "Search evidence-backed document nodes with source-segment fallback."}

import json
import os
import re
import sqlite3
from pathlib import Path


def execute(params):
    root = _document_root(params["document_id"])
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(params.get("limit") or 8), 20))
    include_candidates = params.get("include_source_candidates") is not False
    conn = sqlite3.connect(root / "index.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        nodes = _query_nodes(conn, query, limit)
        candidates = _query_segments(conn, query, limit) if include_candidates and (not nodes or len(nodes) < limit) else []
    finally:
        conn.close()
    return {
        "success": True,
        "document_id": _sanitize(params["document_id"]),
        "query": query,
        "nodes": nodes,
        "source_candidates": candidates,
        "next_action": "Answer only from node statements and evidence. Read selected source_candidates with document-kb-read when coverage is incomplete or a node is missing.",
    }


def _query_nodes(conn, query, limit):
    match = _fts_query(query)
    try:
        rows = conn.execute(
            """SELECT n.*, bm25(knowledge_fts) AS score
               FROM knowledge_fts JOIN knowledge_node n ON n.node_id = knowledge_fts.node_id
               WHERE knowledge_fts MATCH ? ORDER BY score LIMIT ?""",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        rows = _node_like_query(conn, query, limit)
    return [_node_payload(conn, row) for row in rows]


def _query_segments(conn, query, limit):
    match = _fts_query(query)
    try:
        rows = conn.execute(
            """SELECT s.segment_id, s.ordinal, s.heading_path, s.page_start, s.page_end, bm25(segment_fts) AS score
               FROM segment_fts JOIN segment s ON s.segment_id = segment_fts.segment_id
               WHERE segment_fts MATCH ? ORDER BY score LIMIT ?""",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        rows = _segment_like_query(conn, query, limit)
    return [
        {
            "segment_id": row["segment_id"],
            "ordinal": row["ordinal"],
            "heading_path": [item for item in row["heading_path"].split(" > ") if item],
            "source_page_start": row["page_start"],
            "source_page_end": row["page_end"],
        }
        for row in rows
    ]


def _node_like_query(conn, query, limit):
    like = f"%{query}%"
    return conn.execute("SELECT *, 0 AS score FROM knowledge_node WHERE title LIKE ? OR statement LIKE ? ORDER BY title LIMIT ?", (like, like, limit)).fetchall()


def _segment_like_query(conn, query, limit):
    like = f"%{query}%"
    return conn.execute("SELECT segment_id, ordinal, heading_path, page_start, page_end, 0 AS score FROM segment WHERE content LIKE ? ORDER BY ordinal LIMIT ?", (like, limit)).fetchall()


def _node_payload(conn, row):
    evidence = conn.execute("SELECT segment_id, quote, page, locator FROM knowledge_evidence WHERE node_id = ? ORDER BY segment_id", (row["node_id"],)).fetchall()
    return {
        "node_id": row["node_id"],
        "type": row["node_type"],
        "title": row["title"],
        "statement": row["statement"],
        "scope": row["scope"],
        "aliases": _json_list(row["aliases_json"]),
        "relations": _json_list(row["relations_json"]),
        "confidence": row["confidence"],
        "evidence": [dict(item) for item in evidence],
    }


def _fts_query(query):
    terms = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:20]) or '""'


def _json_list(value):
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


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


def _sanitize(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip()).strip("-_").lower()[:80]
