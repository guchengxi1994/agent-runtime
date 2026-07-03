from __future__ import annotations

import asyncio
import json
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

setup_logging()
settings = load_settings()

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
    logger.info("Artisan Python Plugin Server starting")
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
        policy = json.loads(execution_policy) if execution_policy else None
    except json.JSONDecodeError:
        return json_response({"error": "execution_policy must be valid JSON"}, 400)

    logger.info("Execute bundle request: plugin_id=%s, filename=%s", plugin_id, bundle.filename)
    async with execution_slot():
        result = await run_bundle_execute(plugin_id, bundle_bytes, parsed_params, policy)
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
