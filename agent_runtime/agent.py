from __future__ import annotations

import json
import hashlib
import inspect
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from .config import AgentRuntimeSettings
from .models import (
    AgentDefinition,
    ChatRequest,
    ChatResponse,
    ConversationState,
    RuntimeStepTrace,
    SkillDefinition,
    SkillExecutionContext,
    ToolCallTrace,
)
from .permissions import is_allowed
from .registry import FileRegistry
from .logging_utils import logger
from .runner_client import SandboxClient


BASE_SYSTEM_PROMPT = """You are an enterprise agent runtime.
You answer users through chat and may activate harness skills or execute executable skills when useful.
Skills are server-provided capability documents. Some skills are harness-only and some are executable.
If a harness skill may be relevant, call `activate_skill` to load the complete SKILL.md before applying it.
After a skill is activated, follow its harness document. Let the harness guide whether executable skills are needed and in what order.
If required user input is missing, call `request_user_input` instead of guessing.
When calling `request_user_input`, make the user-facing request self-contained:
- Ask only for information that blocks the next planning or execution step.
- Prefer 1-3 fields; every field must have name, label, type, required, and description.
- Field names must be snake_case, but labels must be natural user-facing Chinese.
- Descriptions must tell the user exactly what to provide, acceptable choices or scope, and one short example.
- Do not use vague labels such as "scope", "depth", or "constraints" without explaining what each means.
- If reasonable defaults are safe, proceed with assumptions instead of asking.
Only use the skills provided in this request. Do not invent skills.
If a requested action requires unavailable data, permissions, or executable skills, say what is missing.
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
REQUEST_USER_INPUT_TOOL = {
    "type": "function",
    "function": {
        "name": "request_user_input",
        "description": (
            "Pause the current run and ask the user for missing information required to continue. "
            "Use only when the missing information blocks the next planning or execution step. "
            "Every requested field must be concrete and explain what the user should provide."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "A concise, self-contained question in the user's language. "
                        "It must say why the input is needed and summarize the fields below."
                    ),
                },
                "fields": {
                    "type": "array",
                    "description": "One to three concrete structured fields the user should provide.",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Stable snake_case field name, for example primary_question.",
                            },
                            "label": {
                                "type": "string",
                                "description": "Short user-facing Chinese label, for example 研究主问题.",
                            },
                            "type": {
                                "type": "string",
                                "enum": ["string", "number", "integer", "boolean", "object", "array"],
                                "description": "Expected answer type.",
                            },
                            "required": {
                                "type": "boolean",
                                "description": "Whether this field is required before execution can continue.",
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "Specific guidance in Chinese. Include acceptable choices or boundaries "
                                    "and one short example answer."
                                ),
                            },
                        },
                        "required": ["name", "label", "type", "required", "description"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["question", "fields"],
            "additionalProperties": False,
        },
    },
}


class AgentRequestError(ValueError):
    pass


StepCallback = Callable[[RuntimeStepTrace], Awaitable[None] | None]
StreamCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class AgentRuntime:
    def __init__(self, settings: AgentRuntimeSettings, registry: FileRegistry) -> None:
        self.settings = settings
        self.registry = registry
        openai_kwargs: dict[str, Any] = {}
        if settings.openai_api_key:
            openai_kwargs["api_key"] = settings.openai_api_key
        if settings.openai_base_url:
            openai_kwargs["base_url"] = settings.openai_base_url
        logger.info(
            "OpenAI client config: model=%s base_url=%s base_url_source=%s api_key_present=%s api_key_source=%s api_key_masked=%s api_key_length=%s api_key_sha256=%s env_file=%s",
            settings.model,
            settings.openai_base_url or "(default)",
            settings.openai_base_url_source,
            bool(settings.openai_api_key),
            settings.openai_api_key_source,
            self._mask_secret(settings.openai_api_key),
            len(settings.openai_api_key or ""),
            self._secret_fingerprint(settings.openai_api_key),
            settings.env_file_loaded or "(none)",
        )
        self.openai = AsyncOpenAI(**openai_kwargs)
        self.runner = SandboxClient(settings.sandbox_url, settings.request_timeout_seconds)
        self.conversations: dict[str, ConversationState] = {}
        self.executions: list[dict[str, Any]] = []

    async def chat(
        self,
        request: ChatRequest,
        on_step: StepCallback | None = None,
        on_delta: StreamCallback | None = None,
    ) -> ChatResponse:
        agent = self._resolve_agent(request)
        conversation = self._get_or_create_conversation(request, agent)
        run_id = f"run_{uuid.uuid4().hex}"
        available_skills = self._available_skills(request, agent)
        activated_skills = self._explicit_or_active_skills(request, conversation, available_skills)
        pending_input_request = conversation.pending_input_request
        conversation.pending_input_request = None

        conversation.messages.append({"role": "user", "content": request.message})
        conversation.updated_at = datetime.now(timezone.utc)

        messages = [
            {"role": "system", "content": self._build_system_prompt(agent, available_skills, activated_skills)}
        ]
        if pending_input_request:
            messages.append({"role": "system", "content": self._build_pending_input_prompt(pending_input_request)})
        messages.extend(conversation.messages)
        traces: list[ToolCallTrace] = []
        steps: list[RuntimeStepTrace] = []

        for round_index in range(self.settings.max_runtime_rounds + 1):
            executable_skills = self._select_executable_skills(request, agent, available_skills)
            runtime_tools = self._runtime_tools(executable_skills, bool(available_skills))
            skill_by_name = {skill.name: skill for skill in executable_skills}
            openai_kwargs: dict[str, Any] = {
                "model": self.settings.model,
                "messages": messages,
            }
            if self.settings.reasoning_effort:
                openai_kwargs["reasoning_effort"] = self.settings.reasoning_effort
            if runtime_tools:
                openai_kwargs["tools"] = runtime_tools
                openai_kwargs["tool_choice"] = "auto"
            self._record_step(
                steps,
                on_step,
                kind="thinking",
                label="model_planning",
                status="started",
                detail="Calling the model to plan the next observable action.",
                metadata={
                    "run_id": run_id,
                    "round": round_index,
                    "model": self.settings.model,
                    "tool_candidates": [tool["function"]["name"] for tool in runtime_tools],
                },
            )
            model_turn = await self._complete_model_turn(
                openai_kwargs,
                on_delta=on_delta,
                run_id=run_id,
                round_index=round_index,
            )
            assistant_message = model_turn["assistant_message"]
            reasoning_text = model_turn["reasoning_text"]
            messages.append(assistant_message)
            conversation.messages.append(assistant_message)

            tool_calls = model_turn["tool_calls"]
            self._record_step(
                steps,
                on_step,
                kind="thinking",
                label="model_planning",
                status="completed",
                detail=self._thinking_detail(reasoning_text),
                metadata={
                    "run_id": run_id,
                    "round": round_index,
                    "tool_call_count": len(tool_calls),
                    "reasoning_content_available": bool(reasoning_text),
                    "reasoning_content_exposed": bool(reasoning_text and self.settings.expose_reasoning_content),
                },
            )
            if not tool_calls:
                content = str(model_turn["content"] or "")
                conversation.updated_at = datetime.now(timezone.utc)
                self._record_step(
                    steps,
                    on_step,
                    kind="final",
                    label="assistant_response",
                    status="completed",
                    detail="Model returned a final assistant message.",
                    metadata={"run_id": run_id, "content_length": len(content)},
                )
                self._log_run_summary(run_id, "completed", steps, content)
                return ChatResponse(
                    conversation_id=conversation.id,
                    agent_id=agent.id,
                    message=content,
                    steps=steps,
                    tool_calls=traces,
                    model=self.settings.model,
                )

            for call in tool_calls:
                function = call.get("function") if isinstance(call, dict) else {}
                function = function if isinstance(function, dict) else {}
                tool_name = str(function.get("name") or "").strip()
                args = self._parse_tool_arguments(str(function.get("arguments") or ""))
                tool_call_id = str(call.get("id") or f"call_{uuid.uuid4().hex}") if isinstance(call, dict) else f"call_{uuid.uuid4().hex}"
                execution_id: str | None = None
                if tool_name == "activate_skill":
                    result = self._activate_skill(args, available_skills, activated_skills, conversation)
                    messages[0] = {
                        "role": "system",
                        "content": self._build_system_prompt(agent, available_skills, activated_skills),
                    }
                    self._record_call_step(steps, on_step, run_id, "runtime_call", tool_name, tool_call_id, result)
                elif tool_name == "read_skill_resource":
                    result = self._read_skill_resource(args, activated_skills)
                    self._record_call_step(steps, on_step, run_id, "runtime_call", tool_name, tool_call_id, result)
                elif tool_name == "request_user_input":
                    question = str(args.get("question", "")).strip() or "请补充继续执行所需的信息。"
                    fields = self._parse_requested_fields(args.get("fields"))
                    result = {
                        "success": True,
                        "status": "waiting_for_user",
                        "question": question,
                        "fields": [field.model_dump() for field in fields],
                        "run_id": run_id,
                    }
                    conversation.pending_input_request = {
                        "question": question,
                        "fields": result["fields"],
                        "run_id": run_id,
                    }
                    self._record_step(
                        steps,
                        on_step,
                        kind="waiting_for_user",
                        label="request_user_input",
                        status="waiting",
                        detail=question,
                        metadata={
                            "run_id": run_id,
                            "field_names": [field.name for field in fields],
                        },
                        tool_call_id=tool_call_id,
                    )
                    traces.append(
                        ToolCallTrace(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            arguments=args,
                            result=result,
                            execution_id=None,
                        )
                    )
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    assistant_question = {"role": "assistant", "content": question}
                    messages.append(tool_message)
                    messages.append(assistant_question)
                    conversation.messages.append(tool_message)
                    conversation.messages.append(assistant_question)
                    conversation.updated_at = datetime.now(timezone.utc)
                    logger.info(
                        "INPUT_REQUIRED run_id=%s question=%s fields=%s tool_call_id=%s",
                        run_id,
                        question,
                        [
                            {
                                "name": field.name,
                                "label": field.label,
                                "required": field.required,
                                "description": field.description,
                            }
                            for field in fields
                        ],
                        tool_call_id,
                    )
                    self._log_run_summary(run_id, "waiting_for_user", steps, question)
                    return ChatResponse(
                        conversation_id=conversation.id,
                        agent_id=agent.id,
                        message=question,
                        status="waiting_for_user",
                        requested_inputs=fields,
                        steps=steps,
                        tool_calls=traces,
                        model=self.settings.model,
                    )
                else:
                    skill = skill_by_name.get(tool_name)
                    if skill is None:
                        result = {"success": False, "error": f"Executable skill is not available: {tool_name}"}
                        self._record_call_step(steps, on_step, run_id, "runtime_call", tool_name, tool_call_id, result)
                    elif not is_allowed(skill.permissions, request.user):
                        result = {"success": False, "error": f"Permission denied for skill: {tool_name}"}
                        self._record_call_step(steps, on_step, run_id, "runtime_call", tool_name, tool_call_id, result)
                    else:
                        script = self.registry.read_skill_entrypoint(skill)
                        result = await self.runner.execute_skill(
                            skill,
                            args,
                            SkillExecutionContext(
                                agent_id=agent.id,
                                conversation_id=conversation.id,
                                user_id=request.user.id,
                                run_id=run_id,
                            ),
                            script,
                            base_policy=agent.execution_policy,
                        )
                        execution = result.get("execution") if isinstance(result, dict) else None
                        if isinstance(execution, dict):
                            execution_id = execution.get("execution_id")
                            self.executions.append(execution)
                        self._record_call_step(
                            steps,
                            on_step,
                            run_id,
                            "sandbox_execution",
                            tool_name,
                            tool_call_id,
                            result,
                            execution_id=execution_id,
                        )
                traces.append(
                    ToolCallTrace(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        arguments=args,
                        result=result,
                        execution_id=execution_id,
                    )
                )
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
                messages.append(tool_message)
                conversation.messages.append(tool_message)

        final_message = "runtime 调用轮次超过上限，已停止。请缩小问题范围或提高 AGENT_RUNTIME_MAX_RUNTIME_ROUNDS。"
        conversation.messages.append({"role": "assistant", "content": final_message})
        conversation.updated_at = datetime.now(timezone.utc)
        self._record_step(
            steps,
            on_step,
            kind="final",
            label="runtime_round_limit",
            status="failed",
            detail=final_message,
            metadata={"run_id": run_id, "max_runtime_rounds": self.settings.max_runtime_rounds},
        )
        self._log_run_summary(run_id, "failed", steps, final_message)
        return ChatResponse(
            conversation_id=conversation.id,
            agent_id=agent.id,
            message=final_message,
            steps=steps,
            tool_calls=traces,
            model=self.settings.model,
        )

    async def _complete_model_turn(
        self,
        openai_kwargs: dict[str, Any],
        *,
        on_delta: StreamCallback | None,
        run_id: str,
        round_index: int,
    ) -> dict[str, Any]:
        if on_delta is None:
            return await self._complete_model_turn_non_stream(openai_kwargs)
        return await self._complete_model_turn_stream(
            openai_kwargs,
            on_delta=on_delta,
            run_id=run_id,
            round_index=round_index,
        )

    async def _complete_model_turn_non_stream(self, openai_kwargs: dict[str, Any]) -> dict[str, Any]:
        completion = await self.openai.chat.completions.create(**openai_kwargs)
        choice = completion.choices[0]
        assistant_message = choice.message.model_dump(exclude_none=True)
        reasoning_text = self._extract_reasoning_text(choice.message, assistant_message)
        assistant_message = self._sanitize_assistant_message(assistant_message)
        content = choice.message.content or assistant_message.get("content") or ""
        tool_calls = self._normalize_tool_calls(choice.message.tool_calls or assistant_message.get("tool_calls") or [])
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        if content:
            assistant_message["content"] = content
        return {
            "assistant_message": assistant_message,
            "content": content,
            "reasoning_text": reasoning_text,
            "tool_calls": tool_calls,
        }

    async def _complete_model_turn_stream(
        self,
        openai_kwargs: dict[str, Any],
        *,
        on_delta: StreamCallback,
        run_id: str,
        round_index: int,
    ) -> dict[str, Any]:
        stream = await self.openai.chat.completions.create(**{**openai_kwargs, "stream": True})
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}
        reasoning_redacted_emitted = False

        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            delta_payload = self._model_dump(delta)

            content_delta = self._extract_delta_string(delta, delta_payload, "content")
            if content_delta:
                content_parts.append(content_delta)
                self._emit_delta(
                    on_delta,
                    {
                        "kind": "assistant",
                        "delta": content_delta,
                        "run_id": run_id,
                        "round": round_index,
                    },
                )

            reasoning_delta = self._extract_delta_reasoning(delta, delta_payload)
            if reasoning_delta:
                reasoning_parts.append(reasoning_delta)
                if self.settings.expose_reasoning_content:
                    self._emit_delta(
                        on_delta,
                        {
                            "kind": "thinking",
                            "delta": reasoning_delta,
                            "redacted": False,
                            "run_id": run_id,
                            "round": round_index,
                        },
                    )
                elif not reasoning_redacted_emitted:
                    reasoning_redacted_emitted = True
                    self._emit_delta(
                        on_delta,
                        {
                            "kind": "thinking",
                            "delta": "",
                            "redacted": True,
                            "run_id": run_id,
                            "round": round_index,
                        },
                    )

            for tool_call_delta in self._extract_tool_call_deltas(delta, delta_payload):
                index = self._coerce_tool_call_index(tool_call_delta.get("index"), len(tool_call_parts))
                current = tool_call_parts.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if isinstance(tool_call_delta.get("id"), str) and tool_call_delta["id"]:
                    current["id"] = tool_call_delta["id"]
                if isinstance(tool_call_delta.get("type"), str) and tool_call_delta["type"]:
                    current["type"] = tool_call_delta["type"]
                function_delta = tool_call_delta.get("function") if isinstance(tool_call_delta.get("function"), dict) else {}
                name_delta = function_delta.get("name")
                if isinstance(name_delta, str) and name_delta:
                    current["function"]["name"] += name_delta
                    self._emit_delta(
                        on_delta,
                        {
                            "kind": "tool_call",
                            "phase": "name",
                            "delta": name_delta,
                            "name": current["function"]["name"],
                            "tool_call_index": index,
                            "tool_call_id": current["id"] or None,
                            "run_id": run_id,
                            "round": round_index,
                        },
                    )
                arguments_delta = function_delta.get("arguments")
                if isinstance(arguments_delta, str) and arguments_delta:
                    current["function"]["arguments"] += arguments_delta
                    self._emit_delta(
                        on_delta,
                        {
                            "kind": "tool_call",
                            "phase": "arguments",
                            "delta": arguments_delta,
                            "name": current["function"]["name"],
                            "tool_call_index": index,
                            "tool_call_id": current["id"] or None,
                            "run_id": run_id,
                            "round": round_index,
                        },
                    )

        content = "".join(content_parts)
        reasoning_text = "".join(reasoning_parts).strip() or None
        tool_calls = self._finalize_stream_tool_calls(tool_call_parts)
        assistant_message: dict[str, Any] = {"role": "assistant"}
        if content:
            assistant_message["content"] = content
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        return {
            "assistant_message": assistant_message,
            "content": content,
            "reasoning_text": reasoning_text,
            "tool_calls": tool_calls,
        }

    @staticmethod
    def _model_dump(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(exclude_none=True)
            return dumped if isinstance(dumped, dict) else {}
        return {}

    @staticmethod
    def _extract_delta_string(delta: Any, delta_payload: dict[str, Any], key: str) -> str:
        value = delta_payload.get(key)
        if not isinstance(value, str):
            value = getattr(delta, key, None)
        return value if isinstance(value, str) else ""

    @staticmethod
    def _extract_delta_reasoning(delta: Any, delta_payload: dict[str, Any]) -> str:
        for key in ("reasoning_content", "reasoning"):
            value = delta_payload.get(key)
            if isinstance(value, str):
                return value
        model_extra = getattr(delta, "model_extra", None)
        if isinstance(model_extra, dict):
            for key in ("reasoning_content", "reasoning"):
                value = model_extra.get(key)
                if isinstance(value, str):
                    return value
        return ""

    def _extract_tool_call_deltas(self, delta: Any, delta_payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw_tool_calls = delta_payload.get("tool_calls")
        if raw_tool_calls is None:
            raw_tool_calls = getattr(delta, "tool_calls", None)
        if not isinstance(raw_tool_calls, list):
            return []
        normalized: list[dict[str, Any]] = []
        for raw in raw_tool_calls:
            payload = self._model_dump(raw)
            if payload:
                normalized.append(payload)
        return normalized

    @staticmethod
    def _coerce_tool_call_index(value: Any, fallback: int) -> int:
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return fallback

    @staticmethod
    def _normalize_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_tool_calls, list):
            return []
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_tool_calls):
            payload = AgentRuntime._model_dump(raw)
            if not payload:
                function_obj = getattr(raw, "function", None)
                payload = {
                    "id": getattr(raw, "id", None),
                    "type": getattr(raw, "type", "function"),
                    "function": {
                        "name": getattr(function_obj, "name", None),
                        "arguments": getattr(function_obj, "arguments", None),
                    },
                }
            function = payload.get("function") if isinstance(payload.get("function"), dict) else {}
            name = str(function.get("name") or "").strip()
            arguments = function.get("arguments")
            if not name:
                continue
            normalized.append(
                {
                    "id": str(payload.get("id") or f"call_{uuid.uuid4().hex}"),
                    "type": str(payload.get("type") or "function"),
                    "function": {
                        "name": name,
                        "arguments": arguments if isinstance(arguments, str) else "",
                    },
                }
            )
        return normalized

    @staticmethod
    def _finalize_stream_tool_calls(tool_call_parts: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        for index in sorted(tool_call_parts):
            item = tool_call_parts[index]
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            tool_calls.append(
                {
                    "id": str(item.get("id") or f"call_{uuid.uuid4().hex}"),
                    "type": str(item.get("type") or "function"),
                    "function": {
                        "name": name,
                        "arguments": function.get("arguments") if isinstance(function.get("arguments"), str) else "",
                    },
                }
            )
        return tool_calls

    def _resolve_agent(self, request: ChatRequest) -> AgentDefinition:
        try:
            agent = self.registry.get_agent(request.agent_id)
        except Exception as exc:
            raise AgentRequestError(str(exc)) from exc
        if not agent.enabled or not is_allowed(agent.permissions, request.user):
            raise AgentRequestError(f"Agent unavailable or permission denied: {request.agent_id}")
        return agent

    def _get_or_create_conversation(
        self,
        request: ChatRequest,
        agent: AgentDefinition | None = None,
    ) -> ConversationState:
        agent = agent or self._resolve_agent(request)
        if request.conversation_id and request.conversation_id in self.conversations:
            conversation = self.conversations[request.conversation_id]
            if conversation.agent_id != agent.id:
                raise AgentRequestError(
                    f"Conversation {conversation.id} belongs to agent {conversation.agent_id}, not {agent.id}"
                )
            return conversation
        conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex}"
        conversation = ConversationState(id=conversation_id, agent_id=agent.id)
        self.conversations[conversation_id] = conversation
        return conversation

    def _available_skills(
        self,
        request: ChatRequest,
        agent: AgentDefinition | None = None,
    ) -> dict[str, SkillDefinition]:
        agent = agent or self._resolve_agent(request)
        skills = {skill.name: skill for skill in self.registry.accessible_skills(request.user)}
        if agent.skill_ids is not None:
            skills = {name: skill for name, skill in skills.items() if name in set(agent.skill_ids)}
        return skills

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

    def _select_executable_skills(
        self,
        request: ChatRequest,
        agent: AgentDefinition,
        available_skills: dict[str, SkillDefinition],
    ) -> list[SkillDefinition]:
        return [skill for skill in available_skills.values() if skill.executable]

    def _build_system_prompt(
        self,
        agent: AgentDefinition,
        available_skills: dict[str, SkillDefinition],
        activated_skills: list[SkillDefinition],
    ) -> str:
        parts = [BASE_SYSTEM_PROMPT]
        parts.append(
            "Current agent:\n"
            f"- id: {agent.id}\n"
            f"- name: {agent.name}\n"
            f"- description: {agent.description or 'No description'}\n"
            "The server exposes executable skills as callable functions and harness skills through activate_skill."
        )
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

    def _runtime_tools(self, executable_skills: list[SkillDefinition], include_skill_activation: bool) -> list[dict[str, Any]]:
        result = [REQUEST_USER_INPUT_TOOL, *[skill.to_openai_tool() for skill in executable_skills]]
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

    @staticmethod
    def _parse_requested_fields(raw: Any) -> list[Any]:
        from .models import RequestedInputField

        if not isinstance(raw, list):
            return []
        allowed_types = {"string", "number", "integer", "boolean", "object", "array"}
        fields = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            field_type = str(item.get("type", "string")).strip() or "string"
            if field_type not in allowed_types:
                field_type = "string"
            label = str(item.get("label", "")).strip() or AgentRuntime._humanize_input_label(name)
            description = str(item.get("description", "")).strip()
            if len(description) < 8:
                description = AgentRuntime._fallback_input_description(name, label)
            fields.append(
                RequestedInputField(
                    name=name,
                    label=label,
                    type=field_type,
                    required=item.get("required") if isinstance(item.get("required"), bool) else True,
                    description=description,
                )
            )
        return fields

    @staticmethod
    def _humanize_input_label(name: str) -> str:
        normalized = name.strip().lower().replace("-", "_")
        label_map = {
            "primary_question": "研究主问题",
            "research_question": "研究主问题",
            "question": "核心问题",
            "depth": "调研深度",
            "depth_level": "调研深度",
            "scope": "调研范围",
            "scope_constraints": "范围约束",
            "constraints": "限制条件",
            "time_range": "时间范围",
            "region": "地域范围",
            "industry": "行业范围",
            "output_format": "输出形式",
            "audience": "使用对象",
        }
        if normalized in label_map:
            return label_map[normalized]
        return normalized.replace("_", " ").strip().title() or "补充信息"

    @staticmethod
    def _fallback_input_description(name: str, label: str) -> str:
        normalized = name.strip().lower().replace("-", "_")
        description_map = {
            "primary_question": "请说明这次任务最想回答的核心问题。示例：炼钢工序的能耗平衡如何建模并识别主要节能点？",
            "research_question": "请说明这次任务最想回答的核心问题。示例：炼钢工序的能耗平衡如何建模并识别主要节能点？",
            "question": "请补充需要优先回答的问题。示例：比较转炉与电炉炼钢的能耗边界和关键影响因素。",
            "depth": "请选择期望深度。示例：快速综述、工程可执行方案、学术级深度调研。",
            "depth_level": "请选择期望深度。示例：快速综述、工程可执行方案、学术级深度调研。",
            "scope": "请说明调研边界，包括行业、地区、时间范围或要排除的内容。示例：中国钢铁行业，2020年以来，聚焦炼钢不含轧钢。",
            "scope_constraints": "请说明调研边界和限制条件，包括行业、地区、时间范围或要排除的内容。示例：中国钢铁行业，2020年以来，聚焦炼钢不含轧钢。",
            "constraints": "请说明必须遵守的限制条件。示例：只使用公开来源，优先中文资料，需要可追溯引用。",
            "time_range": "请说明关注的时间范围。示例：2020年至今，或最近三年。",
            "region": "请说明关注的国家、地区或市场。示例：中国、欧盟、全球对比。",
            "industry": "请说明关注的行业或业务场景。示例：长流程钢厂炼钢工序。",
            "output_format": "请说明希望的输出形式。示例：结构化报告、表格对比、执行清单。",
            "audience": "请说明报告给谁使用。示例：工程技术团队、管理层、论文写作。",
        }
        return description_map.get(normalized, f"请补充“{label}”的具体要求，并给出一个示例或边界。")

    @staticmethod
    def _build_pending_input_prompt(pending_input_request: dict[str, Any]) -> str:
        question = str(pending_input_request.get("question", "")).strip()
        fields = pending_input_request.get("fields") if isinstance(pending_input_request.get("fields"), list) else []
        return (
            "The previous runtime step paused with request_user_input.\n"
            f"Question asked to the user: {question or 'N/A'}\n"
            f"Requested fields: {json.dumps(fields, ensure_ascii=False)}\n"
            "Treat the latest user message as the user's answer to that request. "
            "Continue the same workflow if sufficient; call request_user_input again only if required information is still missing."
        )

    def _record_call_step(
        self,
        steps: list[RuntimeStepTrace],
        on_step: StepCallback | None,
        run_id: str,
        kind: str,
        label: str,
        tool_call_id: str,
        result: dict[str, Any],
        *,
        execution_id: str | None = None,
    ) -> None:
        success = result.get("success") if isinstance(result, dict) else None
        self._record_step(
            steps,
            on_step,
            kind=kind,
            label=label,
            status="completed" if success is not False else "failed",
            detail=str(result.get("error") or result.get("phase") or ""),
            metadata={
                "run_id": run_id,
                "success": success,
                "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
            },
            tool_call_id=tool_call_id,
            execution_id=execution_id,
        )
        if kind == "sandbox_execution":
            logger.info(
                "TOOL_USED run_id=%s tool=%s status=%s success=%s execution_id=%s tool_call_id=%s detail=%s",
                run_id,
                label,
                "completed" if success is not False else "failed",
                success,
                execution_id,
                tool_call_id,
                str(result.get("error") or result.get("phase") or ""),
            )

    def _record_step(
        self,
        steps: list[RuntimeStepTrace],
        on_step: StepCallback | None,
        *,
        kind: str,
        label: str,
        status: str = "completed",
        detail: str = "",
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        execution_id: str | None = None,
    ) -> RuntimeStepTrace:
        step = RuntimeStepTrace(
            step_id=f"step_{len(steps) + 1:03d}",
            kind=kind,
            label=label,
            status=status,
            detail=detail,
            metadata=metadata or {},
            tool_call_id=tool_call_id,
            execution_id=execution_id,
        )
        steps.append(step)
        self._emit_step(on_step, step)
        logger.debug(
            "runtime_step step_id=%s kind=%s label=%s status=%s tool_call_id=%s execution_id=%s detail=%s metadata=%s",
            step.step_id,
            step.kind,
            step.label,
            step.status,
            step.tool_call_id,
            step.execution_id,
            step.detail,
            json.dumps(step.metadata, ensure_ascii=False, default=str),
        )
        return step

    @staticmethod
    def _emit_step(on_step: StepCallback | None, step: RuntimeStepTrace) -> None:
        if on_step is None:
            return
        result = on_step(step)
        if inspect.isawaitable(result):
            raise RuntimeError("Async step callbacks must be awaited through chat_stream")

    @staticmethod
    def _emit_delta(on_delta: StreamCallback | None, payload: dict[str, Any]) -> None:
        if on_delta is None:
            return
        result = on_delta(payload)
        if inspect.isawaitable(result):
            raise RuntimeError("Async stream callbacks must be synchronous in chat_stream")

    @staticmethod
    def _log_run_summary(
        run_id: str,
        status: str,
        steps: list[RuntimeStepTrace],
        final_message: str,
    ) -> None:
        tool_names = [step.label for step in steps if step.kind == "sandbox_execution"]
        runtime_calls = [step.label for step in steps if step.kind in {"runtime_call", "waiting_for_user"}]
        logger.info(
            "RUN_DONE run_id=%s status=%s used_tool=%s tools=%s sandbox_calls=%s runtime_calls=%s final_chars=%s",
            run_id,
            status,
            bool(tool_names),
            tool_names or [],
            len(tool_names),
            runtime_calls or [],
            len(final_message or ""),
        )

    def _thinking_detail(self, reasoning_text: str | None) -> str:
        if reasoning_text and self.settings.expose_reasoning_content:
            return reasoning_text
        if reasoning_text:
            return "Model reasoning content was returned by the provider but is redacted by AGENT_RUNTIME_EXPOSE_REASONING_CONTENT=false."
        return "Model planning completed; this provider did not return explicit reasoning content."

    @staticmethod
    def _extract_reasoning_text(message: Any, assistant_message: dict[str, Any]) -> str | None:
        candidates = [
            assistant_message.get("reasoning_content"),
            assistant_message.get("reasoning"),
        ]
        model_extra = getattr(message, "model_extra", None)
        if isinstance(model_extra, dict):
            candidates.extend([model_extra.get("reasoning_content"), model_extra.get("reasoning")])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    @staticmethod
    def _sanitize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(message)
        sanitized.pop("reasoning_content", None)
        sanitized.pop("reasoning", None)
        return sanitized

    @staticmethod
    def _mask_secret(value: str | None) -> str:
        if not value:
            return "(missing)"
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}"

    @staticmethod
    def _secret_fingerprint(value: str | None) -> str:
        if not value:
            return "(missing)"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
