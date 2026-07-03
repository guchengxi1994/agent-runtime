from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv() -> None:
    env_file = os.getenv("AGENT_RUNTIME_ENV_FILE") or os.getenv("ENV_FILE")
    candidates = [Path(env_file)] if env_file else env_file_candidates()
    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


def env_file_candidates() -> list[Path]:
    cwd = Path.cwd().resolve()
    candidates = [cwd / ".env", *[parent / ".env" for parent in cwd.parents]]
    package_root = Path(__file__).resolve().parents[1]
    candidates.append(package_root / ".env")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def env(key: str, fallback: str) -> str:
    return os.getenv(key, fallback)


def optional_env(*keys: str) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return None


def bool_env(key: str, fallback: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return fallback
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentRuntimeSettings:
    host: str
    port: int
    model: str
    openai_base_url: str | None
    reasoning_effort: str | None
    expose_reasoning_content: bool
    registry_dir: Path
    sandbox_url: str
    admin_token: str | None
    max_runtime_rounds: int
    request_timeout_seconds: float

    @property
    def admin_auth_enabled(self) -> bool:
        return bool(self.admin_token)

    def ensure_directories(self) -> None:
        (self.registry_dir / "skills").mkdir(parents=True, exist_ok=True)
        (self.registry_dir / "agents").mkdir(parents=True, exist_ok=True)


def load_settings() -> AgentRuntimeSettings:
    load_dotenv()
    registry_dir = Path(env("AGENT_RUNTIME_REGISTRY_DIR", "./registry")).resolve()
    settings = AgentRuntimeSettings(
        host=env("AGENT_RUNTIME_HOST", "0.0.0.0"),
        port=int(env("AGENT_RUNTIME_PORT", "8010")),
        model=env("AGENT_RUNTIME_MODEL", "gpt-4.1-mini"),
        openai_base_url=optional_env("AGENT_RUNTIME_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
        reasoning_effort=optional_env("AGENT_RUNTIME_REASONING_EFFORT"),
        expose_reasoning_content=bool_env("AGENT_RUNTIME_EXPOSE_REASONING_CONTENT", False),
        registry_dir=registry_dir,
        sandbox_url=env("AGENT_RUNTIME_SANDBOX_URL", "http://127.0.0.1:8001"),
        admin_token=os.getenv("AGENT_RUNTIME_ADMIN_TOKEN") or None,
        max_runtime_rounds=int(env("AGENT_RUNTIME_MAX_RUNTIME_ROUNDS", "6")),
        request_timeout_seconds=float(env("AGENT_RUNTIME_REQUEST_TIMEOUT_SECONDS", "600")),
    )
    settings.ensure_directories()
    return settings

