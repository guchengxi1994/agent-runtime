from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_SUMMARY_CHARS = 700
MAX_CONTEXT_CHARS = 8000
NON_EVIDENCE_TOOLS = {"activate_skill", "read_skill_resource", "request_user_input", "model_planning"}


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    relative_path: str
    summary: str
    kind: str

    def observation(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_path": self.relative_path,
            "artifact_kind": self.kind,
            "summary": self.summary,
            "read_hint": "Call read_artifact with this artifact_id if full details are needed.",
        }


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_tool_artifact(
        self,
        *,
        conversation_id: str,
        run_id: str,
        tool_name: str,
        tool_call_id: str,
        kind: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        execution_id: str | None = None,
    ) -> ArtifactRecord:
        conversation_root = self._conversation_root(conversation_id)
        artifacts_dir = conversation_root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        manifest = self._read_manifest(conversation_id)
        artifact_id = f"art_{len(manifest) + 1:04d}_{sanitize_id(tool_name)}"
        relative_path = f"artifacts/{artifact_id}.json"
        artifact_path = conversation_root / relative_path
        created_at = now_iso()
        summary = summarize_result(result)
        status = "failed" if effective_success(result) is False else "completed"
        payload = {
            "artifact_id": artifact_id,
            "created_at": created_at,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "execution_id": execution_id,
            "kind": kind,
            "arguments": arguments,
            "result": result,
            "summary": summary,
            "status": status,
        }
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        entry = {
            "artifact_id": artifact_id,
            "created_at": created_at,
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "execution_id": execution_id,
            "kind": kind,
            "relative_path": relative_path,
            "summary": summary,
            "status": status,
        }
        manifest.append(entry)
        self._write_manifest(conversation_id, manifest)
        self._append_timeline(conversation_id, entry)
        self._write_manifest_markdown(conversation_id, manifest)
        self._write_working_state(conversation_id, manifest)
        return ArtifactRecord(
            artifact_id=artifact_id,
            relative_path=relative_path,
            summary=summary,
            kind=kind,
        )

    def write_model_trace(
        self,
        *,
        conversation_id: str,
        run_id: str,
        round_index: int,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> ArtifactRecord:
        conversation_root = self._conversation_root(conversation_id)
        artifacts_dir = conversation_root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        manifest = self._read_manifest(conversation_id)
        artifact_id = f"art_{len(manifest) + 1:04d}_model-planning"
        relative_path = f"artifacts/{artifact_id}.json"
        artifact_path = conversation_root / relative_path
        created_at = now_iso()
        summary = summarize_model_trace(round_index, request, response)
        payload = {
            "artifact_id": artifact_id,
            "created_at": created_at,
            "conversation_id": conversation_id,
            "run_id": run_id,
            "tool_name": "model_planning",
            "tool_call_id": None,
            "execution_id": None,
            "kind": "model_planning",
            "round_index": round_index,
            "request": request,
            "response": response,
            "summary": summary,
            "status": "completed",
        }
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        entry = {
            "artifact_id": artifact_id,
            "created_at": created_at,
            "run_id": run_id,
            "tool_name": "model_planning",
            "tool_call_id": None,
            "execution_id": None,
            "kind": "model_planning",
            "relative_path": relative_path,
            "summary": summary,
            "status": "completed",
            "round_index": round_index,
        }
        manifest.append(entry)
        self._write_manifest(conversation_id, manifest)
        self._append_timeline(conversation_id, entry)
        self._write_manifest_markdown(conversation_id, manifest)
        self._write_working_state(conversation_id, manifest)
        return ArtifactRecord(
            artifact_id=artifact_id,
            relative_path=relative_path,
            summary=summary,
            kind="model_planning",
        )

    def list_artifacts(self, conversation_id: str, limit: int = 50) -> dict[str, Any]:
        self._refresh_manifest_summaries(conversation_id)
        manifest = self._read_manifest(conversation_id)
        limit = max(1, min(int(limit or 50), 200))
        return {
            "success": True,
            "conversation_id": conversation_id,
            "count": len(manifest),
            "artifacts": manifest[-limit:],
        }

    def read_artifact(self, conversation_id: str, artifact_id: str, max_chars: int = 12000) -> dict[str, Any]:
        artifact_id = str(artifact_id or "").strip()
        if not artifact_id:
            return {"success": False, "error": "artifact_id is required"}
        self._refresh_manifest_summaries(conversation_id)
        manifest = self._read_manifest(conversation_id)
        entry = next((item for item in manifest if item.get("artifact_id") == artifact_id), None)
        if entry is None:
            return {"success": False, "error": f"Artifact not found: {artifact_id}"}
        relative_path = str(entry.get("relative_path") or "")
        artifact_path = (self._conversation_root(conversation_id) / relative_path).resolve()
        try:
            artifact_path.relative_to(self._conversation_root(conversation_id).resolve())
        except ValueError:
            return {"success": False, "error": f"Artifact path escapes conversation root: {artifact_id}"}
        if not artifact_path.is_file():
            return {"success": False, "error": f"Artifact file missing: {artifact_id}"}
        max_chars = max(1000, min(int(max_chars or 12000), 50000))
        content = artifact_path.read_text(encoding="utf-8")
        truncated = len(content) > max_chars
        return {
            "success": True,
            "artifact_id": artifact_id,
            "relative_path": relative_path,
            "summary": entry.get("summary", ""),
            "content": content[:max_chars],
            "truncated": truncated,
            "total_chars": len(content),
        }

    def build_context(self, conversation_id: str) -> str:
        self._refresh_manifest_summaries(conversation_id)
        working_state = self._conversation_root(conversation_id) / "working_state.md"
        if not working_state.is_file():
            return ""
        content = working_state.read_text(encoding="utf-8")
        if len(content) > MAX_CONTEXT_CHARS:
            content = content[-MAX_CONTEXT_CHARS:]
        return (
            "Artifact context for this conversation. Use list_artifacts/read_artifact when full details are needed; "
            "do not assume all artifact content is already in the prompt.\n\n"
            f"{content}"
        )

    def _conversation_root(self, conversation_id: str) -> Path:
        root = self.root / "conversations" / sanitize_id(conversation_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _manifest_path(self, conversation_id: str) -> Path:
        return self._conversation_root(conversation_id) / "manifest.json"

    def _read_manifest(self, conversation_id: str) -> list[dict[str, Any]]:
        path = self._manifest_path(conversation_id)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _refresh_manifest_summaries(self, conversation_id: str) -> None:
        manifest = self._read_manifest(conversation_id)
        if not manifest:
            return
        root = self._conversation_root(conversation_id).resolve()
        manifest_changed = False
        payload_changed = False
        for item in manifest:
            relative_path = str(item.get("relative_path") or "")
            artifact_path = (root / relative_path).resolve()
            try:
                artifact_path.relative_to(root)
            except ValueError:
                continue
            if not artifact_path.is_file():
                continue
            try:
                payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                continue
            summary = summarize_result(result)
            status = "failed" if effective_success(result) is False else "completed"
            if item.get("summary") != summary:
                item["summary"] = summary
                manifest_changed = True
            if item.get("status") != status:
                item["status"] = status
                manifest_changed = True
            if payload.get("summary") != summary or payload.get("status") != status:
                payload["summary"] = summary
                payload["status"] = status
                artifact_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                payload_changed = True
        if manifest_changed:
            self._write_manifest(conversation_id, manifest)
        if manifest_changed or payload_changed:
            self._write_manifest_markdown(conversation_id, manifest)
            self._write_working_state(conversation_id, manifest)

    def _write_manifest(self, conversation_id: str, manifest: list[dict[str, Any]]) -> None:
        self._manifest_path(conversation_id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _append_timeline(self, conversation_id: str, entry: dict[str, Any]) -> None:
        path = self._conversation_root(conversation_id) / "timeline.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _write_manifest_markdown(self, conversation_id: str, manifest: list[dict[str, Any]]) -> None:
        lines = ["# Artifact Manifest", ""]
        for item in manifest[-100:]:
            lines.append(
                f"- `{item.get('artifact_id')}` [{item.get('kind')}] {item.get('tool_name')} "
                f"status={item.get('status')} run={item.get('run_id')}"
            )
            lines.append(f"  - summary: {item.get('summary', '')}")
            lines.append(f"  - path: {item.get('relative_path', '')}")
        (self._conversation_root(conversation_id) / "manifest.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    def _write_working_state(self, conversation_id: str, manifest: list[dict[str, Any]]) -> None:
        recent = manifest[-20:]
        evidence = [item for item in recent if _is_successful_evidence(item)]
        failed_or_empty = [item for item in recent if _is_failed_or_empty_attempt(item)]
        lines = [
            "# Working State",
            "",
            "This is a compact artifact index. It is not a full transcript.",
            "",
            "## Successful Evidence Artifacts",
        ]
        if evidence:
            for item in evidence[-10:]:
                lines.append(_format_artifact_line(item))
        else:
            lines.append("- No successful evidence artifacts yet.")
        lines.extend(
            [
                "",
                "## Failed or Empty Attempts",
            ]
        )
        if failed_or_empty:
            for item in failed_or_empty[-10:]:
                lines.append(_format_artifact_line(item))
        else:
            lines.append("- No failed or empty attempts yet.")
        lines.extend(
            [
                "",
                "## Recent Tool Timeline",
            ]
        )
        for item in recent:
            lines.append(_format_artifact_line(item))
        lines.extend(
            [
                "",
                "## Usage Guidance",
                "- Use successful evidence artifacts as usable observations, not just the latest tool status.",
                "- Distinguish partial failures or empty attempts from overall tool failure.",
                "- Do not say all search or fetch tools failed when successful evidence artifacts exist.",
                "- Call `read_artifact` with an artifact id when exact tool output is needed.",
                "- Call `list_artifacts` when deciding which stored observation to inspect.",
            ]
        )
        (self._conversation_root(conversation_id) / "working_state.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )


def effective_success(result: dict[str, Any]) -> bool | None:
    if not isinstance(result, dict):
        return None
    wrapper_success = result.get("success")
    if wrapper_success is False:
        return False
    data = result.get("data")
    if isinstance(data, dict):
        data_success = data.get("success")
        if data_success is False:
            return False
        if data_success is True:
            return True
    if wrapper_success is True:
        return True
    return None


def result_error_message(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    containers = [result]
    data = result.get("data")
    if isinstance(data, dict):
        containers.append(data)
    sandbox_response = result.get("sandbox_response")
    if isinstance(sandbox_response, dict):
        containers.append(sandbox_response)
    for container in containers:
        error = container.get("error")
        error_type = container.get("error_type") or container.get("code")
        if error and error_type:
            return trim(f"{error_type}: {error}")
        if error or error_type:
            return trim(str(error or error_type))
    return ""


def summarize_result(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return trim(str(result))
    if effective_success(result) is False:
        return trim(f"Failed: {result_error_message(result) or 'Tool failed'}")
    data = result.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            count = len(data["results"])
            query = str(data.get("query") or "").strip()
            query_text = f" for query: {query}" if query else ""
            preview = preview_items(data["results"])
            suffix = f" {preview}" if preview else ""
            return trim(f"{count} result(s){query_text}.{suffix}")
        page_summary = summarize_page_data(data)
        if page_summary:
            return page_summary
        if data.get("summary"):
            return trim(str(data["summary"]))
        if data.get("query"):
            return trim(f"Tool data for query: {data.get('query')}; keys={sorted(data.keys())}")
        return trim(f"Tool data keys={sorted(data.keys())}")
    if result.get("phase"):
        return trim(f"Tool completed at phase={result.get('phase')}; keys={sorted(result.keys())}")
    return trim(f"Tool result keys={sorted(result.keys())}")


def summarize_page_data(data: dict[str, Any]) -> str:
    parts = []
    title = str(data.get("title") or "").strip()
    url = str(data.get("url") or "").strip()
    description = str(data.get("description") or "").strip()
    text = str(data.get("text") or data.get("markdown") or data.get("content") or "").strip()
    if title:
        parts.append(f"title={title}")
    if url:
        parts.append(f"url={url}")
    if description:
        parts.append(f"description={description}")
    if text:
        parts.append(f"excerpt={text}")
    return trim("; ".join(parts)) if parts else ""


def summarize_model_trace(round_index: int, request: dict[str, Any], response: dict[str, Any]) -> str:
    messages = request.get("messages") if isinstance(request, dict) else None
    tools = request.get("tools") if isinstance(request, dict) else None
    tool_calls = response.get("tool_calls") if isinstance(response, dict) else None
    content = response.get("content") if isinstance(response, dict) else ""
    reasoning_available = bool(response.get("reasoning_content_available")) if isinstance(response, dict) else False
    reasoning_exposed = bool(response.get("reasoning_content_exposed")) if isinstance(response, dict) else False
    return trim(
        "model planning "
        f"round={round_index}; "
        f"model={request.get('model') if isinstance(request, dict) else None}; "
        f"input_messages={len(messages) if isinstance(messages, list) else 0}; "
        f"tools={len(tools) if isinstance(tools, list) else 0}; "
        f"output_tool_calls={len(tool_calls) if isinstance(tool_calls, list) else 0}; "
        f"output_chars={len(content) if isinstance(content, str) else 0}; "
        f"reasoning_available={reasoning_available}; "
        f"reasoning_exposed={reasoning_exposed}"
    )


def _format_artifact_line(item: dict[str, Any]) -> str:
    return (
        f"- `{item.get('artifact_id')}` step={item.get('tool_name')} "
        f"status={item.get('status')} summary={item.get('summary', '')}"
    )


def _is_successful_evidence(item: dict[str, Any]) -> bool:
    if item.get("status") != "completed":
        return False
    if item.get("tool_name") in NON_EVIDENCE_TOOLS:
        return False
    return not _looks_empty_summary(str(item.get("summary") or ""))


def _is_failed_or_empty_attempt(item: dict[str, Any]) -> bool:
    return item.get("status") == "failed" or _looks_empty_summary(str(item.get("summary") or ""))


def _looks_empty_summary(summary: str) -> bool:
    return summary.strip().startswith("0 result(s)")


def preview_items(items: list[Any], limit: int = 3) -> str:
    previews = []
    for item in items[:limit]:
        if isinstance(item, dict):
            title = item.get("title") or item.get("url") or item.get("name")
            if title:
                previews.append(str(title))
        else:
            previews.append(str(item))
    return "; ".join(previews)


def trim(value: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def sanitize_id(value: str) -> str:
    value = str(value or "").strip()
    sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return sanitized[:96] or f"id_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
