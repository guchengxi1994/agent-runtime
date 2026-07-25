from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import (
    AgentDefinition,
    AgentSummary,
    McpServerDefinition,
    McpServerSummary,
    PermissionPolicy,
    SkillDefinition,
    SkillPackage,
    SkillPackageFile,
    SkillResource,
    SkillSummary,
    UserContext,
)
from .permissions import is_allowed


class RegistryError(RuntimeError):
    pass


MAX_SKILL_PACKAGE_FILES = 32
MAX_SKILL_FILE_BYTES = 256 * 1024
MAX_SKILL_PACKAGE_BYTES = 1024 * 1024
FORBIDDEN_SKILL_FILE_SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}
FORBIDDEN_SKILL_FILE_NAMES = {"skill.md", ".env", ".env.local", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}


class FileRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.skills_dir = root / "skills"
        self.agents_dir = root / "agents"
        self.mcp_servers_dir = root / "mcp_servers"
        self.agents: dict[str, AgentDefinition] = {}
        self.skills: dict[str, SkillDefinition] = {}
        self.mcp_servers: dict[str, McpServerDefinition] = {}

    def reload(self) -> None:
        self.agents = self._load_agents()
        self.skills = self._load_skills()
        self.mcp_servers = self._load_mcp_servers()

    def _load_agents(self) -> dict[str, AgentDefinition]:
        agents: dict[str, AgentDefinition] = {}
        if not self.agents_dir.exists():
            return agents
        for path in sorted(self.agents_dir.glob("*.json")):
            agent = AgentDefinition.model_validate_json(path.read_text(encoding="utf-8"))
            if agent.id in agents:
                raise RegistryError(f"Duplicate agent id: {agent.id}")
            agents[agent.id] = agent
        return agents

    def _load_skills(self) -> dict[str, SkillDefinition]:
        skills: dict[str, SkillDefinition] = {}
        for path in sorted(item for item in self.skills_dir.iterdir() if item.is_dir() and not item.name.startswith(".")):
            skill = self._load_skill_package(path)
            if skill.name == "mcp":
                raise RegistryError("Skill name `mcp` is reserved for the built-in MCP harness")
            if skill.name in skills:
                raise RegistryError(f"Duplicate skill name: {skill.name}")
            skills[skill.name] = skill
        return skills

    def _load_mcp_servers(self) -> dict[str, McpServerDefinition]:
        servers: dict[str, McpServerDefinition] = {}
        if not self.mcp_servers_dir.exists():
            return servers
        paths = sorted([*self.mcp_servers_dir.glob("*.yaml"), *self.mcp_servers_dir.glob("*.yml")])
        for path in paths:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                server = McpServerDefinition.model_validate(raw)
            except (OSError, yaml.YAMLError, ValidationError) as exc:
                raise RegistryError(f"Invalid MCP server config {path}: {exc}") from exc
            if server.id in servers:
                raise RegistryError(f"Duplicate MCP server id: {server.id}")
            servers[server.id] = server
        return servers

    def _load_skill_package(self, skill_dir: Path) -> SkillDefinition:
        instructions_path = skill_dir / "SKILL.md"
        if not instructions_path.is_file():
            raise RegistryError(f"Skill package missing SKILL.md: {skill_dir}")

        frontmatter, body = parse_skill_markdown(instructions_path.read_text(encoding="utf-8"))
        name = normalize_required_string(frontmatter, "name", instructions_path)
        description = normalize_required_string(frontmatter, "description", instructions_path)
        if name != skill_dir.name:
            raise RegistryError(f"Skill name must match directory name: name={name}, dir={skill_dir.name}")

        body = body.strip()
        if not body:
            raise RegistryError(f"Skill {name} has empty SKILL.md body")

        metadata = frontmatter.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise RegistryError(f"Skill {name} metadata must be an object")
        permissions = extract_permissions(metadata)
        runtime_meta = extract_runtime_metadata(metadata)

        try:
            return SkillDefinition.model_validate(
                {
                    "name": name,
                    "description": description,
                    "enabled": normalize_enabled(metadata),
                    "permissions": permissions,
                    "metadata": metadata,
                    "capability_hints": normalize_string_list(
                        runtime_meta.get("capabilities") or runtime_meta.get("capability_hints")
                    ),
                    "mcp_dependencies": runtime_meta.get("mcp_dependencies") or [],
                    "executable": normalize_bool(runtime_meta.get("executable"), False),
                    "parameters_schema": normalize_object(
                        runtime_meta.get("parameters_schema") or runtime_meta.get("parameters"),
                        {"type": "object", "properties": {}, "additionalProperties": False},
                    ),
                    "execution_policy": normalize_object(runtime_meta.get("execution_policy"), {}),
                    "required_secrets": normalize_string_map(runtime_meta.get("required_secrets")),
                    "entrypoint": normalize_optional_string(runtime_meta.get("entrypoint")) or "skill.py",
                    "body": body,
                    "resources": discover_skill_resources(skill_dir),
                    "skill_dir": str(skill_dir),
                    "skill_file": str(instructions_path),
                }
            )
        except ValidationError as exc:
            raise RegistryError(f"Invalid skill package {skill_dir}: {exc}") from exc

    def save_agent(self, agent: AgentDefinition) -> None:
        path = self.agents_dir / f"{agent.id}.json"
        path.write_text(agent.model_dump_json(indent=2), encoding="utf-8")
        self.reload()

    def save_skill(self, skill: SkillPackage) -> str:
        """Backward-compatible admin save: replacing an existing package is explicit here."""
        result = self.save_skill_package(skill.model_copy(update={"overwrite": True}))
        return str(result["name"])

    def save_skill_package(self, skill: SkillPackage) -> dict[str, object]:
        """Atomically create or replace one validated, text-only skill package."""
        name, normalized_content, normalized_files, definition = self._validate_skill_package(skill)
        skill_dir = self.skills_dir / name
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        if skill_dir.exists() and not skill.overwrite:
            raise RegistryError(f"Skill package already exists: {name}. Set overwrite=true only after explicit confirmation.")

        staging_dir = self.skills_dir / f".{name}.staging-{uuid.uuid4().hex}"
        backup_dir: Path | None = None
        try:
            staging_dir.mkdir()
            (staging_dir / "SKILL.md").write_text(normalized_content, encoding="utf-8")
            for item in normalized_files:
                target = staging_dir / item.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item.content, encoding="utf-8")

            if skill_dir.exists():
                backup_dir = self.skills_dir / f".{name}.backup-{uuid.uuid4().hex}"
                skill_dir.replace(backup_dir)
            staging_dir.replace(skill_dir)
            self.reload()
        except Exception as exc:
            if backup_dir is not None and backup_dir.exists():
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)
                backup_dir.replace(skill_dir)
                try:
                    self.reload()
                except Exception:
                    pass
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            if isinstance(exc, RegistryError):
                raise
            raise RegistryError(f"Unable to save skill package {name}: {exc}") from exc
        else:
            if backup_dir is not None and backup_dir.exists():
                shutil.rmtree(backup_dir)

        return {
            "name": name,
            "path": str(skill_dir),
            "executable": definition.executable,
            "files": ["SKILL.md", *[item.path for item in normalized_files]],
        }

    def _validate_skill_package(
        self,
        skill: SkillPackage,
    ) -> tuple[str, str, list[SkillPackageFile], SkillDefinition]:
        if len(skill.files) > MAX_SKILL_PACKAGE_FILES:
            raise RegistryError(f"A skill package can contain at most {MAX_SKILL_PACKAGE_FILES} additional files")
        if not isinstance(skill.content, str) or not skill.content.strip():
            raise RegistryError("SKILL.md content is required")

        normalized_content = skill.content.strip() + "\n"
        content_size = len(normalized_content.encode("utf-8"))
        if content_size > MAX_SKILL_FILE_BYTES:
            raise RegistryError(f"SKILL.md exceeds the {MAX_SKILL_FILE_BYTES} byte limit")

        frontmatter, body = parse_skill_markdown(normalized_content)
        name = normalize_required_string(frontmatter, "name", self.skills_dir)
        description = normalize_required_string(frontmatter, "description", self.skills_dir)
        if not body.strip():
            raise RegistryError(f"Skill {name} has empty SKILL.md body")
        metadata = frontmatter.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise RegistryError(f"Skill {name} metadata must be an object")
        runtime_meta = extract_runtime_metadata(metadata)
        try:
            definition = SkillDefinition.model_validate(
                {
                    "name": name,
                    "description": description,
                    "enabled": normalize_enabled(metadata),
                    "permissions": extract_permissions(metadata),
                    "metadata": metadata,
                    "capability_hints": normalize_string_list(
                        runtime_meta.get("capabilities") or runtime_meta.get("capability_hints")
                    ),
                    "mcp_dependencies": runtime_meta.get("mcp_dependencies") or [],
                    "executable": normalize_bool(runtime_meta.get("executable"), False),
                    "parameters_schema": normalize_object(
                        runtime_meta.get("parameters_schema") or runtime_meta.get("parameters"),
                        {"type": "object", "properties": {}, "additionalProperties": False},
                    ),
                    "execution_policy": normalize_object(runtime_meta.get("execution_policy"), {}),
                    "required_secrets": normalize_string_map(runtime_meta.get("required_secrets")),
                    "entrypoint": normalize_optional_string(runtime_meta.get("entrypoint")) or "skill.py",
                    "body": body.strip(),
                    "resources": [],
                    "skill_dir": str(self.skills_dir / name),
                    "skill_file": str(self.skills_dir / name / "SKILL.md"),
                }
            )
        except ValidationError as exc:
            raise RegistryError(f"Invalid skill package {name}: {exc}") from exc

        normalized_files: list[SkillPackageFile] = []
        seen_paths: set[str] = set()
        total_size = content_size
        for item in skill.files:
            path = self._normalize_skill_package_path(item.path)
            if path in seen_paths:
                raise RegistryError(f"Duplicate skill package file: {path}")
            seen_paths.add(path)
            if not isinstance(item.content, str):
                raise RegistryError(f"Skill package file {path} must contain UTF-8 text")
            file_size = len(item.content.encode("utf-8"))
            if file_size > MAX_SKILL_FILE_BYTES:
                raise RegistryError(f"Skill package file {path} exceeds the {MAX_SKILL_FILE_BYTES} byte limit")
            total_size += file_size
            normalized_files.append(SkillPackageFile(path=path, content=item.content))
        if total_size > MAX_SKILL_PACKAGE_BYTES:
            raise RegistryError(f"Skill package exceeds the {MAX_SKILL_PACKAGE_BYTES} byte limit")

        entrypoint = definition.entrypoint.replace("\\", "/")
        if definition.executable and entrypoint not in seen_paths:
            raise RegistryError(f"Executable skill {name} must include its entrypoint file: {definition.entrypoint}")
        return name, normalized_content, normalized_files, definition

    @staticmethod
    def _normalize_skill_package_path(raw_path: str) -> str:
        if not isinstance(raw_path, str):
            raise RegistryError("Skill package file paths must be strings")
        path = raw_path.strip().replace("\\", "/")
        candidate = Path(path)
        if (
            not path
            or candidate.is_absolute()
            or candidate.drive
            or ".." in candidate.parts
            or "." in candidate.parts
        ):
            raise RegistryError(f"Invalid skill package file path: {raw_path}")
        if any(part.startswith(".") or part == "__pycache__" for part in candidate.parts):
            raise RegistryError(f"Hidden or cache files are not allowed in skill packages: {raw_path}")
        if candidate.name.lower() in FORBIDDEN_SKILL_FILE_NAMES or candidate.suffix.lower() in FORBIDDEN_SKILL_FILE_SUFFIXES:
            raise RegistryError(f"Sensitive or binary file types are not allowed in skill packages: {raw_path}")
        normalized = candidate.as_posix()
        if normalized.lower() == "skill.md":
            raise RegistryError("SKILL.md must be supplied only through skill_markdown")
        return normalized

    def get_agent(self, agent_id: str) -> AgentDefinition:
        agent = self.agents.get(agent_id)
        if agent is not None:
            return agent
        if agent_id == "default":
            return AgentDefinition(id="default", name="Default Agent", description="Default runtime agent")
        raise RegistryError(f"Agent not found: {agent_id}")

    def accessible_agents(self, user: UserContext) -> list[AgentDefinition]:
        loaded_agents = [agent for agent in self.agents.values() if agent.enabled and is_allowed(agent.permissions, user)]
        if loaded_agents:
            return loaded_agents
        default_agent = AgentDefinition(id="default", name="Default Agent", description="Default runtime agent")
        return [default_agent] if is_allowed(default_agent.permissions, user) else []

    def accessible_skills(self, user: UserContext) -> list[SkillDefinition]:
        return [skill for skill in self.skills.values() if skill.enabled and is_allowed(skill.permissions, user)]

    def agent_summaries(self, user: UserContext) -> list[AgentSummary]:
        return [
            AgentSummary(
                id=agent.id,
                name=agent.name,
                description=agent.description,
                enabled=agent.enabled,
                skill_ids=agent.skill_ids,
                mcp_server_ids=agent.mcp_server_ids,
                capability_hints=agent.capability_hints,
            )
            for agent in self.accessible_agents(user)
        ]

    def accessible_mcp_servers(self, user: UserContext) -> list[McpServerDefinition]:
        return [server for server in self.mcp_servers.values() if server.enabled and is_allowed(server.permissions, user)]

    def mcp_server_summaries(self, user: UserContext) -> list[McpServerSummary]:
        return [
            McpServerSummary(
                id=server.id,
                description=server.description,
                enabled=server.enabled,
                transport=server.transport,
                tool_allowlist=server.tool_allowlist,
            )
            for server in self.accessible_mcp_servers(user)
        ]

    def skill_summaries(self, user: UserContext) -> list[SkillSummary]:
        return [
            SkillSummary(
                name=skill.name,
                description=skill.description,
                enabled=skill.enabled,
                executable=skill.executable,
                capability_hints=skill.capability_hints,
                resources=skill.resources,
            )
            for skill in self.accessible_skills(user)
        ]

    def read_skill_resource(self, skill: SkillDefinition, resource_path: str) -> str:
        requested = resource_path.strip().replace("\\", "/")
        if not requested or requested.startswith("/") or ".." in Path(requested).parts:
            raise RegistryError(f"Invalid skill resource path: {resource_path}")
        root = Path(skill.skill_dir).resolve()
        target = (root / requested).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RegistryError(f"Skill resource escapes package: {resource_path}") from exc
        if not target.is_file():
            raise RegistryError(f"Skill resource not found: {resource_path}")
        return target.read_text(encoding="utf-8")

    def read_skill_entrypoint(self, skill: SkillDefinition) -> str:
        entrypoint = skill.entrypoint.strip().replace("\\", "/")
        if not entrypoint or entrypoint.startswith("/") or ".." in Path(entrypoint).parts:
            raise RegistryError(f"Invalid skill entrypoint: {skill.entrypoint}")
        root = Path(skill.skill_dir).resolve()
        target = (root / entrypoint).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RegistryError(f"Skill entrypoint escapes package: {skill.entrypoint}") from exc
        if not target.is_file():
            raise RegistryError(f"Skill entrypoint not found: {skill.entrypoint}")
        return target.read_text(encoding="utf-8")


def parse_skill_markdown(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---\n"):
        raise RegistryError("SKILL.md must start with YAML frontmatter")
    marker = "\n---"
    end = raw.find(marker, 4)
    if end == -1:
        raise RegistryError("SKILL.md frontmatter is not closed")
    frontmatter_raw = raw[4:end]
    body = raw[end + len(marker) :].lstrip("\r\n")
    parsed = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(parsed, dict):
        raise RegistryError("SKILL.md frontmatter must be a YAML object")
    return parsed, body


def normalize_required_string(frontmatter: dict[str, object], field: str, path: Path) -> str:
    value = frontmatter.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{path}: frontmatter field `{field}` is required")
    return value.strip()


def normalize_optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegistryError("Optional frontmatter string field has invalid type")
    return value.strip() or None


def normalize_bool(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def normalize_object(value: object, fallback: dict[str, object]) -> dict[str, object]:
    if value is None:
        return dict(fallback)
    if not isinstance(value, dict):
        raise RegistryError("Expected an object")
    return value


def normalize_string_map(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RegistryError("Expected a string map")
    normalized: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(raw, str) or not raw.strip():
            raise RegistryError("Expected a string map")
        normalized[key.strip()] = raw.strip()
    return normalized


def normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return [item.strip() for item in value]
    raise RegistryError("Expected a string or string array")


def extract_permissions(metadata: dict[str, object]) -> PermissionPolicy:
    runtime_meta = extract_runtime_metadata(metadata)
    if not isinstance(runtime_meta, dict):
        return PermissionPolicy()
    permissions = runtime_meta.get("permissions") or {}
    if not isinstance(permissions, dict):
        return PermissionPolicy()
    return PermissionPolicy.model_validate(permissions)


def normalize_enabled(metadata: dict[str, object]) -> bool:
    runtime_meta = extract_runtime_metadata(metadata)
    if not isinstance(runtime_meta, dict):
        return True
    return normalize_bool(runtime_meta.get("enabled"), True)


def extract_runtime_metadata(metadata: dict[str, object]) -> dict[str, object]:
    runtime_meta = metadata.get("agent_runtime") or metadata.get("agent-runtime") or {}
    return runtime_meta if isinstance(runtime_meta, dict) else {}


def discover_skill_resources(skill_dir: Path) -> list[SkillResource]:
    resources: list[SkillResource] = []
    for path in sorted(skill_dir.rglob("*")):
        if (
            not path.is_file()
            or path.name == "SKILL.md"
            or path.name.startswith(".")
            or "__pycache__" in path.parts
            or path.suffix.lower() in {".pyc", ".pyo"}
        ):
            continue
        relative = path.relative_to(skill_dir).as_posix()
        resources.append(SkillResource(path=relative, title=path.stem.replace("-", " ").replace("_", " ")))
    return resources
