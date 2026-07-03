from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def get_env(key: str, fallback: str) -> str:
    return os.getenv(key, fallback)


@dataclass(frozen=True)
class RuntimeSettings:
    port: int
    runtime_dir: Path
    venv_root: Path
    script_root: Path
    pip_cache_dir: Path
    temp_root: Path
    execution_timeout_ms: int
    validation_timeout_ms: int
    max_execution_timeout_ms: int
    idle_timeout_ms: int
    validation_idle_timeout_ms: int
    max_idle_timeout_ms: int
    max_request_body_bytes: int
    max_concurrent_executions: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    process_cpu_seconds: int
    process_memory_bytes: int
    process_fsize_bytes: int
    process_nproc: int
    process_nofile: int
    default_pip_index_url: str | None
    default_pip_extra_index_url: str | None
    default_pip_trusted_host: str | None
    runtime_build: str
    runner_result_prefix: str

    def ensure_directories(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.venv_root.mkdir(parents=True, exist_ok=True)
        self.script_root.mkdir(parents=True, exist_ok=True)
        self.pip_cache_dir.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)


def load_settings() -> RuntimeSettings:
    port = int(get_env("ARTISAN_PLUGIN_SERVER_PORT", get_env("PORT", "8001")))
    runtime_dir = Path(get_env("ARTISAN_PLUGIN_RUNTIME_DIR", "./plugins_runtime")).resolve()
    venv_root = Path(get_env("ARTISAN_PLUGIN_VENV_ROOT", str(runtime_dir / "venvs"))).resolve()
    script_root = Path(get_env("ARTISAN_PLUGIN_SCRIPT_ROOT", str(runtime_dir / "scripts"))).resolve()
    pip_cache_dir = Path(
        get_env("ARTISAN_PLUGIN_PIP_CACHE_DIR", str(runtime_dir / "pip_cache"))
    ).resolve()
    temp_root = Path(get_env("ARTISAN_PLUGIN_TEMP_DIR", str(runtime_dir / "tmp"))).resolve()
    settings = RuntimeSettings(
        port=port,
        runtime_dir=runtime_dir,
        venv_root=venv_root,
        script_root=script_root,
        pip_cache_dir=pip_cache_dir,
        temp_root=temp_root,
        execution_timeout_ms=int(get_env("ARTISAN_PLUGIN_TIMEOUT", "300000")),
        validation_timeout_ms=int(get_env("ARTISAN_PLUGIN_VALIDATE_TIMEOUT", "60000")),
        max_execution_timeout_ms=int(get_env("ARTISAN_PLUGIN_MAX_TIMEOUT", "900000")),
        idle_timeout_ms=int(get_env("ARTISAN_PLUGIN_IDLE_TIMEOUT", "120000")),
        validation_idle_timeout_ms=int(get_env("ARTISAN_PLUGIN_VALIDATE_IDLE_TIMEOUT", "30000")),
        max_idle_timeout_ms=int(get_env("ARTISAN_PLUGIN_MAX_IDLE_TIMEOUT", "300000")),
        max_request_body_bytes=int(get_env("ARTISAN_PLUGIN_MAX_BODY_BYTES", "1048576")),
        max_concurrent_executions=int(get_env("ARTISAN_PLUGIN_MAX_CONCURRENCY", "8")),
        max_stdout_bytes=int(get_env("ARTISAN_PLUGIN_MAX_STDOUT_BYTES", str(1024 * 1024))),
        max_stderr_bytes=int(get_env("ARTISAN_PLUGIN_MAX_STDERR_BYTES", str(1024 * 1024))),
        process_cpu_seconds=int(get_env("ARTISAN_PLUGIN_PROCESS_CPU_SECONDS", "120")),
        process_memory_bytes=int(
            get_env("ARTISAN_PLUGIN_PROCESS_MEMORY_BYTES", str(1536 * 1024 * 1024))
        ),
        process_fsize_bytes=int(get_env("ARTISAN_PLUGIN_PROCESS_FSIZE_BYTES", str(50 * 1024 * 1024))),
        process_nproc=int(get_env("ARTISAN_PLUGIN_PROCESS_NPROC", "64")),
        process_nofile=int(get_env("ARTISAN_PLUGIN_PROCESS_NOFILE", "512")),
        default_pip_index_url=get_env(
            "ARTISAN_PLUGIN_PIP_INDEX_URL",
            "https://pypi.tuna.tsinghua.edu.cn/simple",
        )
        or None,
        default_pip_extra_index_url=get_env("ARTISAN_PLUGIN_PIP_EXTRA_INDEX_URL", "") or None,
        default_pip_trusted_host=get_env(
            "ARTISAN_PLUGIN_PIP_TRUSTED_HOST",
            "pypi.tuna.tsinghua.edu.cn",
        )
        or None,
        runtime_build="python-venv-stateless-v2",
        runner_result_prefix="__ARTISAN_PLUGIN_RESULT__=",
    )
    settings.ensure_directories()
    return settings
