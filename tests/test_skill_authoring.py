from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.agent import AgentRuntime
from agent_runtime.config import AgentRuntimeSettings
from agent_runtime.models import ChatRequest, SkillPackage, SkillPackageFile
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


def write_skill_author(settings: AgentRuntimeSettings) -> None:
    directory = settings.registry_dir / "skills" / "skill-author"
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("SKILL.md").write_text(
        """---
name: skill-author
description: Create runtime skill packages.
metadata:
  agent_runtime:
    capabilities: [skill-authoring]
---

# Skill Author

Create packages with create_skill_package.
""",
        encoding="utf-8",
    )


def harness_markdown(name: str) -> str:
    return f"""---
name: {name}
description: A test harness package.
metadata:
  agent_runtime:
    capabilities: [test]
---

# {name}

Use this test harness when requested.
"""


def executable_markdown(name: str, entrypoint: str = "skill.py") -> str:
    return f"""---
name: {name}
description: A test executable package.
metadata:
  agent_runtime:
    executable: true
    entrypoint: {entrypoint}
    parameters_schema:
      type: object
      properties: {{}}
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      packages: []
---

# {name}

Execute this test package when requested.
"""


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
        self.tool_sets: list[set[str]] = []

    async def create(self, **kwargs):
        self.calls += 1
        self.tool_sets.append({tool["function"]["name"] for tool in kwargs.get("tools", [])})
        if self.calls == 1:
            return ToolCompletion(self.tool_name, self.arguments)
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
                "id": "call_skill_author",
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


def test_registry_creates_executable_package_with_text_resources(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()

    result = registry.save_skill_package(
        SkillPackage(
            content=executable_markdown("text-formatter"),
            files=[
                SkillPackageFile(
                    path="skill.py",
                    content=(
                        "definition = {'name': 'text-formatter', 'description': 'Format text.', "
                        "'category': 'utility', 'parameters': []}\n\n"
                        "def execute(params):\n    return {'text': str(params.get('text', ''))}\n"
                    ),
                ),
                SkillPackageFile(path="references/style.md", content="# Style\n\nKeep output concise.\n"),
            ],
        )
    )

    loaded = registry.skills["text-formatter"]
    assert result["name"] == "text-formatter"
    assert result["executable"] is True
    assert result["files"] == ["SKILL.md", "skill.py", "references/style.md"]
    assert loaded.executable is True
    assert [resource.path for resource in loaded.resources] == ["references/style.md", "skill.py"]


def test_registry_rejects_duplicate_paths_unsafe_paths_and_missing_entrypoint(tmp_path):
    settings = make_settings(tmp_path)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()

    with pytest.raises(RegistryError, match="entrypoint"):
        registry.save_skill_package(SkillPackage(content=executable_markdown("missing-entrypoint")))
    with pytest.raises(RegistryError, match="Invalid skill package file path"):
        registry.save_skill_package(
            SkillPackage(content=harness_markdown("unsafe-path"), files=[SkillPackageFile(path="../escape.py", content="x")])
        )

    registry.save_skill_package(SkillPackage(content=harness_markdown("duplicate-skill")))
    with pytest.raises(RegistryError, match="already exists"):
        registry.save_skill_package(SkillPackage(content=harness_markdown("duplicate-skill")))


def test_create_skill_package_is_exposed_only_for_active_skill_author(tmp_path):
    settings = make_settings(tmp_path)
    write_skill_author(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)

    inactive_tools = runtime._runtime_tools([], True, include_skill_authoring=False)
    active_tools = runtime._runtime_tools([], True, include_skill_authoring=True)
    denied = runtime._create_skill_package({"skill_markdown": harness_markdown("not-allowed")}, [])

    assert "create_skill_package" not in {tool["function"]["name"] for tool in inactive_tools}
    assert "create_skill_package" in {tool["function"]["name"] for tool in active_tools}
    assert denied["success"] is False
    assert "skill-author" in denied["error"]


def test_active_skill_author_creates_package_through_runtime_tool(tmp_path):
    settings = make_settings(tmp_path)
    write_skill_author(settings)
    registry = FileRegistry(settings.registry_dir)
    registry.reload()
    runtime = AgentRuntime(settings, registry)
    client = SequenceToolOpenAI(
        "create_skill_package",
        {
            "skill_markdown": executable_markdown("generated-echo"),
            "files": [
                {
                    "path": "skill.py",
                    "content": (
                        "definition = {'name': 'generated-echo', 'description': 'Echo text.', "
                        "'category': 'utility', 'parameters': []}\n\n"
                        "def execute(params):\n    return {'value': params.get('value')}\n"
                    ),
                }
            ],
        },
        "Created generated-echo.",
    )
    runtime.openai = client

    response = asyncio.run(runtime.chat(ChatRequest(message="create an echo skill", skill_ids=["skill-author"])))

    assert response.message == "Created generated-echo."
    assert registry.skills["generated-echo"].executable is True
    assert (settings.registry_dir / "skills" / "generated-echo" / "skill.py").is_file()
    assert "create_skill_package" in client.chat.completions.tool_sets[0]
    assert response.tool_calls[0].result["success"] is True
