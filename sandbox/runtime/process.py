from __future__ import annotations

import asyncio
import ast
import codecs
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .config import RuntimeSettings
from .errors import RequestError
from .logging_utils import logger
from .models import (
    ExecutionBudget,
    ExecutionConfig,
    PhaseContext,
    ProcessProgress,
    SubprocessResult,
    TaskWorkspace,
)
from .parsing import sanitize_plugin_id

try:
    import resource
except ImportError:
    resource = None


def build_plugin_wrapper(script: str) -> str:
    return (
        f"{script.rstrip()}\n\n"
        "if 'definition' not in globals():\n"
        "    raise RuntimeError('Plugin script must define `definition`')\n\n"
        "if 'execute' not in globals() or not callable(execute):\n"
        "    raise RuntimeError('Plugin script must define callable `execute(params)`')\n"
    )


def build_script_wrapper(script: str, runtime_hint: str = "python") -> str:
    if runtime_hint == "python":
        return build_plugin_wrapper(script)
    return script


def build_runtime_payload(action: str, params: dict[str, Any] | None = None) -> str:
    return json.dumps({"action": action, "params": params or {}}, ensure_ascii=False)


def build_process_env(
    config: ExecutionConfig,
    venv_dir: Path,
    tmp_dir: Path,
    settings: RuntimeSettings,
) -> dict[str, str]:
    tmp_home = tmp_dir / "home"
    tmp_home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", os.environ.get("LANG", "C.UTF-8")),
        "HOME": str(tmp_home),
        "XDG_CACHE_HOME": str(tmp_home / ".cache"),
        "XDG_CONFIG_HOME": str(tmp_home / ".config"),
        "PIP_CACHE_DIR": str(settings.pip_cache_dir),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "TMP": str(tmp_dir),
        "TEMP": str(tmp_dir),
        "TMPDIR": str(tmp_dir),
        "VIRTUAL_ENV": str(venv_dir),
    }
    if config.pip_index_url:
        env["PIP_INDEX_URL"] = config.pip_index_url
    if config.pip_extra_index_url:
        env["PIP_EXTRA_INDEX_URL"] = config.pip_extra_index_url
    if config.pip_trusted_host:
        env["PIP_TRUSTED_HOST"] = config.pip_trusted_host
    env.update(config.env)
    return env


def create_task_workspace(plugin_id: str, settings: RuntimeSettings) -> TaskWorkspace:
    task_root = settings.temp_root / f"task_{sanitize_plugin_id(plugin_id)}_{uuid.uuid4().hex}"
    tmp_dir = task_root / "tmp"
    venv_dir = task_root / "venv"
    task_root.mkdir(parents=True, exist_ok=False)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "[%s] Created task workspace: root=%s, tmp=%s, venv=%s",
        plugin_id,
        task_root,
        tmp_dir,
        venv_dir,
    )
    return TaskWorkspace(
        root=task_root,
        script_path=task_root / "plugin.py",
        payload_path=task_root / "payload.json",
        venv_dir=venv_dir,
        tmp_dir=tmp_dir,
    )


def _normalize_relative_path(path_value: Any, field_name: str) -> str:
    if not isinstance(path_value, str) or not path_value.strip():
        raise RequestError(400, f"{field_name} must be a non-empty string")
    normalized = path_value.replace("\\", "/").strip()
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in f"/{normalized}":
        raise RequestError(400, f"{field_name} must stay within the uploaded bundle")
    return normalized


def parse_bundle_manifest(bundle_root: Path) -> dict[str, Any]:
    manifest_path = bundle_root / "manifest.json"
    logger.info("Reading bundle manifest: path=%s", manifest_path)
    if not manifest_path.is_file():
        raise RequestError(400, "Bundle is missing manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RequestError(400, f"Invalid manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RequestError(400, "manifest.json must be a JSON object")

    runtime = manifest.get("runtime")
    if runtime != "python":
        raise RequestError(400, "manifest.json runtime must be 'python'")

    entrypoint = _normalize_relative_path(manifest.get("entrypoint"), "manifest.json entrypoint")
    entrypoint_path = (bundle_root / entrypoint).resolve()
    try:
        entrypoint_path.relative_to(bundle_root.resolve())
    except ValueError as exc:
        raise RequestError(400, "Bundle entrypoint escapes bundle root") from exc
    if not entrypoint_path.is_file():
        raise RequestError(400, f"Bundle entrypoint not found: {entrypoint}")
    if entrypoint_path.suffix.lower() != ".py":
        raise RequestError(400, "Bundle entrypoint must be a .py file")

    logger.info(
        "Bundle manifest resolved: runtime=%s, entrypoint=%s, entrypoint_path=%s",
        runtime,
        entrypoint,
        entrypoint_path,
    )

    return {
        "runtime": runtime,
        "entrypoint": entrypoint,
        "entrypoint_path": entrypoint_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
    }


def extract_bundle_zip(
    bundle_bytes: bytes,
    destination: Path,
    *,
    max_files: int = 200,
    max_total_bytes: int = 50 * 1024 * 1024,
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Extracting bundle zip: bytes=%s, destination=%s, max_files=%s, max_total_bytes=%s",
        len(bundle_bytes),
        destination,
        max_files,
        max_total_bytes,
    )
    total_uncompressed = 0
    file_count = 0
    extracted_files: list[str] = []

    try:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as archive:
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                if info.is_dir():
                    continue
                if name.startswith("/") or name.startswith("../") or "/../" in f"/{name}":
                    raise RequestError(400, f"Illegal bundle path: {name}")
                if info.file_size < 0:
                    raise RequestError(400, f"Illegal bundle file size: {name}")

                total_uncompressed += info.file_size
                file_count += 1
                if file_count > max_files:
                    raise RequestError(400, f"Bundle contains too many files (> {max_files})")
                if total_uncompressed > max_total_bytes:
                    raise RequestError(
                        400,
                        f"Bundle uncompressed size exceeds {max_total_bytes} bytes",
                    )

                target = (destination / name).resolve()
                try:
                    target.relative_to(destination.resolve())
                except ValueError as exc:
                    raise RequestError(400, f"Illegal bundle path: {name}") from exc

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted_files.append(name)
    except zipfile.BadZipFile as exc:
        raise RequestError(400, "Uploaded bundle is not a valid zip file") from exc

    logger.info(
        "Bundle extracted: destination=%s, file_count=%s, total_uncompressed_bytes=%s, files=%s",
        destination,
        file_count,
        total_uncompressed,
        extracted_files,
    )

    return {
        "file_count": file_count,
        "total_uncompressed_bytes": total_uncompressed,
        "files": extracted_files,
    }


async def cleanup_workspace(workspace: TaskWorkspace | None) -> None:
    if workspace is None:
        return
    logger.info("Cleaning up workspace: root=%s", workspace.root)
    await asyncio.to_thread(shutil.rmtree, workspace.root, True)
    logger.info("Workspace cleanup finished: root=%s", workspace.root)


def unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        if path.exists():
            path.unlink()


def maybe_set_limit(limit: int, setter) -> None:
    if resource is None or limit <= 0:
        return
    try:
        setter(limit)
    except (OSError, ValueError):
        # Some platforms expose RLIMIT constants but reject specific limits
        # in preexec_fn, for example RLIMIT_AS on macOS. Keep isolation
        # best-effort instead of failing process startup.
        return


def build_preexec_fn(timeout_ms: int, settings: RuntimeSettings):
    if os.name == "nt":
        return None

    cpu_seconds = max(1, min(settings.process_cpu_seconds, max(1, timeout_ms // 1000)))
    memory_bytes = settings.process_memory_bytes
    fsize_bytes = settings.process_fsize_bytes
    nproc = settings.process_nproc
    nofile = settings.process_nofile

    def apply_limits() -> None:
        if resource is None:
            return
        maybe_set_limit(cpu_seconds, lambda value: resource.setrlimit(resource.RLIMIT_CPU, (value, value)))
        maybe_set_limit(
            memory_bytes,
            lambda value: resource.setrlimit(resource.RLIMIT_AS, (value, value)),
        )
        maybe_set_limit(
            fsize_bytes,
            lambda value: resource.setrlimit(resource.RLIMIT_FSIZE, (value, value)),
        )
        maybe_set_limit(
            nproc,
            lambda value: resource.setrlimit(resource.RLIMIT_NPROC, (value, value)),
        )
        maybe_set_limit(
            nofile,
            lambda value: resource.setrlimit(resource.RLIMIT_NOFILE, (value, value)),
        )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply_limits


def terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        process.kill()


def start_budget(timeout_ms: int) -> ExecutionBudget:
    started_at = time.monotonic()
    return ExecutionBudget(
        started_at=started_at,
        deadline=started_at + (timeout_ms / 1000),
        timeout_ms=timeout_ms,
    )


PHASE_PRE_EXECUTE = "pre-execute"
PHASE_DEP_INSTALL = "dep-install"
PHASE_EXECUTE = "execute"
PHASE_POST_EXECUTE = "post-execute"

COMPILE_ACTIVITY_GRACE_MS = 30000
LOG_PREVIEW_LIMIT = 400
DEPENDENCY_MARKER_FILENAME = ".agent_runtime_dependencies.json"

_venv_locks: dict[str, asyncio.Lock] = {}


def phase_elapsed_ms(phase_ctx: PhaseContext) -> int:
    return int((time.monotonic() - phase_ctx.started_at) * 1000)


def budget_elapsed_ms(budget: ExecutionBudget) -> int:
    return int((time.monotonic() - budget.started_at) * 1000)


def trim_log_excerpt(text: str, limit: int = LOG_PREVIEW_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def build_recent_output_excerpt(stdout: str, stderr: str, limit: int = LOG_PREVIEW_LIMIT) -> str:
    chunks: list[str] = []
    stderr_excerpt = trim_log_excerpt(stderr[-limit:], limit)
    stdout_excerpt = trim_log_excerpt(stdout[-limit:], limit)
    if stderr_excerpt:
        chunks.append(f"stderr: {stderr_excerpt}")
    if stdout_excerpt:
        chunks.append(f"stdout: {stdout_excerpt}")
    return " | ".join(chunks)


def build_recent_output_excerpt_from_progress(progress: ProcessProgress) -> str:
    return progress.last_output_preview


def get_venv_lock(venv_dir: Path) -> asyncio.Lock:
    lock_key = str(venv_dir)
    lock = _venv_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _venv_locks[lock_key] = lock
    return lock


def canonical_packages(packages: list[str]) -> list[str]:
    normalized: list[str] = []
    for package in packages:
        package = package.strip()
        if package and package not in normalized:
            normalized.append(package)
    return sorted(normalized)


def build_dependency_marker(config: ExecutionConfig) -> dict[str, Any]:
    return {
        "version": 1,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": canonical_packages(config.packages),
        "pip_index_url": config.pip_index_url,
        "pip_extra_index_url": config.pip_extra_index_url,
        "pip_trusted_host": config.pip_trusted_host,
    }


def dependency_marker_path(venv_dir: Path) -> Path:
    return venv_dir / DEPENDENCY_MARKER_FILENAME


def read_dependency_marker(venv_dir: Path) -> dict[str, Any] | None:
    marker_path = dependency_marker_path(venv_dir)
    if not marker_path.is_file():
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return marker if isinstance(marker, dict) else None


def cached_dependencies_match(venv_dir: Path, config: ExecutionConfig) -> bool:
    if not config.packages:
        return True
    return read_dependency_marker(venv_dir) == build_dependency_marker(config)


def write_dependency_marker(venv_dir: Path, config: ExecutionConfig) -> None:
    marker_path = dependency_marker_path(venv_dir)
    marker_path.write_text(
        json.dumps(build_dependency_marker(config), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def detect_oom_hint(returncode: int, stderr: str, stdout: str) -> str | None:
    combined = f"{stderr}\n{stdout}".lower()
    if "killed" in combined or "out of memory" in combined or "oom" in combined:
        return "Process was killed after memory pressure; likely OOM or RLIMIT_AS exhaustion"
    if os.name != "nt" and returncode < 0 and abs(returncode) == signal.SIGKILL:
        return "Process exited with SIGKILL; likely OOM kill or forced termination"
    return None


def raise_phase_error(
    status: int,
    message: str,
    *,
    phase_ctx: PhaseContext,
    code: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload = {
        "phase_elapsed_ms": phase_elapsed_ms(phase_ctx),
        **(details or {}),
    }
    raise RequestError(
        status,
        message,
        code=code,
        phase=phase_ctx.phase,
        details=payload,
    )


def raise_failed_subprocess_error(
    *,
    phase_ctx: PhaseContext,
    step: str,
    result: SubprocessResult,
    message: str,
    status: int = 500,
    code: str = "subprocess_failed",
) -> None:
    oom_hint = detect_oom_hint(result.returncode, result.stderr, result.stdout)
    details = {
        "step": step,
        "returncode": result.returncode,
        "recent_output": build_recent_output_excerpt(result.stdout, result.stderr),
        "stdout_bytes": result.progress.stdout_size,
        "stderr_bytes": result.progress.stderr_size,
    }
    if oom_hint:
        details["hint"] = oom_hint
    raise_phase_error(
        status,
        message,
        phase_ctx=phase_ctx,
        code=code,
        details=details,
    )


async def emit_runtime_event(
    stdout_queue: asyncio.Queue[dict[str, Any]] | None,
    *,
    event: str,
    data: dict[str, Any],
) -> None:
    if stdout_queue is None:
        return
    await stdout_queue.put({"event": event, "data": data})


def _phase_check(name: str, phase: str, detail: str, ok: bool = True, severity: str = "error") -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "severity": severity,
        "detail": detail,
        "phase": phase,
    }


async def emit_phase_event(
    stdout_queue: asyncio.Queue[dict[str, Any]] | None,
    plugin_id: str,
    phase: str,
    status: str,
    **extra: Any,
) -> PhaseContext:
    phase_ctx = PhaseContext(
        plugin_id=plugin_id,
        phase=phase,
        started_at=time.monotonic(),
    )
    payload = {
        "plugin_id": plugin_id,
        "phase": phase,
        "status": status,
        "timestamp": int(time.time() * 1000),
        **extra,
    }
    await emit_runtime_event(stdout_queue, event="phase", data=payload)
    logger.info("[%s] phase=%s status=%s %s", plugin_id, phase, status, extra if extra else "")
    return phase_ctx


async def complete_phase_event(
    stdout_queue: asyncio.Queue[dict[str, Any]] | None,
    phase_ctx: PhaseContext,
    *,
    status: str = "completed",
    **extra: Any,
) -> None:
    payload = {
        "plugin_id": phase_ctx.plugin_id,
        "phase": phase_ctx.phase,
        "status": status,
        "timestamp": int(time.time() * 1000),
        "elapsed_ms": phase_elapsed_ms(phase_ctx),
        **extra,
    }
    await emit_runtime_event(stdout_queue, event="phase", data=payload)
    logger.info("[%s] phase=%s status=%s elapsed_ms=%s %s", phase_ctx.plugin_id, phase_ctx.phase, status, payload["elapsed_ms"], extra if extra else "")


def remaining_timeout_ms(
    budget: ExecutionBudget,
    settings: RuntimeSettings,
    *,
    phase_ctx: PhaseContext | None = None,
) -> int:
    remaining = int((budget.deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        details = {
            "budget_elapsed_ms": budget_elapsed_ms(budget),
            "timeout_ms": budget.timeout_ms,
        }
        if phase_ctx is not None:
            details["phase_elapsed_ms"] = phase_elapsed_ms(phase_ctx)
            raise RequestError(
                500,
                f"{phase_ctx.phase} total timeout ({budget.timeout_ms}ms)",
                code="phase_total_timeout",
                phase=phase_ctx.phase,
                details=details,
            )
        raise RequestError(500, "Execution timeout", code="execution_timeout", details=details)
    return min(remaining, settings.max_execution_timeout_ms)


def get_venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


async def run_subprocess(
    command: list[str],
    *,
    settings: RuntimeSettings,
    phase_ctx: PhaseContext,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout_ms: int,
    idle_timeout_ms: int,
    stdout_queue: asyncio.Queue[dict[str, Any]] | None = None,
    stream_name: str = "stdout",
) -> SubprocessResult:
    logger.info(
        "[%s] Starting subprocess: phase=%s, stream=%s, cwd=%s, timeout_ms=%s, idle_timeout_ms=%s, command=%s",
        phase_ctx.plugin_id,
        phase_ctx.phase,
        stream_name,
        cwd,
        timeout_ms,
        idle_timeout_ms,
        command,
    )
    preexec_fn = build_preexec_fn(timeout_ms, settings)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=False,
        bufsize=0,
        start_new_session=os.name != "nt",
        preexec_fn=preexec_fn,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    loop = asyncio.get_running_loop()
    limit_error: list[str] = []
    state_lock = threading.Lock()
    progress = ProcessProgress(last_output_at=time.monotonic())
    line_buffers = {
        stream_name: "",
        "stderr": "",
    }
    stdout_hasher = hashlib.sha1()
    stderr_hasher = hashlib.sha1()

    def contains_compile_hint(text: str) -> bool:
        lowered = text.lower()
        return any(
            hint in lowered
            for hint in (
                "building wheel",
                "preparing metadata",
                "pyproject.toml",
                "running build",
                "compiling",
                "cargo",
                "meson",
                "cmake",
                "rustc",
                "gcc",
                "g++",
            )
        )

    def emit_line_event(event: str, text: str) -> None:
        if stdout_queue is None:
            return
        if event == "stdout" and text.startswith(settings.runner_result_prefix):
            return
        loop.call_soon_threadsafe(
            stdout_queue.put_nowait,
            {"event": event, "data": {"line": text}},
        )

    def flush_lines(event: str) -> None:
        buffer = line_buffers[event]
        lines: list[str] = []
        while buffer:
            newline_positions = [idx for idx in (buffer.find("\n"), buffer.find("\r")) if idx >= 0]
            if not newline_positions:
                break
            split_at = min(newline_positions)
            line = buffer[:split_at]
            delimiter = buffer[split_at]
            buffer = buffer[split_at + 1 :]
            if delimiter == "\r" and buffer.startswith("\n"):
                buffer = buffer[1:]
            lines.append(line)
        line_buffers[event] = buffer
        for line in lines:
            emit_line_event(event, line)

    def flush_pending_line(event: str) -> None:
        if not line_buffers[event]:
            return
        emit_line_event(event, line_buffers[event])
        line_buffers[event] = ""

    def consume(stream: Any, sink: list[str], event: str) -> None:
        if stream is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            text = decoder.decode(chunk)
            if not text:
                continue
            with state_lock:
                progress.last_output_at = time.monotonic()
                progress.last_activity_stream = event
                progress.activity_count += 1
                if event == "stderr":
                    stderr_hasher.update(chunk)
                    progress.stderr_size += len(chunk)
                    progress.stderr_digest = stderr_hasher.hexdigest()[:16]
                    if settings.max_stderr_bytes > 0 and progress.stderr_size > settings.max_stderr_bytes:
                        limit_error.append(f"stderr exceeded {settings.max_stderr_bytes} bytes")
                        terminate_process_tree(process)
                        break
                else:
                    stdout_hasher.update(chunk)
                    progress.stdout_size += len(chunk)
                    progress.stdout_digest = stdout_hasher.hexdigest()[:16]
                    if settings.max_stdout_bytes > 0 and progress.stdout_size > settings.max_stdout_bytes:
                        limit_error.append(f"{event} exceeded {settings.max_stdout_bytes} bytes")
                        terminate_process_tree(process)
                        break

                progress.last_output_digest = hashlib.sha1(
                    f"{progress.stdout_digest}|{progress.stderr_digest}".encode("utf-8")
                ).hexdigest()[:16]
                tail = f"{progress.last_output_preview}\n{text}" if progress.last_output_preview else text
                progress.last_output_preview = trim_log_excerpt(tail[-1200:])
                if phase_ctx.phase == PHASE_DEP_INSTALL and contains_compile_hint(text):
                    progress.compile_guard_active = True

                sink.append(text)
                line_buffers[event] += text
                flush_lines(event)

        final_text = decoder.decode(b"", final=True)
        if final_text:
            with state_lock:
                progress.last_output_at = time.monotonic()
                progress.last_activity_stream = event
                progress.activity_count += 1
                progress.last_output_preview = trim_log_excerpt(f"{progress.last_output_preview}\n{final_text}"[-1200:])
                sink.append(final_text)
                line_buffers[event] += final_text
                flush_lines(event)
        with state_lock:
            flush_pending_line(event)
        stream.close()

    wait_task = asyncio.create_task(asyncio.to_thread(process.wait))
    stdout_task = asyncio.create_task(asyncio.to_thread(consume, process.stdout, stdout_chunks, stream_name))
    stderr_task = asyncio.create_task(asyncio.to_thread(consume, process.stderr, stderr_chunks, "stderr"))

    try:
        started_at = time.monotonic()
        last_reported_digest = ""
        last_reported_at = 0.0
        while True:
            if wait_task.done():
                break

            now = time.monotonic()
            elapsed_total_ms = int((now - started_at) * 1000)
            with state_lock:
                idle_elapsed_ms = int((now - progress.last_output_at) * 1000)
                last_output_digest = progress.last_output_digest
                last_output_preview = progress.last_output_preview
                compile_guard_active = progress.compile_guard_active
                stdout_size = progress.stdout_size
                stderr_size = progress.stderr_size
                last_activity_stream = progress.last_activity_stream

            if (
                stdout_queue is not None
                and last_output_digest
                and last_output_digest != last_reported_digest
                and (now - last_reported_at) >= 1.0
            ):
                await emit_runtime_event(
                    stdout_queue,
                    event="activity",
                    data={
                        "plugin_id": phase_ctx.plugin_id,
                        "phase": phase_ctx.phase,
                        "stream": last_activity_stream or stream_name,
                        "stdout_hash": progress.stdout_digest,
                        "stderr_hash": progress.stderr_digest,
                        "combined_hash": last_output_digest,
                        "stdout_bytes": stdout_size,
                        "stderr_bytes": stderr_size,
                        "idle_elapsed_ms": idle_elapsed_ms,
                    },
                )
                last_reported_digest = last_output_digest
                last_reported_at = now

            if timeout_ms > 0 and elapsed_total_ms >= timeout_ms:
                raise_phase_error(
                    500,
                    f"{phase_ctx.phase} total timeout ({timeout_ms}ms)",
                    phase_ctx=phase_ctx,
                    code="phase_total_timeout",
                    details={
                        "step": stream_name,
                        "timeout_ms": timeout_ms,
                        "recent_output": last_output_preview,
                        "stdout_bytes": stdout_size,
                        "stderr_bytes": stderr_size,
                    },
                )

            compile_guard_ms = COMPILE_ACTIVITY_GRACE_MS if compile_guard_active else 0
            effective_idle_timeout_ms = idle_timeout_ms + compile_guard_ms
            if idle_timeout_ms > 0 and idle_elapsed_ms >= effective_idle_timeout_ms:
                raise_phase_error(
                    500,
                    f"{phase_ctx.phase} idle timeout ({idle_timeout_ms}ms)",
                    phase_ctx=phase_ctx,
                    code="phase_idle_timeout",
                    details={
                        "step": stream_name,
                        "idle_timeout_ms": idle_timeout_ms,
                        "effective_idle_timeout_ms": effective_idle_timeout_ms,
                        "compile_guard_active": compile_guard_active,
                        "recent_output": last_output_preview,
                        "last_output_hash": last_output_digest,
                        "stdout_bytes": stdout_size,
                        "stderr_bytes": stderr_size,
                    },
                )

            await asyncio.sleep(0.2)

        await asyncio.gather(stdout_task, stderr_task, wait_task)
    except RequestError:
        terminate_process_tree(process)
        await asyncio.to_thread(process.wait)
        raise
    finally:
        if not stdout_task.done():
            stdout_task.cancel()
        if not stderr_task.done():
            stderr_task.cancel()
        if not wait_task.done():
            wait_task.cancel()

    if limit_error:
        raise_phase_error(
            500,
            limit_error[0],
            phase_ctx=phase_ctx,
            code="output_limit_exceeded",
            details={
                "step": stream_name,
                "recent_output": build_recent_output_excerpt_from_progress(progress),
                "stdout_bytes": progress.stdout_size,
                "stderr_bytes": progress.stderr_size,
            },
        )

    logger.info(
        "[%s] Subprocess finished: phase=%s, stream=%s, returncode=%s, stdout_bytes=%s, stderr_bytes=%s, command=%s",
        phase_ctx.plugin_id,
        phase_ctx.phase,
        stream_name,
        process.returncode or 0,
        progress.stdout_size,
        progress.stderr_size,
        command,
    )

    return SubprocessResult(
        returncode=process.returncode or 0,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        progress=progress,
    )


async def ensure_venv(
    workspace: TaskWorkspace,
    config: ExecutionConfig,
    budget: ExecutionBudget,
    settings: RuntimeSettings,
    plugin_id: str,
    stdout_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> Path:
    venv_dir = settings.venv_root / config.venv_key if config.venv_key else workspace.venv_dir
    if config.venv_key:
        async with get_venv_lock(venv_dir):
            return await ensure_venv_unlocked(
                workspace,
                config,
                budget,
                settings,
                plugin_id,
                stdout_queue,
                venv_dir,
            )
    return await ensure_venv_unlocked(workspace, config, budget, settings, plugin_id, stdout_queue, venv_dir)


async def ensure_venv_unlocked(
    workspace: TaskWorkspace,
    config: ExecutionConfig,
    budget: ExecutionBudget,
    settings: RuntimeSettings,
    plugin_id: str,
    stdout_queue: asyncio.Queue[dict[str, Any]] | None,
    venv_dir: Path,
) -> Path:
    venv_python = get_venv_python(venv_dir)
    if config.venv_key and venv_python.is_file() and cached_dependencies_match(venv_dir, config):
        await emit_runtime_event(
            stdout_queue,
            event="venv",
            data={
                "plugin_id": plugin_id,
                "phase": PHASE_PRE_EXECUTE,
                "line": f"Reusing cached venv: {config.venv_key}",
                "venv_key": config.venv_key,
                "venv_dir": str(venv_dir),
            },
        )
        logger.info("[%s] Reusing cached venv: key=%s, venv_dir=%s", plugin_id, config.venv_key, venv_dir)
        return venv_dir

    if config.venv_key and venv_python.is_file():
        await emit_runtime_event(
            stdout_queue,
            event="venv",
            data={
                "plugin_id": plugin_id,
                "phase": PHASE_PRE_EXECUTE,
                "line": f"Cached venv dependency marker is missing or stale; syncing packages: {config.venv_key}",
                "venv_key": config.venv_key,
                "venv_dir": str(venv_dir),
                "packages": config.packages,
            },
        )
        logger.info(
            "[%s] Cached venv dependency marker is missing or stale; syncing packages: key=%s, venv_dir=%s, packages=%s",
            plugin_id,
            config.venv_key,
            venv_dir,
            config.packages,
        )
    else:
        if config.venv_key and venv_dir.exists():
            logger.warning("[%s] Removing incomplete cached venv before recreate: venv_dir=%s", plugin_id, venv_dir)
            await asyncio.to_thread(shutil.rmtree, venv_dir, True)

        pre_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_PRE_EXECUTE,
            "in_progress",
            step="create_venv",
            workspace=str(workspace.root),
            venv_dir=str(venv_dir),
            venv_key=config.venv_key,
        )
        logger.info("[%s] Creating venv: workspace=%s, venv_dir=%s", plugin_id, workspace.root, venv_dir)
        create_command = [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)]
        create_result = await run_subprocess(
            create_command,
            settings=settings,
            phase_ctx=pre_phase,
            cwd=workspace.root,
            timeout_ms=remaining_timeout_ms(budget, settings, phase_ctx=pre_phase),
            idle_timeout_ms=config.idle_timeout_ms,
            stdout_queue=stdout_queue,
            stream_name="venv",
        )
        if create_result.returncode != 0:
            raise_failed_subprocess_error(
                phase_ctx=pre_phase,
                step="create_venv",
                result=create_result,
                message=f"Failed to create venv: {create_result.stderr.strip() or 'unknown error'}",
                code="venv_create_failed",
            )
        await complete_phase_event(
            stdout_queue,
            pre_phase,
            step="create_venv",
            venv_dir=str(venv_dir),
        )
        logger.info("[%s] Venv created: venv_dir=%s", plugin_id, venv_dir)

    if config.packages and not cached_dependencies_match(venv_dir, config):
        dep_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_DEP_INSTALL,
            "in_progress",
            packages=config.packages,
            pip_index_url=config.pip_index_url,
        )
        logger.info(
            "[%s] Bootstrapping pip in venv: venv_python=%s, packages=%s, pip_index_url=%s",
            plugin_id,
            venv_python,
            config.packages,
            config.pip_index_url,
        )
        bootstrap_command = [
            sys.executable,
            "-m",
            "pip",
            "--python",
            str(venv_python),
            "install",
            "--upgrade",
            "pip",
            "setuptools",
            "wheel",
        ]
        bootstrap_result = await run_subprocess(
            bootstrap_command,
            settings=settings,
            phase_ctx=dep_phase,
            env=build_process_env(config, venv_dir, workspace.tmp_dir, settings),
            cwd=workspace.root,
            timeout_ms=remaining_timeout_ms(budget, settings, phase_ctx=dep_phase),
            idle_timeout_ms=config.idle_timeout_ms,
            stdout_queue=stdout_queue,
            stream_name="pip",
        )
        if bootstrap_result.returncode != 0:
            raise_failed_subprocess_error(
                phase_ctx=dep_phase,
                step="bootstrap_pip",
                result=bootstrap_result,
                message=f"Failed to bootstrap pip in venv: {bootstrap_result.stderr.strip() or 'unknown error'}",
                code="pip_bootstrap_failed",
            )
        logger.info("[%s] Pip bootstrap completed: venv_python=%s", plugin_id, venv_python)

        install_command = [
            sys.executable,
            "-m",
            "pip",
            "--python",
            str(venv_python),
            "install",
            *config.packages,
        ]
        install_result = await run_subprocess(
            install_command,
            settings=settings,
            phase_ctx=dep_phase,
            env=build_process_env(config, venv_dir, workspace.tmp_dir, settings),
            cwd=workspace.root,
            timeout_ms=remaining_timeout_ms(budget, settings, phase_ctx=dep_phase),
            idle_timeout_ms=config.idle_timeout_ms,
            stdout_queue=stdout_queue,
            stream_name="pip",
        )
        if install_result.returncode != 0:
            raise_failed_subprocess_error(
                phase_ctx=dep_phase,
                step="install_packages",
                result=install_result,
                message=f"Failed to install packages: {install_result.stderr.strip() or 'unknown error'}",
                code="package_install_failed",
            )
        write_dependency_marker(venv_dir, config)
        await complete_phase_event(
            stdout_queue,
            dep_phase,
            packages=config.packages,
            dependency_marker=str(dependency_marker_path(venv_dir)),
            stdout_bytes=install_result.progress.stdout_size + bootstrap_result.progress.stdout_size,
            stderr_bytes=install_result.progress.stderr_size + bootstrap_result.progress.stderr_size,
        )
        logger.info(
            "[%s] Package installation completed: venv_python=%s, packages=%s",
            plugin_id,
            venv_python,
            config.packages,
        )
    elif config.packages:
        await emit_runtime_event(
            stdout_queue,
            event="venv",
            data={
                "plugin_id": plugin_id,
                "phase": PHASE_DEP_INSTALL,
                "line": "Dependencies already match cached marker",
                "venv_key": config.venv_key,
                "venv_dir": str(venv_dir),
                "packages": config.packages,
            },
        )
        logger.info("[%s] Dependencies already match cached marker: venv_dir=%s", plugin_id, venv_dir)
    else:
        await emit_runtime_event(
            stdout_queue,
            event="activity",
            data={
                "plugin_id": plugin_id,
                "phase": PHASE_DEP_INSTALL,
                "stream": "pip",
                "message": "No dependency installation requested",
            },
        )
        logger.info("[%s] No extra packages requested for venv: venv_dir=%s", plugin_id, venv_dir)

    return venv_dir


def parse_runner_result(stdout: str, settings: RuntimeSettings) -> dict[str, Any] | None:
    result_line: str | None = None
    for line in stdout.splitlines():
        if line.startswith(settings.runner_result_prefix):
            result_line = line[len(settings.runner_result_prefix) :]
    if not result_line:
        return None
    return json.loads(result_line)


def _make_check(
    name: str,
    ok: bool,
    detail: str,
    severity: str = "error",
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "severity": severity,
        "detail": detail,
    }


def _normalize_parameter_list(parameters: Any) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    if not isinstance(parameters, list):
        return ["definition.parameters must be a list"], normalized

    allowed_types = {"string", "number", "integer", "boolean", "array", "object"}
    for index, item in enumerate(parameters):
        if not isinstance(item, dict):
            errors.append(f"definition.parameters[{index}] must be an object")
            continue

        name = item.get("name")
        param_type = item.get("type")
        description = item.get("description")
        required = item.get("required")

        if not isinstance(name, str) or not name.strip():
            errors.append(f"definition.parameters[{index}].name must be a non-empty string")
        if not isinstance(param_type, str) or not param_type.strip():
            errors.append(f"definition.parameters[{index}].type must be a non-empty string")
        elif param_type not in allowed_types:
            errors.append(
                f"definition.parameters[{index}].type must be one of {sorted(allowed_types)}"
            )
        if not isinstance(description, str) or not description.strip():
            errors.append(
                f"definition.parameters[{index}].description must be a non-empty string"
            )
        if not isinstance(required, bool):
            errors.append(f"definition.parameters[{index}].required must be a boolean")

        enum_value = item.get("enum")
        if enum_value is not None and not isinstance(enum_value, list):
            errors.append(f"definition.parameters[{index}].enum must be a list when provided")

        normalized.append(item)

    return errors, normalized


def validate_python_plugin_structure(script: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    try:
        tree = ast.parse(script)
        checks.append(_make_check("python_parse", True, "Python AST parse succeeded"))
    except SyntaxError as exc:
        detail = f"SyntaxError: {exc.msg} (line {exc.lineno}, column {exc.offset})"
        checks.append(_make_check("python_parse", False, detail))
        return {
            "valid": False,
            "errors": [detail],
            "warnings": warnings,
            "checks": checks,
            "definition": None,
        }

    has_definition = False
    has_execute = False
    execute_is_async = False

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "definition":
                    has_definition = True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "definition":
                has_definition = True
        elif isinstance(node, ast.FunctionDef) and node.name == "execute":
            has_execute = True
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == "execute":
            has_execute = True
            execute_is_async = True

    checks.append(
        _make_check(
            "definition_symbol",
            has_definition,
            "Found top-level `definition` symbol" if has_definition else "Missing top-level `definition` symbol",
        )
    )
    checks.append(
        _make_check(
            "execute_symbol",
            has_execute,
            (
                "Found top-level async `execute(params)` function"
                if execute_is_async
                else "Found top-level `execute(params)` function"
            )
            if has_execute
            else "Missing top-level `execute(params)` function",
        )
    )

    if not has_definition:
        errors.append("Missing top-level `definition` symbol")
    if not has_execute:
        errors.append("Missing top-level `execute(params)` function")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "definition": None,
    }


def validate_definition_payload(definition: Any) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    if not isinstance(definition, dict):
        error = "definition must be an object"
        checks.append(_make_check("definition_shape", False, error))
        return {
            "valid": False,
            "errors": [error],
            "warnings": warnings,
            "checks": checks,
            "definition": definition,
        }

    checks.append(_make_check("definition_shape", True, "definition is an object"))

    name = definition.get("name")
    description = definition.get("description")
    category = definition.get("category")
    parameters = definition.get("parameters")

    if not isinstance(name, str) or not name.strip():
        errors.append("definition.name must be a non-empty string")
        checks.append(_make_check("definition_name", False, errors[-1]))
    else:
        checks.append(_make_check("definition_name", True, "definition.name is valid"))

    if not isinstance(description, str) or not description.strip():
        errors.append("definition.description must be a non-empty string")
        checks.append(_make_check("definition_description", False, errors[-1]))
    else:
        checks.append(
            _make_check("definition_description", True, "definition.description is valid")
        )

    if category is None:
        warnings.append("definition.category is missing; runtime will treat it as optional")
        checks.append(
            _make_check(
                "definition_category",
                True,
                "definition.category is optional and currently missing",
                "warning",
            )
        )
    elif not isinstance(category, str) or not category.strip():
        errors.append("definition.category must be a non-empty string when provided")
        checks.append(_make_check("definition_category", False, errors[-1]))
    else:
        checks.append(_make_check("definition_category", True, "definition.category is valid"))

    parameter_errors, _ = _normalize_parameter_list(parameters)
    if parameter_errors:
        errors.extend(parameter_errors)
        checks.append(
            _make_check(
                "definition_parameters",
                False,
                "; ".join(parameter_errors),
            )
        )
    else:
        checks.append(
            _make_check(
                "definition_parameters",
                True,
                f"definition.parameters is valid ({len(parameters)} item(s))",
            )
        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "definition": definition,
    }


async def execute_plugin_runtime(
    *,
    settings: RuntimeSettings,
    plugin_id: str,
    script: str,
    params: dict[str, Any],
    config: ExecutionConfig,
    stdout_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    budget = start_budget(config.timeout_ms)
    workspace = create_task_workspace(plugin_id, settings)
    wrapper = build_plugin_wrapper(script)
    logger.info(
        "[%s] Starting inline plugin execution: workspace=%s, timeout_ms=%s, idle_timeout_ms=%s",
        plugin_id,
        workspace.root,
        config.timeout_ms,
        config.idle_timeout_ms,
    )

    try:
        pre_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_PRE_EXECUTE,
            "in_progress",
            mode="inline",
            step="prepare_files",
            workspace=str(workspace.root),
        )
        workspace.script_path.write_text(wrapper, encoding="utf-8")
        workspace.payload_path.write_text(build_runtime_payload("execute", params), encoding="utf-8")
        logger.info(
            "[%s] Prepared inline execution files: script_path=%s, payload_path=%s",
            plugin_id,
            workspace.script_path,
            workspace.payload_path,
        )
        await complete_phase_event(
            stdout_queue,
            pre_phase,
            step="prepare_files",
            script_path=str(workspace.script_path),
            payload_path=str(workspace.payload_path),
        )

        venv_dir = await ensure_venv(workspace, config, budget, settings, plugin_id, stdout_queue)
        runner_path = Path(__file__).resolve().parent.parent / "runner.py"
        venv_python = get_venv_python(venv_dir)
        command = [str(venv_python), str(runner_path), str(workspace.script_path), str(workspace.payload_path)]
        logger.info("[%s] Launching inline runner: cwd=%s, command=%s", plugin_id, workspace.root, command)
        execute_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_EXECUTE,
            "in_progress",
            mode="inline",
            cwd=str(workspace.root),
        )
        result_meta = await run_subprocess(
            command,
            settings=settings,
            phase_ctx=execute_phase,
            env=build_process_env(config, venv_dir, workspace.tmp_dir, settings),
            cwd=workspace.root,
            timeout_ms=remaining_timeout_ms(budget, settings, phase_ctx=execute_phase),
            idle_timeout_ms=config.idle_timeout_ms,
            stdout_queue=stdout_queue,
            stream_name="stdout",
        )
        try:
            result = parse_runner_result(result_meta.stdout, settings)
        except json.JSONDecodeError:
            logger.warning("[%s] Inline execution returned invalid JSON result", plugin_id)
            raise_phase_error(
                500,
                "Invalid plugin result JSON",
                phase_ctx=execute_phase,
                code="invalid_runner_result",
                details={
                    "recent_output": build_recent_output_excerpt(result_meta.stdout, result_meta.stderr),
                    "returncode": result_meta.returncode,
                },
            )
        if result is None:
            logger.warning("[%s] Inline execution produced no runner result", plugin_id)
            raise_failed_subprocess_error(
                phase_ctx=execute_phase,
                step="runner_execute",
                result=result_meta,
                message=result_meta.stderr.strip() or "Plugin process exited before returning a result",
                code="runner_result_missing",
            )
        if result_meta.returncode != 0 and result.get("success") is not False:
            raise_failed_subprocess_error(
                phase_ctx=execute_phase,
                step="runner_execute",
                result=result_meta,
                message=result_meta.stderr.strip() or "Plugin execution failed",
                code="plugin_execution_failed",
            )
        if isinstance(result, dict):
            result.setdefault("phase", PHASE_EXECUTE)
            if result_meta.returncode != 0:
                result.setdefault("details", {})
                if isinstance(result["details"], dict):
                    result["details"].setdefault("returncode", result_meta.returncode)
        await complete_phase_event(
            stdout_queue,
            execute_phase,
            returncode=result_meta.returncode,
            stdout_bytes=result_meta.progress.stdout_size,
            stderr_bytes=result_meta.progress.stderr_size,
            result_success=result.get("success"),
        )
        logger.info("[%s] Inline execution completed: success=%s", plugin_id, result.get("success"))
        return result
    finally:
        post_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_POST_EXECUTE,
            "in_progress",
            keep_venv=config.keep_venv,
        )
        if config.keep_venv:
            logger.info("[%s] keep_venv enabled; preserving venv at %s", plugin_id, workspace.venv_dir)
            unlink_if_exists(workspace.script_path)
            unlink_if_exists(workspace.payload_path)
            await complete_phase_event(
                stdout_queue,
                post_phase,
                preserved_venv=str(workspace.venv_dir),
                cleaned_paths=[str(workspace.script_path), str(workspace.payload_path)],
            )
        else:
            await cleanup_workspace(workspace)
            await complete_phase_event(
                stdout_queue,
                post_phase,
                cleaned_workspace=str(workspace.root),
            )


async def execute_bundle_runtime(
    *,
    settings: RuntimeSettings,
    plugin_id: str,
    bundle_bytes: bytes,
    params: dict[str, Any],
    config: ExecutionConfig,
    stdout_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    budget = start_budget(config.timeout_ms)
    workspace = create_task_workspace(plugin_id, settings)
    bundle_dir = workspace.root / "bundle"
    logger.info(
        "[%s] Starting bundle execution: workspace=%s, bundle_dir=%s, bundle_bytes=%s, timeout_ms=%s, idle_timeout_ms=%s",
        plugin_id,
        workspace.root,
        bundle_dir,
        len(bundle_bytes),
        config.timeout_ms,
        config.idle_timeout_ms,
    )

    try:
        pre_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_PRE_EXECUTE,
            "in_progress",
            mode="bundle",
            workspace=str(workspace.root),
            bundle_dir=str(bundle_dir),
            bundle_bytes=len(bundle_bytes),
        )
        bundle_meta = extract_bundle_zip(bundle_bytes, bundle_dir)
        bundle_info = parse_bundle_manifest(bundle_dir)
        script = bundle_info["entrypoint_path"].read_text(encoding="utf-8")
        logger.info(
            "[%s] Bundle ready for execution: file_count=%s, total_uncompressed_bytes=%s, entrypoint=%s",
            plugin_id,
            bundle_meta["file_count"],
            bundle_meta["total_uncompressed_bytes"],
            bundle_info["entrypoint"],
        )
        workspace.script_path.write_text(build_script_wrapper(script), encoding="utf-8")
        workspace.payload_path.write_text(build_runtime_payload("execute", params), encoding="utf-8")
        logger.info(
            "[%s] Prepared bundle execution files: script_path=%s, payload_path=%s",
            plugin_id,
            workspace.script_path,
            workspace.payload_path,
        )
        await complete_phase_event(
            stdout_queue,
            pre_phase,
            bundle_files=bundle_meta["file_count"],
            total_uncompressed_bytes=bundle_meta["total_uncompressed_bytes"],
            entrypoint=bundle_info["entrypoint"],
            manifest_path=str(bundle_info["manifest_path"]),
            script_path=str(workspace.script_path),
            payload_path=str(workspace.payload_path),
        )

        venv_dir = await ensure_venv(workspace, config, budget, settings, plugin_id, stdout_queue)
        runner_path = Path(__file__).resolve().parent.parent / "runner.py"
        venv_python = get_venv_python(venv_dir)
        command = [str(venv_python), str(runner_path), str(workspace.script_path), str(workspace.payload_path)]
        logger.info("[%s] Launching bundle runner: cwd=%s, command=%s", plugin_id, bundle_dir, command)
        execute_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_EXECUTE,
            "in_progress",
            mode="bundle",
            cwd=str(bundle_dir),
        )
        result_meta = await run_subprocess(
            command,
            settings=settings,
            phase_ctx=execute_phase,
            env=build_process_env(config, venv_dir, workspace.tmp_dir, settings),
            cwd=bundle_dir,
            timeout_ms=remaining_timeout_ms(budget, settings, phase_ctx=execute_phase),
            idle_timeout_ms=config.idle_timeout_ms,
            stdout_queue=stdout_queue,
            stream_name="stdout",
        )
        try:
            result = parse_runner_result(result_meta.stdout, settings)
        except json.JSONDecodeError:
            logger.warning("[%s] Bundle execution returned invalid JSON result", plugin_id)
            raise_phase_error(
                500,
                "Invalid plugin result JSON",
                phase_ctx=execute_phase,
                code="invalid_runner_result",
                details={
                    "recent_output": build_recent_output_excerpt(result_meta.stdout, result_meta.stderr),
                    "returncode": result_meta.returncode,
                },
            )
        if result is None:
            logger.warning("[%s] Bundle execution produced no runner result", plugin_id)
            raise_failed_subprocess_error(
                phase_ctx=execute_phase,
                step="runner_execute",
                result=result_meta,
                message=result_meta.stderr.strip() or "Plugin process exited before returning a result",
                code="runner_result_missing",
            )
        if result_meta.returncode != 0 and result.get("success") is not False:
            raise_failed_subprocess_error(
                phase_ctx=execute_phase,
                step="runner_execute",
                result=result_meta,
                message=result_meta.stderr.strip() or "Plugin execution failed",
                code="plugin_execution_failed",
            )
        if isinstance(result, dict):
            result.setdefault("phase", PHASE_EXECUTE)
            if result_meta.returncode != 0:
                result.setdefault("details", {})
                if isinstance(result["details"], dict):
                    result["details"].setdefault("returncode", result_meta.returncode)
        await complete_phase_event(
            stdout_queue,
            execute_phase,
            returncode=result_meta.returncode,
            stdout_bytes=result_meta.progress.stdout_size,
            stderr_bytes=result_meta.progress.stderr_size,
            result_success=result.get("success"),
        )
        logger.info("[%s] Bundle execution completed: success=%s", plugin_id, result.get("success"))
        return result
    finally:
        post_phase = await emit_phase_event(
            stdout_queue,
            plugin_id,
            PHASE_POST_EXECUTE,
            "in_progress",
            keep_venv=config.keep_venv,
        )
        if config.keep_venv:
            logger.info("[%s] keep_venv enabled; preserving venv at %s", plugin_id, workspace.venv_dir)
            unlink_if_exists(workspace.script_path)
            unlink_if_exists(workspace.payload_path)
            await complete_phase_event(
                stdout_queue,
                post_phase,
                preserved_venv=str(workspace.venv_dir),
                cleaned_paths=[str(workspace.script_path), str(workspace.payload_path)],
            )
        else:
            await cleanup_workspace(workspace)
            await complete_phase_event(
                stdout_queue,
                post_phase,
                cleaned_workspace=str(workspace.root),
            )


async def validate_plugin_runtime(
    *,
    settings: RuntimeSettings,
    plugin_id: str,
    script: str,
    config: ExecutionConfig,
) -> dict[str, Any]:
    static_validation = validate_python_plugin_structure(script)
    if not static_validation["valid"]:
        logger.info("[%s] Inline validation failed during static checks", plugin_id)
        return static_validation

    budget = start_budget(config.timeout_ms)
    workspace = create_task_workspace(plugin_id, settings)
    wrapper = build_plugin_wrapper(script)
    logger.info(
        "[%s] Starting inline validation: workspace=%s, timeout_ms=%s, idle_timeout_ms=%s",
        plugin_id,
        workspace.root,
        config.timeout_ms,
        config.idle_timeout_ms,
    )

    try:
        pre_phase = PhaseContext(plugin_id=plugin_id, phase=PHASE_PRE_EXECUTE, started_at=time.monotonic())
        workspace.script_path.write_text(wrapper, encoding="utf-8")
        workspace.payload_path.write_text(build_runtime_payload("getDefinition"), encoding="utf-8")
        logger.info(
            "[%s] Prepared inline validation files: script_path=%s, payload_path=%s",
            plugin_id,
            workspace.script_path,
            workspace.payload_path,
        )

        venv_dir = await ensure_venv(workspace, config, budget, settings, plugin_id)
        runner_path = Path(__file__).resolve().parent.parent / "runner.py"
        venv_python = get_venv_python(venv_dir)
        command = [str(venv_python), str(runner_path), str(workspace.script_path), str(workspace.payload_path)]
        logger.info("[%s] Launching inline validation runner: cwd=%s, command=%s", plugin_id, workspace.root, command)
        execute_phase = PhaseContext(plugin_id=plugin_id, phase=PHASE_EXECUTE, started_at=time.monotonic())
        result_meta = await run_subprocess(
            command,
            settings=settings,
            phase_ctx=execute_phase,
            env=build_process_env(config, venv_dir, workspace.tmp_dir, settings),
            cwd=workspace.root,
            timeout_ms=remaining_timeout_ms(budget, settings, phase_ctx=execute_phase),
            idle_timeout_ms=config.idle_timeout_ms,
        )
        if result_meta.returncode != 0 and not result_meta.stdout.strip() and result_meta.stderr.strip():
            error = result_meta.stderr.strip() or "Validation failed"
            logger.warning("[%s] Inline validation runner failed: %s", plugin_id, error)
            return {
                "valid": False,
                "errors": [error],
                "warnings": static_validation["warnings"],
                "checks": static_validation["checks"] + [
                    _phase_check("runtime_get_definition", PHASE_EXECUTE, error, ok=False),
                ],
                "definition": None,
                "phase": PHASE_EXECUTE,
            }
        result = parse_runner_result(result_meta.stdout, settings)
        if result is None:
            error = result_meta.stderr.strip() or "Plugin produced no validation result"
            logger.warning("[%s] Inline validation produced no runner result", plugin_id)
            return {
                "valid": False,
                "errors": [error],
                "warnings": static_validation["warnings"],
                "checks": static_validation["checks"] + [
                    _phase_check("runtime_get_definition", PHASE_EXECUTE, error, ok=False),
                ],
                "definition": None,
                "phase": PHASE_EXECUTE,
            }
        definition = result.get("definition")
        if definition is None:
            error = result.get("error") or "No definition returned"
            logger.warning("[%s] Inline validation returned no definition", plugin_id)
            return {
                "valid": False,
                "errors": [error],
                "warnings": static_validation["warnings"],
                "checks": static_validation["checks"] + [
                    _phase_check("runtime_get_definition", PHASE_EXECUTE, error, ok=False),
                ],
                "definition": None,
                "phase": PHASE_EXECUTE,
            }

        definition_validation = validate_definition_payload(definition)
        logger.info("[%s] Inline validation completed: valid=%s", plugin_id, definition_validation["valid"])
        runtime_checks = [
            _phase_check(
                "runtime_get_definition",
                PHASE_EXECUTE,
                "Runtime loaded plugin and returned definition successfully",
            )
        ]
        return {
            "valid": static_validation["valid"] and definition_validation["valid"],
            "errors": [*static_validation["errors"], *definition_validation["errors"]],
            "warnings": [*static_validation["warnings"], *definition_validation["warnings"]],
            "checks": [
                _phase_check("prepare_validation", PHASE_PRE_EXECUTE, "Prepared validation payload"),
                *static_validation["checks"],
                *runtime_checks,
                *definition_validation["checks"],
            ],
            "definition": definition_validation["definition"],
            "phase": PHASE_EXECUTE,
        }
    except json.JSONDecodeError as exc:
        error = f"Invalid validation result: {exc}"
        logger.warning("[%s] Inline validation JSON decode failed: %s", plugin_id, error)
        return {
            "valid": False,
            "errors": [error],
            "warnings": static_validation["warnings"],
            "checks": static_validation["checks"] + [
                _phase_check("runtime_get_definition", PHASE_EXECUTE, error, ok=False),
            ],
            "definition": None,
            "phase": PHASE_EXECUTE,
        }
    finally:
        if config.keep_venv:
            unlink_if_exists(workspace.script_path)
            unlink_if_exists(workspace.payload_path)
        else:
            await cleanup_workspace(workspace)


async def validate_bundle_runtime(
    *,
    settings: RuntimeSettings,
    plugin_id: str,
    bundle_bytes: bytes,
    config: ExecutionConfig,
) -> dict[str, Any]:
    budget = start_budget(config.timeout_ms)
    workspace = create_task_workspace(plugin_id, settings)
    bundle_dir = workspace.root / "bundle"
    logger.info(
        "[%s] Starting bundle validation: workspace=%s, bundle_dir=%s, bundle_bytes=%s, timeout_ms=%s, idle_timeout_ms=%s",
        plugin_id,
        workspace.root,
        bundle_dir,
        len(bundle_bytes),
        config.timeout_ms,
        config.idle_timeout_ms,
    )

    try:
        pre_phase = PhaseContext(plugin_id=plugin_id, phase=PHASE_PRE_EXECUTE, started_at=time.monotonic())
        bundle_meta = extract_bundle_zip(bundle_bytes, bundle_dir)
        bundle_info = parse_bundle_manifest(bundle_dir)
        script = bundle_info["entrypoint_path"].read_text(encoding="utf-8")
        logger.info(
            "[%s] Bundle ready for validation: file_count=%s, total_uncompressed_bytes=%s, entrypoint=%s",
            plugin_id,
            bundle_meta["file_count"],
            bundle_meta["total_uncompressed_bytes"],
            bundle_info["entrypoint"],
        )
        static_validation = validate_python_plugin_structure(script)
        if not static_validation["valid"]:
            logger.info("[%s] Bundle validation failed during static checks", plugin_id)
            return static_validation

        workspace.script_path.write_text(build_script_wrapper(script), encoding="utf-8")
        workspace.payload_path.write_text(build_runtime_payload("getDefinition"), encoding="utf-8")
        logger.info(
            "[%s] Prepared bundle validation files: script_path=%s, payload_path=%s",
            plugin_id,
            workspace.script_path,
            workspace.payload_path,
        )

        venv_dir = await ensure_venv(workspace, config, budget, settings, plugin_id)
        runner_path = Path(__file__).resolve().parent.parent / "runner.py"
        venv_python = get_venv_python(venv_dir)
        command = [str(venv_python), str(runner_path), str(workspace.script_path), str(workspace.payload_path)]
        logger.info("[%s] Launching bundle validation runner: cwd=%s, command=%s", plugin_id, bundle_dir, command)
        execute_phase = PhaseContext(plugin_id=plugin_id, phase=PHASE_EXECUTE, started_at=time.monotonic())
        result_meta = await run_subprocess(
            command,
            settings=settings,
            phase_ctx=execute_phase,
            env=build_process_env(config, venv_dir, workspace.tmp_dir, settings),
            cwd=bundle_dir,
            timeout_ms=remaining_timeout_ms(budget, settings, phase_ctx=execute_phase),
            idle_timeout_ms=config.idle_timeout_ms,
        )
        if result_meta.returncode != 0 and not result_meta.stdout.strip() and result_meta.stderr.strip():
            error = result_meta.stderr.strip() or "Validation failed"
            logger.warning("[%s] Bundle validation runner failed: %s", plugin_id, error)
            return {
                "valid": False,
                "errors": [error],
                "warnings": static_validation["warnings"],
                "checks": static_validation["checks"] + [
                    _phase_check(
                        "bundle_manifest",
                        PHASE_PRE_EXECUTE,
                        f"Loaded bundle manifest with {bundle_meta['file_count']} file(s)",
                    ),
                    _phase_check("runtime_get_definition", PHASE_EXECUTE, error, ok=False),
                ],
                "definition": None,
                "phase": PHASE_EXECUTE,
            }
        result = parse_runner_result(result_meta.stdout, settings)
        if result is None:
            error = result_meta.stderr.strip() or "Plugin produced no validation result"
            logger.warning("[%s] Bundle validation produced no runner result", plugin_id)
            return {
                "valid": False,
                "errors": [error],
                "warnings": static_validation["warnings"],
                "checks": static_validation["checks"] + [
                    _phase_check(
                        "bundle_manifest",
                        PHASE_PRE_EXECUTE,
                        f"Loaded bundle manifest with {bundle_meta['file_count']} file(s)",
                    ),
                    _phase_check("runtime_get_definition", PHASE_EXECUTE, error, ok=False),
                ],
                "definition": None,
                "phase": PHASE_EXECUTE,
            }
        definition = result.get("definition")
        if definition is None:
            error = result.get("error") or "No definition returned"
            logger.warning("[%s] Bundle validation returned no definition", plugin_id)
            return {
                "valid": False,
                "errors": [error],
                "warnings": static_validation["warnings"],
                "checks": static_validation["checks"] + [
                    _phase_check(
                        "bundle_manifest",
                        PHASE_PRE_EXECUTE,
                        f"Loaded bundle manifest with {bundle_meta['file_count']} file(s)",
                    ),
                    _phase_check("runtime_get_definition", PHASE_EXECUTE, error, ok=False),
                ],
                "definition": None,
                "phase": PHASE_EXECUTE,
            }

        definition_validation = validate_definition_payload(definition)
        logger.info("[%s] Bundle validation completed: valid=%s", plugin_id, definition_validation["valid"])
        return {
            "valid": static_validation["valid"] and definition_validation["valid"],
            "errors": [*static_validation["errors"], *definition_validation["errors"]],
            "warnings": [*static_validation["warnings"], *definition_validation["warnings"]],
            "checks": [
                _phase_check(
                    "bundle_manifest",
                    PHASE_PRE_EXECUTE,
                    f"Loaded bundle manifest with {bundle_meta['file_count']} file(s)",
                ),
                _phase_check(
                    "prepare_validation",
                    PHASE_PRE_EXECUTE,
                    f"Prepared validation entrypoint {bundle_info['entrypoint']}",
                ),
                *static_validation["checks"],
                _phase_check(
                    "runtime_get_definition",
                    PHASE_EXECUTE,
                    "Runtime loaded bundle entrypoint and returned definition successfully",
                ),
                *definition_validation["checks"],
            ],
            "definition": definition_validation["definition"],
            "phase": PHASE_EXECUTE,
        }
    except json.JSONDecodeError as exc:
        error = f"Invalid validation result: {exc}"
        logger.warning("[%s] Bundle validation JSON decode failed: %s", plugin_id, error)
        return {
            "valid": False,
            "errors": [error],
            "warnings": [],
            "checks": [
                _phase_check("bundle_manifest", PHASE_PRE_EXECUTE, error, ok=False),
            ],
            "definition": None,
            "phase": PHASE_PRE_EXECUTE,
        }
    finally:
        if config.keep_venv:
            unlink_if_exists(workspace.script_path)
            unlink_if_exists(workspace.payload_path)
        else:
            await cleanup_workspace(workspace)
