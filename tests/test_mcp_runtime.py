from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.agent import AgentRuntime
from agent_runtime.config import AgentRuntimeSettings
from agent_runtime.mcp import MCP_HARNESS_NAME, mcp_function_name
from agent_runtime.models import ChatRequest, UserContext
from agent_runtime.registry import FileRegistry, RegistryError


def make_settings(tmp_path) -> AgentRuntimeSettings:
    settings = AgentRuntimeSettings(
        host="127.0.0.1",
        port=8010,
        model="test-model",
        memory_model=None,
        openai_api_key="test-key",
        openai_base_url=None,
        reasoning_effort=None,
        expose_reasoning_content=False,
        registry_dir=tmp_path / "registry",
        artifacts_dir=tmp_path / "artifacts",
        sandbox_url="http://127.0.0.1:8001",
        mcp_gateway_url="http://127.0.0.1:8002",
        admin_token=None,
        max_runtime_rounds=2,
        model_context_tokens=131072,
        context_compaction_threshold=0.8,
        memory_enabled=False,
        memory_context_tokens=4000,
        memory_max_entries=200,
        request_timeout_seconds=30,
    )
    settings.ensure_directories()
    return settings


def write_test_server(settings: AgentRuntimeSettings, extra: str = "") -> None:
    settings.registry_dir.joinpath("mcp_servers", "test-tools.yaml").write_text(
        "\n".join(
            [
                "id: test-tools",
                "description: Test MCP tools.",
                "transport: stdio",
                "command: python",
                "args:",
                "  - mcp_server/server.py",
                "tool_allowlist:",
                "  - add",
                "timeout_ms: 30000",
                extra,
            ]
        ),
        encoding="utf-8",
    )


def write_dependency_skill(
    settings: AgentRuntimeSettings,
    *,
    name: str = "mcp-composition-demo",
    alias: str = "sum_numbers",
    tool_name: str = "add",
    required: bool = True,
) -> None:
    skill_dir = settings.registry_dir / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    required_text = "true" if required else "false"
    skill_dir.joinpath("SKILL.md").write_text(
        f"""---
name: {name}
description: Demonstrate a declarative MCP tool dependency.
metadata:
  agent_runtime:
    mcp_dependencies:
      - alias: {alias}
        server_id: test-tools
        tool_name: {tool_name}
        required: {required_text}
---

# MCP Composition Demo

Use `{alias}` for deterministic addition. The runtime binds this alias to the configured MCP tool when this skill is activated.
""",
        encoding="utf-8",
    )


class RecordingMcpGateway:
    def __init__(self) -> None:
        self.list_calls: list[str] = []
        self.tool_calls: list[dict] = []

    async def list_tools(self, server):
        self.list_calls.append(server.id)
        return {
            "success": True,
            "tools": [
                {
                    "name": "add",
                    "description": "Add two values.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "not-allowlisted",
                    "description": "Must not be exposed.",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ],
        }

    async def call_tool(self, server, tool_name, arguments, context):
        self.tool_calls.append(
            {
                "server": server.id,
                "tool_name": tool_name,
                "arguments": arguments,
                "context": context.model_dump(),
            }
        )
        return {
            "success": True,
            "server_id": server.id,
            "tool_name": tool_name,
            "data": {"content": json.dumps({"sum": arguments["a"] + arguments["b"]})},
            "execution": {"execution_id": "mcp_test_execution", "server_id": server.id},
        }


class SequenceToolOpenAI:
    def __init__(self, tool_name: str, arguments: dict, final_content: str) -> None:
        self.chat = SequenceToolChat(tool_name, arguments, final_content)


class SequenceToolChat:
    def __init__(self, tool_name: str, arguments: dict, final_content: str) -> None:
        self.completions = SequenceToolCompletions(tool_name, arguments, final_content)


class SequenceToolCompletions:
    def __init__(self, tool_name: str, arguments: dict, final_content: str) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.final_content = final_content
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return ToolCompletion(self.tool_name, self.arguments)
        return TextCompletion(self.final_content)


class ReplanningToolOpenAI:
    def __init__(self, tool_name: str, calls: list[dict], final_content: str) -> None:
        self.chat = ReplanningToolChat(tool_name, calls, final_content)


class ReplanningToolChat:
    def __init__(self, tool_name: str, calls: list[dict], final_content: str) -> None:
        self.completions = ReplanningToolCompletions(tool_name, calls, final_content)


class ReplanningToolCompletions:
    def __init__(self, tool_name: str, calls: list[dict], final_content: str) -> None:
        self.tool_name = tool_name
        self.tool_calls = list(calls)
        self.final_content = final_content
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.tool_calls:
            return ToolCompletion(self.tool_name, self.tool_calls.pop(0))
        return TextCompletion(self.final_content)


class ToolCompletion:
    def __init__(self, tool_name: str, arguments: dict) -> None:
        self.choices = [ToolChoice(tool_name, arguments)]


class ToolChoice:
    def __init__(self, tool_name: str, arguments: dict) -> None:
        self.message = ToolMessage(tool_name, arguments)


class ToolMessage:
    def __init__(self, tool_name: str, arguments: dict) -> None:
        self.content = None
        self.tool_calls = [
            {
                "id": "call_mcp_test",
                "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(arguments)},
            }
        ]

    def model_dump(self, exclude_none: bool = True):
        return {"role": "assistant", "tool_calls": self.tool_calls}


class TextCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [TextChoice(content)]


class TextChoice:
    def __init__(self, content: str) -> None:
        self.message = TextMessage(content)


class TextMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = []

    def model_dump(self, exclude_none: bool = True):
        return {"role": "assistant", "content": self.content}


def test_registry_loads_allowlisted_mcp_server(tmp_path):
    settings = make_settings(tmp_path)
    write_test_server(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()

    server = registry.mcp_servers["test-tools"]
    summaries = registry.mcp_server_summaries(UserContext())

    assert server.transport == "stdio"
    assert server.command == "python"
    assert [summary.id for summary in summaries] == ["test-tools"]
    assert summaries[0].tool_allowlist == ["add"]


def test_registry_rejects_incomplete_stdio_mcp_server(tmp_path):
    settings = make_settings(tmp_path)
    settings.registry_dir.joinpath("mcp_servers", "bad.yaml").write_text(
        "id: bad\ndescription: missing command\ntransport: stdio\n",
        encoding="utf-8",
    )
    registry = FileRegistry(settings.registry_dir)

    with pytest.raises(RegistryError, match="stdio MCP servers require command"):
        registry.reload()


def test_mcp_activation_exposes_only_allowlisted_typed_tools(tmp_path):
    settings = make_settings(tmp_path)
    write_test_server(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    gateway = RecordingMcpGateway()
    runtime.mcp = gateway
    request = ChatRequest(message="use MCP")
    conversation = runtime._get_or_create_conversation(request)
    servers = runtime._available_mcp_servers(request)
    skills = runtime._available_skills(request, available_mcp_servers=servers)
    activated = []

    denied, _ = asyncio.run(runtime._activate_mcp_server({"server_id": "test-tools"}, servers, conversation, {}))
    assert denied["success"] is False

    activation, _ = asyncio.run(runtime._activate_skill({"skill_name": MCP_HARNESS_NAME}, skills, activated, conversation))
    result, tools = asyncio.run(
        runtime._activate_mcp_server({"server_id": "test-tools"}, servers, conversation, {})
    )
    names = [tool["function"]["name"] for tool in runtime._runtime_tools([], False, mcp_tools=tools)]

    assert activation["success"] is True
    assert result["success"] is True
    assert result["server_id"] == "test-tools"
    assert mcp_function_name("test-tools", "add") in names
    assert all("not-allowlisted" not in name for name in names)
    assert conversation.active_mcp_server_ids == ["test-tools"]
    assert gateway.list_calls == ["test-tools"]
    assert runtime._read_skill_resource({"skill_name": "mcp", "path": "README.md"}, activated)["success"] is False


def test_skill_activation_binds_declared_mcp_tool_to_its_alias(tmp_path):
    settings = make_settings(tmp_path)
    write_test_server(settings)
    write_dependency_skill(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    gateway = RecordingMcpGateway()
    runtime.mcp = gateway
    request = ChatRequest(message="use declarative MCP dependency")
    conversation = runtime._get_or_create_conversation(request)
    servers = runtime._available_mcp_servers(request)
    skills = runtime._available_skills(request, available_mcp_servers=servers)
    activated = []

    result, tools = asyncio.run(
        runtime._activate_skill(
            {"skill_name": "mcp-composition-demo"},
            skills,
            activated,
            conversation,
            available_mcp_servers=servers,
            mcp_catalog_by_server={},
            occupied_tool_names=set(),
        )
    )

    assert result["success"] is True
    assert result["mcp_dependencies"] == [
        {
            "alias": "sum_numbers",
            "server_id": "test-tools",
            "tool_name": "add",
            "required": True,
            "available": True,
            "function_name": "sum_numbers",
        }
    ]
    assert [tool.function_name for tool in tools] == ["sum_numbers"]
    assert tools[0].remote_name == "add"
    assert conversation.active_skill_ids == ["mcp-composition-demo"]
    assert conversation.active_mcp_server_ids == []
    assert gateway.list_calls == ["test-tools"]


def test_required_mcp_dependency_prevents_skill_activation(tmp_path):
    settings = make_settings(tmp_path)
    write_test_server(settings)
    write_dependency_skill(settings, tool_name="missing_tool")
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    runtime.mcp = RecordingMcpGateway()
    request = ChatRequest(message="activate a missing dependency")
    conversation = runtime._get_or_create_conversation(request)
    servers = runtime._available_mcp_servers(request)
    skills = runtime._available_skills(request, available_mcp_servers=servers)

    result, tools = asyncio.run(
        runtime._activate_skill(
            {"skill_name": "mcp-composition-demo"},
            skills,
            [],
            conversation,
            available_mcp_servers=servers,
        )
    )

    assert result["success"] is False
    assert result["error_type"] == "required_mcp_dependency_unavailable"
    assert tools == []
    assert conversation.active_skill_ids == []


def test_runtime_routes_mcp_tool_to_gateway_and_writes_mcp_artifact(tmp_path):
    settings = make_settings(tmp_path)
    write_test_server(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    gateway = RecordingMcpGateway()
    runtime.mcp = gateway
    request = ChatRequest(message="calculate 2 plus 3", conversation_id="conv_mcp", workspace_id="ws_mcp")
    conversation = runtime._get_or_create_conversation(request)
    conversation.active_skill_ids = [MCP_HARNESS_NAME]
    conversation.active_mcp_server_ids = ["test-tools"]
    function_name = mcp_function_name("test-tools", "add")
    runtime.openai = SequenceToolOpenAI(function_name, {"a": 2, "b": 3}, "The MCP result is 5.")

    response = asyncio.run(runtime.chat(request))
    artifacts = runtime.artifacts.list_artifacts(workspace_id="ws_mcp")["artifacts"]

    assert response.message == "The MCP result is 5."
    assert gateway.tool_calls[0]["server"] == "test-tools"
    assert gateway.tool_calls[0]["tool_name"] == "add"
    assert gateway.tool_calls[0]["arguments"] == {"a": 2, "b": 3}
    assert any(step.kind == "mcp_execution" for step in response.steps)
    assert any(item["kind"] == "mcp_execution" for item in artifacts)
    assert runtime.executions[0]["execution_id"] == "mcp_test_execution"


def test_runtime_replans_an_api_parameter_error_without_an_inspection_skill(tmp_path):
    settings = make_settings(tmp_path)
    write_test_server(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    gateway = RecordingMcpGateway()
    runtime.mcp = gateway
    request = ChatRequest(message="calculate 2 plus 3", conversation_id="conv_api_replan", workspace_id="ws_api_replan")
    conversation = runtime._get_or_create_conversation(request)
    conversation.active_skill_ids = [MCP_HARNESS_NAME]
    conversation.active_mcp_server_ids = ["test-tools"]
    function_name = mcp_function_name("test-tools", "add")
    runtime.openai = ReplanningToolOpenAI(
        function_name,
        [{"a": "two", "b": 3}, {"a": 2, "b": 3}],
        "Corrected the API arguments and got 5.",
    )

    async def rejecting_call_tool(server, tool_name, arguments, context):
        gateway.tool_calls.append({"server": server.id, "tool_name": tool_name, "arguments": arguments})
        if isinstance(arguments.get("a"), str):
            return {
                "success": False,
                "error_type": "invalid_request",
                "error": "Field a must be a number, not string.",
                "validation_errors": [{"field": "a", "expected": "number", "actual": "string"}],
            }
        return {"success": True, "data": {"content": '{"sum": 5}'}}

    gateway.call_tool = rejecting_call_tool
    response = asyncio.run(runtime.chat(request))

    assert response.message == "Corrected the API arguments and got 5."
    assert [call["arguments"] for call in gateway.tool_calls] == [{"a": "two", "b": 3}, {"a": 2, "b": 3}]
    second_request = runtime.openai.chat.completions.requests[1]
    replan_context = json.dumps(second_request["messages"], ensure_ascii=False)
    replan_messages = [
        str(message.get("content") or "")
        for message in second_request["messages"]
        if message.get("role") == "system" and "Runtime replan feedback" in str(message.get("content") or "")
    ]
    assert "invalid_request" in replan_context
    assert "Field a must be a number" in replan_context
    assert any('"a": {"type": "number"}' in message for message in replan_messages)
    assert "pg-table-profile" not in replan_context


def test_runtime_auto_exposes_declared_mcp_alias_for_an_active_skill(tmp_path):
    settings = make_settings(tmp_path)
    write_test_server(settings)
    write_dependency_skill(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    gateway = RecordingMcpGateway()
    runtime.mcp = gateway
    request = ChatRequest(message="calculate 2 plus 3", conversation_id="conv_bound_mcp", workspace_id="ws_bound_mcp")
    conversation = runtime._get_or_create_conversation(request)
    conversation.active_skill_ids = ["mcp-composition-demo"]
    runtime.openai = SequenceToolOpenAI("sum_numbers", {"a": 2, "b": 3}, "The declarative MCP result is 5.")

    response = asyncio.run(runtime.chat(request))

    assert response.message == "The declarative MCP result is 5."
    assert gateway.list_calls == ["test-tools"]
    assert gateway.tool_calls[0]["tool_name"] == "add"
    assert gateway.tool_calls[0]["arguments"] == {"a": 2, "b": 3}
    assert any(call.tool_name == "sum_numbers" for call in response.tool_calls)
