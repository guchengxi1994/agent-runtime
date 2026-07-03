from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env(key: str, fallback: str) -> str:
    return os.getenv(key, fallback)


@dataclass(frozen=True)
class AgentRuntimeSettings:
    host: str
    port: int
    model: str
    registry_dir: Path
    plugin_server_url: str
    admin_token: str | None
    max_tool_rounds: int
    request_timeout_seconds: float

    @property
    def admin_auth_enabled(self) -> bool:
        return bool(self.admin_token)

    def ensure_directories(self) -> None:
        (self.registry_dir / "tools").mkdir(parents=True, exist_ok=True)
        (self.registry_dir / "skills").mkdir(parents=True, exist_ok=True)


def load_settings() -> AgentRuntimeSettings:
    registry_dir = Path(env("AGENT_RUNTIME_REGISTRY_DIR", "./registry")).resolve()
    settings = AgentRuntimeSettings(
        host=env("AGENT_RUNTIME_HOST", "0.0.0.0"),
        port=int(env("AGENT_RUNTIME_PORT", "8010")),
        model=env("AGENT_RUNTIME_MODEL", "gpt-4.1-mini"),
        registry_dir=registry_dir,
        plugin_server_url=env("AGENT_RUNTIME_PLUGIN_SERVER_URL", "http://127.0.0.1:8001"),
        admin_token=os.getenv("AGENT_RUNTIME_ADMIN_TOKEN") or None,
        max_tool_rounds=int(env("AGENT_RUNTIME_MAX_TOOL_ROUNDS", "6")),
        request_timeout_seconds=float(env("AGENT_RUNTIME_REQUEST_TIMEOUT_SECONDS", "600")),
    )
    settings.ensure_directories()
    return settings

