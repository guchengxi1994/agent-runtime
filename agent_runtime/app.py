from __future__ import annotations

from pathlib import Path
import asyncio
import hashlib
import json
import re
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from .agent import AgentRequestError, AgentRuntime
from .config import load_settings
from .file_ingest import (
    FileIngestError,
    parse_form_json_field,
    parse_form_skill_ids,
    parse_uploaded_files,
)
from .logging_utils import setup_logging
from .models import AgentDefinition, ChatRequest, ChatResponse, SkillPackage, SkillSummary, UserContext
from .mcp import builtin_mcp_skill
from .models import RuntimeStepTrace
from .registry import FileRegistry, RegistryError

settings = load_settings()
setup_logging()
registry = FileRegistry(settings.registry_dir)
registry.reload()
runtime = AgentRuntime(settings, registry)
STATIC_DIR = Path(__file__).resolve().parent / "static"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
MAX_DOCUMENT_UPLOAD_BYTES = 100 * 1024 * 1024


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict) -> FileResponse:
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE_HEADERS)
        return response

app = FastAPI(title="Agent Runtime", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")


def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    if not settings.admin_auth_enabled:
        return
    expected = f"Bearer {settings.admin_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": "server_chat_runtime",
        "model": settings.model,
        "memory_model": settings.memory_model or settings.model,
        "model_context_tokens": settings.model_context_tokens,
        "context_compaction_threshold": settings.context_compaction_threshold,
        "context_compaction_threshold_tokens": int(
            settings.model_context_tokens * settings.context_compaction_threshold
        ),
        "memory_enabled": settings.memory_enabled,
        "memory_context_tokens": settings.memory_context_tokens,
        "memory_max_entries": settings.memory_max_entries,
        "openai_api_key_configured": bool(settings.openai_api_key),
        "openai_api_key_source": settings.openai_api_key_source,
        "openai_base_url": settings.openai_base_url,
        "openai_base_url_source": settings.openai_base_url_source,
        "reasoning_effort": settings.reasoning_effort,
        "expose_reasoning_content": settings.expose_reasoning_content,
        "env_file_loaded": settings.env_file_loaded,
        "registry_dir": str(settings.registry_dir),
        "artifacts_dir": str(settings.artifacts_dir),
        "sandbox_url": settings.sandbox_url,
        "mcp_gateway_url": settings.mcp_gateway_url,
        "admin_auth_enabled": settings.admin_auth_enabled,
        "agents": len(registry.agents) or 1,
        "skills": len(registry.skills),
        "executable_skills": sum(1 for skill in registry.skills.values() if skill.executable),
        "mcp_servers": len(registry.mcp_servers),
        "conversations": len(runtime.conversations),
        "executions": len(runtime.executions),
    }


@app.post("/admin/reload")
async def reload_registry(_: Annotated[None, Depends(require_admin)]) -> dict[str, object]:
    try:
        registry.reload()
    except RegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "success": True,
        "agents": len(registry.agents) or 1,
        "skills": len(registry.skills),
        "mcp_servers": len(registry.mcp_servers),
    }


@app.post("/admin/agents")
async def register_agent(
    agent: AgentDefinition,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, object]:
    registry.save_agent(agent)
    return {"success": True, "id": agent.id}


@app.post("/admin/skills")
async def register_skill(
    skill: SkillPackage,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, object]:
    name = registry.save_skill(skill)
    return {"success": True, "name": name}


@app.get("/agents")
async def list_agents() -> dict[str, object]:
    return {"agents": [agent.model_dump() for agent in registry.agent_summaries(UserContext())]}


@app.get("/skills")
async def list_skills() -> dict[str, object]:
    user = UserContext()
    skills = registry.skill_summaries(user)
    servers = registry.accessible_mcp_servers(user)
    if servers:
        builtin = builtin_mcp_skill(servers)
        skills.append(
            SkillSummary(
                name=builtin.name,
                description=builtin.description,
                enabled=True,
                executable=False,
            )
        )
    return {"skills": [skill.model_dump() for skill in skills]}


@app.get("/mcp-servers")
async def list_mcp_servers() -> dict[str, object]:
    return {"servers": [server.model_dump() for server in registry.mcp_server_summaries(UserContext())]}


@app.get("/executions")
async def list_executions() -> dict[str, object]:
    return {"executions": runtime.executions[-100:]}


@app.get("/workspaces/{workspace_id}/memory")
async def get_workspace_memory(workspace_id: str) -> dict[str, object]:
    document = runtime.memory.ensure(workspace_id)
    jobs = runtime.memory.list_jobs(workspace_id, limit=20)
    job_counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        job_counts[status] = job_counts.get(status, 0) + 1
    return {
        "workspace_id": document.workspace_id,
        "revision": document.revision,
        "updated_at": document.updated_at,
        "entries": [entry.to_dict() for entry in document.entries.values()],
        "model_context": runtime.memory.build_context(
            document,
            max_tokens=settings.memory_context_tokens,
        ),
        "markdown": runtime.memory.render(document),
        "jobs": [
            {key: value for key, value in job.items() if key != "payload"}
            for job in jobs
        ],
        "job_counts": job_counts,
    }


@app.post("/workspaces/{workspace_id}/memory/retry")
async def retry_workspace_memory(workspace_id: str) -> dict[str, object]:
    retried = runtime.retry_memory_jobs(workspace_id)
    return {"workspace_id": workspace_id, "retried": retried}


@app.post("/workspaces/{workspace_id}/documents/upload")
async def upload_workspace_document(workspace_id: str, file: UploadFile = File(...)) -> dict[str, object]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    filename = _safe_document_filename(file.filename)
    payload = await file.read(MAX_DOCUMENT_UPLOAD_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded document is empty.")
    if len(payload) > MAX_DOCUMENT_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Document exceeds {MAX_DOCUMENT_UPLOAD_BYTES} bytes.")
    digest = hashlib.sha256(payload).hexdigest()
    stored_name = f"{digest[:12]}-{filename}"
    root = settings.artifacts_dir / "workspaces" / normalized_workspace_id / "document_knowledge" / "uploads_raw"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / stored_name
    if not destination.exists():
        destination.write_bytes(payload)
    return {
        "success": True,
        "workspace_id": normalized_workspace_id,
        "source_path": str(destination.relative_to(settings.artifacts_dir / "workspaces" / normalized_workspace_id)),
        "filename": filename,
        "size_bytes": len(payload),
        "sha256": digest,
        "next_action": "Ask the agent to call document-markdown-convert with source_path. The raw file remains outside chat context.",
    }


async def build_chat_request(http_request: Request) -> ChatRequest:
    content_type = (http_request.headers.get("content-type") or "").lower()
    try:
        if content_type.startswith("application/json"):
            payload = await http_request.json()
            return ChatRequest.model_validate(payload)

        if content_type.startswith("multipart/form-data") or content_type.startswith("application/x-www-form-urlencoded"):
            form = await http_request.form()
            files: list[StarletteUploadFile] = [
                value
                for _, value in form.multi_items()
                if isinstance(value, StarletteUploadFile)
            ]
            attachments = await parse_uploaded_files(files)
            metadata = parse_form_json_field(form.get("metadata"), field_name="metadata", expected_type=dict)
            skill_ids = parse_form_skill_ids(form.getlist("skill_ids"), form.get("skill_ids"))
            user = parse_form_json_field(form.get("user"), field_name="user", expected_type=dict)
            message = str(form.get("message") or "").strip()
            if not message and attachments:
                message = "Please read the uploaded files and continue with the task."
            return ChatRequest.model_validate(
                {
                    "message": message,
                    "agent_id": str(form.get("agent_id") or "default"),
                    "workspace_id": str(form.get("workspace_id") or "").strip() or None,
                    "conversation_id": str(form.get("conversation_id") or "").strip() or None,
                    "skill_ids": skill_ids,
                    "attachments": [attachment.model_dump() for attachment in attachments],
                    "user": user,
                    "metadata": metadata,
                }
            )
    except (ValidationError, FileIngestError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raise HTTPException(status_code=415, detail="Unsupported content type. Use application/json or multipart/form-data.")


def _normalize_workspace_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-_").lower()[:80]
    if not normalized or normalized != value:
        raise HTTPException(status_code=400, detail="workspace_id must contain only lowercase letters, digits, underscores, or hyphens.")
    return normalized


def _safe_document_filename(value: str | None) -> str:
    name = Path(value or "document").name.strip()
    cleaned = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip(".-")
    if not cleaned:
        raise HTTPException(status_code=400, detail="Uploaded document filename is invalid.")
    return cleaned[:180]


@app.post("/chat", response_model=ChatResponse)
async def chat(http_request: Request) -> ChatResponse:
    request = await build_chat_request(http_request)
    try:
        return await runtime.chat(request)
    except AgentRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/chat/stream")
async def chat_stream(http_request: Request) -> StreamingResponse:
    request = await build_chat_request(http_request)

    async def event_stream():
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        heartbeat_seconds = 10.0

        def on_step(step: RuntimeStepTrace) -> None:
            queue.put_nowait({"event": "step", "data": step.model_dump(mode="json")})

        def on_delta(payload: dict[str, object]) -> None:
            queue.put_nowait({"event": "delta", "data": payload})

        async def worker() -> None:
            try:
                response = await runtime.chat(request, on_step=on_step, on_delta=on_delta)
                await queue.put({"event": "message", "data": response.model_dump(mode="json")})
                await queue.put({"event": "done", "data": {"conversation_id": response.conversation_id}})
            except AgentRequestError as exc:
                await queue.put({"event": "error", "data": {"error": str(exc)}})
            except Exception as exc:
                await queue.put({"event": "error", "data": {"error": str(exc)}})

        task = asyncio.create_task(worker())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                event = str(item["event"])
                data = item["data"]
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if event in {"done", "error"}:
                    break
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=NO_CACHE_HEADERS,
    )
