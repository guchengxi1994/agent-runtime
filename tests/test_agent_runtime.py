from __future__ import annotations

import asyncio
import json

from agent_runtime.agent import AgentRuntime
from agent_runtime.app import app
from agent_runtime.config import AgentRuntimeSettings, load_settings
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
        openai_api_key="test-key",
        openai_base_url=None,
        reasoning_effort=None,
        expose_reasoning_content=False,
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


def test_load_settings_reads_env_file_for_model_base_url(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_RUNTIME_MODEL=test-reasoning-model",
                "OPENAI_API_KEY=test-env-key # local development key",
                "OPENAI_BASE_URL=https://llm-gateway.example/v1",
                "AGENT_RUNTIME_EXPOSE_REASONING_CONTENT=true",
                f"AGENT_RUNTIME_REGISTRY_DIR={tmp_path / 'registry'}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_RUNTIME_ENV_FILE", str(env_file))
    monkeypatch.delenv("AGENT_RUNTIME_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_EXPOSE_REASONING_CONTENT", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_REGISTRY_DIR", raising=False)

    settings = load_settings()

    assert settings.model == "test-reasoning-model"
    assert settings.openai_api_key == "test-env-key"
    assert settings.openai_base_url == "https://llm-gateway.example/v1"
    assert settings.env_file_loaded == str(env_file.resolve())
    assert settings.openai_api_key_source == f"dotenv:{env_file.resolve()}"
    assert settings.openai_base_url_source == f"dotenv:{env_file.resolve()}"
    assert settings.expose_reasoning_content is True


def test_env_file_overrides_inherited_openai_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=dotenv-key",
                "OPENAI_BASE_URL=https://dotenv.example/v1",
                f"AGENT_RUNTIME_REGISTRY_DIR={tmp_path / 'registry'}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_RUNTIME_ENV_FILE", str(env_file))
    monkeypatch.setenv("OPENAI_API_KEY", "inherited-process-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://process.example/v1")
    monkeypatch.delenv("AGENT_RUNTIME_REGISTRY_DIR", raising=False)

    settings = load_settings()

    assert settings.openai_api_key == "dotenv-key"
    assert settings.openai_base_url == "https://dotenv.example/v1"
    assert settings.openai_api_key_source == f"dotenv:{env_file.resolve()}"
    assert settings.openai_base_url_source == f"dotenv:{env_file.resolve()}"


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
    assert response.steps
    assert any(step.kind == "thinking" for step in response.steps)
    assert any(step.kind == "waiting_for_user" for step in response.steps)
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
