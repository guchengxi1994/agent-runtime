from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import Request

from .config import RuntimeSettings
from .errors import RequestError
from .models import ExecutionConfig


def is_record(value: Any) -> bool:
    return isinstance(value, dict)


def sanitize_plugin_id(plugin_id: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in plugin_id)
    return sanitized or "plugin"


def normalize_timeout(value: Any, fallback: int, settings: RuntimeSettings) -> int:
    if value is None:
        return fallback
    if not isinstance(value, (int, float)) or value <= 0:
        raise RequestError(400, "execution_policy.timeout_ms must be a positive number")
    return min(int(value), settings.max_execution_timeout_ms)


def normalize_idle_timeout(
    value: Any,
    fallback: int,
    timeout_ms: int,
    settings: RuntimeSettings,
) -> int:
    if value is None:
        normalized = fallback
    else:
        if not isinstance(value, (int, float)) or value <= 0:
            raise RequestError(400, "execution_policy.idle_timeout_ms must be a positive number")
        normalized = int(value)
    normalized = min(normalized, settings.max_idle_timeout_ms)
    return min(normalized, timeout_ms)


def normalize_packages(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise RequestError(400, "execution_policy.packages must be a string array")
    return [item.strip() for item in value]


def normalize_optional_string(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestError(400, f"execution_policy.{field} must be a string")
    value = value.strip()
    return value or None


def normalize_bool(field: str, value: Any, fallback: bool) -> bool:
    if value is None:
        return fallback
    if not isinstance(value, bool):
        raise RequestError(400, f"execution_policy.{field} must be a boolean")
    return value


def normalize_env(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not is_record(value):
        raise RequestError(400, "execution_policy.env must be an object")
    normalized: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            raise RequestError(400, "execution_policy.env contains invalid key")
        if not isinstance(raw, str):
            raise RequestError(400, f"execution_policy.env.{key} must be a string")
        normalized[key] = raw
    return normalized


def normalize_venv_key(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestError(400, "execution_policy.venv_key must be a string")
    value = value.strip()
    if not value:
        return None
    sanitized = sanitize_plugin_id(value)
    if len(sanitized) > 96:
        sanitized = sanitized[:96]
    return sanitized


def normalize_execution_config(
    policy: Any,
    fallback_timeout: int,
    settings: RuntimeSettings,
) -> ExecutionConfig:
    if policy is None:
        idle_timeout_ms = min(
            settings.idle_timeout_ms
            if fallback_timeout == settings.execution_timeout_ms
            else settings.validation_idle_timeout_ms,
            fallback_timeout,
        )
        return ExecutionConfig(
            timeout_ms=fallback_timeout,
            idle_timeout_ms=idle_timeout_ms,
            packages=[],
            pip_index_url=settings.default_pip_index_url,
            pip_extra_index_url=settings.default_pip_extra_index_url,
            pip_trusted_host=settings.default_pip_trusted_host,
            keep_venv=False,
            env={},
            venv_key=None,
        )
    if not is_record(policy):
        raise RequestError(400, "execution_policy must be an object")

    timeout_ms = normalize_timeout(policy.get("timeout_ms"), fallback_timeout, settings)
    default_idle_timeout = (
        settings.idle_timeout_ms
        if fallback_timeout == settings.execution_timeout_ms
        else settings.validation_idle_timeout_ms
    )
    return ExecutionConfig(
        timeout_ms=timeout_ms,
        idle_timeout_ms=normalize_idle_timeout(
            policy.get("idle_timeout_ms"),
            default_idle_timeout,
            timeout_ms,
            settings,
        ),
        packages=normalize_packages(policy.get("packages")),
        pip_index_url=normalize_optional_string("pip_index_url", policy.get("pip_index_url"))
        if "pip_index_url" in policy
        else settings.default_pip_index_url,
        pip_extra_index_url=normalize_optional_string(
            "pip_extra_index_url", policy.get("pip_extra_index_url")
        )
        if "pip_extra_index_url" in policy
        else settings.default_pip_extra_index_url,
        pip_trusted_host=normalize_optional_string(
            "pip_trusted_host", policy.get("pip_trusted_host")
        )
        if "pip_trusted_host" in policy
        else settings.default_pip_trusted_host,
        keep_venv=normalize_bool("keep_venv", policy.get("keep_venv"), False),
        env=normalize_env(policy.get("env")),
        venv_key=normalize_venv_key(policy.get("venv_key")),
    )


def parse_script_execution_request(body: Any) -> tuple[str, str, dict[str, Any], Any]:
    if not is_record(body):
        raise RequestError(400, "Request body must be a JSON object")
    script = body.get("script")
    if not isinstance(script, str) or not script.strip():
        raise RequestError(400, "script is required")
    params = body.get("params", {})
    if not is_record(params):
        raise RequestError(400, "params must be a JSON object")
    return (
        body["id"] if isinstance(body.get("id"), str) and body["id"] else f"inline_{uuid.uuid4().hex}",
        script,
        params,
        body.get("execution_policy"),
    )


async def read_json_body(request: Request, settings: RuntimeSettings) -> Any:
    raw = await request.body()
    if len(raw) > settings.max_request_body_bytes:
        raise RequestError(413, f"Request body too large ({len(raw)} bytes)")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RequestError(400, "Invalid JSON body") from exc
