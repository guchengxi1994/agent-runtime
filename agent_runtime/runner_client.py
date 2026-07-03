from __future__ import annotations

from typing import Any

import httpx

from .models import SkillDefinition, SkillExecutionContext


class SandboxExecutionError(RuntimeError):
    pass


class SandboxClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

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


