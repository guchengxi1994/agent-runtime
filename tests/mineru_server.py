from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import os
import sys
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import httpx
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

load_dotenv()


class ConfigurationError(RuntimeError):
    """Raised when runtime configuration is invalid."""


class Settings(BaseModel):
    upstream_base_url: str = "https://mineru.net"
    api_token: str | None = None
    timeout_seconds: float = 30.0
    result_timeout_seconds: float = 300.0
    poll_interval_seconds: float = 3.0
    verify_ssl: bool = True
    download_use_proxy: bool = False
    default_language: str = "ch"
    default_model_version: str = "vlm"
    default_enable_formula: bool = True
    default_enable_table: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ParseFileResponse(BaseModel):
    status: str = Field(default="ok", description="代理调用状态。")
    file_name: str = Field(alias="fileName", description="上传文件名。")
    content_type: str | None = Field(default=None, alias="contentType", description="上传文件 MIME 类型。")
    size_bytes: int = Field(alias="sizeBytes", description="上传文件大小，单位 byte。")
    language: str = Field(description="本次解析语言参数。")
    task_id: str | None = Field(default=None, alias="taskId", description="兼容字段。精准解析本地上传场景下这里返回 batch_id。")
    batch_id: str | None = Field(default=None, alias="batchId", description="MinerU 精准解析 batch_id。")
    state: str = Field(description="MinerU 最终任务状态，成功时为 done。")
    markdown_url: str | None = Field(default=None, alias="markdownUrl", description="兼容字段。精准解析 API 不直接返回 markdown_url，因此通常为空。")
    full_zip_url: str | None = Field(default=None, alias="fullZipUrl", description="MinerU 精准解析结果 zip 下载地址。")
    markdown: str = Field(description="代理下载后的 Markdown 文本。")
    poll_count: int = Field(alias="pollCount", description="轮询次数。")
    content_list: list[dict[str, Any]] = Field(default_factory=list, alias="contentList", description="zip 中的 *_content_list.json。")
    content_list_v2: Any = Field(default=None, alias="contentListV2", description="zip 中的 *_content_list_v2.json。")
    archive_assets: dict[str, str] = Field(default_factory=dict, alias="archiveAssets", description="zip 中图片资产，key=相对路径，value=data URL。")
    raw_result: dict[str, Any] = Field(alias="rawResult", description="MinerU 最终查询接口的原始结果。")

    model_config = {"populate_by_name": True}


class LocalFileParseResult(BaseModel):
    """Response compatible with MinerU's local ``/file_parse`` API."""

    status: str = "done"
    results: dict[str, dict[str, Any]]
    task_id: str | None = None
    batch_id: str | None = None
    poll_count: int = 0


class PreciseParseResult(BaseModel):
    file_name: str
    content_type: str | None
    size_bytes: int
    language: str
    batch_id: str
    state: str
    full_zip_url: str
    markdown: str
    poll_count: int
    content_list: list[dict[str, Any]]
    content_list_v2: Any
    archive_assets: dict[str, str]
    raw_result: dict[str, Any]


def _configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | mineru-server | {message}",
        colorize=False,
        backtrace=True,
        diagnose=False,
    )


def _read_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive number.") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive number.")
    return value


def _read_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    normalized = raw_value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value.")


def _read_optional_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _read_extra_headers() -> dict[str, str]:
    raw_value = os.getenv("MINERU_EXTRA_HEADERS_JSON", "").strip()
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("MINERU_EXTRA_HEADERS_JSON must be a valid JSON object.") from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError("MINERU_EXTRA_HEADERS_JSON must be a JSON object.")
    headers: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ConfigurationError("MINERU_EXTRA_HEADERS_JSON keys and values must be strings.")
        headers[key] = value
    return headers


@lru_cache
def get_settings() -> Settings:
    return Settings(
        upstream_base_url=os.getenv("MINERU_UPSTREAM_BASE_URL", "https://mineru.net").strip()
        or "https://mineru.net",
        api_token=_read_optional_env("MINERU_API_TOKEN", "MINERU_OCR_TOKEN"),
        timeout_seconds=_read_float_env("MINERU_REQUEST_TIMEOUT_SECONDS", 30.0),
        result_timeout_seconds=_read_float_env("MINERU_RESULT_TIMEOUT_SECONDS", 300.0),
        poll_interval_seconds=_read_float_env("MINERU_POLL_INTERVAL_SECONDS", 3.0),
        verify_ssl=_read_bool_env("MINERU_VERIFY_SSL", True),
        download_use_proxy=_read_bool_env("MINERU_DOWNLOAD_USE_PROXY", False),
        default_language=os.getenv("MINERU_DEFAULT_LANGUAGE", "ch").strip() or "ch",
        default_model_version=os.getenv("MINERU_MODEL_VERSION", "vlm").strip() or "vlm",
        default_enable_formula=_read_bool_env("MINERU_ENABLE_FORMULA", True),
        default_enable_table=_read_bool_env("MINERU_ENABLE_TABLE", True),
        extra_headers=_read_extra_headers(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    settings = get_settings()
    logger.info(
        "startup upstream_base_url={} timeout_seconds={} result_timeout_seconds={} poll_interval_seconds={} verify_ssl={} token_configured={} model_version={} enable_formula={} enable_table={}",
        settings.upstream_base_url,
        settings.timeout_seconds,
        settings.result_timeout_seconds,
        settings.poll_interval_seconds,
        settings.verify_ssl,
        bool(settings.api_token),
        settings.default_model_version,
        settings.default_enable_formula,
        settings.default_enable_table,
    )
    app.state.http_client = httpx.AsyncClient(verify=settings.verify_ssl, follow_redirects=True)
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        logger.info("shutdown")


app = FastAPI(
    title="MinerU Proxy Server",
    description=(
        "轻量代理服务：接收本地 multipart 文件，串联 MinerU 精准解析 API "
        "file-urls/batch -> PUT signed URL -> extract-results/batch -> download zip，"
        "并把 markdown、content_list.json、图片资产一起返回。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)


def _trace_id(request: Request) -> str:
    value = getattr(request.state, "trace_id", None)
    if isinstance(value, str) and value:
        return value
    generated = f"mineru_{uuid.uuid4().hex[:12]}"
    request.state.trace_id = generated
    return generated


def _preview_text(value: str, limit: int = 300) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-request-id") or f"mineru_{uuid.uuid4().hex[:12]}"
    request.state.trace_id = trace_id
    started_at = time.monotonic()
    client_host = request.client.host if request.client else None
    logger.info(
        "request.start trace_id={} method={} path={} query={} client={}",
        trace_id,
        request.method,
        request.url.path,
        request.url.query or None,
        client_host,
    )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request.unhandled_error trace_id={} method={} path={} duration_ms={}",
            trace_id,
            request.method,
            request.url.path,
            int((time.monotonic() - started_at) * 1000),
        )
        raise
    response.headers["X-Request-Id"] = trace_id
    logger.info(
        "request.finished trace_id={} method={} path={} status_code={} duration_ms={}",
        trace_id,
        request.method,
        request.url.path,
        response.status_code,
        int((time.monotonic() - started_at) * 1000),
    )
    return response


def _build_headers(settings: Settings) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if settings.api_token:
        token = settings.api_token
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers["Authorization"] = token
    headers.update(settings.extra_headers)
    return headers


def _extract_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="MinerU returned a non-object JSON payload.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail={"message": "MinerU response is missing data.", "body": payload})
    return data


def _extract_data_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="MinerU returned a non-object JSON payload.")
    data = payload.get("data")
    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail={"message": "MinerU response is missing data list.", "body": payload})
    normalized: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


def _extract_batch_upload_data(payload: Any) -> tuple[str, list[str]]:
    data = _extract_data(payload)
    batch_id = _pick_first_string(data, "batch_id", "batchId")
    raw_urls = data.get("file_urls")
    file_urls = [item.strip() for item in raw_urls if isinstance(item, str) and item.strip()] if isinstance(raw_urls, list) else []
    if not batch_id or not file_urls:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "MinerU precise file-urls/batch response is missing batch_id or file_urls.",
                "body": data,
            },
        )
    return batch_id, file_urls


def _extract_batch_result_data(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    data = _extract_data(payload)
    batch_id = _pick_first_string(data, "batch_id", "batchId")
    extract_result = data.get("extract_result")
    results = [item for item in extract_result if isinstance(item, dict)] if isinstance(extract_result, list) else []
    if not batch_id:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "MinerU precise batch result is missing batch_id.",
                "body": data,
            },
        )
    return batch_id, results


def _pick_first_string(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _raise_http_error(stage: str, response: httpx.Response) -> None:
    try:
        body = response.json()
    except ValueError:
        body = response.text[:2000]
    logger.error(
        "upstream.http_error stage={} status_code={} content_type={} body_preview={}",
        stage,
        response.status_code,
        response.headers.get("content-type"),
        _preview_text(response.text),
    )
    raise HTTPException(
        status_code=502,
        detail={
            "message": f"MinerU request failed during {stage}.",
            "status_code": response.status_code,
            "body": body,
        },
    )


def _json_from_response(stage: str, response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        logger.error(
            "upstream.non_json stage={} status_code={} content_type={} body_preview={}",
            stage,
            response.status_code,
            response.headers.get("content-type"),
            _preview_text(response.text),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"MinerU returned a non-JSON response during {stage}.",
                "status_code": response.status_code,
                "body": response.text[:2000],
            },
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"MinerU returned an unexpected JSON payload during {stage}.",
                "status_code": response.status_code,
                "body": payload,
            },
        )
    return payload


async def _request_precise_upload_slot(
    request: Request,
    file_name: str,
    content_type: str | None,
    language: str,
) -> tuple[str, str]:
    settings = get_settings()
    trace_id = _trace_id(request)
    url = f"{settings.upstream_base_url.rstrip('/')}/api/v4/file-urls/batch"
    payload = {
        "files": [
            {
                "name": file_name,
                "is_ocr": True,
                "data_id": f"data_{uuid.uuid4().hex}",
            }
        ],
        "language": language,
        "enable_formula": settings.default_enable_formula,
        "enable_table": settings.default_enable_table,
        "model_version": settings.default_model_version,
    }
    started_at = time.monotonic()
    logger.info(
        "precise.file.request_start trace_id={} file_name={} language={} content_type={} model_version={} url={}",
        trace_id,
        file_name,
        language,
        content_type,
        settings.default_model_version,
        url,
    )
    try:
        response = await request.app.state.http_client.post(
            url,
            headers=_build_headers(settings),
            json=payload,
            timeout=settings.timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        logger.exception(
            "precise.file.request_timeout trace_id={} file_name={} timeout_seconds={}",
            trace_id,
            file_name,
            settings.timeout_seconds,
        )
        raise HTTPException(status_code=504, detail="Timed out while requesting MinerU precise upload slot.") from exc
    except httpx.HTTPError as exc:
        logger.exception("precise.file.request_failed trace_id={} file_name={}", trace_id, file_name)
        raise HTTPException(status_code=502, detail=f"Failed to call MinerU precise file-urls/batch: {exc}") from exc

    logger.info(
        "precise.file.request_response trace_id={} status_code={} content_type={} duration_ms={}",
        trace_id,
        response.status_code,
        response.headers.get("content-type"),
        int((time.monotonic() - started_at) * 1000),
    )

    if response.status_code >= 400:
        _raise_http_error("request_precise_upload_slot", response)

    payload_json = _json_from_response("request_precise_upload_slot", response)
    logger.info(
        "precise.file.slot_raw_payload trace_id={} payload={}",
        trace_id,
        _dump_json(payload_json),
    )
    batch_id, file_urls = _extract_batch_upload_data(payload_json)
    upload_url = file_urls[0]
    logger.info(
        "precise.file.slot_received trace_id={} batch_id={} file_url_count={} upload_url_length={}",
        trace_id,
        batch_id,
        len(file_urls),
        len(upload_url),
    )
    return batch_id, upload_url


async def _upload_file_bytes(
    request: Request,
    upload_url: str,
    content: bytes,
    content_type: str | None,
) -> None:
    trace_id = _trace_id(request)
    started_at = time.monotonic()
    logger.info(
        "parse.file.upload_start trace_id={} size_bytes={} content_type={} upload_host={} note={}",
        trace_id,
        len(content),
        content_type,
        httpx.URL(upload_url).host,
        "signed_url_upload_does_not_forward_content_type_header",
    )
    try:
        response = await request.app.state.http_client.put(
            upload_url,
            content=content,
            timeout=get_settings().timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        logger.exception("parse.file.upload_timeout trace_id={} size_bytes={}", trace_id, len(content))
        raise HTTPException(status_code=504, detail="Timed out while uploading file to MinerU signed URL.") from exc
    except httpx.HTTPError as exc:
        logger.exception("parse.file.upload_failed trace_id={} size_bytes={}", trace_id, len(content))
        raise HTTPException(status_code=502, detail=f"Failed to upload file to MinerU signed URL: {exc}") from exc
    logger.info(
        "parse.file.upload_response trace_id={} status_code={} content_type={} duration_ms={}",
        trace_id,
        response.status_code,
        response.headers.get("content-type"),
        int((time.monotonic() - started_at) * 1000),
    )
    if response.status_code >= 400:
        _raise_http_error("upload_signed_file", response)
    logger.info("parse.file.upload_finished trace_id={} status_code={}", trace_id, response.status_code)


async def _poll_batch_until_done(
    request: Request,
    batch_id: str,
) -> tuple[dict[str, Any], int]:
    settings = get_settings()
    trace_id = _trace_id(request)
    url = f"{settings.upstream_base_url.rstrip('/')}/api/v4/extract-results/batch/{batch_id}"
    deadline = time.monotonic() + settings.result_timeout_seconds
    poll_count = 0
    last_state = "unknown"
    logger.info(
        "precise.file.poll_loop_start trace_id={} batch_id={} poll_interval_seconds={} timeout_seconds={}",
        trace_id,
        batch_id,
        settings.poll_interval_seconds,
        settings.result_timeout_seconds,
    )

    while time.monotonic() < deadline:
        poll_count += 1
        poll_started_at = time.monotonic()
        try:
            response = await request.app.state.http_client.get(
                url,
                headers=_build_headers(settings),
                timeout=settings.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            logger.exception("precise.file.poll_timeout trace_id={} batch_id={} poll_count={}", trace_id, batch_id, poll_count)
            raise HTTPException(status_code=504, detail="Timed out while polling MinerU precise batch task.") from exc
        except httpx.HTTPError as exc:
            logger.exception("precise.file.poll_failed trace_id={} batch_id={} poll_count={}", trace_id, batch_id, poll_count)
            raise HTTPException(status_code=502, detail=f"Failed to poll MinerU precise batch task: {exc}") from exc

        logger.info(
            "precise.file.poll_response trace_id={} batch_id={} poll_count={} status_code={} content_type={} duration_ms={}",
            trace_id,
            batch_id,
            poll_count,
            response.status_code,
            response.headers.get("content-type"),
            int((time.monotonic() - poll_started_at) * 1000),
        )

        if response.status_code >= 400:
            _raise_http_error("poll_precise_batch_task", response)

        payload_json = _json_from_response("poll_precise_batch_task", response)
        logger.info(
            "precise.file.poll_raw_payload trace_id={} batch_id={} poll_count={} payload={}",
            trace_id,
            batch_id,
            poll_count,
            _dump_json(payload_json),
        )
        response_batch_id, extract_result = _extract_batch_result_data(payload_json)
        if response_batch_id != batch_id:
            logger.warning(
                "precise.file.poll_batch_id_mismatch trace_id={} requested_batch_id={} response_batch_id={}",
                trace_id,
                batch_id,
                response_batch_id,
            )
        if not extract_result:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "MinerU precise poll response extract_result is empty.",
                    "batch_id": batch_id,
                    "body": payload_json,
                },
            )
        first = extract_result[0]
        state = _pick_first_string(first, "state", "extract_result", "extractResult") or "unknown"
        if state.lower() in {"success", "succeeded", "completed", "done"}:
            state = "done"
        elif state.lower() in {"running", "processing", "pending", "queued"}:
            state = "processing"
        last_state = state
        logger.info(
            "precise.file.poll trace_id={} batch_id={} poll_count={} state={} remaining_timeout_ms={}",
            trace_id,
            batch_id,
            poll_count,
            state,
            max(0, int((deadline - time.monotonic()) * 1000)),
        )

        if state.lower() == "done":
            logger.info(
                "precise.file.poll_done trace_id={} batch_id={} poll_count={} raw_result={}",
                trace_id,
                batch_id,
                poll_count,
                _dump_json(first),
            )
            return first, poll_count
        if state.lower() in {"failed", "error", "canceled", "cancelled"}:
            logger.error(
                "precise.file.poll_terminal_failure trace_id={} batch_id={} poll_count={} state={} data_keys={}",
                trace_id,
                batch_id,
                poll_count,
                state,
                sorted(first.keys()),
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "MinerU precise batch task finished with failure state.",
                    "batch_id": batch_id,
                    "state": state,
                    "body": first,
                },
            )
        await asyncio.sleep(settings.poll_interval_seconds)

    logger.error(
        "precise.file.poll_deadline_exceeded trace_id={} batch_id={} poll_count={} last_state={}",
        trace_id,
        batch_id,
        poll_count,
        last_state,
    )
    raise HTTPException(
        status_code=504,
        detail={
            "message": "Timed out waiting for MinerU precise parse result.",
            "batch_id": batch_id,
            "last_state": last_state,
            "timeout_seconds": settings.result_timeout_seconds,
        },
    )


def _download_zip_with_requests(
    full_zip_url: str,
    timeout_seconds: float,
    verify_ssl: bool,
    use_proxy: bool,
) -> tuple[int, str | None, bytes]:
    """Blocking zip download via requests. Runs in a worker thread.

    By default proxies are disabled (per-scheme None), which also tells requests
    to ignore HTTP_PROXY/HTTPS_PROXY/ALL_PROXY from the environment. The signed
    CDN URL is reachable directly, and routing it through an env proxy is what
    breaks the TLS tunnel (httpcore http_proxy start_tls ConnectError). Set
    MINERU_DOWNLOAD_USE_PROXY=true to opt back into the environment proxy.
    """
    proxies = None if use_proxy else {"http": None, "https": None}
    response = requests.get(
        full_zip_url,
        timeout=timeout_seconds,
        verify=verify_ssl,
        proxies=proxies,
        headers={"Accept": "application/zip, application/octet-stream, */*"},
    )
    return response.status_code, response.headers.get("content-type"), response.content


async def _download_zip_and_extract(
    request: Request,
    full_zip_url: str,
) -> tuple[str, list[dict[str, Any]], Any, dict[str, str]]:
    settings = get_settings()
    trace_id = _trace_id(request)
    started_at = time.monotonic()
    logger.info(
        "precise.file.zip_download_start trace_id={} url={} use_proxy={}",
        trace_id,
        full_zip_url,
        settings.download_use_proxy,
    )
    try:
        status_code, content_type, zip_bytes = await asyncio.to_thread(
            _download_zip_with_requests,
            full_zip_url,
            settings.timeout_seconds,
            settings.verify_ssl,
            settings.download_use_proxy,
        )
    except requests.exceptions.Timeout as exc:
        logger.exception("precise.file.zip_download_timeout trace_id={} url={}", trace_id, full_zip_url)
        raise HTTPException(status_code=504, detail="Timed out while downloading MinerU precise zip result.") from exc
    except requests.exceptions.RequestException as exc:
        logger.exception("precise.file.zip_download_failed trace_id={} url={}", trace_id, full_zip_url)
        raise HTTPException(status_code=502, detail=f"Failed to download MinerU precise zip result: {exc}") from exc
    logger.info(
        "precise.file.zip_download_response trace_id={} status_code={} content_type={} duration_ms={}",
        trace_id,
        status_code,
        content_type,
        int((time.monotonic() - started_at) * 1000),
    )
    if status_code >= 400:
        logger.error(
            "precise.file.zip_download_http_error trace_id={} status_code={} content_type={}",
            trace_id,
            status_code,
            content_type,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "MinerU request failed during download_precise_zip.",
                "status_code": status_code,
            },
        )

    markdown = ""
    content_list: list[dict[str, Any]] = []
    content_list_v2: Any = None
    archive_assets: dict[str, str] = {}
    logger.info("precise.file.zip_download_finished trace_id={} size_bytes={}", trace_id, len(zip_bytes))
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            logger.info("precise.file.zip_entries trace_id={} entries={}", trace_id, _dump_json(names))
            for name in names:
                normalized_name = name.replace("\\", "/")
                lower_name = normalized_name.lower()
                if lower_name.endswith("/"):
                    continue
                with archive.open(name) as handle:
                    payload = handle.read()
                if lower_name.endswith("/full.md") or lower_name.endswith("full.md"):
                    markdown = payload.decode("utf-8", errors="replace")
                elif lower_name.endswith("content_list.json"):
                    try:
                        parsed = json.loads(payload.decode("utf-8"))
                        if isinstance(parsed, list):
                            content_list = parsed
                    except json.JSONDecodeError:
                        logger.warning("precise.file.content_list_invalid_json trace_id={} path={}", trace_id, normalized_name)
                elif lower_name.endswith("content_list_v2.json"):
                    try:
                        content_list_v2 = json.loads(payload.decode("utf-8"))
                    except json.JSONDecodeError:
                        logger.warning("precise.file.content_list_v2_invalid_json trace_id={} path={}", trace_id, normalized_name)
                elif _looks_like_image_path(lower_name):
                    archive_assets[normalized_name] = _to_data_url(normalized_name, payload)
    except zipfile.BadZipFile as exc:
        logger.exception("precise.file.zip_invalid trace_id={} url={}", trace_id, full_zip_url)
        raise HTTPException(status_code=502, detail=f"MinerU precise result zip is invalid: {exc}") from exc

    logger.info(
        "precise.file.markdown_download_finished trace_id={} size_bytes={} preview={}",
        trace_id,
        len(markdown.encode("utf-8")),
        _preview_text(markdown),
    )
    logger.info(
        "precise.file.markdown_download_full trace_id={} markdown={}",
        trace_id,
        markdown,
    )
    logger.info(
        "precise.file.archive_assets trace_id={} image_count={} content_list_count={} has_content_list_v2={}",
        trace_id,
        len(archive_assets),
        len(content_list),
        content_list_v2 is not None,
    )
    return markdown, content_list, content_list_v2, archive_assets


async def _parse_precise_file(
    request: Request,
    *,
    file_name: str,
    content: bytes,
    content_type: str | None,
    language: str,
) -> PreciseParseResult:
    """Run the online precise parsing flow and normalize its zip result."""
    trace_id = _trace_id(request)
    started_at = time.monotonic()
    batch_id, upload_url = await _request_precise_upload_slot(
        request,
        file_name=file_name,
        content_type=content_type,
        language=language,
    )
    await _upload_file_bytes(
        request,
        upload_url=upload_url,
        content=content,
        content_type=content_type,
    )
    raw_result, poll_count = await _poll_batch_until_done(request, batch_id=batch_id)
    full_zip_url = _pick_first_string(raw_result, "full_zip_url", "fullZipUrl")
    if not full_zip_url:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "MinerU precise result is missing full_zip_url.",
                "batch_id": batch_id,
                "body": raw_result,
            },
        )
    markdown, content_list, content_list_v2, archive_assets = await _download_zip_and_extract(
        request,
        full_zip_url,
    )
    state = _pick_first_string(raw_result, "state") or "done"
    logger.info(
        "precise.file.finished trace_id={} file_name={} batch_id={} poll_count={} state={} raw_result_keys={} content_list_count={} asset_count={} duration_ms={}",
        trace_id,
        file_name,
        batch_id,
        poll_count,
        state,
        sorted(raw_result.keys()),
        len(content_list),
        len(archive_assets),
        int((time.monotonic() - started_at) * 1000),
    )
    return PreciseParseResult(
        file_name=file_name,
        content_type=content_type,
        size_bytes=len(content),
        language=language,
        batch_id=batch_id,
        state=state,
        full_zip_url=full_zip_url,
        markdown=markdown,
        poll_count=poll_count,
        content_list=content_list,
        content_list_v2=content_list_v2,
        archive_assets=archive_assets,
        raw_result=raw_result,
    )


def _local_file_parse_response(result: PreciseParseResult) -> LocalFileParseResult:
    """Translate the precise API result to the local MinerU result envelope."""
    result_key = _local_result_key(result.file_name)
    return LocalFileParseResult(
        status=result.state,
        task_id=result.batch_id,
        batch_id=result.batch_id,
        poll_count=result.poll_count,
        results={
            result_key: {
                "md_content": result.markdown,
                "content_list": json.dumps(result.content_list, ensure_ascii=False),
                "content_list_v2": json.dumps(result.content_list_v2, ensure_ascii=False)
                if result.content_list_v2 is not None
                else None,
                "images": result.archive_assets,
            }
        },
    )


def _local_result_key(file_name: str) -> str:
    stem = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    return stem or "upload"


def _looks_like_image_path(path: str) -> bool:
    return path.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff"))


def _to_data_url(path: str, payload: bytes) -> str:
    mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


@app.get("/")
async def index() -> dict[str, Any]:
    return {
        "service": "mineru-proxy-server",
        "endpoints": [
            "/health",
            "/file_parse",
            "/v1/parse/file",
        ],
    }


@app.get("/health", response_model=None)
async def health() -> Any:
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        return JSONResponse(status_code=503, content={"status": "misconfigured", "detail": str(exc)})
    return {
        "status": "ok",
        "upstream_base_url": settings.upstream_base_url,
        "verify_ssl": settings.verify_ssl,
        "token_configured": bool(settings.api_token),
        "default_language": settings.default_language,
    }


@app.post(
    "/v1/parse/file",
    response_model=ParseFileResponse,
    summary="上传本地文件并返回精准解析结果",
    description=(
        "接收 multipart 文件，内部完成 MinerU 精准解析 API file-urls/batch 申请上传地址、"
        "PUT 上传文件、轮询 extract-results/batch、下载 full zip，并把 markdown、"
        "content_list.json、图片资产一起返回。"
    ),
)
async def parse_file(
    request: Request,
    file: UploadFile = File(..., description="待解析文件。"),
    language: str | None = Form(
        default=None,
        description="MinerU language 参数。不传时使用服务默认值，例如 ch 或 en。",
    ),
) -> ParseFileResponse:
    trace_id = _trace_id(request)
    file_name = file.filename or "upload.bin"
    resolved_language = (language or get_settings().default_language).strip() or get_settings().default_language
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    logger.info(
        "parse.file.start trace_id={} file_name={} content_type={} size_bytes={} language={}",
        trace_id,
        file_name,
        file.content_type,
        len(content),
        resolved_language,
    )
    result = await _parse_precise_file(
        request,
        file_name=file_name,
        content=content,
        content_type=file.content_type,
        language=resolved_language,
    )
    return ParseFileResponse(
        fileName=result.file_name,
        contentType=result.content_type,
        sizeBytes=result.size_bytes,
        language=result.language,
        taskId=result.batch_id,
        batchId=result.batch_id,
        state=result.state,
        fullZipUrl=result.full_zip_url,
        markdown=result.markdown,
        pollCount=result.poll_count,
        contentList=result.content_list,
        contentListV2=result.content_list_v2,
        archiveAssets=result.archive_assets,
        rawResult=result.raw_result,
    )


@app.post(
    "/file_parse",
    response_model=LocalFileParseResult,
    summary="兼容本地 MinerU 文件解析接口",
    description=(
        "兼容 POST /file_parse 的 multipart 契约。接收 files 和 lang_list，"
        "内部调用在线 MinerU 精准解析 API，并返回 results[filename] 下的 "
        "md_content、content_list、content_list_v2、images。"
    ),
)
async def parse_local_file(
    request: Request,
    files: list[UploadFile] = File(..., description="待解析文件。当前一次只支持一个文件。"),
    lang_list: str | None = Form(default=None, description="MinerU 识别语言，例如 ch 或 en。"),
    return_md: bool | None = Form(default=None, description="兼容字段。Markdown 始终返回。"),
    return_content_list: bool | None = Form(default=None, description="兼容字段。结构化 content list 始终返回。"),
    return_images: bool | None = Form(default=None, description="兼容字段。图片资产始终返回。"),
    response_format_zip: bool | None = Form(default=None, description="兼容字段。代理始终返回 JSON。"),
) -> LocalFileParseResult:
    del return_md, return_content_list, return_images, response_format_zip
    if len(files) != 1:
        raise HTTPException(
            status_code=400,
            detail="The compatibility /file_parse endpoint currently accepts exactly one file.",
        )

    file = files[0]
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    settings = get_settings()
    file_name = file.filename or "upload.bin"
    language = (lang_list or settings.default_language).strip() or settings.default_language
    trace_id = _trace_id(request)
    logger.info(
        "local.file_parse.start trace_id={} file_name={} content_type={} size_bytes={} lang_list={}",
        trace_id,
        file_name,
        file.content_type,
        len(content),
        language,
    )
    result = await _parse_precise_file(
        request,
        file_name=file_name,
        content=content,
        content_type=file.content_type,
        language=language,
    )
    response = JSONResponse(content=_local_file_parse_response(result).model_dump(mode="json"))
    response.headers["X-MinerU-Task-Id"] = result.batch_id
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9999)
