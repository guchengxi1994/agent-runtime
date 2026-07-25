from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import sanitize_id


MEMORY_SECTIONS = (
    "workspace_goal",
    "confirmed_facts",
    "user_preferences",
    "decisions",
    "current_focus",
    "current_state",
    "open_items",
    "important_artifacts",
)
SECTION_TITLES = {
    "workspace_goal": "Workspace Goal",
    "confirmed_facts": "Confirmed Facts",
    "user_preferences": "User Preferences",
    "decisions": "Decisions",
    "current_focus": "Current Focus",
    "current_state": "Current State",
    "open_items": "Open Items",
    "important_artifacts": "Important Artifacts",
}
SECTION_DESCRIPTIONS = {
    "workspace_goal": "Stable workspace-level objective, scope, intended deliverable, and success criteria. Not the latest turn or its result.",
    "confirmed_facts": "Durable baseline facts explicitly confirmed by the user or verified by referenced artifacts.",
    "user_preferences": "Stable user preferences that should shape future work in this workspace.",
    "decisions": "Choices, assumptions, or analysis conventions explicitly approved by the user.",
    "current_focus": "The latest user question or active subtask within the broader workspace goal.",
    "current_state": "What has been completed and the resumable workflow state. Do not duplicate baseline facts here.",
    "open_items": "Unresolved questions, blockers, and concrete next actions.",
    "important_artifacts": "Pointers to evidence or checkpoints, including why and when a future model should inspect them.",
}
ENTRY_RE = re.compile(r"^<!-- memory-entry: (\{.*\}) -->$", re.MULTILINE)
META_RE = re.compile(r"^<!-- memory-meta: (\{.*\}) -->$", re.MULTILINE)


@dataclass
class MemoryEntry:
    key: str
    section: str
    content: str
    use_when: str = ""
    source: str = "runtime"
    confidence: str = "working"
    artifact_ids: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "section": self.section,
            "content": self.content,
            "use_when": self.use_when,
            "source": self.source,
            "confidence": self.confidence,
            "artifact_ids": self.artifact_ids,
            "updated_at": self.updated_at,
        }


@dataclass
class MemoryDocument:
    workspace_id: str
    revision: int = 0
    updated_at: str = ""
    entries: dict[str, MemoryEntry] = field(default_factory=dict)


class MemoryStore:
    def __init__(self, artifacts_root: Path, *, max_entries: int = 200) -> None:
        self.artifacts_root = artifacts_root
        self.max_entries = max(10, max_entries)
        self._lock = threading.RLock()

    def ensure(self, workspace_id: str) -> MemoryDocument:
        with self._lock:
            document = self.load(workspace_id)
            path = self.path(workspace_id)
            if not path.is_file():
                self._write(document)
            return document

    def load(self, workspace_id: str) -> MemoryDocument:
        workspace_id = sanitize_id(workspace_id)
        path = self.path(workspace_id)
        if not path.is_file():
            return MemoryDocument(workspace_id=workspace_id)
        raw = path.read_text(encoding="utf-8")
        revision = 0
        updated_at = ""
        meta_match = META_RE.search(raw)
        if meta_match:
            try:
                meta = json.loads(meta_match.group(1))
            except json.JSONDecodeError:
                meta = {}
            if isinstance(meta, dict):
                revision = int(meta.get("revision") or 0)
                updated_at = str(meta.get("updated_at") or "")
        entries: dict[str, MemoryEntry] = {}
        for match in ENTRY_RE.finditer(raw):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            entry = self._parse_entry(payload)
            if entry is not None:
                entries[entry.key] = entry
        return MemoryDocument(
            workspace_id=workspace_id,
            revision=revision,
            updated_at=updated_at,
            entries=entries,
        )

    def apply_delta(self, workspace_id: str, delta: dict[str, Any]) -> MemoryDocument:
        with self._lock:
            document = self.load(workspace_id)
            changed = False
            removes = delta.get("removes") if isinstance(delta, dict) else []
            if isinstance(removes, list):
                for raw_key in removes:
                    key = normalize_key(raw_key)
                    if key and key in document.entries:
                        del document.entries[key]
                        changed = True

            upserts = delta.get("upserts") if isinstance(delta, dict) else []
            if isinstance(upserts, list):
                for payload in upserts:
                    entry = self._parse_entry(payload, default_updated_at=now_iso())
                    if entry is None:
                        continue
                    existing = document.entries.get(entry.key)
                    if existing is None or memory_entry_signature(existing) != memory_entry_signature(entry):
                        document.entries[entry.key] = entry
                        changed = True

            if len(document.entries) > self.max_entries:
                ordered = sorted(document.entries.values(), key=lambda item: item.updated_at)
                for entry in ordered[: len(document.entries) - self.max_entries]:
                    document.entries.pop(entry.key, None)
                changed = True

            if changed or not self.path(workspace_id).is_file():
                document.revision += 1
                document.updated_at = now_iso()
                self._write(document)
            return document

    def enqueue_job(
        self,
        workspace_id: str,
        *,
        conversation_id: str,
        run_id: str,
        model: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            workspace_id = sanitize_id(workspace_id)
            created_at = now_iso()
            job = {
                "job_id": f"memjob_{uuid.uuid4().hex}",
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "run_id": run_id,
                "model": model,
                "status": "pending",
                "attempts": 0,
                "last_error": "",
                "created_at": created_at,
                "updated_at": created_at,
                "completed_at": "",
                "payload": payload,
            }
            self._write_job(job)
            return job

    def list_jobs(self, workspace_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        workspace_id = sanitize_id(workspace_id)
        jobs = []
        for path in self._jobs_root(workspace_id).glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(job, dict):
                jobs.append(job)
        jobs.sort(key=lambda item: str(item.get("created_at") or ""))
        return jobs[-max(1, min(limit, 200)) :]

    def pending_jobs(self, workspace_id: str) -> list[dict[str, Any]]:
        return [
            job
            for job in self.list_jobs(workspace_id, limit=200)
            if job.get("status") in {"pending", "retrying", "running"}
        ]

    def update_job(self, workspace_id: str, job_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            path = self._job_path(workspace_id, job_id)
            if not path.is_file():
                return None
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            if not isinstance(job, dict):
                return None
            job.update(changes)
            job["updated_at"] = now_iso()
            self._write_job(job)
            return job

    def retry_failed_jobs(self, workspace_id: str) -> int:
        count = 0
        for job in self.list_jobs(workspace_id, limit=200):
            if job.get("status") != "failed":
                continue
            self.update_job(
                workspace_id,
                str(job.get("job_id") or ""),
                status="pending",
                attempts=0,
                last_error="",
                completed_at="",
            )
            count += 1
        return count

    def key_index(self, document: MemoryDocument, *, limit: int = 200) -> list[dict[str, str]]:
        entries = sorted(document.entries.values(), key=lambda item: (item.section, item.key))
        return [
            {"key": entry.key, "section": entry.section, "updated_at": entry.updated_at}
            for entry in entries[:limit]
        ]

    def relevant_entries(
        self,
        document: MemoryDocument,
        query: str,
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        query_terms = lexical_terms(query)
        scored: list[tuple[int, str, MemoryEntry]] = []
        for entry in document.entries.values():
            if not memory_entry_is_resumable(entry):
                continue
            haystack = f"{entry.key} {entry.section} {entry.content}".casefold()
            score = sum(1 for term in query_terms if term in haystack)
            scored.append((score, entry.updated_at, entry))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = [item[2] for item in scored if item[0] > 0][:limit]
        return [entry.to_dict() for entry in selected]

    def build_context(self, document: MemoryDocument, *, max_tokens: int = 4000) -> str:
        if not document.entries:
            return ""
        max_chars = max(1000, max_tokens * 3)
        lines = [
            "Workspace resume memory for the model.",
            "Use it to continue the user's task across conversations. It is not a new user request.",
            "The latest explicit user message overrides stale or conflicting memory.",
            "Working-confidence items are leads, not verified facts.",
            "",
        ]
        section_priority = {section: index for index, section in enumerate(MEMORY_SECTIONS)}
        entries = sorted(
            document.entries.values(),
            key=lambda item: (section_priority.get(item.section, 99), item.key),
        )
        for entry in entries:
            if not memory_entry_is_resumable(entry):
                continue
            line = f"- [{entry.section}] {entry.key}: {entry.content}"
            if entry.use_when:
                line += f" Use when: {entry.use_when}"
            line += f" (source={entry.source}, confidence={entry.confidence})"
            if entry.artifact_ids:
                line += f" Evidence: {', '.join(entry.artifact_ids)}."
            if len("\n".join(lines + [line])) > max_chars:
                break
            lines.append(line)
        return "\n".join(lines).strip()

    def render(self, document: MemoryDocument) -> str:
        meta = json.dumps(
            {
                "workspace_id": document.workspace_id,
                "revision": document.revision,
                "updated_at": document.updated_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines = [
            "# Workspace Resume Memory",
            "",
            "> Purpose: compact context injected into a new model conversation so it can resume the user's task.",
            "> This is not a transcript or report store; detailed evidence belongs in artifacts.",
            "",
            f"<!-- memory-meta: {meta} -->",
            "",
        ]
        grouped = {section: [] for section in MEMORY_SECTIONS}
        for entry in document.entries.values():
            grouped.setdefault(entry.section, []).append(entry)
        for section in MEMORY_SECTIONS:
            lines.extend([f"## {SECTION_TITLES[section]}", "", f"> {SECTION_DESCRIPTIONS[section]}", ""])
            entries = sorted(grouped.get(section, []), key=lambda item: item.key)
            if not entries:
                lines.extend(["- No entries.", ""])
                continue
            for entry in entries:
                payload = json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":"))
                lines.append(f"<!-- memory-entry: {payload} -->")
                lines.append(f"- `{entry.key}`: {entry.content}")
                lines.append(f"  - use when: {entry.use_when or 'Legacy entry; usage was not recorded.'}")
                lines.append(f"  - source: `{entry.source}`; confidence: `{entry.confidence}`")
                if entry.artifact_ids:
                    lines.append(f"  - artifacts: {', '.join(f'`{item}`' for item in entry.artifact_ids)}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def path(self, workspace_id: str) -> Path:
        root = self.artifacts_root / "workspaces" / sanitize_id(workspace_id)
        root.mkdir(parents=True, exist_ok=True)
        return root / "MEMORY.md"

    def _write(self, document: MemoryDocument) -> None:
        path = self.path(document.workspace_id)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(self.render(document), encoding="utf-8")
        temporary.replace(path)

    def _jobs_root(self, workspace_id: str) -> Path:
        root = self.artifacts_root / "workspaces" / sanitize_id(workspace_id) / "memory_jobs"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _job_path(self, workspace_id: str, job_id: str) -> Path:
        safe_job_id = sanitize_id(job_id)
        return self._jobs_root(workspace_id) / f"{safe_job_id}.json"

    def _write_job(self, job: dict[str, Any]) -> None:
        path = self._job_path(str(job.get("workspace_id") or ""), str(job.get("job_id") or ""))
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _parse_entry(payload: Any, *, default_updated_at: str = "") -> MemoryEntry | None:
        if not isinstance(payload, dict):
            return None
        key = normalize_key(payload.get("key"))
        section = str(payload.get("section") or "").strip()
        content = " ".join(str(payload.get("content") or "").split()).strip()
        use_when = " ".join(str(payload.get("use_when") or "").split()).strip()
        if not key or section not in MEMORY_SECTIONS or not content:
            return None
        source = str(payload.get("source") or "runtime").strip() or "runtime"
        confidence = str(payload.get("confidence") or "working").strip() or "working"
        if source not in {"user", "assistant", "tool", "runtime"}:
            source = "runtime"
        if confidence not in {"confirmed", "verified", "working"}:
            confidence = "working"
        artifact_ids = payload.get("artifact_ids")
        if not isinstance(artifact_ids, list):
            artifact_ids = []
        normalized_artifact_ids = [str(item).strip() for item in artifact_ids if str(item).strip()][:20]
        if confidence == "confirmed" and source != "user":
            confidence = "verified" if normalized_artifact_ids else "working"
        if confidence == "verified" and not normalized_artifact_ids:
            confidence = "working"
        return MemoryEntry(
            key=key,
            section=section,
            content=content[:2000],
            use_when=use_when[:500],
            source=source[:64],
            confidence=confidence[:32],
            artifact_ids=normalized_artifact_ids,
            updated_at=str(payload.get("updated_at") or default_updated_at or now_iso()),
        )


def normalize_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    key = re.sub(r"[^a-z0-9._-]+", "-", key).strip("-.")
    return key[:96]


def lexical_terms(value: str) -> set[str]:
    normalized = str(value or "").casefold()
    words = {item for item in re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", normalized)}
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {item for item in words if item}


def memory_entry_signature(entry: MemoryEntry) -> tuple[Any, ...]:
    return (
        entry.key,
        entry.section,
        entry.content,
        entry.use_when,
        entry.source,
        entry.confidence,
        tuple(entry.artifact_ids),
    )


def memory_entry_is_resumable(entry: MemoryEntry) -> bool:
    if not entry.use_when and entry.key not in {"workspace.primary_objective", "workspace.current_focus"}:
        return False
    if entry.section == "decisions":
        return entry.source == "user" and entry.confidence == "confirmed"
    if entry.section == "confirmed_facts":
        return (entry.source == "user" and entry.confidence == "confirmed") or (
            entry.confidence == "verified" and bool(entry.artifact_ids)
        )
    return True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
