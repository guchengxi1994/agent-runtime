from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import (
    PermissionPolicy,
    SkillDefinition,
    SkillPackage,
    SkillResource,
    SkillSummary,
    ToolDefinition,
    ToolSummary,
    UserContext,
)
from .permissions import is_allowed


class RegistryError(RuntimeError):
    pass


class FileRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.tools_dir = root / "tools"
        self.skills_dir = root / "skills"
        self.tools: dict[str, ToolDefinition] = {}
        self.skills: dict[str, SkillDefinition] = {}

    def reload(self) -> None:
        self.tools = self._load_tools()
        self.skills = self._load_skills()

    def _load_tools(self) -> dict[str, ToolDefinition]:
        tools: dict[str, ToolDefinition] = {}
        for path in sorted(self.tools_dir.glob("*.json")):
            tool = ToolDefinition.model_validate_json(path.read_text(encoding="utf-8"))
            if tool.id in tools:
                raise RegistryError(f"Duplicate tool id: {tool.id}")
            if any(existing.name == tool.name for existing in tools.values()):
                raise RegistryError(f"Duplicate tool name: {tool.name}")
            tools[tool.id] = tool
        return tools

    def _load_skills(self) -> dict[str, SkillDefinition]:
        skills: dict[str, SkillDefinition] = {}
        for path in sorted(item for item in self.skills_dir.iterdir() if item.is_dir()):
            skill = self._load_skill_package(path)
            if skill.name in skills:
                raise RegistryError(f"Duplicate skill name: {skill.name}")
            skills[skill.name] = skill
        return skills

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

        try:
            return SkillDefinition.model_validate(
                {
                    "name": name,
                    "description": description,
                    "enabled": normalize_enabled(metadata),
                    "permissions": permissions,
                    "metadata": metadata,
                    "body": body,
                    "resources": discover_skill_resources(skill_dir),
                    "skill_dir": str(skill_dir),
                    "skill_file": str(instructions_path),
                }
            )
        except ValidationError as exc:
            raise RegistryError(f"Invalid skill package {skill_dir}: {exc}") from exc

    def save_tool(self, tool: ToolDefinition) -> None:
        path = self.tools_dir / f"{tool.id}.json"
        path.write_text(tool.model_dump_json(indent=2), encoding="utf-8")
        self.reload()

    def save_skill(self, skill: SkillPackage) -> str:
        frontmatter, _ = parse_skill_markdown(skill.content)
        name = normalize_required_string(frontmatter, "name", self.skills_dir)
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill.content.strip() + "\n", encoding="utf-8")
        self.reload()
        return name

    def accessible_tools(self, user: UserContext) -> list[ToolDefinition]:
        return [tool for tool in self.tools.values() if tool.enabled and is_allowed(tool.permissions, user)]

    def accessible_skills(self, user: UserContext) -> list[SkillDefinition]:
        return [skill for skill in self.skills.values() if skill.enabled and is_allowed(skill.permissions, user)]

    def tool_summaries(self, user: UserContext) -> list[ToolSummary]:
        return [
            ToolSummary(
                id=tool.id,
                name=tool.name,
                description=tool.description,
                enabled=tool.enabled,
                permissions=tool.permissions,
                metadata=tool.metadata,
            )
            for tool in self.accessible_tools(user)
        ]

    def skill_summaries(self, user: UserContext) -> list[SkillSummary]:
        return [
            SkillSummary(
                name=skill.name,
                description=skill.description,
                enabled=skill.enabled,
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


def normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return [item.strip() for item in value]
    raise RegistryError("Expected a string or string array")


def extract_permissions(metadata: dict[str, object]) -> PermissionPolicy:
    runtime_meta = metadata.get("agent_runtime") or metadata.get("agent-runtime") or {}
    if not isinstance(runtime_meta, dict):
        return PermissionPolicy()
    permissions = runtime_meta.get("permissions") or {}
    if not isinstance(permissions, dict):
        return PermissionPolicy()
    return PermissionPolicy.model_validate(permissions)


def normalize_enabled(metadata: dict[str, object]) -> bool:
    runtime_meta = metadata.get("agent_runtime") or metadata.get("agent-runtime") or {}
    if not isinstance(runtime_meta, dict):
        return True
    enabled = runtime_meta.get("enabled")
    return enabled if isinstance(enabled, bool) else True


def discover_skill_resources(skill_dir: Path) -> list[SkillResource]:
    resources: list[SkillResource] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.name == "SKILL.md" or path.name.startswith("."):
            continue
        relative = path.relative_to(skill_dir).as_posix()
        resources.append(SkillResource(path=relative, title=path.stem.replace("-", " ").replace("_", " ")))
    return resources
