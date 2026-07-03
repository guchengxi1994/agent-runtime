from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import AgentRequestError, AgentRuntime
from .config import load_settings
from .models import ChatRequest, ChatResponse, SkillPackage, ToolDefinition, UserContext
from .registry import FileRegistry, RegistryError

settings = load_settings()
registry = FileRegistry(settings.registry_dir)
registry.reload()
runtime = AgentRuntime(settings, registry)
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Agent Runtime", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    if not settings.admin_auth_enabled:
        return
    expected = f"Bearer {settings.admin_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mode": "server_chat_runtime",
        "model": settings.model,
        "plugin_server_url": settings.plugin_server_url,
        "registry_dir": str(settings.registry_dir),
        "admin_auth_enabled": settings.admin_auth_enabled,
        "tools": len(registry.tools),
        "skills": len(registry.skills),
        "conversations": len(runtime.conversations),
    }


@app.post("/admin/reload")
async def reload_registry(_: Annotated[None, Depends(require_admin)]) -> dict[str, object]:
    try:
        registry.reload()
    except RegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "tools": len(registry.tools), "skills": len(registry.skills)}


@app.post("/admin/tools")
async def register_tool(
    tool: ToolDefinition,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, object]:
    registry.save_tool(tool)
    return {"success": True, "id": tool.id}


@app.post("/admin/skills")
async def register_skill(
    skill: SkillPackage,
    _: Annotated[None, Depends(require_admin)],
) -> dict[str, object]:
    name = registry.save_skill(skill)
    return {"success": True, "name": name}


@app.get("/tools")
async def list_tools() -> dict[str, object]:
    return {"tools": [tool.model_dump() for tool in registry.tool_summaries(UserContext())]}


@app.get("/skills")
async def list_skills() -> dict[str, object]:
    return {"skills": [skill.model_dump() for skill in registry.skill_summaries(UserContext())]}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        return await runtime.chat(request)
    except AgentRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
