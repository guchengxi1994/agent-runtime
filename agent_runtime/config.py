from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DotenvLoadResult:
    path: Path | None
    loaded_keys: frozenset[str]


def load_dotenv() -> DotenvLoadResult:
    env_file = os.getenv("AGENT_RUNTIME_ENV_FILE") or os.getenv("ENV_FILE")
    candidates = [Path(env_file)] if env_file else env_file_candidates()
    for path in candidates:
        if not path.is_file():
            continue
        loaded_keys: set[str] = set()
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
            value = parse_dotenv_value(value)
            if key:
                os.environ[key] = value
                loaded_keys.add(key)
        return DotenvLoadResult(path=path.resolve(), loaded_keys=frozenset(loaded_keys))
    return DotenvLoadResult(path=None, loaded_keys=frozenset())


def parse_dotenv_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    comment_start = value.find(" #")
    if comment_start >= 0:
        value = value[:comment_start].rstrip()
    return value


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


def env_source(key: str, dotenv: DotenvLoadResult) -> str:
    value = os.getenv(key)
    if not value or not value.strip():
        return "unset"
    if key in dotenv.loaded_keys and dotenv.path is not None:
        return f"dotenv:{dotenv.path}"
    return "process"


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
    memory_model: str | None
    openai_api_key: str | None
    openai_base_url: str | None
    reasoning_effort: str | None
    expose_reasoning_content: bool
    registry_dir: Path
    artifacts_dir: Path
    sandbox_url: str
    admin_token: str | None
    max_runtime_rounds: int
    model_context_tokens: int
    context_compaction_threshold: float
    memory_enabled: bool
    memory_context_tokens: int
    memory_max_entries: int
    request_timeout_seconds: float
    mcp_gateway_url: str = "http://127.0.0.1:8002"
    env_file_loaded: str | None = None
    openai_api_key_source: str = "unset"
    openai_base_url_source: str = "unset"

    @property
    def admin_auth_enabled(self) -> bool:
        return bool(self.admin_token)

    def ensure_directories(self) -> None:
        (self.registry_dir / "skills").mkdir(parents=True, exist_ok=True)
        (self.registry_dir / "agents").mkdir(parents=True, exist_ok=True)
        (self.registry_dir / "mcp_servers").mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> AgentRuntimeSettings:
    dotenv = load_dotenv()
    registry_dir = Path(env("AGENT_RUNTIME_REGISTRY_DIR", "./registry")).resolve()
    artifacts_dir = Path(env("AGENT_RUNTIME_ARTIFACTS_DIR", "./artifacts")).resolve()
    settings = AgentRuntimeSettings(
        host=env("AGENT_RUNTIME_HOST", "0.0.0.0"),
        port=int(env("AGENT_RUNTIME_PORT", "8010")),
        model=env("AGENT_RUNTIME_MODEL", "gpt-4.1-mini"),
        memory_model=optional_env("AGENT_RUNTIME_MEMORY_MODEL"),
        openai_api_key=optional_env("OPENAI_API_KEY"),
        openai_base_url=optional_env("OPENAI_BASE_URL"),
        reasoning_effort=optional_env("AGENT_RUNTIME_REASONING_EFFORT"),
        expose_reasoning_content=bool_env("AGENT_RUNTIME_EXPOSE_REASONING_CONTENT", False),
        registry_dir=registry_dir,
        artifacts_dir=artifacts_dir,
        sandbox_url=env("AGENT_RUNTIME_SANDBOX_URL", "http://127.0.0.1:8001"),
        mcp_gateway_url=env("AGENT_RUNTIME_MCP_GATEWAY_URL", "http://127.0.0.1:8002"),
        admin_token=os.getenv("AGENT_RUNTIME_ADMIN_TOKEN") or None,
        max_runtime_rounds=int(env("AGENT_RUNTIME_MAX_RUNTIME_ROUNDS", "6")),
        model_context_tokens=int(env("AGENT_RUNTIME_MODEL_CONTEXT_TOKENS", "131072")),
        context_compaction_threshold=float(env("AGENT_RUNTIME_CONTEXT_COMPACTION_THRESHOLD", "0.8")),
        memory_enabled=bool_env("AGENT_RUNTIME_MEMORY_ENABLED", True),
        memory_context_tokens=int(env("AGENT_RUNTIME_MEMORY_CONTEXT_TOKENS", "4000")),
        memory_max_entries=int(env("AGENT_RUNTIME_MEMORY_MAX_ENTRIES", "200")),
        request_timeout_seconds=float(env("AGENT_RUNTIME_REQUEST_TIMEOUT_SECONDS", "600")),
        env_file_loaded=str(dotenv.path) if dotenv.path else None,
        openai_api_key_source=env_source("OPENAI_API_KEY", dotenv),
        openai_base_url_source=env_source("OPENAI_BASE_URL", dotenv),
    )
    if settings.model_context_tokens <= 0:
        raise ValueError("AGENT_RUNTIME_MODEL_CONTEXT_TOKENS must be positive")
    if not 0 < settings.context_compaction_threshold < 1:
        raise ValueError("AGENT_RUNTIME_CONTEXT_COMPACTION_THRESHOLD must be between 0 and 1")
    if settings.memory_context_tokens <= 0:
        raise ValueError("AGENT_RUNTIME_MEMORY_CONTEXT_TOKENS must be positive")
    if settings.memory_max_entries <= 0:
        raise ValueError("AGENT_RUNTIME_MEMORY_MAX_ENTRIES must be positive")
    settings.ensure_directories()
    return settings
