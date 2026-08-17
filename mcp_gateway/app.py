from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, Field

from agent_runtime.models import McpServerDefinition, SkillExecutionContext


logger = logging.getLogger("mcp_gateway")
MAX_TEXT_CHARS = 40000
MAX_CONTENT_ITEMS = 50


class ListToolsRequest(BaseModel):
    server: McpServerDefinition


class CallToolRequest(BaseModel):
    server: McpServerDefinition
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: SkillExecutionContext


class MissingMcpEnvironment(RuntimeError):
    pass


@asynccontextmanager
async def mcp_session(server: McpServerDefinition) -> AsyncIterator[ClientSession]:
    if server.transport == "stdio":
        parameters = StdioServerParameters(
            command=str(server.command),
            args=server.args,
            env=build_stdio_env(server.env),
        )
        async with stdio_client(parameters) as transport:
            async with ClientSession(transport[0], transport[1]) as session:
                await session.initialize()
                yield session
        return

    headers = resolve_values(server.headers)
    async with streamablehttp_client(str(server.url), headers=headers) as transport:
        async with ClientSession(transport[0], transport[1]) as session:
            await session.initialize()
            yield session


def resolve_values(values: dict[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, value in values.items():
        if value.startswith("env:"):
            source = value[4:].strip()
            secret = os.getenv(source)
            if not source or secret is None:
                raise MissingMcpEnvironment(source or key)
            resolved[key] = secret
        else:
            resolved[key] = value
    return resolved


def build_stdio_env(configured_env: dict[str, str]) -> dict[str, str]:
    # Do not inherit service secrets such as OPENAI_API_KEY into child MCP servers.
    passthrough = ["PATH", "HOME", "LANG", "LC_ALL", "PYTHONIOENCODING", "SYSTEMROOT", "COMSPEC"]
    environment = {key: value for key in passthrough if (value := os.getenv(key)) is not None}
    environment.update(resolve_values(configured_env))
    return environment


def model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(by_alias=True, exclude_none=True)
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def normalize_tool(tool: Any) -> dict[str, Any]:
    raw = model_dump(tool)
    schema = raw.get("inputSchema") or raw.get("input_schema")
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    return {
        "name": str(raw.get("name") or ""),
        "description": str(raw.get("description") or ""),
        "input_schema": schema,
    }


def normalize_call_result(result: Any) -> tuple[bool, dict[str, Any]]:
    raw = model_dump(result)
    is_error = bool(raw.get("isError") or raw.get("is_error"))
    content = raw.get("content") if isinstance(raw.get("content"), list) else []
    normalized_content: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for item in content[:MAX_CONTENT_ITEMS]:
        entry = model_dump(item)
        item_type = str(entry.get("type") or "")
        if item_type == "text":
            text = str(entry.get("text") or "")
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + "..."
            normalized_content.append({"type": "text", "text": text})
            text_parts.append(text)
        elif item_type == "resource_link":
            normalized_content.append(
                {
                    "type": "resource_link",
                    "name": str(entry.get("name") or ""),
                    "uri": str(entry.get("uri") or ""),
                    "mimeType": str(entry.get("mimeType") or entry.get("mime_type") or ""),
                }
            )
        else:
            normalized_content.append({"type": item_type or "unknown"})
    structured = raw.get("structuredContent") or raw.get("structured_content")
    data: dict[str, Any] = {"content": "\n".join(text_parts), "content_items": normalized_content}
    if isinstance(structured, dict):
        data["structured_content"] = structured
    return is_error, data


def execution_metadata(
    *,
    server: McpServerDefinition,
    tool_name: str,
    context: SkillExecutionContext | None,
    started_at: float,
) -> dict[str, Any]:
    return {
        "execution_id": f"mcp_{uuid.uuid4().hex}",
        "server_id": server.id,
        "transport": server.transport,
        "tool_name": tool_name,
        "agent_id": context.agent_id if context else "",
        "conversation_id": context.conversation_id if context else "",
        "workspace_id": context.workspace_id if context else "",
        "run_id": context.run_id if context else "",
        "tool_call_id": context.tool_call_id if context else "",
        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        "timeout_ms": server.timeout_ms,
    }


async def list_tools(server: McpServerDefinition) -> list[dict[str, Any]]:
    async with asyncio.timeout(server.timeout_ms / 1000):
        async with mcp_session(server) as session:
            result = await session.list_tools()
    tools = [normalize_tool(tool) for tool in getattr(result, "tools", [])]
    tools = [tool for tool in tools if tool["name"]]
    if server.tool_allowlist:
        tools = [tool for tool in tools if tool["name"] in server.tool_allowlist]
    return tools


app = FastAPI(title="Agent Runtime MCP Gateway", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "mode": "mcp_gateway", "supported_transports": ["stdio", "streamable_http"]}


@app.post("/mcp/tools/list")
async def list_mcp_tools(request: ListToolsRequest) -> JSONResponse:
    try:
        tools = await list_tools(request.server)
    except MissingMcpEnvironment:
        return JSONResponse(
            {"success": False, "error": "MCP server environment is not configured.", "error_type": "missing_mcp_environment"},
            status_code=424,
        )
    except TimeoutError:
        return JSONResponse(
            {"success": False, "error": "MCP tools/list timed out.", "error_type": "mcp_timeout"}, status_code=504
        )
    except Exception:
        logger.exception("MCP tools/list failed for server=%s", request.server.id)
        return JSONResponse(
            {"success": False, "error": "MCP tools/list failed.", "error_type": "mcp_discovery_failed"}, status_code=502
        )
    return JSONResponse({"success": True, "server_id": request.server.id, "tools": tools})


@app.post("/mcp/tools/call")
async def call_mcp_tool(request: CallToolRequest) -> JSONResponse:
    if request.server.tool_allowlist and request.tool_name not in request.server.tool_allowlist:
        return JSONResponse(
            {"success": False, "error": "MCP tool is not allowlisted for this server.", "error_type": "mcp_tool_not_allowed"},
            status_code=403,
        )
    started_at = time.monotonic()
    try:
        async with asyncio.timeout(request.server.timeout_ms / 1000):
            async with mcp_session(request.server) as session:
                result = await session.call_tool(request.tool_name, request.arguments)
        is_error, data = normalize_call_result(result)
    except MissingMcpEnvironment:
        return JSONResponse(
            {
                "success": False,
                "error": "MCP server environment is not configured.",
                "error_type": "missing_mcp_environment",
                "execution": execution_metadata(
                    server=request.server,
                    tool_name=request.tool_name,
                    context=request.context,
                    started_at=started_at,
                ),
            },
            status_code=424,
        )
    except TimeoutError:
        return JSONResponse(
            {
                "success": False,
                "error": "MCP tool call timed out.",
                "error_type": "mcp_timeout",
                "execution": execution_metadata(
                    server=request.server,
                    tool_name=request.tool_name,
                    context=request.context,
                    started_at=started_at,
                ),
            },
            status_code=504,
        )
    except Exception:
        logger.exception("MCP tools/call failed for server=%s tool=%s", request.server.id, request.tool_name)
        return JSONResponse(
            {
                "success": False,
                "error": "MCP tool call failed.",
                "error_type": "mcp_call_failed",
                "execution": execution_metadata(
                    server=request.server,
                    tool_name=request.tool_name,
                    context=request.context,
                    started_at=started_at,
                ),
            },
            status_code=502,
        )
    return JSONResponse(
        {
            "success": not is_error,
            "server_id": request.server.id,
            "tool_name": request.tool_name,
            "is_error": is_error,
            "data": data,
            "execution": execution_metadata(
                server=request.server,
                tool_name=request.tool_name,
                context=request.context,
                started_at=started_at,
            ),
        }
    )
