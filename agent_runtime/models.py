from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


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


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    parameters_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    script: str
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    permissions: PermissionPolicy = Field(default_factory=PermissionPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        value = value.strip()
        if not TOOL_NAME_RE.match(value):
            raise ValueError("must match ^[A-Za-z0-9_-]{1,64}$")
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


class SkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class ToolSummary(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    permissions: PermissionPolicy
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillSummary(BaseModel):
    name: str
    description: str
    enabled: bool
    resources: list[SkillResource] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    skill_ids: list[str] | None = None
    tool_ids: list[str] | None = None
    user: UserContext = Field(default_factory=UserContext)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallTrace(BaseModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    model: str


class ConversationState(BaseModel):
    id: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    active_skill_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
