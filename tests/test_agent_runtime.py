from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

from agent_runtime.artifacts import ArtifactStore
from agent_runtime.agent import AgentRuntime
from agent_runtime.app import app
from agent_runtime.config import AgentRuntimeSettings, load_settings
from agent_runtime.models import ChatRequest, PermissionPolicy, SkillSummary, UserContext
from agent_runtime.permissions import is_allowed
from agent_runtime.registry import FileRegistry
from fastapi.testclient import TestClient
from sandbox.runtime.models import ExecutionConfig
from sandbox.runtime.process import cached_dependencies_match, write_dependency_marker


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
        artifacts_dir=tmp_path / "artifacts",
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
                f"AGENT_RUNTIME_ARTIFACTS_DIR={tmp_path / 'artifacts'}",
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
    monkeypatch.delenv("AGENT_RUNTIME_ARTIFACTS_DIR", raising=False)

    settings = load_settings()

    assert settings.model == "test-reasoning-model"
    assert settings.openai_api_key == "test-env-key"
    assert settings.openai_base_url == "https://llm-gateway.example/v1"
    assert settings.env_file_loaded == str(env_file.resolve())
    assert settings.openai_api_key_source == f"dotenv:{env_file.resolve()}"
    assert settings.openai_base_url_source == f"dotenv:{env_file.resolve()}"
    assert settings.expose_reasoning_content is True
    assert settings.artifacts_dir == (tmp_path / "artifacts").resolve()


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


def test_system_prompt_uses_contextual_continuation_policy(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    prompt = runtime._build_system_prompt(
        registry.get_agent("default"),
        runtime._available_skills(ChatRequest(message="test")),
        [],
    )

    assert "infer from the full conversation" in prompt
    assert "Do not rely on literal keyword matching" in prompt
    assert "avoid repeating successful tool calls" in prompt


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
    artifacts = runtime.artifacts.list_artifacts(response.conversation_id)["artifacts"]
    model_trace = next(item for item in artifacts if item["kind"] == "model_planning")
    model_trace_content = json.loads(runtime.artifacts.read_artifact(response.conversation_id, model_trace["artifact_id"])["content"])
    assert model_trace["tool_name"] == "model_planning"
    assert model_trace_content["request"]["messages"][0]["role"] == "system"
    assert model_trace_content["response"]["tool_calls"][0]["function"]["name"] == "request_user_input"


def test_request_user_input_does_not_synthesize_missing_field_guidance(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.openai = FakeOpenAI(
        tool_name="request_user_input",
        arguments={
            "question": "为了精准规划研究方向，请您回答以下问题。",
            "fields": [{"name": "primary_question"}, {"name": "depth"}, {"name": "scope"}],
        },
    )

    response = asyncio.run(runtime.chat(ChatRequest(message="调研炼钢能耗平衡")))

    assert response.status == "waiting_for_user"
    assert [field.name for field in response.requested_inputs] == ["primary_question", "depth", "scope"]
    assert [field.label for field in response.requested_inputs] == [None, None, None]
    assert [field.description for field in response.requested_inputs] == ["", "", ""]


def test_streaming_final_answer_emits_token_deltas(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.openai = FakeStreamingOpenAI(
        [
            {"content": "你"},
            {"content": "好"},
        ]
    )
    deltas = []

    response = asyncio.run(runtime.chat(ChatRequest(message="打个招呼"), on_delta=deltas.append))

    assert response.message == "你好"
    assert [delta["delta"] for delta in deltas if delta["kind"] == "assistant"] == ["你", "好"]


def test_streaming_tool_call_emits_planning_deltas(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    arguments = json.dumps(
        {
            "question": "需要确认研究主问题。",
            "fields": [{"name": "primary_question"}],
        },
        ensure_ascii=False,
    )
    runtime.openai = FakeStreamingOpenAI(
        [
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_stream",
                        "type": "function",
                        "function": {"name": "request_user_input", "arguments": ""},
                    }
                ]
            },
            {"tool_calls": [{"index": 0, "function": {"arguments": arguments[:12]}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": arguments[12:]}}]},
        ]
    )
    deltas = []

    response = asyncio.run(runtime.chat(ChatRequest(message="调研炼钢能耗"), on_delta=deltas.append))

    assert response.status == "waiting_for_user"
    assert any(delta["kind"] == "tool_call" and delta["phase"] == "name" for delta in deltas)
    assert any(delta["kind"] == "tool_call" and delta["phase"] == "arguments" for delta in deltas)


def test_frontend_entrypoint_serves_static_page():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "对话入口" in response.text


def test_builtin_research_skills_are_registered():
    registry = FileRegistry(Path("registry").resolve())
    registry.reload()

    assert "academic-deep-research" in registry.skills
    assert registry.skills["academic-deep-research"].executable is False
    assert registry.skills["web-search"].executable is True
    assert registry.skills["web-search"].execution_policy["packages"] == ["requests==2.32.3"]
    assert registry.skills["web-fetch"].executable is True
    assert registry.skills["web-fetch"].execution_policy["packages"] == [
        "requests==2.32.3",
        "beautifulsoup4==4.12.3",
    ]
    assert registry.skills["web-search-quark"].executable is True
    assert registry.skills["web-search-quark"].execution_policy["packages"] == ["requests==2.32.3"]
    assert "no-API-key" in registry.skills["web-search-quark"].description
    assert "web-search-quark" in registry.skills["web-search"].description
    assert registry.skills["steel-energy-control"].executable is False
    assert registry.skills["steel-process-map"].executable is True
    assert registry.skills["steel-energy-balance"].executable is True
    assert registry.skills["steel-savings-prioritizer"].executable is True
    assert "steel-industry energy control" in registry.skills["steel-energy-control"].description


def test_steel_energy_skills_execute_representative_cases():
    process_map = load_skill_module(Path("registry/skills/steel-process-map/skill.py"))
    energy_balance = load_skill_module(Path("registry/skills/steel-energy-balance/skill.py"))
    prioritizer = load_skill_module(Path("registry/skills/steel-savings-prioritizer/skill.py"))

    mapped = process_map.execute(
        {
            "process_route": "hot-rolling",
            "product_or_grade": "hot rolled coil",
            "boundary": "reheating furnace and mill",
            "objective": "saving plan",
        }
    )
    assert mapped["matched_route"] == "hot-rolling"
    assert "reheating furnace" in mapped["stages"]
    assert "steel-energy-balance if numeric energy streams are available" in mapped["next_skill_suggestions"]

    balanced = energy_balance.execute(
        {
            "production_tonnes": 10000,
            "product_basis": "rolled product",
            "energy_streams": [
                {"name": "fuel gas", "amount": 85000, "unit": "GJ", "role": "input", "category": "fuel"},
                {"name": "electricity", "amount": 1200000, "unit": "kWh", "role": "input", "category": "electricity"},
                {"name": "steam export", "amount": 3000, "unit": "GJ", "role": "exported", "category": "recovered"},
            ],
        }
    )
    assert balanced["gross_input_gj"] == 89320
    assert balanced["net_input_gj"] == 86320
    assert round(balanced["intensity_gj_per_t"], 3) == 8.632

    ranked = prioritizer.execute(
        {
            "baseline_energy_gj_per_year": 1000000,
            "energy_price_per_gj": 45,
            "measures": [
                {"name": "hot charging increase", "saving_percent": 4, "capex": 3000000},
                {"name": "compressed air leak repair", "annual_energy_saving_gj": 8000, "capex": 100000, "risk_level": "low"},
            ],
        }
    )
    assert ranked["ranked_measures"][0]["annual_energy_saving_gj"] > 0
    assert ranked["quick_wins"]


def make_execution_config(packages: list[str]) -> ExecutionConfig:
    return ExecutionConfig(
        timeout_ms=60000,
        idle_timeout_ms=20000,
        packages=packages,
        pip_index_url="https://pypi.example/simple",
        pip_extra_index_url=None,
        pip_trusted_host="pypi.example",
        keep_venv=False,
        env={},
        venv_key="test_venv",
    )


def load_skill_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_cached_venv_requires_matching_dependency_marker(tmp_path):
    venv_dir = tmp_path / "venv"
    venv_dir.mkdir()
    config = make_execution_config(["requests==2.32.3"])

    assert cached_dependencies_match(venv_dir, config) is False

    write_dependency_marker(venv_dir, config)

    assert cached_dependencies_match(venv_dir, config) is True
    assert cached_dependencies_match(venv_dir, make_execution_config(["requests==2.32.2"])) is False
    assert cached_dependencies_match(venv_dir, make_execution_config([])) is True


def test_artifact_store_writes_manifest_timeline_and_content(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.write_tool_artifact(
        conversation_id="conv_test",
        run_id="run_test",
        tool_name="web-search",
        tool_call_id="call_test",
        kind="sandbox_execution",
        arguments={"query": "steel energy"},
        result={"success": True, "data": {"query": "steel energy", "results": [{"title": "A", "url": "https://a"}]}},
    )

    listed = store.list_artifacts("conv_test")
    read = store.read_artifact("conv_test", record.artifact_id)
    context = store.build_context("conv_test")

    assert listed["count"] == 1
    assert listed["artifacts"][0]["artifact_id"] == record.artifact_id
    assert read["success"] is True
    assert "steel energy" in read["content"]
    assert "Successful Evidence Artifacts" in context
    assert "Recent Tool Timeline" in context
    assert (tmp_path / "artifacts" / "conversations" / "conv_test" / "timeline.jsonl").is_file()


def test_artifact_store_writes_model_trace(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.write_model_trace(
        conversation_id="conv_test",
        run_id="run_test",
        round_index=0,
        request={
            "model": "test-model",
            "messages": [{"role": "system", "content": "system prompt"}, {"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "demo"}}],
            "tool_choice": "auto",
        },
        response={
            "assistant_message": {"role": "assistant", "content": "hi"},
            "content": "hi",
            "tool_calls": [],
            "reasoning_content_available": True,
            "reasoning_content_exposed": False,
            "reasoning_content": "[redacted by AGENT_RUNTIME_EXPOSE_REASONING_CONTENT=false]",
        },
    )

    listed = store.list_artifacts("conv_test")
    read = store.read_artifact("conv_test", record.artifact_id)
    payload = json.loads(read["content"])
    context = store.build_context("conv_test")

    assert listed["artifacts"][0]["kind"] == "model_planning"
    assert listed["artifacts"][0]["tool_name"] == "model_planning"
    assert "input_messages=2" in listed["artifacts"][0]["summary"]
    assert payload["request"]["messages"][1]["content"] == "hello"
    assert payload["response"]["assistant_message"]["content"] == "hi"
    assert "model_planning" in context
    assert "No successful evidence artifacts yet." in context


def test_artifact_store_marks_nested_skill_failure(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.write_tool_artifact(
        conversation_id="conv_test",
        run_id="run_test",
        tool_name="web-search-quark",
        tool_call_id="call_test",
        kind="sandbox_execution",
        arguments={"query": "steel energy"},
        result={
            "success": True,
            "data": {
                "success": False,
                "error_type": "captcha",
                "error": "Quark returned a CAPTCHA page.",
                "results": [],
            },
        },
    )

    listed = store.list_artifacts("conv_test")
    context = store.build_context("conv_test")

    assert listed["artifacts"][0]["artifact_id"] == record.artifact_id
    assert listed["artifacts"][0]["status"] == "failed"
    assert "Failed: captcha" in listed["artifacts"][0]["summary"]
    assert "Failed or Empty Attempts" in context


def test_artifact_store_refreshes_legacy_manifest_status(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    conversation_root = tmp_path / "artifacts" / "conversations" / "conv_test"
    artifact_dir = conversation_root / "artifacts"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "art_0001_web-search-quark.json"
    payload = {
        "artifact_id": "art_0001_web-search-quark",
        "tool_name": "web-search-quark",
        "result": {
            "success": True,
            "data": {
                "success": False,
                "error_type": "captcha",
                "error": "Quark returned a CAPTCHA page.",
                "results": [],
            },
        },
        "summary": "0 result(s).",
    }
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (conversation_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "artifact_id": "art_0001_web-search-quark",
                    "tool_name": "web-search-quark",
                    "relative_path": "artifacts/art_0001_web-search-quark.json",
                    "summary": "0 result(s).",
                    "status": "completed",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (conversation_root / "working_state.md").write_text("legacy", encoding="utf-8")

    listed = store.list_artifacts("conv_test")
    refreshed_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    context = store.build_context("conv_test")

    assert listed["artifacts"][0]["status"] == "failed"
    assert "Failed: captcha" in listed["artifacts"][0]["summary"]
    assert refreshed_payload["status"] == "failed"
    assert "Failed or Empty Attempts" in context


def test_artifact_store_summarizes_fetch_evidence(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    record = store.write_tool_artifact(
        conversation_id="conv_test",
        run_id="run_test",
        tool_name="web-fetch",
        tool_call_id="call_test",
        kind="sandbox_execution",
        arguments={"url": "https://example.com/report"},
        result={
            "success": True,
            "data": {
                "title": "Steel report",
                "url": "https://example.com/report",
                "description": "Industry data",
                "text": "Energy balance content with useful numbers.",
            },
        },
    )

    listed = store.list_artifacts("conv_test")
    context = store.build_context("conv_test")

    assert listed["artifacts"][0]["artifact_id"] == record.artifact_id
    assert listed["artifacts"][0]["status"] == "completed"
    assert "Steel report" in listed["artifacts"][0]["summary"]
    assert "https://example.com/report" in listed["artifacts"][0]["summary"]
    assert "Energy balance content" in listed["artifacts"][0]["summary"]
    assert "Successful Evidence Artifacts" in context


def test_artifact_observation_uses_effective_success_and_page_excerpt():
    nested_failure = {
        "success": True,
        "data": {
            "success": False,
            "error_type": "captcha",
            "error": "Quark returned a CAPTCHA page.",
            "results": [],
        },
    }
    failed_observation = AgentRuntime._build_artifact_observation(
        "web-search-quark",
        nested_failure,
        {"artifact_id": "art_0001_web-search-quark", "summary": "Failed: captcha"},
    )

    assert failed_observation["success"] is False
    assert failed_observation["effective_status"] == "failed"
    assert failed_observation["wrapper_success"] is True
    assert failed_observation["data_success"] is False
    assert "captcha" in failed_observation["error"]

    fetch_observation = AgentRuntime._build_artifact_observation(
        "web-fetch",
        {
            "success": True,
            "data": {
                "title": "Steel report",
                "url": "https://example.com/report",
                "description": "Industry data",
                "text": "Energy balance content with useful numbers.",
            },
        },
        {"artifact_id": "art_0002_web-fetch", "summary": "Steel report"},
    )

    assert fetch_observation["success"] is True
    assert fetch_observation["title"] == "Steel report"
    assert fetch_observation["url"] == "https://example.com/report"
    assert "Energy balance content" in fetch_observation["text_excerpt"]


class FakeOpenAI:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.chat = FakeChat(tool_name=tool_name, arguments=arguments)


class FakeStreamingOpenAI:
    def __init__(self, chunks: list[dict]):
        self.chat = FakeStreamingChat(chunks)


class FakeChat:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.completions = FakeCompletions(tool_name=tool_name, arguments=arguments)


class FakeStreamingChat:
    def __init__(self, chunks: list[dict]):
        self.completions = FakeStreamingCompletions(chunks)


class FakeCompletions:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.tool_name = tool_name
        self.arguments = arguments

    async def create(self, **_kwargs):
        return FakeCompletion(self.tool_name, self.arguments)


class FakeStreamingCompletions:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks

    async def create(self, **kwargs):
        assert kwargs.get("stream") is True
        return FakeAsyncStream(self.chunks)


class FakeAsyncStream:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self.chunks):
            raise StopAsyncIteration
        chunk = FakeStreamChunk(self.chunks[self._index])
        self._index += 1
        return chunk


class FakeStreamChunk:
    def __init__(self, delta: dict):
        self.choices = [FakeStreamChoice(delta)]


class FakeStreamChoice:
    def __init__(self, delta: dict):
        self.delta = FakeStreamDelta(delta)


class FakeStreamDelta:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self, exclude_none: bool = True):
        return self.payload


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
