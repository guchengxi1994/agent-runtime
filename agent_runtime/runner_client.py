from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx

from .models import SkillDefinition, SkillExecutionContext


class SandboxExecutionError(RuntimeError):
    pass


class SandboxClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def execute_bundle_skill(
        self,
        skill: SkillDefinition,
        arguments: dict[str, Any],
        context: SkillExecutionContext,
        *,
        base_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bundle_bytes = build_skill_bundle(skill)
        data = {
            "params": json.dumps(arguments, ensure_ascii=False),
            "skill": json.dumps(skill.model_dump(), ensure_ascii=False),
            "context": json.dumps(context.model_dump(), ensure_ascii=False),
            "base_policy": json.dumps(base_policy or {}, ensure_ascii=False),
        }
        files = {
            "bundle": ("skill-bundle.zip", bundle_bytes, "application/zip"),
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/bundle/execute", data=data, files=files)
        try:
            data = response.json()
        except ValueError as exc:
            raise SandboxExecutionError(f"Sandbox returned non-JSON response: {response.text[:500]}") from exc
        if response.status_code >= 400:
            return {
                "success": False,
                "error": data.get("error", f"Sandbox HTTP {response.status_code}"),
                "sandbox_response": data,
            }
        return data

    async def execute_skill(
        self,
        skill: SkillDefinition,
        arguments: dict[str, Any],
        context: SkillExecutionContext,
        script: str,
        *,
        base_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "skill": skill.model_dump(),
            "arguments": arguments,
            "context": context.model_dump(),
            "script": script,
            "base_policy": base_policy or {},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/skills/execute", json=payload)
        try:
            data = response.json()
        except ValueError as exc:
            raise SandboxExecutionError(f"Sandbox returned non-JSON response: {response.text[:500]}") from exc
        if response.status_code >= 400:
            return {
                "success": False,
                "error": data.get("error", f"Sandbox HTTP {response.status_code}"),
                "sandbox_response": data,
            }
        return data


def skill_uses_bundle(skill: SkillDefinition) -> bool:
    entrypoint = skill.entrypoint.strip().replace("\\", "/")
    for resource in skill.resources:
        if resource.path.strip().replace("\\", "/") != entrypoint:
            return True
    return False


def build_skill_bundle(skill: SkillDefinition) -> bytes:
    root = Path(skill.skill_dir).resolve()
    entrypoint = skill.entrypoint.strip().replace("\\", "/")
    bundle_paths = {entrypoint}
    for resource in skill.resources:
        relative = resource.path.strip().replace("\\", "/")
        if relative and relative != "manifest.json":
            bundle_paths.add(relative)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "runtime": "python",
                    "entrypoint": entrypoint,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        for relative in sorted(bundle_paths):
            path = (root / relative).resolve()
            path.relative_to(root)
            if not path.is_file():
                continue
            archive.write(path, arcname=relative)
    return buffer.getvalue()

