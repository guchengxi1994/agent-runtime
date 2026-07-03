from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PermissionPolicy(BaseModel):
    tenant_ids: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class UserContext(BaseModel):
    id: str = "anonymous"
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class SkillResource(BaseModel):
    path: str
    title: str | None = None
    description: str = ""


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    enabled: bool = True
    permissions: PermissionPolicy = Field(default_factory=PermissionPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)
    capability_hints: list[str] = Field(default_factory=list)
    executable: bool = False
    parameters_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    required_secrets: dict[str, str] = Field(default_factory=dict)
    entrypoint: str = "skill.py"
    body: str
    resources: list[SkillResource] = Field(default_factory=list)
    skill_dir: str
    skill_file: str

    @field_validator("name")
    @classmethod
    def validate_skill_name(cls, value: str) -> str:
        value = value.strip()
        if not SKILL_NAME_RE.match(value):
            raise ValueError("must match ^[a-z0-9][a-z0-9-]{0,63}$")
        return value

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


class SkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    enabled: bool = True
    skill_ids: list[str] | None = None
    capability_hints: list[str] = Field(default_factory=list)
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    permissions: PermissionPolicy = Field(default_factory=PermissionPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        value = value.strip()
        if not AGENT_ID_RE.match(value):
            raise ValueError("must match ^[A-Za-z0-9_-]{1,64}$")
        return value


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    skill_ids: list[str] | None = None
    capability_hints: list[str] = Field(default_factory=list)


class SkillSummary(BaseModel):
    name: str
    description: str
    enabled: bool
    executable: bool = False
    capability_hints: list[str] = Field(default_factory=list)
    resources: list[SkillResource] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    agent_id: str = "default"
    conversation_id: str | None = None
    skill_ids: list[str] | None = None
    user: UserContext = Field(default_factory=UserContext)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, value: str) -> str:
        value = value.strip() or "default"
        if not AGENT_ID_RE.match(value):
            raise ValueError("must match ^[A-Za-z0-9_-]{1,64}$")
        return value


class SkillExecutionContext(BaseModel):
    agent_id: str
    conversation_id: str
    user_id: str
    run_id: str


class SkillExecutionRequest(BaseModel):
    skill: SkillDefinition
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: SkillExecutionContext
    script: str
    base_policy: dict[str, Any] = Field(default_factory=dict)


class RequestedInputField(BaseModel):
    name: str
    label: str | None = None
    type: str = "string"
    required: bool = True
    description: str = ""


class ToolCallTrace(BaseModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    execution_id: str | None = None


class RuntimeStepTrace(BaseModel):
    step_id: str
    kind: str
    label: str
    status: str = "completed"
    detail: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = None
    execution_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatResponse(BaseModel):
    conversation_id: str
    agent_id: str
    message: str
    status: str = "completed"
    requested_inputs: list[RequestedInputField] = Field(default_factory=list)
    steps: list[RuntimeStepTrace] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    model: str


class ConversationState(BaseModel):
    id: str
    agent_id: str = "default"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    active_skill_ids: list[str] = Field(default_factory=list)
    pending_input_request: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
