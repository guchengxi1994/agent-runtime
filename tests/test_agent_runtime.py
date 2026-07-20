from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

from agent_runtime.artifacts import ArtifactStore
from agent_runtime.agent import AgentRuntime
from agent_runtime.app import app
from agent_runtime.config import AgentRuntimeSettings, load_settings
from agent_runtime.memory import MemoryStore
from agent_runtime.models import ChatAttachment, ChatRequest, PermissionPolicy, SkillSummary, UserContext
from agent_runtime.permissions import is_allowed
from agent_runtime.registry import FileRegistry
from agent_runtime.runner_client import build_skill_bundle, skill_uses_bundle
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
        memory_model=None,
        openai_api_key="test-key",
        openai_base_url=None,
        reasoning_effort=None,
        expose_reasoning_content=False,
        registry_dir=registry_dir,
        artifacts_dir=tmp_path / "artifacts",
        sandbox_url="http://127.0.0.1:8001",
        admin_token=None,
        max_runtime_rounds=1,
        model_context_tokens=131072,
        context_compaction_threshold=0.8,
        memory_enabled=False,
        memory_context_tokens=4000,
        memory_max_entries=200,
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


def test_skill_uses_bundle_when_directory_has_extra_files(tmp_path):
    settings = make_settings(tmp_path)
    write_skill(
        settings.registry_dir / "skills" / "bundle-skill",
        name="bundle-skill",
        body="Read local resources when available.",
        runtime_metadata="""  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties: {}
      additionalProperties: false
""",
    )
    skill_dir = settings.registry_dir / "skills" / "bundle-skill"
    skill_dir.joinpath("skill.py").write_text(
        "definition = {'name': 'bundle-skill'}\ndef execute(params):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    skill_dir.joinpath("cases").mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("cases", "sample.txt").write_text("sample case", encoding="utf-8")

    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    skill = registry.skills["bundle-skill"]

    assert skill_uses_bundle(skill) is True
    bundle = build_skill_bundle(skill)
    assert bundle


def test_build_skill_bundle_includes_dotenv_when_present(tmp_path):
    settings = make_settings(tmp_path)
    write_skill(
        settings.registry_dir / "skills" / "dotenv-skill",
        name="dotenv-skill",
        body="Read dotenv from bundle.",
        runtime_metadata="""  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties: {}
      additionalProperties: false
""",
    )
    skill_dir = settings.registry_dir / "skills" / "dotenv-skill"
    skill_dir.joinpath("skill.py").write_text(
        "definition = {'name': 'dotenv-skill'}\ndef execute(params):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    skill_dir.joinpath(".env").write_text("PGHOST=pg.local\n", encoding="utf-8")

    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    skill = registry.skills["dotenv-skill"]
    bundle = build_skill_bundle(skill)

    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(bundle), "r") as archive:
        assert ".env" in archive.namelist()


def test_skill_uses_inline_when_only_entrypoint_exists(tmp_path):
    settings = make_settings(tmp_path)
    write_skill(
        settings.registry_dir / "skills" / "inline-skill",
        name="inline-skill",
        body="Inline only.",
        runtime_metadata="""  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties: {}
      additionalProperties: false
""",
    )
    skill_dir = settings.registry_dir / "skills" / "inline-skill"
    skill_dir.joinpath("skill.py").write_text(
        "definition = {'name': 'inline-skill'}\ndef execute(params):\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    skill = registry.skills["inline-skill"]

    assert skill_uses_bundle(skill) is False


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
                "AGENT_RUNTIME_MEMORY_MODEL=test-memory-model",
                "OPENAI_API_KEY=test-env-key # local development key",
                "OPENAI_BASE_URL=https://llm-gateway.example/v1",
                "AGENT_RUNTIME_EXPOSE_REASONING_CONTENT=true",
                "AGENT_RUNTIME_MODEL_CONTEXT_TOKENS=64000",
                "AGENT_RUNTIME_CONTEXT_COMPACTION_THRESHOLD=0.75",
                f"AGENT_RUNTIME_REGISTRY_DIR={tmp_path / 'registry'}",
                f"AGENT_RUNTIME_ARTIFACTS_DIR={tmp_path / 'artifacts'}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_RUNTIME_ENV_FILE", str(env_file))
    monkeypatch.delenv("AGENT_RUNTIME_MODEL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_MEMORY_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_EXPOSE_REASONING_CONTENT", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_REGISTRY_DIR", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_ARTIFACTS_DIR", raising=False)

    settings = load_settings()

    assert settings.model == "test-reasoning-model"
    assert settings.memory_model == "test-memory-model"
    assert settings.openai_api_key == "test-env-key"
    assert settings.openai_base_url == "https://llm-gateway.example/v1"
    assert settings.env_file_loaded == str(env_file.resolve())
    assert settings.openai_api_key_source == f"dotenv:{env_file.resolve()}"
    assert settings.openai_base_url_source == f"dotenv:{env_file.resolve()}"
    assert settings.expose_reasoning_content is True
    assert settings.model_context_tokens == 64000
    assert settings.context_compaction_threshold == 0.75
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


def test_runtime_tools_hide_artifact_read_until_recovery_is_needed(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)

    tools = runtime._runtime_tools([], include_skill_activation=False)
    names = [tool["function"]["name"] for tool in tools]

    assert "list_artifacts" in names
    assert "read_artifact" not in names
    assert "create_checkpoint" in names

    recovery_tools = runtime._runtime_tools([], include_skill_activation=False, include_artifact_read=True)
    recovery_names = [tool["function"]["name"] for tool in recovery_tools]
    assert "read_artifact" in recovery_names


def test_conversation_id_reuses_workspace_from_artifact_metadata_after_restart(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    first_runtime = AgentRuntime(settings, registry)
    request = ChatRequest(message="hello", workspace_id="ws_saved", conversation_id="conv_saved")
    conversation = first_runtime._get_or_create_conversation(request)
    first_runtime.artifacts.register_conversation(conversation.id, conversation.workspace_id, "default")

    second_runtime = AgentRuntime(settings, registry)
    restored = second_runtime._get_or_create_conversation(ChatRequest(message="resume", conversation_id="conv_saved"))

    assert restored.workspace_id == "ws_saved"


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
    artifacts = runtime.artifacts.list_artifacts(workspace_id=response.workspace_id)["artifacts"]
    model_trace = next(item for item in artifacts if item["kind"] == "model_planning")
    model_trace_content = json.loads(
        runtime.artifacts.read_artifact(workspace_id=response.workspace_id, artifact_id=model_trace["artifact_id"])["content"]
    )
    assert model_trace["tool_name"] == "model_planning"
    assert model_trace_content["content"]["request"]["messages"][0]["role"] == "system"
    assert model_trace_content["content"]["response"]["tool_calls"][0]["function"]["name"] == "request_user_input"


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


def test_runtime_executes_inline_skill_without_extra_resources(tmp_path):
    settings = make_settings(tmp_path)
    write_skill(
        settings.registry_dir / "skills" / "inline-skill",
        name="inline-skill",
        body="Use inline execution.",
        runtime_metadata="""  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties: {}
      additionalProperties: false
""",
    )
    skill_dir = settings.registry_dir / "skills" / "inline-skill"
    skill_dir.joinpath("skill.py").write_text(
        "definition = {'name': 'inline-skill'}\ndef execute(params):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.runner = RecordingRunner()
    runtime.openai = FakeOpenAI(tool_name="inline-skill", arguments={})

    response = asyncio.run(runtime.chat(ChatRequest(message="run inline skill")))

    assert response.message == "runtime 调用轮次超过上限，已停止。请缩小问题范围或提高 AGENT_RUNTIME_MAX_RUNTIME_ROUNDS。"
    assert runtime.runner.calls
    assert runtime.runner.calls[0]["mode"] == "inline"


def test_runtime_executes_bundle_skill_when_extra_resources_exist(tmp_path):
    settings = make_settings(tmp_path)
    write_skill(
        settings.registry_dir / "skills" / "bundle-skill",
        name="bundle-skill",
        body="Use bundle execution.",
        runtime_metadata="""  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties: {}
      additionalProperties: false
""",
    )
    skill_dir = settings.registry_dir / "skills" / "bundle-skill"
    skill_dir.joinpath("skill.py").write_text(
        "definition = {'name': 'bundle-skill'}\ndef execute(params):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    skill_dir.joinpath("cases").mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("cases", "sample.txt").write_text("sample case", encoding="utf-8")
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.runner = RecordingRunner()
    runtime.openai = FakeOpenAI(tool_name="bundle-skill", arguments={})

    response = asyncio.run(runtime.chat(ChatRequest(message="run bundle skill")))

    assert response.message == "runtime 调用轮次超过上限，已停止。请缩小问题范围或提高 AGENT_RUNTIME_MAX_RUNTIME_ROUNDS。"
    assert runtime.runner.calls
    assert runtime.runner.calls[0]["mode"] == "bundle"


def test_chat_injects_attachment_context_as_separate_message(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.openai = CapturingFinalAnswerOpenAI("已读取附件")

    response = asyncio.run(
        runtime.chat(
            ChatRequest(
                message="请总结这份表结构",
                attachments=[
                    ChatAttachment(
                        filename="schema.sql",
                        content_type="text/plain",
                        parser="text",
                        text="CREATE TABLE machine (id BIGINT PRIMARY KEY, line_id BIGINT);",
                        original_bytes=64,
                    )
                ],
            )
        )
    )

    assert response.message == "已读取附件"
    sent_messages = runtime.openai.chat.completions.last_kwargs["messages"]
    assert any(message["role"] == "user" and message["content"] == "请总结这份表结构" for message in sent_messages)
    attachment_context = next(
        message["content"]
        for message in sent_messages
        if message["role"] == "user" and message["content"].startswith("Parsed attachments for the immediately preceding user request.")
    )
    assert "schema.sql" in attachment_context
    assert "CREATE TABLE machine" in attachment_context


def test_context_budget_uses_real_prompt_usage_to_calibrate_next_estimate(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    conversation = runtime._get_or_create_conversation(ChatRequest(message="hello"))

    runtime._record_model_usage(
        conversation,
        {"prompt_tokens": 2000, "completion_tokens": 120},
        raw_estimated_prompt_tokens=1000,
        update_context_calibration=True,
    )
    budget = runtime._context_budget(
        [{"role": "user", "content": "x" * 4000}],
        [],
        conversation,
    )

    assert conversation.cumulative_prompt_tokens == 2000
    assert conversation.cumulative_completion_tokens == 120
    assert conversation.last_prompt_tokens == 2000
    assert conversation.token_estimate_ratio == 2.0
    assert budget["estimated_prompt_tokens"] == budget["raw_estimated_prompt_tokens"] * 2


def test_memory_store_applies_stable_key_delta_and_renders_markdown(tmp_path):
    store = MemoryStore(tmp_path / "artifacts")
    created = store.ensure("ws_memory")

    assert created.revision == 0
    assert store.path("ws_memory").is_file()

    first = store.apply_delta(
        "ws_memory",
        {
            "upserts": [
                {
                    "key": "analysis.target",
                    "section": "confirmed_facts",
                    "content": "分析对象为 1 号转炉",
                    "use_when": "后续分析该转炉时使用",
                    "source": "user",
                    "confidence": "confirmed",
                }
            ],
            "removes": [],
        },
    )
    same = store.apply_delta(
        "ws_memory",
        {
            "upserts": [
                {
                    "key": "analysis.target",
                    "section": "confirmed_facts",
                    "content": "分析对象为 1 号转炉",
                    "use_when": "后续分析该转炉时使用",
                    "source": "user",
                    "confidence": "confirmed",
                }
            ],
            "removes": [],
        },
    )
    removed = store.apply_delta("ws_memory", {"upserts": [], "removes": ["analysis.target"]})

    assert first.revision == 1
    assert same.revision == 1
    assert removed.revision == 2
    assert "analysis.target" not in removed.entries
    markdown = store.path("ws_memory").read_text(encoding="utf-8")
    assert "# Workspace Resume Memory" in markdown
    assert "## Confirmed Facts" in markdown


def test_memory_jobs_are_persisted_and_failed_jobs_can_be_retried(tmp_path):
    store = MemoryStore(tmp_path / "artifacts")
    job = store.enqueue_job(
        "ws_jobs",
        conversation_id="conv_jobs",
        run_id="run_jobs",
        model="cheap-model",
        payload={"current_user_questions": ["继续分析什么？"]},
    )

    reloaded = MemoryStore(tmp_path / "artifacts")
    assert reloaded.pending_jobs("ws_jobs")[0]["job_id"] == job["job_id"]

    reloaded.update_job("ws_jobs", job["job_id"], status="failed", attempts=3, last_error="timeout")
    assert reloaded.retry_failed_jobs("ws_jobs") == 1
    retried = reloaded.pending_jobs("ws_jobs")[0]
    assert retried["status"] == "pending"
    assert retried["attempts"] == 0


def test_new_conversation_loads_workspace_memory_and_queues_background_delta(tmp_path):
    settings = make_settings(tmp_path)
    settings = AgentRuntimeSettings(
        **{**settings.__dict__, "memory_enabled": True, "memory_model": "test-memory-model"}
    )
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.memory.apply_delta(
        "ws_memory",
        {
            "upserts": [
                {
                    "key": "output.language",
                    "section": "user_preferences",
                    "content": "输出使用中文",
                    "use_when": "生成任何用户可见内容时使用",
                    "source": "user",
                    "confidence": "confirmed",
                }
            ],
            "removes": [],
        },
    )
    runtime.openai = SequentialTextOpenAI(
        [
            ("已按中文输出。", {"prompt_tokens": 300, "completion_tokens": 10}),
            (
                json.dumps(
                    {
                        "upserts": [
                            {
                                "key": "analysis.target",
                                "section": "confirmed_facts",
                                "content": "分析对象为 1 号转炉",
                                "use_when": "继续分析目标设备时使用",
                                "source": "user",
                                "confidence": "confirmed",
                                "artifact_ids": [],
                            }
                        ],
                        "removes": [],
                    },
                    ensure_ascii=False,
                ),
                {"prompt_tokens": 180, "completion_tokens": 40},
            ),
        ]
    )

    async def run_chat_and_memory():
        response = await runtime.chat(
            ChatRequest(
                message="分析对象是 1 号转炉",
                workspace_id="ws_memory",
                conversation_id="conv_memory",
            )
        )
        planning_calls = len(runtime.openai.chat.completions.calls)
        await runtime.wait_for_memory_updates("ws_memory")
        return response, planning_calls

    response, calls_before_wait = asyncio.run(run_chat_and_memory())

    assert response.message == "已按中文输出。"
    conversation = runtime.conversations["conv_memory"]
    assert conversation.memory_loaded is True
    assert "输出使用中文" in (conversation.memory_context or "")
    document = runtime.memory.load("ws_memory")
    assert document.entries["workspace.primary_objective"].content == "分析对象是 1 号转炉"
    assert document.entries["workspace.current_request"].content == "分析对象是 1 号转炉"
    assert document.entries["analysis.target"].content == "分析对象为 1 号转炉"
    assert any(step.label == "memory_update" and step.status == "queued" for step in response.steps)
    assert calls_before_wait == 1
    planning_messages = runtime.openai.chat.completions.calls[0]["messages"]
    assert any("Workspace resume memory for the model" in message.get("content", "") for message in planning_messages)
    memory_payload = json.loads(runtime.openai.chat.completions.calls[1]["messages"][1]["content"])
    assert runtime.openai.chat.completions.calls[1]["model"] == "test-memory-model"
    assert "existing_memory_key_index" in memory_payload
    assert "relevant_existing_memory" in memory_payload
    assert "# Workspace Memory" not in runtime.openai.chat.completions.calls[1]["messages"][1]["content"]


def test_memory_model_defaults_to_main_model(tmp_path):
    settings = make_settings(tmp_path)
    settings = AgentRuntimeSettings(**{**settings.__dict__, "memory_enabled": True})
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.openai = SequentialTextOpenAI(
        [
            ("完成。", {"prompt_tokens": 20, "completion_tokens": 2}),
            ('{"upserts":[],"removes":[]}', {"prompt_tokens": 10, "completion_tokens": 2}),
        ]
    )

    async def run_chat_and_memory():
        await runtime.chat(ChatRequest(message="记录这个回合"))
        await runtime.wait_for_memory_updates()

    asyncio.run(run_chat_and_memory())

    assert runtime.openai.chat.completions.calls[1]["model"] == "test-model"


def test_memory_delta_rejects_protected_questions_and_entries_without_usage():
    delta = AgentRuntime._parse_memory_delta(
        json.dumps(
            {
                "upserts": [
                    {
                        "key": "workspace.current_request",
                        "section": "workspace_goal",
                        "content": "模型擅自改写的问题",
                        "use_when": "always",
                    },
                    {
                        "key": "findings.one_off_stats",
                        "section": "confirmed_facts",
                        "content": "某次查询共有 11107 条",
                    },
                    {
                        "key": "findings.tool_stats",
                        "section": "confirmed_facts",
                        "content": "某次工具查询共有 11107 条",
                        "use_when": "撰写报告时使用",
                        "source": "tool",
                        "artifact_ids": ["art_0018_query"],
                    },
                    {
                        "key": "workflow.next_step",
                        "section": "open_items",
                        "content": "等待用户确认分析口径",
                        "use_when": "恢复该分析任务时先检查用户是否已确认口径",
                    },
                ],
                "removes": ["workspace.primary_objective"],
            },
            ensure_ascii=False,
        )
    )

    assert [item["key"] for item in delta["upserts"]] == ["workflow.next_step"]
    assert delta["removes"] == []


def test_chat_compacts_old_history_before_next_model_call(tmp_path):
    settings = make_settings(tmp_path)
    settings = AgentRuntimeSettings(
        **{
            **settings.__dict__,
            "model_context_tokens": 5000,
            "context_compaction_threshold": 0.8,
        }
    )
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    request = ChatRequest(message="继续完成当前分析", conversation_id="conv_compact")
    conversation = runtime._get_or_create_conversation(request)
    conversation.messages.extend(
        [
            {"role": "user", "content": "旧需求：" + "A" * 8000},
            {"role": "assistant", "content": "旧结论：" + "B" * 8000},
        ]
    )
    runtime.openai = SequentialTextOpenAI(
        [
            ("# Resume\n- 用户正在做炼钢效率分析。", {"prompt_tokens": 820, "completion_tokens": 30}),
            ("已继续分析。", {"prompt_tokens": 420, "completion_tokens": 12}),
        ]
    )

    response = asyncio.run(runtime.chat(request))

    assert response.message == "已继续分析。"
    assert conversation.context_summary.startswith("# Resume")
    assert conversation.messages == [
        {"role": "user", "content": "继续完成当前分析"},
        {"role": "assistant", "content": "已继续分析。"},
    ]
    assert conversation.cumulative_prompt_tokens == 1240
    assert conversation.cumulative_completion_tokens == 42
    assert any(step.label == "context_compaction" and step.status == "completed" for step in response.steps)
    final_messages = runtime.openai.chat.completions.calls[-1]["messages"]
    assert any(message["role"] == "system" and "Compacted conversation context" in message["content"] for message in final_messages)
    assert any(message["role"] == "user" and message["content"] == "继续完成当前分析" for message in final_messages)
    assert all("旧需求" not in str(message.get("content")) for message in final_messages)


def test_context_compaction_defers_memory_flush_until_turn_end(tmp_path):
    settings = make_settings(tmp_path)
    settings = AgentRuntimeSettings(
        **{
            **settings.__dict__,
            "model_context_tokens": 5000,
            "context_compaction_threshold": 0.8,
            "memory_enabled": True,
        }
    )
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    request = ChatRequest(message="继续分析", workspace_id="ws_compact_memory", conversation_id="conv_compact_memory")
    conversation = runtime._get_or_create_conversation(request)
    conversation.messages.extend(
        [
            {"role": "user", "content": "旧目标：" + "A" * 8000},
            {"role": "assistant", "content": "旧进度：" + "B" * 8000},
        ]
    )
    runtime.openai = SequentialTextOpenAI(
        [
            ("# Resume\n- 正在分析炼钢效率。", {"prompt_tokens": 800, "completion_tokens": 30}),
            ("分析已继续。", {"prompt_tokens": 400, "completion_tokens": 12}),
            (
                json.dumps(
                    {
                        "upserts": [
                            {
                                "key": "analysis.state",
                                "section": "current_state",
                                "content": "正在分析炼钢效率",
                                "use_when": "恢复尚未完成的效率分析时使用",
                                "source": "runtime",
                                "confidence": "working",
                                "artifact_ids": [],
                            }
                        ],
                        "removes": [],
                    },
                    ensure_ascii=False,
                ),
                {"prompt_tokens": 240, "completion_tokens": 45},
            ),
        ]
    )

    async def run_chat_and_memory():
        response = await runtime.chat(request)
        calls_before_wait = len(runtime.openai.chat.completions.calls)
        await runtime.wait_for_memory_updates("ws_compact_memory")
        return response, calls_before_wait

    response, calls_before_wait = asyncio.run(run_chat_and_memory())

    assert len(runtime.openai.chat.completions.calls) == 3
    assert calls_before_wait == 2
    assert conversation.memory_dirty is False
    assert runtime.memory.load("ws_compact_memory").entries["analysis.state"].content == "正在分析炼钢效率"
    queued_updates = [step for step in response.steps if step.label == "memory_update" and step.status == "queued"]
    assert len(queued_updates) == 1
    memory_payload = json.loads(runtime.openai.chat.completions.calls[2]["messages"][1]["content"])
    assert memory_payload["context_was_compacted"] is True
    assert "正在分析炼钢效率" in memory_payload["compacted_context_summary"]


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
    assert "echarts.min.js" in response.text


def test_workspace_memory_api_returns_read_only_markdown(tmp_path, monkeypatch):
    import importlib

    app_module = importlib.import_module("agent_runtime.app")
    monkeypatch.setattr(app_module.runtime, "memory", MemoryStore(tmp_path / "artifacts"))
    client = TestClient(app)

    response = client.get("/workspaces/ws_api_memory_test/memory")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_id"] == "ws_api_memory_test"
    assert payload["revision"] >= 0
    assert payload["markdown"].startswith("# Workspace Resume Memory")
    assert "model_context" in payload
    assert payload["job_counts"] == {}


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


def test_industrial_ontology_skills_and_agent_are_registered():
    registry = FileRegistry(Path("registry").resolve())
    registry.reload()

    assert "industrial-ontology-engineering" in registry.skills
    assert registry.skills["industrial-ontology-engineering"].executable is False
    assert "ontology-marketplace" in registry.skills
    assert "ontology-template-marketplace" in registry.skills
    assert registry.skills["ontology-template-marketplace"].executable is True
    assert "ontology-structure-validator" in registry.skills
    assert registry.skills["ontology-structure-validator"].executable is True
    assert "ontology-registry-upsert" in registry.skills
    assert registry.skills["ontology-registry-upsert"].executable is True
    assert "ontology-runtime-resolve" in registry.skills
    assert registry.skills["ontology-runtime-resolve"].executable is True
    assert "timeseries-query-sql" in registry.skills
    assert registry.skills["timeseries-query-sql"].executable is True
    assert "energy-anomaly-diagnose" in registry.skills
    assert registry.skills["energy-anomaly-diagnose"].executable is True
    assert registry.skills["ontology-template-marketplace"].execution_policy["packages"] == []
    assert registry.skills["ontology-structure-validator"].execution_policy["packages"] == []

    agent = registry.get_agent("industrial_ontology_designer")
    assert "industrial-ontology-engineering" in set(agent.skill_ids or [])
    assert "ontology-template-marketplace" in set(agent.skill_ids or [])
    assert "ontology-structure-validator" in set(agent.skill_ids or [])
    assert "ontology-registry-upsert" in set(agent.skill_ids or [])
    assert "ontology-runtime-resolve" in set(agent.skill_ids or [])
    assert "timeseries-query-sql" in set(agent.skill_ids or [])
    assert "energy-anomaly-diagnose" in set(agent.skill_ids or [])


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


def test_industrial_ontology_executables_handle_representative_cases():
    template_marketplace = load_skill_module(Path("registry/skills/ontology-template-marketplace/skill.py"))
    structure_validator = load_skill_module(Path("registry/skills/ontology-structure-validator/skill.py"))

    template = template_marketplace.execute(
        {
            "industry": "battery",
            "scope": "line",
            "include_behaviors": True,
            "include_mappings": False,
        }
    )
    assert template["matched_template"] == "battery"
    assert "Cell" in [item["name"] for item in template["objects"]]
    assert template["mapping_targets"] == []
    assert "Machine" in template["focus_objects"]

    validation = structure_validator.execute(
        {
            "ontology": {
                "objects": [
                    {
                        "name": "Machine",
                        "aliases": ["Device"],
                        "properties": [
                            {"name": "temperature", "type": "number"},
                            {"name": "temperature", "type": "number"},
                        ],
                        "behaviors": [{"name": "start"}, {"name": "start"}],
                    },
                    {
                        "name": "Alarm",
                        "properties": [{"name": "status", "type": "enum", "enum_values": ["Running", "Idle"]}],
                    },
                ],
                "relations": [{"source": "Alarm", "predicate": "generatedBy", "target": "Machine"}],
                "mappings": [{"object": "Machine", "property": "temperature", "source_kind": "sql", "source_path": "device.temp"}],
            }
        }
    )
    assert validation["release_recommendation"] == "review"
    assert any(item["code"] == "property.duplicate" for item in validation["warnings"])
    assert any(item["code"] == "behavior.duplicate" for item in validation["warnings"])
    assert any(item["code"] == "property.missing_unit" for item in validation["warnings"])


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
    store.register_conversation("conv_test", "ws_test", "default")
    record = store.write_tool_artifact(
        workspace_id="ws_test",
        conversation_id="conv_test",
        run_id="run_test",
        tool_name="web-search",
        tool_call_id="call_test",
        kind="sandbox_execution",
        arguments={"query": "steel energy"},
        result={"success": True, "data": {"query": "steel energy", "results": [{"title": "A", "url": "https://a"}]}},
    )

    listed = store.list_artifacts(workspace_id="ws_test")
    read = store.read_artifact(workspace_id="ws_test", artifact_id=record.artifact_id)
    context = store.build_context("ws_test", "conv_test")

    assert listed["count"] == 1
    assert listed["artifacts"][0]["artifact_id"] == record.artifact_id
    assert listed["artifacts"][0]["workspace_id"] == "ws_test"
    assert listed["artifacts"][0]["stats"]["input_chars"] > 0
    assert listed["artifacts"][0]["stats"]["output_chars"] > 0
    assert read["success"] is True
    assert read["stats"]["input_chars"] > 0
    assert "steel energy" in read["content"]
    assert "Recent Successful Evidence" in context
    assert "Recent Workspace Timeline" in context
    assert (tmp_path / "artifacts" / "workspaces" / "ws_test" / "timeline.jsonl").is_file()


def test_runtime_rejects_repeated_artifact_read_in_same_run(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    record = runtime.artifacts.write_tool_artifact(
        workspace_id="ws_repeat_read",
        conversation_id="conv_repeat_read",
        run_id="run_prior",
        tool_name="web-search",
        tool_call_id="call_prior",
        kind="sandbox_execution",
        arguments={"query": "steel"},
        result={"success": True, "data": {"summary": "prior evidence"}},
    )
    runtime.openai = FakeOpenAI(
        tool_name="read_artifact",
        arguments={"artifact_id": record.artifact_id},
    )

    response = asyncio.run(
        runtime.chat(
            ChatRequest(
                message="继续之前的分析",
                workspace_id="ws_repeat_read",
                conversation_id="conv_repeat_read",
            )
        )
    )

    reads = [trace for trace in response.tool_calls if trace.tool_name == "read_artifact"]
    assert len(reads) == 2
    assert reads[0].result["success"] is True
    assert reads[1].result == {
        "success": False,
        "error": "Artifact already read in this run; reuse the previous observation.",
        "artifact_id": record.artifact_id,
    }


def test_artifact_store_writes_model_trace(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    store.register_conversation("conv_test", "ws_test", "default")
    record = store.write_model_trace(
        workspace_id="ws_test",
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

    listed = store.list_artifacts(workspace_id="ws_test")
    read = store.read_artifact(workspace_id="ws_test", artifact_id=record.artifact_id)
    payload = json.loads(read["content"])
    context = store.build_context("ws_test", "conv_test")

    assert listed["artifacts"][0]["kind"] == "model_planning"
    assert listed["artifacts"][0]["tool_name"] == "model_planning"
    assert "input_messages=2" in listed["artifacts"][0]["summary"]
    assert listed["artifacts"][0]["stats"]["input_messages"] == 2
    assert listed["artifacts"][0]["stats"]["output_chars"] == 2
    assert payload["content"]["request"]["messages"][1]["content"] == "hello"
    assert payload["content"]["response"]["assistant_message"]["content"] == "hi"
    assert "model_planning" in context
    assert "No successful evidence artifacts yet." in context


def test_artifact_store_marks_nested_skill_failure(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    store.register_conversation("conv_test", "ws_test", "default")
    record = store.write_tool_artifact(
        workspace_id="ws_test",
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

    listed = store.list_artifacts(workspace_id="ws_test")
    context = store.build_context("ws_test", "conv_test")

    assert listed["artifacts"][0]["artifact_id"] == record.artifact_id
    assert listed["artifacts"][0]["status"] == "failed"
    assert "Failed: captcha" in listed["artifacts"][0]["summary"]
    assert "Failed or Empty Attempts" in context


def test_artifact_store_refreshes_workspace_manifest_status(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    workspace_root = tmp_path / "artifacts" / "workspaces" / "ws_test"
    artifact_dir = workspace_root / "artifacts"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "art_0001_web-search-quark.json"
    payload = {
        "artifact_id": "art_0001_web-search-quark",
        "workspace_id": "ws_test",
        "conversation_id": "conv_test",
        "tool_name": "web-search-quark",
        "kind": "sandbox_execution",
        "summary": "0 result(s).",
        "status": "completed",
        "content": {
            "result": {
                "success": True,
                "data": {
                    "success": False,
                    "error_type": "captcha",
                    "error": "Quark returned a CAPTCHA page.",
                    "results": [],
                },
            },
        },
    }
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (workspace_root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "artifact_id": "art_0001_web-search-quark",
                    "workspace_id": "ws_test",
                    "conversation_id": "conv_test",
                    "kind": "sandbox_execution",
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
    (tmp_path / "artifacts" / "conversations" / "conv_test").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "conversations" / "conv_test" / "meta.json").write_text(
        json.dumps({"conversation_id": "conv_test", "workspace_id": "ws_test", "agent_id": "default"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (workspace_root / "working_state.md").write_text("legacy", encoding="utf-8")

    listed = store.list_artifacts(workspace_id="ws_test")
    refreshed_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    context = store.build_context("ws_test", "conv_test")

    assert listed["artifacts"][0]["status"] == "failed"
    assert "Failed: captcha" in listed["artifacts"][0]["summary"]
    assert refreshed_payload["status"] == "failed"
    assert "Failed or Empty Attempts" in context


def test_artifact_store_summarizes_fetch_evidence(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    store.register_conversation("conv_test", "ws_test", "default")
    record = store.write_tool_artifact(
        workspace_id="ws_test",
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

    listed = store.list_artifacts(workspace_id="ws_test")
    context = store.build_context("ws_test", "conv_test")

    assert listed["artifacts"][0]["artifact_id"] == record.artifact_id
    assert listed["artifacts"][0]["status"] == "completed"
    assert "Steel report" in listed["artifacts"][0]["summary"]
    assert "https://example.com/report" in listed["artifacts"][0]["summary"]
    assert "Energy balance content" in listed["artifacts"][0]["summary"]
    assert "Recent Successful Evidence" in context


def test_artifact_store_writes_checkpoint_and_resolves_workspace_from_conversation(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    store.register_conversation("conv_test", "ws_test", "default")
    record = store.write_checkpoint(
        workspace_id="ws_test",
        conversation_id="conv_test",
        run_id="run_test",
        title="Ontology draft checkpoint",
        summary="Preserves current object and mapping draft for resume.",
        payload={"objects": ["Machine", "Alarm"], "next_step": "validation"},
        tags=["ontology", "checkpoint"],
    )

    listed = store.list_artifacts(conversation_id="conv_test", kind="checkpoint")
    read = store.read_artifact(conversation_id="conv_test", artifact_id=record.artifact_id)
    context = store.build_context("ws_test", "conv_test")

    assert listed["success"] is True
    assert listed["workspace_id"] == "ws_test"
    assert listed["artifacts"][0]["kind"] == "checkpoint"
    assert read["success"] is True
    assert "Ontology draft checkpoint" in read["content"]
    assert "Latest Checkpoints" in context


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

    query_observation = AgentRuntime._build_artifact_observation(
        "pg-report-query",
        {
            "success": True,
            "data": {
                "mode": "executed",
                "summary": "共返回 3 个分组；排名第一的是制造业，案件数为 12。",
                "sql": "SELECT industry, COUNT(*) AS case_count FROM enterprise_risk_events GROUP BY 1 ORDER BY 2 DESC LIMIT 3;",
                "columns": ["industry", "case_count"],
                "rows": [
                    {"industry": "制造业", "case_count": 12},
                    {"industry": "建筑业", "case_count": 8},
                ],
                "chart_spec": {
                    "renderer": "echarts",
                    "chart_type": "bar",
                    "title": "行业排行",
                    "option": {"series": []},
                },
            },
        },
        {"artifact_id": "art_0003_pg-report-query", "summary": "行业排行"},
    )

    assert query_observation["mode"] == "executed"
    assert query_observation["row_count"] == 2
    assert query_observation["columns"] == ["industry", "case_count"]
    assert query_observation["rows_preview"][0]["industry"] == "制造业"
    assert query_observation["chart_renderer"] == "echarts"
    assert query_observation["chart_type"] == "bar"

    profile_observation = AgentRuntime._build_artifact_observation(
        "pg-table-profile",
        {
            "success": True,
            "data": {
                "mode": "profiled",
                "summary": "表 public.enterprise_risk_events 共 11107 行、18 个字段；可优先用于分组的字段包括 event_source、region、industry。",
                "dimension_candidates": ["event_source", "region", "industry", "accepted_date"],
                "time_candidates": ["accepted_date"],
                "filterable_enums": {
                    "event_source": ["judicial_case", "administrative_penalty"],
                    "region": ["天宁街道", "雕庄街道"],
                },
                "schema_overview": "event_source = judicial_case, administrative_penalty\nregion = 天宁街道, 雕庄街道",
                "columns": ["column_name", "data_type", "enum_values_preview"],
                "rows": [
                    {"column_name": "event_source", "data_type": "text", "enum_values_preview": "judicial_case, administrative_penalty"},
                    {"column_name": "region", "data_type": "text", "enum_values_preview": "天宁街道, 雕庄街道"},
                ],
            },
        },
        {"artifact_id": "art_0004_pg-table-profile", "summary": "schema profile"},
    )

    assert profile_observation["mode"] == "profiled"
    assert profile_observation["dimension_candidates"][:2] == ["event_source", "region"]
    assert profile_observation["enum_preview"]["event_source"] == ["judicial_case", "administrative_penalty"]
    assert "event_source" in profile_observation["schema_overview"]


def test_artifact_observation_marks_omitted_preview_content_as_truncated():
    observation = AgentRuntime._build_artifact_observation(
        "table-query",
        {"success": True, "data": {"rows": [{"id": index} for index in range(6)]}},
        {"artifact_id": "art_0001_table-query", "summary": "six rows"},
    )

    assert observation["row_count"] == 6
    assert len(observation["rows_preview"]) == 5
    assert observation["truncated"] is True


class FakeOpenAI:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.chat = FakeChat(tool_name=tool_name, arguments=arguments)


class FakeStreamingOpenAI:
    def __init__(self, chunks: list[dict]):
        self.chat = FakeStreamingChat(chunks)


class CapturingFinalAnswerOpenAI:
    def __init__(self, content: str):
        self.chat = CapturingFinalAnswerChat(content)


class SequentialTextOpenAI:
    def __init__(self, responses: list[tuple[str, dict]]):
        self.chat = SequentialTextChat(responses)


class FakeChat:
    def __init__(self, *, tool_name: str, arguments: dict):
        self.completions = FakeCompletions(tool_name=tool_name, arguments=arguments)


class CapturingFinalAnswerChat:
    def __init__(self, content: str):
        self.completions = CapturingFinalAnswerCompletions(content)


class SequentialTextChat:
    def __init__(self, responses: list[tuple[str, dict]]):
        self.completions = SequentialTextCompletions(responses)


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


class CapturingFinalAnswerCompletions:
    def __init__(self, content: str):
        self.content = content
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeTextCompletion(self.content)


class SequentialTextCompletions:
    def __init__(self, responses: list[tuple[str, dict]]):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content, usage = self.responses.pop(0)
        return FakeTextCompletion(content, usage=usage)


class RecordingRunner:
    def __init__(self):
        self.calls = []

    async def execute_skill(self, skill, arguments, context, script, *, base_policy=None):
        self.calls.append(
            {
                "mode": "inline",
                "skill": skill.name,
                "arguments": arguments,
                "context": context.model_dump(),
                "script": script,
                "base_policy": base_policy,
            }
        )
        return {"success": True, "data": {"mode": "inline"}}

    async def execute_bundle_skill(self, skill, arguments, context, *, base_policy=None):
        self.calls.append(
            {
                "mode": "bundle",
                "skill": skill.name,
                "arguments": arguments,
                "context": context.model_dump(),
                "base_policy": base_policy,
            }
        )
        return {"success": True, "data": {"mode": "bundle"}}


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


class FakeTextCompletion:
    def __init__(self, content: str, usage: dict | None = None):
        self.choices = [FakeTextChoice(content)]
        self.usage = usage


class FakeChoice:
    def __init__(self, tool_name: str, arguments: dict):
        self.message = FakeMessage(tool_name, arguments)


class FakeTextChoice:
    def __init__(self, content: str):
        self.message = FakeTextMessage(content)


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


class FakeTextMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = []

    def model_dump(self, exclude_none: bool = True):
        payload = {
            "role": "assistant",
            "content": self.content,
        }
        if not exclude_none:
            payload["tool_calls"] = []
        return payload


class FakeToolCall:
    def __init__(self, tool_name: str, arguments: dict):
        self.id = "call_request_input"
        self.function = FakeFunction(tool_name, arguments)


class FakeFunction:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = json.dumps(arguments, ensure_ascii=False)
