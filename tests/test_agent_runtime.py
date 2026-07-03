from __future__ import annotations

import asyncio
import json

from agent_runtime.agent import AgentRuntime
from agent_runtime.app import app
from agent_runtime.config import AgentRuntimeSettings
from agent_runtime.models import ChatRequest, PermissionPolicy, SkillSummary, UserContext
from agent_runtime.permissions import is_allowed
from agent_runtime.registry import FileRegistry
from fastapi.testclient import TestClient


def write_skill(
    skill_dir,
    *,
    name: str = "demo-skill",
    body: str = "Use this harness carefully.",
    runtime_metadata: str = "",
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"""---
name: {name}
description: Demo harness document for tests.
metadata:
  owner: tests
{runtime_metadata}
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
        sandbox_url="http://127.0.0.1:8001",
        admin_token=None,
        max_runtime_rounds=1,
        request_timeout_seconds=30,
    )
    settings.ensure_directories()
    return settings


def test_registry_loads_executable_skill_summary_from_frontmatter(tmp_path):
    settings = make_settings(tmp_path)
    write_skill(
        settings.registry_dir / "skills" / "demo-skill",
        body="When needed, call this executable skill.",
        runtime_metadata="""  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties: {}
      additionalProperties: false
""",
    )
    settings.registry_dir.joinpath("skills", "demo-skill", "skill.py").write_text(
        "definition = {'name': 'demo'}\ndef execute(params):\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    registry = FileRegistry(settings.registry_dir)
    registry.reload()

    assert list(registry.skills) == ["demo-skill"]
    summary = registry.skill_summaries(UserContext())[0]
    assert summary.name == "demo-skill"
    assert summary.description == "Demo harness document for tests."
    assert summary.executable is True
    assert set(SkillSummary.model_fields) == {
        "name",
        "description",
        "enabled",
        "executable",
        "capability_hints",
        "resources",
    }


def test_permissions_require_all_scopes():
    policy = PermissionPolicy(scopes=["skills:execute", "finance:read"])

    assert is_allowed(policy, UserContext(scopes=["skills:execute", "finance:read"]))
    assert not is_allowed(policy, UserContext(scopes=["skills:execute"]))


def test_activate_skill_returns_full_harness_without_backend_parsing(tmp_path):
    settings = make_settings(tmp_path)
    body = "When the question is about steel markets, decide the analysis workflow from this text."
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


def test_request_user_input_pauses_and_records_tool_result(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.openai = FakeOpenAI(
        tool_name="request_user_input",
        arguments={
            "question": "需要哪个表达式？",
            "fields": [{"name": "expression", "label": "表达式"}],
        },
    )

    response = asyncio.run(runtime.chat(ChatRequest(message="帮我算一下")))
    conversation = runtime.conversations[response.conversation_id]
    tool_messages = [message for message in conversation.messages if message.get("role") == "tool"]

    assert response.status == "waiting_for_user"
    assert response.message == "需要哪个表达式？"
    assert response.requested_inputs[0].name == "expression"
    assert conversation.pending_input_request is not None
    assert tool_messages
    assert json.loads(tool_messages[-1]["content"])["status"] == "waiting_for_user"


def test_frontend_entrypoint_serves_static_page():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Harness-driven analysis console" in response.text


class FakeOpenAI:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.chat = FakeChat(tool_name=tool_name, arguments=arguments)


class FakeChat:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.completions = FakeCompletions(tool_name=tool_name, arguments=arguments)


class FakeCompletions:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.tool_name = tool_name
        self.arguments = arguments

    async def create(self, **_kwargs):
        return FakeCompletion(self.tool_name, self.arguments)


class FakeCompletion:
    def __init__(self, tool_name: str, arguments: dict):
        self.choices = [FakeChoice(tool_name, arguments)]


class FakeChoice:
    def __init__(self, tool_name: str, arguments: dict):
        self.message = FakeMessage(tool_name, arguments)


class FakeMessage:
    def __init__(self, tool_name: str, arguments: dict):
        self.content = None
        self.tool_calls = [FakeToolCall(tool_name, arguments)]

    def model_dump(self, exclude_none: bool = True):
        payload = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": self.tool_calls[0].id,
                    "type": "function",
                    "function": {
                        "name": self.tool_calls[0].function.name,
                        "arguments": self.tool_calls[0].function.arguments,
                    },
                }
            ],
        }
        if not exclude_none:
            payload["content"] = None
        return payload


class FakeToolCall:
    def __init__(self, tool_name: str, arguments: dict):
        self.id = "call_request_input"
        self.function = FakeFunction(tool_name, arguments)


class FakeFunction:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = json.dumps(arguments, ensure_ascii=False)
