from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PluginRecord:
    id: str
    name: str
    version: str
    author: str | None
    description: str
    definition: dict[str, Any]
    script: str
    is_active: bool


@dataclass
class ExecutionConfig:
    timeout_ms: int
    idle_timeout_ms: int
    packages: list[str]
    pip_index_url: str | None
    pip_extra_index_url: str | None
    pip_trusted_host: str | None
    keep_venv: bool
    env: dict[str, str]


@dataclass
class ExecutionBudget:
    started_at: float
    deadline: float
    timeout_ms: int


@dataclass
class TaskWorkspace:
    root: Path
    script_path: Path
    payload_path: Path
    venv_dir: Path
    tmp_dir: Path


@dataclass
class PhaseContext:
    plugin_id: str
    phase: str
    started_at: float


@dataclass
class ProcessProgress:
    stdout_size: int = 0
    stderr_size: int = 0
    last_output_at: float = 0.0
    last_activity_stream: str = ""
    stdout_digest: str = ""
    stderr_digest: str = ""
    last_output_digest: str = ""
    last_output_preview: str = ""
    activity_count: int = 0
    compile_guard_active: bool = False


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str
    progress: ProcessProgress
