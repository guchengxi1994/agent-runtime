from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from typing_extensions import deprecated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from runtime.config import load_settings
from runtime.errors import RequestError
from runtime.logging_utils import logger, setup_logging
from runtime.parsing import (
    is_record,
    normalize_execution_config,
    parse_script_execution_request,
    read_json_body,
)
from runtime.process import (
    execute_bundle_runtime,
    execute_plugin_runtime,
    validate_bundle_runtime,
    validate_plugin_runtime,
)

settings = load_settings()
setup_logging()

active_executions = 0
active_execution_lock = asyncio.Lock()


def json_response(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status)


def error_payload(exc: RequestError | Exception, status: int | None = None) -> dict[str, Any]:
    if isinstance(exc, RequestError):
        return exc.to_dict()
    return {
        "success": False,
        "error": str(exc),
        "status": status or 500,
    }


def merge_execution_policy(base: Any, override: Any) -> dict[str, Any]:
    if not isinstance(base, dict):
        base = {}
    if not isinstance(override, dict):
        override = {}

    merged = dict(base)
    for key, value in override.items():
        if key not in {"packages", "env"}:
            merged[key] = value

    packages: list[str] = []
    for source in (base.get("packages"), override.get("packages")):
        if not isinstance(source, list):
            continue
        for item in source:
            if isinstance(item, str) and item.strip() and item.strip() not in packages:
                packages.append(item.strip())
    if packages:
        merged["packages"] = packages

    env: dict[str, str] = {}
    for source in (base.get("env"), override.get("env")):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if isinstance(key, str) and isinstance(value, str):
                env[key] = value
    if env:
        merged["env"] = env
    return merged


def resolve_required_secrets(required_secrets: Any) -> tuple[dict[str, str], list[str]]:
    if required_secrets is None:
        return {}, []
    if not isinstance(required_secrets, dict):
        raise RequestError(400, "skill.required_secrets must be an object")

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for env_name, source in required_secrets.items():
        if not isinstance(env_name, str) or not env_name.strip():
            raise RequestError(400, "skill.required_secrets contains invalid env name")
        if not isinstance(source, str) or not source.strip():
            raise RequestError(400, f"skill.required_secrets.{env_name} must be a non-empty string")
        source_name = source[4:] if source.startswith("env:") else source
        value = os.getenv(source_name)
        if value is None:
            missing.append(source)
            continue
        resolved[env_name] = value
    return resolved, missing


def make_execution_id() -> str:
    return f"exec_{uuid.uuid4().hex}"


def build_agent_plugin_id(context: dict[str, Any], skill: dict[str, Any], execution_id: str) -> str:
    agent_id = str(context.get("agent_id") or "default")
    skill_name = str(skill.get("name") or "skill")
    return f"{agent_id}_{skill_name}_{execution_id}"


def ensure_policy_venv_key(policy: dict[str, Any], context: dict[str, Any], skill: dict[str, Any]) -> dict[str, Any]:
    if policy.get("venv_key"):
        return policy
    packages = policy.get("packages") or []
    key_payload = {
        "agent_id": str(context.get("agent_id") or "default"),
        "skill_name": str(skill.get("name") or "skill"),
        "packages": sorted(item for item in packages if isinstance(item, str)),
    }
    digest = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    policy = dict(policy)
    policy["venv_key"] = f"{key_payload['agent_id']}_{key_payload['skill_name']}_{digest}"
    return policy

def build_skill_execution_metadata(
    *,
    execution_id: str,
    context: dict[str, Any],
    skill: dict[str, Any],
    result: dict[str, Any],
    started_at: float,
    policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "agent_id": str(context.get("agent_id") or "default"),
        "conversation_id": str(context.get("conversation_id") or ""),
        "workspace_id": str(context.get("workspace_id") or ""),
        "run_id": str(context.get("run_id") or ""),
        "user_id": str(context.get("user_id") or ""),
        "skill_name": str(skill.get("name") or ""),
        "entrypoint": str(skill.get("entrypoint") or "skill.py"),
        "phase": result.get("phase"),
        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        "timeout_ms": policy.get("timeout_ms"),
        "packages": policy.get("packages") or [],
    }


@asynccontextmanager
async def execution_slot() -> AsyncIterator[None]:
    global active_executions
    async with active_execution_lock:
        if active_executions >= settings.max_concurrent_executions:
            raise RequestError(
                429,
                f"Too many concurrent plugin executions ({settings.max_concurrent_executions})",
            )
        active_executions += 1
    try:
        yield
    finally:
        async with active_execution_lock:
            active_executions = max(0, active_executions - 1)


def log_stream_line(plugin_id: str, event: str, line: str) -> None:
    if event == "stderr":
        logger.warning("[%s][stderr] %s", plugin_id, line)
        return
    logger.info("[%s][%s] %s", plugin_id, event, line)


def log_stream_payload(plugin_id: str, event: str, data: dict[str, Any]) -> None:
    if event in {"stdout", "stderr", "pip", "venv"}:
        line = str(data.get("line", ""))
        if line:
            log_stream_line(plugin_id, event, line)
        return
    if event == "phase":
        logger.info(
            "[%s][phase] %s status=%s elapsed_ms=%s",
            plugin_id,
            data.get("phase"),
            data.get("status"),
            data.get("elapsed_ms"),
        )
        return
    if event == "activity":
        logger.info(
            "[%s][activity] phase=%s stream=%s hash=%s idle_ms=%s",
            plugin_id,
            data.get("phase"),
            data.get("stream"),
            data.get("combined_hash"),
            data.get("idle_elapsed_ms"),
        )


async def run_plugin_execute(
    plugin_id: str,
    script: str,
    params: dict[str, Any],
    policy: Any,
    stdout_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = normalize_execution_config(policy, settings.execution_timeout_ms, settings)
    return await execute_plugin_runtime(
        settings=settings,
        plugin_id=plugin_id,
        script=script,
        params=params,
        config=config,
        stdout_queue=stdout_queue,
    )


async def run_plugin_validate(
    plugin_id: str,
    script: str,
    policy: Any,
) -> dict[str, Any]:
    config = normalize_execution_config(policy, settings.validation_timeout_ms, settings)
    return await validate_plugin_runtime(
        settings=settings,
        plugin_id=plugin_id,
        script=script,
        config=config,
    )


async def run_bundle_execute(
    plugin_id: str,
    bundle_bytes: bytes,
    params: dict[str, Any],
    policy: Any,
    stdout_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = normalize_execution_config(policy, settings.execution_timeout_ms, settings)
    return await execute_bundle_runtime(
        settings=settings,
        plugin_id=plugin_id,
        bundle_bytes=bundle_bytes,
        params=params,
        config=config,
        stdout_queue=stdout_queue,
    )


async def run_bundle_validate(
    plugin_id: str,
    bundle_bytes: bytes,
    policy: Any,
) -> dict[str, Any]:
    config = normalize_execution_config(policy, settings.validation_timeout_ms, settings)
    return await validate_bundle_runtime(
        settings=settings,
        plugin_id=plugin_id,
        bundle_bytes=bundle_bytes,
        config=config,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Agent Runtime Python Sandbox starting")
    logger.info("Runtime build: %s", settings.runtime_build)
    logger.info("Runtime dir: %s", settings.runtime_dir)
    logger.info("Venv root: %s", settings.venv_root)
    logger.info("Pip cache: %s", settings.pip_cache_dir)
    logger.info("Temp dir: %s", settings.temp_root)
    logger.info("Execution timeout: %sms", settings.execution_timeout_ms)
    logger.info("Max timeout: %sms", settings.max_execution_timeout_ms)
    logger.info("Idle timeout: %sms", settings.idle_timeout_ms)
    logger.info("Validate idle timeout: %sms", settings.validation_idle_timeout_ms)
    logger.info("Max concurrency: %s", settings.max_concurrent_executions)
    logger.info("Max body: %sB", settings.max_request_body_bytes)
    logger.info("Listening on http://localhost:%s", settings.port)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_error_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except RequestError as exc:
        return json_response(exc.to_dict(), exc.status)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error while serving %s", request.url.path)
        return json_response({"success": False, "error": "Internal error", "details": str(exc), "status": 500}, 500)


@app.get("/health")
async def health() -> JSONResponse:
    return json_response(
        {
            "status": "ok",
            "mode": "stateless_execution",
            "runtime": "python",
            "active_executions": active_executions,
            "max_concurrency": settings.max_concurrent_executions,
            "max_body_bytes": settings.max_request_body_bytes,
            "max_timeout_ms": settings.max_execution_timeout_ms,
            "idle_timeout_ms": settings.idle_timeout_ms,
            "validation_idle_timeout_ms": settings.validation_idle_timeout_ms,
            "max_idle_timeout_ms": settings.max_idle_timeout_ms,
            "pip_cache_dir": str(settings.pip_cache_dir),
            "temp_dir": str(settings.temp_root),
            "default_pip_index_url": settings.default_pip_index_url,
            "process_memory_bytes": settings.process_memory_bytes,
            "process_cpu_seconds": settings.process_cpu_seconds,
            "process_nproc": settings.process_nproc,
            "process_nofile": settings.process_nofile,
        }
    )


@deprecated("Use /bundle/execute instead")
@app.post("/execute")
async def execute_inline(request: Request) -> JSONResponse:
    plugin_id, script, params, policy = parse_script_execution_request(await read_json_body(request, settings))
    logger.info("Execute inline plugin request: plugin_id=%s", plugin_id)
    async with execution_slot():
        result = await run_plugin_execute(plugin_id, script, params, policy)
    return json_response(result, 200 if result.get("success") else 500)


@deprecated("Use /bundle/execute/stream instead")
@app.post("/execute/stream")
async def execute_inline_stream(request: Request):
    plugin_id, script, params, policy = parse_script_execution_request(await read_json_body(request, settings))

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        done = asyncio.Event()

        async def worker() -> None:
            async with execution_slot():
                try:
                    await queue.put(
                        {
                            "event": "started",
                            "data": {
                                "plugin_id": plugin_id,
                                "timestamp": int(time.time() * 1000),
                                "execution_policy": policy,
                            },
                        }
                    )
                    result = await run_plugin_execute(plugin_id, script, params, policy, queue)
                    await queue.put({"event": "result", "data": result})
                except RequestError as exc:
                    await queue.put({"event": "error", "data": exc.to_dict()})
                except Exception as exc:
                    logger.exception("Streaming inline execution failed for plugin_id=%s", plugin_id)
                    await queue.put({"event": "error", "data": error_payload(exc, 500)})
                finally:
                    done.set()

        task = asyncio.create_task(worker())
        try:
            while True:
                if done.is_set() and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=5)
                    if isinstance(item.get("data"), dict):
                        log_stream_payload(plugin_id, item["event"], item["data"])
                    yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                    if item["event"] in {"result", "error"}:
                        break
                except asyncio.TimeoutError:
                    yield f"event: ping\ndata: {json.dumps({'ts': int(time.time() * 1000)})}\n\n"
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/validate")
async def validate_plugin(request: Request) -> JSONResponse:
    body = await read_json_body(request, settings)
    if not is_record(body):
        return json_response({"error": "Request body must be a JSON object"}, 400)
    script = body.get("script")
    if not isinstance(script, str) or not script:
        return json_response({"error": "Missing script"}, 400)
    plugin_id = body["id"] if isinstance(body.get("id"), str) and body["id"] else f"temp_{uuid.uuid4().hex}"
    policy = body.get("execution_policy")
    logger.info("Validate plugin request: plugin_id=%s", plugin_id)
    async with execution_slot():
        result = await run_plugin_validate(plugin_id, script, policy)
    return json_response(result)


@app.post("/skills/execute")
async def execute_skill(request: Request) -> JSONResponse:
    body = await read_json_body(request, settings)
    if not is_record(body):
        return json_response({"success": False, "error": "Request body must be a JSON object"}, 400)

    skill = body.get("skill")
    context = body.get("context")
    if not is_record(skill):
        return json_response({"success": False, "error": "skill must be an object"}, 400)
    if not is_record(context):
        return json_response({"success": False, "error": "context must be an object"}, 400)

    script = body.get("script")
    if not isinstance(script, str) or not script.strip():
        return json_response({"success": False, "error": "script is required"}, 400)

    arguments = body.get("arguments", {})
    if not is_record(arguments):
        return json_response({"success": False, "error": "arguments must be a JSON object"}, 400)

    execution_id = make_execution_id()
    started_at = time.monotonic()
    policy = merge_execution_policy(body.get("base_policy"), skill.get("execution_policy"))
    policy = ensure_policy_venv_key(policy, context, skill)
    try:
        secret_env, missing_secrets = resolve_required_secrets(skill.get("required_secrets"))
    except RequestError as exc:
        return json_response(exc.to_dict(), exc.status)
    if missing_secrets:
        return json_response(
            {
                "success": False,
                "error": f"Missing required secret(s): {', '.join(missing_secrets)}",
                "error_type": "missing_required_secrets",
                "execution": build_skill_execution_metadata(
                    execution_id=execution_id,
                    context=context,
                    skill=skill,
                    result={},
                    started_at=started_at,
                    policy=policy,
                ),
            }
        )

    merged_env = dict(policy.get("env") or {})
    merged_env.setdefault("AGENT_RUNTIME_WORKSPACE_ID", str(context.get("workspace_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_CONVERSATION_ID", str(context.get("conversation_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_AGENT_ID", str(context.get("agent_id") or "default"))
    merged_env.setdefault("AGENT_RUNTIME_RUN_ID", str(context.get("run_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_USER_ID", str(context.get("user_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_SKILL_NAME", str(skill.get("name") or ""))
    merged_env.setdefault("AGENT_RUNTIME_ARTIFACTS_DIR", os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR", "/app/artifacts"))
    merged_env.update(secret_env)
    policy["env"] = merged_env

    plugin_id = build_agent_plugin_id(context, skill, execution_id)
    logger.info(
        "Execute skill request: execution_id=%s, agent_id=%s, skill=%s, packages=%s, venv_key=%s",
        execution_id,
        context.get("agent_id"),
        skill.get("name"),
        policy.get("packages") or [],
        policy.get("venv_key"),
    )
    async with execution_slot():
        result = await run_plugin_execute(plugin_id, script, arguments, policy)
    result["execution"] = build_skill_execution_metadata(
        execution_id=execution_id,
        context=context,
        skill=skill,
        result=result,
        started_at=started_at,
        policy=policy,
    )
    return json_response(result)


@app.post("/bundle/validate")
async def validate_bundle(
    bundle: UploadFile = File(...),
    id: str | None = Form(None),
    execution_policy: str | None = Form(None),
) -> JSONResponse:
    plugin_id = id.strip() if isinstance(id, str) and id.strip() else f"bundle_{uuid.uuid4().hex}"
    if not bundle.filename:
        return json_response({"error": "Missing bundle filename"}, 400)
    bundle_bytes = await bundle.read()
    if not bundle_bytes:
        return json_response({"error": "Uploaded bundle is empty"}, 400)

    try:
        policy = json.loads(execution_policy) if execution_policy else None
    except json.JSONDecodeError:
        return json_response({"error": "execution_policy must be valid JSON"}, 400)

    logger.info("Validate bundle request: plugin_id=%s, filename=%s", plugin_id, bundle.filename)
    async with execution_slot():
        result = await run_bundle_validate(plugin_id, bundle_bytes, policy)
    return json_response(result)


@app.post("/bundle/execute")
async def execute_bundle(
    bundle: UploadFile = File(...),
    id: str | None = Form(None),
    params: str | None = Form(None),
    execution_policy: str | None = Form(None),
    skill: str | None = Form(None),
    context: str | None = Form(None),
    base_policy: str | None = Form(None),
) -> JSONResponse:
    plugin_id = id.strip() if isinstance(id, str) and id.strip() else f"bundle_{uuid.uuid4().hex}"
    if not bundle.filename:
        return json_response({"error": "Missing bundle filename"}, 400)
    bundle_bytes = await bundle.read()
    if not bundle_bytes:
        return json_response({"error": "Uploaded bundle is empty"}, 400)

    try:
        parsed_params = json.loads(params) if params else {}
    except json.JSONDecodeError:
        return json_response({"error": "params must be valid JSON"}, 400)
    if not is_record(parsed_params):
        return json_response({"error": "params must be a JSON object"}, 400)

    try:
        parsed_skill = json.loads(skill) if skill else {}
        parsed_context = json.loads(context) if context else {}
        parsed_base_policy = json.loads(base_policy) if base_policy else None
        policy = json.loads(execution_policy) if execution_policy else None
    except json.JSONDecodeError:
        return json_response({"error": "bundle form fields must be valid JSON"}, 400)
    if skill is not None and not is_record(parsed_skill):
        return json_response({"error": "skill must be a JSON object"}, 400)
    if context is not None and not is_record(parsed_context):
        return json_response({"error": "context must be a JSON object"}, 400)

    logger.info("Execute bundle request: plugin_id=%s, filename=%s", plugin_id, bundle.filename)
    execution_id = make_execution_id()
    started_at = time.monotonic()
    merged_policy = merge_execution_policy(parsed_base_policy, policy)
    if is_record(parsed_skill):
        merged_policy = merge_execution_policy(merged_policy, parsed_skill.get("execution_policy"))
    if is_record(parsed_context) and is_record(parsed_skill):
        merged_policy = ensure_policy_venv_key(merged_policy, parsed_context, parsed_skill)
    try:
        secret_env, missing_secrets = resolve_required_secrets(parsed_skill.get("required_secrets") if is_record(parsed_skill) else None)
    except RequestError as exc:
        return json_response(exc.to_dict(), exc.status)
    if missing_secrets:
        return json_response(
            {
                "success": False,
                "error": f"Missing required secret(s): {', '.join(missing_secrets)}",
                "error_type": "missing_required_secrets",
                "execution": build_skill_execution_metadata(
                    execution_id=execution_id,
                    context=parsed_context if is_record(parsed_context) else {},
                    skill=parsed_skill if is_record(parsed_skill) else {},
                    result={},
                    started_at=started_at,
                    policy=merged_policy,
                ),
            }
        )
    merged_env = dict(merged_policy.get("env") or {})
    merged_env.setdefault("AGENT_RUNTIME_WORKSPACE_ID", str(parsed_context.get("workspace_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_CONVERSATION_ID", str(parsed_context.get("conversation_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_AGENT_ID", str(parsed_context.get("agent_id") or "default"))
    merged_env.setdefault("AGENT_RUNTIME_RUN_ID", str(parsed_context.get("run_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_USER_ID", str(parsed_context.get("user_id") or ""))
    merged_env.setdefault("AGENT_RUNTIME_SKILL_NAME", str(parsed_skill.get("name") or ""))
    merged_env.setdefault("AGENT_RUNTIME_ARTIFACTS_DIR", os.getenv("AGENT_RUNTIME_ARTIFACTS_DIR", "/app/artifacts"))
    merged_env.update(secret_env)
    merged_policy["env"] = merged_env
    async with execution_slot():
        result = await run_bundle_execute(plugin_id, bundle_bytes, parsed_params, merged_policy)
    if is_record(parsed_skill) and is_record(parsed_context):
        result["execution"] = build_skill_execution_metadata(
            execution_id=execution_id,
            context=parsed_context,
            skill=parsed_skill,
            result=result,
            started_at=started_at,
            policy=merged_policy,
        )
    return json_response(result, 200 if result.get("success") else 500)


@app.post("/bundle/execute/stream")
async def execute_bundle_stream(
    bundle: UploadFile = File(...),
    id: str | None = Form(None),
    params: str | None = Form(None),
    execution_policy: str | None = Form(None),
):
    plugin_id = id.strip() if isinstance(id, str) and id.strip() else f"bundle_{uuid.uuid4().hex}"
    if not bundle.filename:
        return json_response({"error": "Missing bundle filename"}, 400)
    bundle_bytes = await bundle.read()
    if not bundle_bytes:
        return json_response({"error": "Uploaded bundle is empty"}, 400)

    try:
        parsed_params = json.loads(params) if params else {}
    except json.JSONDecodeError:
        return json_response({"error": "params must be valid JSON"}, 400)
    if not is_record(parsed_params):
        return json_response({"error": "params must be a JSON object"}, 400)

    try:
        policy = json.loads(execution_policy) if execution_policy else None
    except json.JSONDecodeError:
        return json_response({"error": "execution_policy must be valid JSON"}, 400)

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        done = asyncio.Event()

        async def worker() -> None:
            async with execution_slot():
                try:
                    await queue.put(
                        {
                            "event": "started",
                            "data": {
                                "plugin_id": plugin_id,
                                "timestamp": int(time.time() * 1000),
                                "execution_policy": policy,
                            },
                        }
                    )
                    result = await run_bundle_execute(
                        plugin_id,
                        bundle_bytes,
                        parsed_params,
                        policy,
                        queue,
                    )
                    await queue.put({"event": "result", "data": result})
                except RequestError as exc:
                    await queue.put({"event": "error", "data": exc.to_dict()})
                except Exception as exc:
                    logger.exception("Streaming bundle execution failed for plugin_id=%s", plugin_id)
                    await queue.put({"event": "error", "data": error_payload(exc, 500)})
                finally:
                    done.set()

        task = asyncio.create_task(worker())
        try:
            while True:
                if done.is_set() and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=5)
                    if isinstance(item.get("data"), dict):
                        log_stream_payload(plugin_id, item["event"], item["data"])
                    yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                    if item["event"] in {"result", "error"}:
                        break
                except asyncio.TimeoutError:
                    yield f"event: ping\ndata: {json.dumps({'ts': int(time.time() * 1000)})}\n\n"
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port, reload=False)
