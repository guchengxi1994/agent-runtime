from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import AgentRequestError, AgentRuntime
from .config import load_settings
from .logging_utils import setup_logging
from .models import AgentDefinition, ChatRequest, ChatResponse, SkillPackage, UserContext
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


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        return await runtime.chat(request)
    except AgentRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
