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
NON_EVIDENCE_TOOLS = {
    "activate_skill",
    "read_skill_resource",
    "request_user_input",
    "model_planning",
    "create_checkpoint",
}


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    workspace_id: str
    conversation_id: str
    relative_path: str
    summary: str
    kind: str

    def observation(self) -> dict[str, Any]:
        return {
            "artifact_ref": {
                "workspace_id": self.workspace_id,
                "conversation_id": self.conversation_id,
                "artifact_id": self.artifact_id,
            },
            "artifact_path": self.relative_path,
            "artifact_kind": self.kind,
            "summary": self.summary,
        }


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def register_conversation(self, conversation_id: str, workspace_id: str, agent_id: str) -> None:
        meta = {
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "updated_at": now_iso(),
        }
        path = self._conversation_meta_path(conversation_id)
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
            if isinstance(existing, dict):
                meta = {
                    "conversation_id": existing.get("conversation_id") or conversation_id,
                    "workspace_id": existing.get("workspace_id") or workspace_id,
                    "agent_id": existing.get("agent_id") or agent_id,
                    "created_at": existing.get("created_at") or now_iso(),
                    "updated_at": now_iso(),
                }
        else:
            meta["created_at"] = meta["updated_at"]
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def resolve_workspace_id(self, *, workspace_id: str | None = None, conversation_id: str | None = None) -> str | None:
        if workspace_id:
            return sanitize_id(workspace_id)
        if not conversation_id:
            return None
        path = self._conversation_meta_path(conversation_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        value = str(payload.get("workspace_id") or "").strip()
        return sanitize_id(value) if value else None

    def write_tool_artifact(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        run_id: str,
        tool_name: str,
        tool_call_id: str,
        kind: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        execution_id: str | None = None,
        tags: list[str] | None = None,
    ) -> ArtifactRecord:
        summary = summarize_result(result)
        status = "failed" if effective_success(result) is False else "completed"
        title = f"{tool_name} output"
        stats = build_tool_artifact_stats(arguments, result)
        return self._write_workspace_artifact(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            execution_id=execution_id,
            kind=kind,
            title=title,
            summary=summary,
            status=status,
            tags=tags or [],
            stats=stats,
            content={"arguments": arguments, "result": result},
        )

    def write_model_trace(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        run_id: str,
        round_index: int,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> ArtifactRecord:
        summary = summarize_model_trace(round_index, request, response)
        stats = build_model_artifact_stats(request, response)
        return self._write_workspace_artifact(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            tool_name="model_planning",
            tool_call_id="",
            execution_id=None,
            kind="model_planning",
            title=f"Model planning round {round_index}",
            summary=summary,
            status="completed",
            tags=["planning"],
            stats=stats,
            content={"round_index": round_index, "request": request, "response": response},
        )

    def write_checkpoint(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        run_id: str,
        title: str,
        summary: str,
        payload: dict[str, Any],
        tags: list[str] | None = None,
    ) -> ArtifactRecord:
        return self._write_workspace_artifact(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            tool_name="create_checkpoint",
            tool_call_id="",
            execution_id=None,
            kind="checkpoint",
            title=title,
            summary=trim(summary or title),
            status="completed",
            tags=tags or [],
            stats={"payload_chars": json_char_count(payload)},
            content={"payload": payload},
        )

    def list_artifacts(
        self,
        *,
        workspace_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        kind: str | None = None,
    ) -> dict[str, Any]:
        resolved_workspace_id = self.resolve_workspace_id(workspace_id=workspace_id, conversation_id=conversation_id)
        if not resolved_workspace_id:
            return {"success": False, "error": "workspace_id or known conversation_id is required"}
        self._refresh_workspace_manifest(resolved_workspace_id)
        manifest = self._read_workspace_manifest(resolved_workspace_id)
        filtered = manifest
        if conversation_id:
            filtered = [item for item in filtered if item.get("conversation_id") == conversation_id]
        if kind:
            filtered = [item for item in filtered if item.get("kind") == kind]
        limit = max(1, min(int(limit or 50), 200))
        return {
            "success": True,
            "workspace_id": resolved_workspace_id,
            "conversation_id": conversation_id,
            "count": len(filtered),
            "artifacts": filtered[-limit:],
        }

    def has_recoverable_artifacts(self, workspace_id: str) -> bool:
        workspace_id = sanitize_id(workspace_id)
        self._refresh_workspace_manifest(workspace_id)
        return any(
            item.get("kind") in {"checkpoint", "sandbox_execution", "mcp_execution"}
            for item in self._read_workspace_manifest(workspace_id)
        )

    def read_artifact(
        self,
        *,
        artifact_id: str,
        workspace_id: str | None = None,
        conversation_id: str | None = None,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        artifact_id = str(artifact_id or "").strip()
        if not artifact_id:
            return {"success": False, "error": "artifact_id is required"}
        resolved_workspace_id = self.resolve_workspace_id(workspace_id=workspace_id, conversation_id=conversation_id)
        if not resolved_workspace_id:
            return {"success": False, "error": "workspace_id or known conversation_id is required"}
        payload, entry = self._load_artifact_payload(resolved_workspace_id, artifact_id)
        if payload is None or entry is None:
            return {"success": False, "error": f"Artifact not found: {artifact_id}"}
        max_chars = max(1000, min(int(max_chars or 12000), 50000))
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        truncated = len(content) > max_chars
        return {
            "success": True,
            "workspace_id": resolved_workspace_id,
            "conversation_id": entry.get("conversation_id"),
            "artifact_id": artifact_id,
            "summary": entry.get("summary", ""),
            "stats": entry.get("stats") or payload.get("stats") or {},
            "content": content[:max_chars],
            "truncated": truncated,
            "total_chars": len(content),
        }

    def build_context(self, workspace_id: str, conversation_id: str | None = None) -> str:
        resolved_workspace_id = self.resolve_workspace_id(workspace_id=workspace_id, conversation_id=conversation_id)
        if not resolved_workspace_id:
            return ""
        self._refresh_workspace_manifest(resolved_workspace_id)
        working_state = self._workspace_root(resolved_workspace_id) / "working_state.md"
        if not working_state.is_file():
            return ""
        content = working_state.read_text(encoding="utf-8")
        if len(content) > MAX_CONTEXT_CHARS:
            content = content[-MAX_CONTEXT_CHARS:]
        prefix = (
            "Workspace resume context for the current request. "
            "The index summaries are sufficient unless exact fields or raw evidence from an older artifact are required. "
            "Do not read artifacts merely to verify or restate a summary."
        )
        if conversation_id:
            prefix += f" Current conversation_id={conversation_id}."
        return f"{prefix}\n\n{content}"

    def _write_workspace_artifact(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        run_id: str,
        tool_name: str,
        tool_call_id: str,
        execution_id: str | None,
        kind: str,
        title: str,
        summary: str,
        status: str,
        tags: list[str],
        stats: dict[str, Any] | None,
        content: dict[str, Any],
    ) -> ArtifactRecord:
        workspace_id = sanitize_id(workspace_id)
        conversation_id = sanitize_id(conversation_id)
        manifest = self._read_workspace_manifest(workspace_id)
        artifact_id = f"art_{len(manifest) + 1:04d}_{sanitize_id(tool_name or kind)}"
        relative_path = f"artifacts/{artifact_id}.json"
        artifact_path = self._workspace_root(workspace_id) / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        created_at = now_iso()
        payload = {
            "artifact_id": artifact_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "created_at": created_at,
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id or None,
            "execution_id": execution_id,
            "kind": kind,
            "title": trim(title or tool_name or kind),
            "summary": trim(summary),
            "status": status,
            "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
            "stats": stats or {},
            "content": content,
        }
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        entry = {
            "artifact_id": artifact_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "created_at": created_at,
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id or None,
            "execution_id": execution_id,
            "kind": kind,
            "title": payload["title"],
            "summary": payload["summary"],
            "status": status,
            "tags": payload["tags"],
            "stats": payload["stats"],
            "relative_path": relative_path,
        }
        manifest.append(entry)
        self._write_workspace_manifest(workspace_id, manifest)
        self._append_workspace_timeline(workspace_id, entry)
        self._write_workspace_manifest_markdown(workspace_id, manifest)
        self._write_workspace_working_state(workspace_id, manifest)
        return ArtifactRecord(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            relative_path=relative_path,
            summary=payload["summary"],
            kind=kind,
        )

    def _load_artifact_payload(
        self,
        workspace_id: str,
        artifact_id: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        self._refresh_workspace_manifest(workspace_id)
        manifest = self._read_workspace_manifest(workspace_id)
        entry = next((item for item in manifest if item.get("artifact_id") == artifact_id), None)
        if entry is None:
            return None, None
        artifact_path = (self._workspace_root(workspace_id) / str(entry.get("relative_path") or "")).resolve()
        try:
            artifact_path.relative_to(self._workspace_root(workspace_id).resolve())
        except ValueError:
            return None, None
        if not artifact_path.is_file():
            return None, None
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None, None
        if not isinstance(payload, dict):
            return None, None
        return payload, entry

    def _workspace_root(self, workspace_id: str) -> Path:
        root = self.root / "workspaces" / sanitize_id(workspace_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _workspace_manifest_path(self, workspace_id: str) -> Path:
        return self._workspace_root(workspace_id) / "manifest.json"

    def _read_workspace_manifest(self, workspace_id: str) -> list[dict[str, Any]]:
        path = self._workspace_manifest_path(workspace_id)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _write_workspace_manifest(self, workspace_id: str, manifest: list[dict[str, Any]]) -> None:
        self._workspace_manifest_path(workspace_id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _append_workspace_timeline(self, workspace_id: str, entry: dict[str, Any]) -> None:
        path = self._workspace_root(workspace_id) / "timeline.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _write_workspace_manifest_markdown(self, workspace_id: str, manifest: list[dict[str, Any]]) -> None:
        lines = ["# Workspace Artifact Manifest", ""]
        for item in manifest[-100:]:
            lines.append(
                f"- `{item.get('artifact_id')}` [{item.get('kind')}] {item.get('tool_name')} "
                f"status={item.get('status')} run={item.get('run_id')} conversation={item.get('conversation_id')}"
            )
            lines.append(f"  - title: {item.get('title', '')}")
            lines.append(f"  - summary: {item.get('summary', '')}")
            if item.get("stats"):
                lines.append(f"  - stats: {format_stats_inline(item.get('stats') or {})}")
            lines.append(f"  - path: {item.get('relative_path', '')}")
        (self._workspace_root(workspace_id) / "manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_workspace_working_state(self, workspace_id: str, manifest: list[dict[str, Any]]) -> None:
        recent = manifest[-30:]
        checkpoints = [item for item in manifest if item.get("kind") == "checkpoint" and item.get("status") == "completed"]
        evidence = [item for item in recent if _is_successful_evidence(item)]
        failed_or_empty = [item for item in recent if _is_failed_or_empty_attempt(item)]
        lines = [
            "# Workspace Working State",
            "",
            f"workspace_id={workspace_id}",
            "",
            "This is a compact workspace artifact index. It is not a full transcript.",
            "",
            "## Latest Checkpoints",
        ]
        if checkpoints:
            for item in checkpoints[-10:]:
                lines.append(_format_artifact_line(item))
        else:
            lines.append("- No checkpoints yet.")
        lines.extend(["", "## Recent Successful Evidence"])
        if evidence:
            for item in evidence[-10:]:
                lines.append(_format_artifact_line(item))
        else:
            lines.append("- No successful evidence artifacts yet.")
        lines.extend(["", "## Failed or Empty Attempts"])
        if failed_or_empty:
            for item in failed_or_empty[-10:]:
                lines.append(_format_artifact_line(item))
        else:
            lines.append("- No failed or empty attempts yet.")
        lines.extend(["", "## Recent Workspace Timeline"])
        for item in recent:
            lines.append(_format_artifact_line(item))
        lines.extend(
            [
                "",
                "## Usage Guidance",
                "- Prefer checkpoint artifacts when resuming longer workflows.",
                "- Use successful evidence artifacts as usable observations, not just the latest tool status.",
                "- Distinguish partial failures or empty attempts from overall tool failure.",
                "- Read an artifact only when an older checkpoint must be resumed or exact omitted fields/raw evidence are required.",
                "- Do not read an artifact merely to verify or restate a successful observation.",
                "- List artifacts only when the compact index does not identify the stored item needed for recovery.",
            ]
        )
        (self._workspace_root(workspace_id) / "working_state.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _refresh_workspace_manifest(self, workspace_id: str) -> None:
        manifest = self._read_workspace_manifest(workspace_id)
        if not manifest:
            return
        root = self._workspace_root(workspace_id).resolve()
        changed = False
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
            if not isinstance(payload, dict):
                continue
            content = payload.get("content")
            result = content.get("result") if isinstance(content, dict) else None
            if not isinstance(result, dict):
                continue
            summary = summarize_result(result)
            status = "failed" if effective_success(result) is False else "completed"
            if item.get("summary") != summary:
                item["summary"] = summary
                changed = True
            if item.get("status") != status:
                item["status"] = status
                changed = True
            if payload.get("summary") != summary or payload.get("status") != status:
                payload["summary"] = summary
                payload["status"] = status
                artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                payload_changed = True
        if changed:
            self._write_workspace_manifest(workspace_id, manifest)
        if changed or payload_changed:
            self._write_workspace_manifest_markdown(workspace_id, manifest)
            self._write_workspace_working_state(workspace_id, manifest)

    def _conversation_root(self, conversation_id: str) -> Path:
        root = self.root / "conversations" / sanitize_id(conversation_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _conversation_meta_path(self, conversation_id: str) -> Path:
        return self._conversation_root(conversation_id) / "meta.json"


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
        if isinstance(data.get("column_profiles"), list):
            column_count = len(data["column_profiles"])
            dimensions = data.get("dimension_candidates") if isinstance(data.get("dimension_candidates"), list) else []
            enums = data.get("filterable_enums") if isinstance(data.get("filterable_enums"), dict) else {}
            summary = str(data.get("summary") or "").strip()
            if summary:
                return trim(
                    f"{summary} column_count={column_count}; dimensions={dimensions[:8]}; enum_fields={list(enums.keys())[:6]}"
                )
            return trim(
                f"Profiled {column_count} column(s); dimensions={dimensions[:8]}; enum_fields={list(enums.keys())[:6]}"
            )
        if isinstance(data.get("rows"), list):
            row_count = len(data["rows"])
            columns = data.get("columns") if isinstance(data.get("columns"), list) else []
            summary = str(data.get("summary") or "").strip()
            preview = preview_items(data["rows"])
            chart_spec = data.get("chart_spec") if isinstance(data.get("chart_spec"), dict) else None
            truncated = bool(data.get("truncated"))
            chart_hint = ""
            if chart_spec:
                chart_hint = f"; chart={chart_spec.get('chart_type') or 'chart'}"
            truncation_hint = ""
            if truncated:
                truncation_hint = f"; truncated_at={data.get('requested_limit') or row_count}"
            if summary:
                return trim(f"{summary} rows={row_count}; columns={columns[:8]}{chart_hint}{truncation_hint}")
            suffix = f"; preview={preview}" if preview else ""
            return trim(f"{row_count} row(s); columns={columns[:8]}{chart_hint}{truncation_hint}{suffix}")
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


def build_model_artifact_stats(request: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    messages = request.get("messages") if isinstance(request, dict) else None
    tools = request.get("tools") if isinstance(request, dict) else None
    tool_calls = response.get("tool_calls") if isinstance(response, dict) else None
    content = response.get("content") if isinstance(response, dict) else ""
    usage = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else {}
    stats = {
        "input_messages": len(messages) if isinstance(messages, list) else 0,
        "input_tools": len(tools) if isinstance(tools, list) else 0,
        "input_chars": estimate_message_chars(messages) if isinstance(messages, list) else 0,
        "request_json_chars": json_char_count(request),
        "output_tool_calls": len(tool_calls) if isinstance(tool_calls, list) else 0,
        "output_chars": len(content) if isinstance(content, str) else 0,
        "response_json_chars": json_char_count(response),
    }
    if usage:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if usage.get(key) is not None:
                stats[key] = usage.get(key)
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict) and details.get("cached_tokens") is not None:
            stats["cached_prompt_tokens"] = details.get("cached_tokens")
        completion_details = usage.get("completion_tokens_details")
        if isinstance(completion_details, dict):
            if completion_details.get("reasoning_tokens") is not None:
                stats["reasoning_tokens"] = completion_details.get("reasoning_tokens")
            if completion_details.get("accepted_prediction_tokens") is not None:
                stats["accepted_prediction_tokens"] = completion_details.get("accepted_prediction_tokens")
    return {key: value for key, value in stats.items() if value not in (None, "", [])}


def build_tool_artifact_stats(arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    stats = {
        "input_chars": json_char_count(arguments),
        "output_chars": json_char_count(result),
        "argument_keys": len(arguments) if isinstance(arguments, dict) else 0,
    }
    if isinstance(result, dict):
        stats["result_keys"] = len(result)
        data = result.get("data")
        if isinstance(data, dict):
            stats["data_keys"] = len(data)
            if isinstance(data.get("rows"), list):
                stats["row_count"] = len(data.get("rows") or [])
            if isinstance(data.get("columns"), list):
                stats["column_count"] = len(data.get("columns") or [])
            if isinstance(data.get("results"), list):
                stats["result_count"] = len(data.get("results") or [])
            if isinstance(data.get("chart_spec"), dict):
                stats["chart_count"] = 1
            if data.get("truncated") is not None:
                stats["truncated"] = bool(data.get("truncated"))
            if data.get("requested_limit") is not None:
                stats["requested_limit"] = data.get("requested_limit")
    return {key: value for key, value in stats.items() if value not in (None, "", [])}


def estimate_message_chars(messages: list[Any]) -> int:
    total = 0
    for message in messages or []:
        if not isinstance(message, dict):
            total += len(str(message))
            continue
        total += len(str(message.get("role") or ""))
        total += estimate_content_chars(message.get("content"))
        if isinstance(message.get("tool_calls"), list):
            total += json_char_count(message.get("tool_calls"))
    return total


def estimate_content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                total += len(str(item.get("type") or ""))
                total += len(str(item.get("text") or ""))
                total += json_char_count(item)
            else:
                total += len(str(item))
        return total
    if content is None:
        return 0
    return len(str(content))


def json_char_count(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except TypeError:
        return len(str(value))


def format_stats_inline(stats: dict[str, Any]) -> str:
    if not isinstance(stats, dict) or not stats:
        return ""
    ordered_keys = [
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_prompt_tokens",
        "reasoning_tokens",
        "input_chars",
        "output_chars",
        "request_json_chars",
        "response_json_chars",
        "input_messages",
        "input_tools",
        "output_tool_calls",
        "row_count",
        "column_count",
        "result_count",
        "chart_count",
        "truncated",
        "requested_limit",
    ]
    parts: list[str] = []
    for key in ordered_keys:
        if key in stats:
            parts.append(f"{key}={stats[key]}")
    for key, value in stats.items():
        if key not in ordered_keys:
            parts.append(f"{key}={value}")
    return ", ".join(parts[:10])


def _format_artifact_line(item: dict[str, Any]) -> str:
    stats_inline = format_stats_inline(item.get("stats") or {})
    suffix = f" stats={stats_inline}" if stats_inline else ""
    return (
        f"- `{item.get('artifact_id')}` kind={item.get('kind')} step={item.get('tool_name')} "
        f"status={item.get('status')} conversation={item.get('conversation_id')} "
        f"summary={item.get('summary', '')}{suffix}"
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
            title = (
                item.get("title")
                or item.get("url")
                or item.get("name")
                or item.get("company_name")
                or item.get("case_title")
                or item.get("industry")
                or item.get("region")
            )
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
