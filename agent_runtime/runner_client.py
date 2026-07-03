from __future__ import annotations

from typing import Any

import httpx

from .models import ToolDefinition


class ToolExecutionError(RuntimeError):
    pass


class PluginRunnerClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def execute(self, tool: ToolDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "id": tool.id,
            "script": tool.script,
            "params": arguments,
            "execution_policy": tool.execution_policy,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/execute", json=payload)
        try:
            data = response.json()
        except ValueError as exc:
            raise ToolExecutionError(f"Plugin runner returned non-JSON response: {response.text[:500]}") from exc
        if response.status_code >= 400:
            return {
                "success": False,
                "error": data.get("error", f"Plugin runner HTTP {response.status_code}"),
                "runner_response": data,
            }
        return data

