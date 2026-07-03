from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI

from .config import AgentRuntimeSettings
from .models import (
    ChatRequest,
    ChatResponse,
    ConversationState,
    SkillDefinition,
    ToolCallTrace,
    ToolDefinition,
)
from .permissions import is_allowed
from .registry import FileRegistry
from .runner_client import PluginRunnerClient


BASE_SYSTEM_PROMPT = """You are an enterprise agent runtime.
You answer users through chat and may activate skills or call tools when useful.
Skills are server-provided harness documents. The skill catalog only contains names and descriptions. If a skill may be relevant, call `activate_skill` to load the complete SKILL.md before applying it.
After a skill is activated, follow its harness document. Let the harness guide whether tools are needed and in what order.
Only use the skills and tools provided in this request. Do not invent skills or tools.
If a requested action requires unavailable data, permissions, or tools, say what is missing.
Return concise, actionable answers."""

ACTIVATE_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "activate_skill",
        "description": "Load the complete harness document for a relevant skill from the server-side skill registry.",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The exact skill name from the available skill catalog.",
                }
            },
            "required": ["skill_name"],
            "additionalProperties": False,
        },
    },
}
READ_SKILL_RESOURCE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_skill_resource",
        "description": "Read an optional resource file from an already activated skill package.",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The exact activated skill name.",
                },
                "path": {
                    "type": "string",
                    "description": "The resource path shown in the activated skill response.",
                },
            },
            "required": ["skill_name", "path"],
            "additionalProperties": False,
        },
    },
}


class AgentRequestError(ValueError):
    pass


class AgentRuntime:
    def __init__(self, settings: AgentRuntimeSettings, registry: FileRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.openai = AsyncOpenAI()
        self.runner = PluginRunnerClient(settings.plugin_server_url, settings.request_timeout_seconds)
        self.conversations: dict[str, ConversationState] = {}

    async def chat(self, request: ChatRequest) -> ChatResponse:
        conversation = self._get_or_create_conversation(request)
        available_skills = self._available_skills(request)
        activated_skills = self._explicit_or_active_skills(request, conversation, available_skills)
        tools = self._select_tools(request)

        conversation.messages.append({"role": "user", "content": request.message})
        conversation.updated_at = datetime.now(timezone.utc)

        messages = [
            {"role": "system", "content": self._build_system_prompt(available_skills, activated_skills)}
        ] + conversation.messages
        traces: list[ToolCallTrace] = []

        for _ in range(self.settings.max_tool_rounds + 1):
            runtime_tools = self._runtime_tools(tools, bool(available_skills))
            tool_by_name = {tool.name: tool for tool in tools}
            openai_kwargs: dict[str, Any] = {
                "model": self.settings.model,
                "messages": messages,
            }
            if runtime_tools:
                openai_kwargs["tools"] = runtime_tools
                openai_kwargs["tool_choice"] = "auto"
            completion = await self.openai.chat.completions.create(
                **openai_kwargs,
            )
            choice = completion.choices[0]
            assistant_message = choice.message.model_dump(exclude_none=True)
            messages.append(assistant_message)
            conversation.messages.append(assistant_message)

            tool_calls = choice.message.tool_calls or []
            if not tool_calls:
                content = choice.message.content or ""
                conversation.updated_at = datetime.now(timezone.utc)
                return ChatResponse(
                    conversation_id=conversation.id,
                    message=content,
                    tool_calls=traces,
                    model=self.settings.model,
                )

            for call in tool_calls:
                tool_name = call.function.name
                args = self._parse_tool_arguments(call.function.arguments)
                if tool_name == "activate_skill":
                    result = self._activate_skill(args, available_skills, activated_skills, conversation)
                elif tool_name == "read_skill_resource":
                    result = self._read_skill_resource(args, activated_skills)
                else:
                    tool = tool_by_name.get(tool_name)
                    if tool is None:
                        result = {"success": False, "error": f"Tool is not available: {tool_name}"}
                    elif not is_allowed(tool.permissions, request.user):
                        result = {"success": False, "error": f"Permission denied for tool: {tool_name}"}
                    else:
                        result = await self.runner.execute(tool, args)
                traces.append(
                    ToolCallTrace(
                        tool_call_id=call.id,
                        tool_name=tool_name,
                        arguments=args,
                        result=result,
                    )
                )
                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
                messages.append(tool_message)
                conversation.messages.append(tool_message)

        final_message = "工具调用轮次超过上限，已停止。请缩小问题范围或提高 AGENT_RUNTIME_MAX_TOOL_ROUNDS。"
        conversation.messages.append({"role": "assistant", "content": final_message})
        conversation.updated_at = datetime.now(timezone.utc)
        return ChatResponse(
            conversation_id=conversation.id,
            message=final_message,
            tool_calls=traces,
            model=self.settings.model,
        )

    def _get_or_create_conversation(self, request: ChatRequest) -> ConversationState:
        if request.conversation_id and request.conversation_id in self.conversations:
            return self.conversations[request.conversation_id]
        conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex}"
        conversation = ConversationState(id=conversation_id)
        self.conversations[conversation_id] = conversation
        return conversation

    def _available_skills(self, request: ChatRequest) -> dict[str, SkillDefinition]:
        return {skill.name: skill for skill in self.registry.accessible_skills(request.user)}

    def _explicit_or_active_skills(
        self,
        request: ChatRequest,
        conversation: ConversationState,
        accessible: dict[str, SkillDefinition],
    ) -> list[SkillDefinition]:
        if request.skill_ids is not None:
            missing = [skill_id for skill_id in request.skill_ids if skill_id not in accessible]
            if missing:
                raise AgentRequestError(f"Skill unavailable or permission denied: {', '.join(missing)}")
            return [accessible[skill_id] for skill_id in request.skill_ids]
        return [accessible[skill_id] for skill_id in conversation.active_skill_ids if skill_id in accessible]

    def _select_tools(self, request: ChatRequest) -> list[ToolDefinition]:
        accessible = {tool.id: tool for tool in self.registry.accessible_tools(request.user)}
        if request.tool_ids is not None:
            missing = [tool_id for tool_id in request.tool_ids if tool_id not in accessible]
            if missing:
                raise AgentRequestError(f"Tool unavailable or permission denied: {', '.join(missing)}")
            return [accessible[tool_id] for tool_id in request.tool_ids]
        return list(accessible.values())

    def _build_system_prompt(
        self,
        available_skills: dict[str, SkillDefinition],
        activated_skills: list[SkillDefinition],
    ) -> str:
        parts = [BASE_SYSTEM_PROMPT]
        if available_skills:
            catalog = [
                f"- {skill.name}: {skill.description}"
                for skill in available_skills.values()
            ]
            parts.append("Available skill catalog:\n" + "\n".join(catalog))
        if not activated_skills:
            return "\n\n".join(parts)
        skill_blocks = []
        for skill in activated_skills:
            skill_blocks.append(
                f"## Activated Skill: {skill.name}\n"
                f"Description: {skill.description}\n"
                f"{skill.body.strip()}"
            )
        parts.append("Activated skills:\n\n" + "\n\n".join(skill_blocks))
        return "\n\n".join(parts)

    def _runtime_tools(self, tools: list[ToolDefinition], include_skill_activation: bool) -> list[dict[str, Any]]:
        result = [tool.to_openai_tool() for tool in tools]
        if include_skill_activation:
            result.insert(0, ACTIVATE_SKILL_TOOL)
            result.insert(1, READ_SKILL_RESOURCE_TOOL)
        return result

    def _activate_skill(
        self,
        args: dict[str, Any],
        available_skills: dict[str, SkillDefinition],
        activated_skills: list[SkillDefinition],
        conversation: ConversationState,
    ) -> dict[str, Any]:
        skill_name = str(args.get("skill_name", "")).strip()
        skill = available_skills.get(skill_name)
        if skill is None:
            return {"success": False, "error": f"Skill is not available: {skill_name}"}
        if skill.name not in conversation.active_skill_ids:
            conversation.active_skill_ids.append(skill.name)
            activated_skills.append(skill)
        return {
            "success": True,
            "skill_name": skill.name,
            "description": skill.description,
            "body": skill.body,
            "resources": [resource.model_dump() for resource in skill.resources],
        }

    def _read_skill_resource(
        self,
        args: dict[str, Any],
        activated_skills: list[SkillDefinition],
    ) -> dict[str, Any]:
        skill_name = str(args.get("skill_name", "")).strip()
        resource_path = str(args.get("path", "")).strip()
        skill_by_name = {skill.name: skill for skill in activated_skills}
        skill = skill_by_name.get(skill_name)
        if skill is None:
            return {"success": False, "error": f"Skill is not activated: {skill_name}"}
        try:
            content = self.registry.read_skill_resource(skill, resource_path)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "skill_name": skill.name,
            "path": resource_path,
            "content": content,
        }

    @staticmethod
    def _parse_tool_arguments(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw_arguments": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
