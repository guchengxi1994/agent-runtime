definition = {
    "name": "document-markdown-convert",
    "description": "Convert a persisted workspace document to Markdown without returning its body.",
}

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote_to_bytes


MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_ASSET_BYTES = 100 * 1024 * 1024
PASSTHROUGH_SUFFIXES = {".md", ".markdown", ".txt"}


def execute(params):
    workspace_id = _workspace_id()
    source = _resolve_source(params["source_path"], workspace_id)
    source_bytes = source.read_bytes()
    if not source_bytes:
        raise ValueError("source_path points to an empty file")
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise ValueError(f"source file exceeds {MAX_SOURCE_BYTES} bytes")

    document_id = _document_id(params.get("document_id") or source.stem)
    root = _documents_root(workspace_id) / document_id
    metadata_path = root / "conversion.json"
    source_hash = _sha256(source_bytes)
    existing = _read_json(metadata_path)
    force = bool(params.get("force"))
    if existing and existing.get("source_hash") == source_hash and _markdown_path(workspace_id, document_id).is_file() and not force:
        return _result(existing, reused=True)
    if existing and existing.get("source_hash") != source_hash and not force:
        return {
            "success": False,
            "error_type": "source_changed",
            "error": "The raw source changed after this document id was converted.",
            "document_id": document_id,
            "retry_guidance": "Use a new document_id to preserve the prior corpus, or ask the user to confirm force=true before replacing the Markdown derivative.",
        }

    requested = str(params.get("converter") or "auto").strip().lower()
    if requested not in {"auto", "anydoc", "mineru"}:
        raise ValueError("converter must be auto, anydoc, or mineru")
    converter = _select_converter(requested, source.suffix.lower())
    if converter == "passthrough":
        markdown = _normalize_text(source_bytes)
        conversion = {"converter": "passthrough", "content_list": [], "content_list_v2": None, "assets": {}}
    elif converter == "anydoc":
        conversion = _convert_with_anydoc(source)
        markdown = conversion["markdown"]
    else:
        conversion = _convert_with_mineru(source, params)
        markdown = conversion["markdown"]
    if conversion.get("conversion_error"):
        return {"success": False, "document_id": document_id, **conversion["conversion_error"]}
    if not markdown.strip():
        return {
            "success": False,
            "error_type": "empty_markdown",
            "error": f"{converter} completed but produced no readable Markdown.",
            "document_id": document_id,
            "retry_guidance": "For a scanned or image-heavy source, retry with converter=mineru after configuring this skill's MinerU connection.",
        }

    markdown = _normalize_markdown(markdown)
    markdown_path = _markdown_path(workspace_id, document_id)
    root.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    assets = _write_assets(root / "assets", conversion.get("assets") or {})
    content_list = conversion.get("content_list") or []
    _write_json(root / "conversion" / "content_list.json", content_list)
    if conversion.get("content_list_v2") is not None:
        _write_json(root / "conversion" / "content_list_v2.json", conversion["content_list_v2"])
    metadata = {
        "schema_version": 1,
        "document_id": document_id,
        "workspace_id": workspace_id,
        "source_path": str(source.relative_to(_workspace_root(workspace_id))),
        "source_hash": source_hash,
        "source_size_bytes": len(source_bytes),
        "markdown_path": str(markdown_path.relative_to(_workspace_root(workspace_id))),
        "markdown_hash": _sha256(markdown.encode("utf-8")),
        "markdown_chars": len(markdown),
        "converter": conversion["converter"],
        "content_item_count": len(content_list),
        "asset_count": len(assets),
        "asset_paths": assets,
        "converted_at": _now(),
    }
    _write_json(metadata_path, metadata)
    return _result(metadata, reused=False)


def _select_converter(requested, suffix):
    if requested != "auto":
        return requested
    return "passthrough" if suffix in PASSTHROUGH_SUFFIXES else "anydoc"


def _convert_with_anydoc(source):
    try:
        import anydoc
    except ImportError:
        return _converter_unavailable("anydoc", "The isolated skill environment does not contain firecrawl-anydoc.")
    try:
        markdown = anydoc.to_markdown(str(source))
    except Exception as exc:
        name = type(exc).__name__
        return _conversion_failure("anydoc", name, str(exc), "Retry with converter=mineru for scanned PDFs or complex page layouts.")
    return {"converter": "anydoc", "markdown": str(markdown), "content_list": [], "content_list_v2": None, "assets": {}}


def _convert_with_mineru(source, params):
    url = str(params.get("mineru_url") or os.getenv("MINERU_PROXY_URL") or "").strip().rstrip("/")
    if not url:
        return _converter_unavailable("mineru", "MINERU_PROXY_URL is not configured.")
    token = str(os.getenv("MINERU_OCR_TOKEN") or "").strip()
    if not token:
        return _conversion_failure(
            "mineru",
            "missing_mineru_token",
            "MINERU_OCR_TOKEN is not configured for document-markdown-convert.",
            "Configure MINERU_OCR_TOKEN in the document-markdown-convert skill environment before retrying.",
        )
    try:
        import requests
    except ImportError:
        return _converter_unavailable("mineru", "The isolated skill environment does not contain requests.")
    try:
        with source.open("rb") as handle:
            response = requests.post(
                f"{url}/v1/parse/file",
                files={"file": (source.name, handle, "application/octet-stream")},
                data={"language": str(params.get("language") or "ch").strip() or "ch"},
                headers={"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"},
                timeout=(20, 580),
            )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _conversion_failure("mineru", type(exc).__name__, str(exc), "Check the MinerU proxy URL and service health before retrying.")
    except ValueError as exc:
        return _conversion_failure("mineru", "invalid_response", str(exc), "The configured MinerU service did not return JSON.")
    markdown = payload.get("markdown")
    if not isinstance(markdown, str):
        return _conversion_failure("mineru", "invalid_response", "MinerU response has no markdown field.", "Check the MinerU proxy version and response.")
    return {
        "converter": "mineru",
        "markdown": markdown,
        "content_list": payload.get("contentList") if isinstance(payload.get("contentList"), list) else [],
        "content_list_v2": payload.get("contentListV2"),
        "assets": payload.get("archiveAssets") if isinstance(payload.get("archiveAssets"), dict) else {},
    }


def _converter_unavailable(converter, error):
    return {
        "converter": converter,
        "markdown": "",
        "content_list": [],
        "content_list_v2": None,
        "assets": {},
        "conversion_error": {"error_type": "converter_unavailable", "error": error},
    }


def _conversion_failure(converter, error_type, error, retry_guidance):
    return {
        "converter": converter,
        "markdown": "",
        "content_list": [],
        "content_list_v2": None,
        "assets": {},
        "conversion_error": {"error_type": error_type, "error": error, "retry_guidance": retry_guidance},
    }


def _write_assets(directory, assets):
    paths, total = [], 0
    for name, value in assets.items():
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        relative = Path(name.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            continue
        payload = _decode_data_url(value)
        if payload is None or total + len(payload) > MAX_ASSET_BYTES:
            continue
        target = (directory / relative).resolve()
        try:
            target.relative_to(directory.resolve())
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        paths.append(str(target.relative_to(directory.parent)))
        total += len(payload)
    return paths


def _decode_data_url(value):
    if not value.startswith("data:") or "," not in value:
        return None
    header, body = value.split(",", 1)
    try:
        return base64.b64decode(body, validate=True) if ";base64" in header.lower() else unquote_to_bytes(body)
    except (ValueError, TypeError):
        return None


def _normalize_text(payload):
    return payload.decode("utf-8-sig", errors="replace")


def _normalize_markdown(markdown):
    return markdown.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def _result(metadata, reused):
    error = metadata.get("conversion_error")
    if error:
        return {"success": False, "document_id": metadata.get("document_id"), **error}
    return {
        "success": True,
        "document_id": metadata["document_id"],
        "markdown_path": metadata["markdown_path"],
        "converter": metadata["converter"],
        "source_hash": metadata["source_hash"],
        "markdown_hash": metadata["markdown_hash"],
        "markdown_chars": metadata["markdown_chars"],
        "content_item_count": metadata["content_item_count"],
        "asset_count": metadata["asset_count"],
        "reused": reused,
        "next_action": "Call document-kb-ingest with markdown_path and document_id. Do not return or load the full Markdown into chat.",
    }


def _workspace_id():
    value = _document_id(os.getenv("AGENT_RUNTIME_WORKSPACE_ID") or "")
    if not value:
        raise ValueError("AGENT_RUNTIME_WORKSPACE_ID is required")
    return value


def _workspace_root(workspace_id):
    artifacts = os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR")
    if not artifacts:
        raise ValueError("AGENT_RUNTIME_ARTIFACTS_DIR is not available in sandbox execution")
    return Path(artifacts).resolve() / "workspaces" / workspace_id


def _documents_root(workspace_id):
    return _workspace_root(workspace_id) / "document_knowledge"


def _resolve_source(raw_path, workspace_id):
    workspace = _workspace_root(workspace_id).resolve()
    path = (workspace / str(raw_path).strip()).resolve()
    raw_uploads = (workspace / "document_knowledge" / "uploads_raw").resolve()
    try:
        path.relative_to(raw_uploads)
    except ValueError as exc:
        raise ValueError("source_path must be workspace-relative and located under document_knowledge/uploads_raw/") from exc
    if not path.is_file():
        raise ValueError("source_path must point to an existing raw document")
    return path


def _markdown_path(workspace_id, document_id):
    return _workspace_root(workspace_id) / "document_knowledge" / "uploads" / f"{document_id}.md"


def _document_id(value):
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip()).strip("-_").lower()
    return cleaned[:80]


def _read_json(path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()
