from __future__ import annotations

import json

from agent_runtime.agent import AgentRuntime
from agent_runtime.app import app
from agent_runtime.config import AgentRuntimeSettings
from agent_runtime.models import ChatRequest, SkillSummary, ToolDefinition, UserContext
from agent_runtime.permissions import is_allowed
from agent_runtime.registry import FileRegistry
from fastapi.testclient import TestClient


def write_json(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_skill(skill_dir, *, name: str = "demo-skill", body: str = "Use this harness carefully.") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"""---
name: {name}
description: Demo harness document for tests.
metadata:
  owner: tests
---

{body}
""",
        encoding="utf-8",
    )


def make_settings(tmp_path) -> AgentRuntimeSettings:
    registry_dir = tmp_path / "registry"
    settings = AgentRuntimeSettings(
        host="127.0.0.1",
        port=8010,
        model="test-model",
        registry_dir=registry_dir,
        plugin_server_url="http://127.0.0.1:8001",
        admin_token=None,
        max_tool_rounds=1,
        request_timeout_seconds=30,
    )
    settings.ensure_directories()
    return settings


def make_tool(tool_id: str = "demo_tool", scopes: list[str] | None = None) -> ToolDefinition:
    return ToolDefinition(
        id=tool_id,
        name=tool_id,
        description="Demo tool",
        parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
        script="definition = {'name': 'demo'}\ndef execute(params):\n    return {'ok': True}\n",
        permissions={"scopes": scopes or []},
    )


def test_registry_loads_skill_summary_from_frontmatter_only(tmp_path):
    settings = make_settings(tmp_path)
    tool = make_tool()
    write_json(settings.registry_dir / "tools" / "demo_tool.json", tool.model_dump())
    write_skill(settings.registry_dir / "skills" / "demo-skill", body="When needed, use demo_tool.")

    registry = FileRegistry(settings.registry_dir)
    registry.reload()

    assert list(registry.tools) == ["demo_tool"]
    assert list(registry.skills) == ["demo-skill"]
    summary = registry.skill_summaries(UserContext())[0]
    assert summary.name == "demo-skill"
    assert summary.description == "Demo harness document for tests."
    assert set(SkillSummary.model_fields) == {"name", "description", "enabled", "resources"}


def test_permissions_require_all_scopes():
    tool = make_tool(scopes=["tools:run", "finance:read"])

    assert is_allowed(tool.permissions, UserContext(scopes=["tools:run", "finance:read"]))
    assert not is_allowed(tool.permissions, UserContext(scopes=["tools:run"]))


def test_activate_skill_returns_full_harness_without_backend_parsing(tmp_path):
    settings = make_settings(tmp_path)
    tool = make_tool()
    body = "When the question is about steel markets, decide the analysis workflow from this text."
    write_json(settings.registry_dir / "tools" / "demo_tool.json", tool.model_dump())
    write_skill(settings.registry_dir / "skills" / "demo-skill", body=body)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    request = ChatRequest(message="analyze steel market")
    conversation = runtime._get_or_create_conversation(request)
    available = runtime._available_skills(request)
    activated = runtime._explicit_or_active_skills(request, conversation, available)

    result = runtime._activate_skill(
        {"skill_name": "demo-skill"},
        available,
        activated,
        conversation,
    )

    assert result["success"] is True
    assert result["body"] == body
    assert conversation.active_skill_ids == ["demo-skill"]


def test_frontend_entrypoint_serves_static_page():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Harness-driven analysis console" in response.text
