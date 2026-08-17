from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MCP_TOOL_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PermissionPolicy(BaseModel):
    tenant_ids: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class McpServerDefinition(BaseModel):
    """An allowlisted MCP server connection owned by the runtime operator."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    enabled: bool = True
    transport: Literal["stdio", "streamable_http"]
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    tool_allowlist: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=60000, ge=1000, le=300000)
    permissions: PermissionPolicy = Field(default_factory=PermissionPolicy)

    @field_validator("id")
    @classmethod
    def validate_server_id(cls, value: str) -> str:
        value = value.strip()
        if not SKILL_NAME_RE.match(value):
            raise ValueError("must match ^[a-z0-9][a-z0-9-]{0,63}$")
        return value

    @field_validator("args", "tool_allowlist")
    @classmethod
    def validate_string_list(cls, values: list[str]) -> list[str]:
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("must contain non-empty strings")
        return [value.strip() for value in values]

    @field_validator("env", "headers")
    @classmethod
    def validate_string_map(cls, values: dict[str, str]) -> dict[str, str]:
        if not all(isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip() for key, value in values.items()):
            raise ValueError("must be a map of non-empty strings")
        return {key.strip(): value.strip() for key, value in values.items()}

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("must use http:// or https://")
        return value or None

    @model_validator(mode="after")
    def validate_transport_configuration(self) -> "McpServerDefinition":
        if self.transport == "stdio":
            if not self.command or not self.command.strip():
                raise ValueError("stdio MCP servers require command")
            if self.url:
                raise ValueError("stdio MCP servers must not define url")
        elif not self.url:
            raise ValueError("streamable_http MCP servers require url")
        return self


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


class McpToolDependency(BaseModel):
    """A typed MCP tool dependency declared by a harness skill."""

    model_config = ConfigDict(extra="forbid")

    alias: str
    server_id: str
    tool_name: str
    required: bool = True

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        value = value.strip()
        if not MCP_TOOL_ALIAS_RE.match(value):
            raise ValueError("must match ^[a-z][a-z0-9_]{0,63}$")
        if value.startswith("mcp_"):
            raise ValueError("must not start with reserved prefix mcp_")
        return value

    @field_validator("server_id")
    @classmethod
    def validate_dependency_server_id(cls, value: str) -> str:
        value = value.strip()
        if not SKILL_NAME_RE.match(value):
            raise ValueError("must match ^[a-z0-9][a-z0-9-]{0,63}$")
        return value

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must be non-empty")
        return value


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    enabled: bool = True
    permissions: PermissionPolicy = Field(default_factory=PermissionPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)
    capability_hints: list[str] = Field(default_factory=list)
    mcp_dependencies: list[McpToolDependency] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def validate_mcp_dependency_aliases(self) -> "SkillDefinition":
        aliases = [dependency.alias for dependency in self.mcp_dependencies]
        if len(aliases) != len(set(aliases)):
            raise ValueError("mcp_dependencies aliases must be unique within a skill")
        if self.executable and self.mcp_dependencies:
            raise ValueError("mcp_dependencies are supported only by harness skills, not executable skills")
        return self

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


class SkillPackageFile(BaseModel):
    """A UTF-8 text resource supplied with a skill package."""

    model_config = ConfigDict(extra="forbid")

    path: str
    content: str


class SkillPackage(BaseModel):
    """A skill package ready to be validated and stored by the registry."""

    model_config = ConfigDict(extra="forbid")

    content: str
    files: list[SkillPackageFile] = Field(default_factory=list)
    overwrite: bool = False


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    enabled: bool = True
    skill_ids: list[str] | None = None
    mcp_server_ids: list[str] | None = None
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
    mcp_server_ids: list[str] | None = None
    capability_hints: list[str] = Field(default_factory=list)


class SkillSummary(BaseModel):
    name: str
    description: str
    enabled: bool
    executable: bool = False
    capability_hints: list[str] = Field(default_factory=list)
    resources: list[SkillResource] = Field(default_factory=list)


class McpServerSummary(BaseModel):
    id: str
    description: str
    enabled: bool
    transport: Literal["stdio", "streamable_http"]
    tool_allowlist: list[str] = Field(default_factory=list)


class ChatAttachment(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    parser: str
    text: str
    original_bytes: int = 0
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class ArtifactRef(BaseModel):
    conversation_id: str
    artifact_id: str
    workspace_id: str | None = None


class ChatRequest(BaseModel):
    message: str
    agent_id: str = "default"
    workspace_id: str | None = None
    conversation_id: str | None = None
    skill_ids: list[str] | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list)
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
    workspace_id: str
    user_id: str
    run_id: str
    tool_call_id: str | None = None


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
    workspace_id: str
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
    workspace_id: str
    agent_id: str = "default"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    active_skill_ids: list[str] = Field(default_factory=list)
    active_mcp_server_ids: list[str] = Field(default_factory=list)
    pending_input_request: dict[str, Any] | None = None
    context_summary: str | None = None
    last_prompt_tokens: int | None = None
    last_prompt_estimated_tokens: int | None = None
    token_estimate_ratio: float = 1.0
    cumulative_prompt_tokens: int = 0
    cumulative_completion_tokens: int = 0
    memory_context: str | None = None
    memory_loaded: bool = False
    memory_revision: int = 0
    memory_dirty: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
