from __future__ import annotations

from pathlib import Path
import asyncio
import json
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
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
from .models import AgentDefinition, ChatRequest, ChatResponse, SkillPackage, UserContext
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
        "admin_auth_enabled": settings.admin_auth_enabled,
        "agents": len(registry.agents) or 1,
        "skills": len(registry.skills),
        "executable_skills": sum(1 for skill in registry.skills.values() if skill.executable),
        "conversations": len(runtime.conversations),
        "executions": len(runtime.executions),
    }


@app.post("/admin/reload")
async def reload_registry(_: Annotated[None, Depends(require_admin)]) -> dict[str, object]:
    try:
        registry.reload()
    except RegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "agents": len(registry.agents) or 1, "skills": len(registry.skills)}


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
    return {"skills": [skill.model_dump() for skill in registry.skill_summaries(UserContext())]}


@app.get("/executions")
async def list_executions() -> dict[str, object]:
    return {"executions": runtime.executions[-100:]}


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
                item = await queue.get()
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
