FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_GATEWAY_PORT=8002

COPY mcp_gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

COPY agent_runtime ./agent_runtime
COPY mcp_gateway ./mcp_gateway
COPY mcp_server ./mcp_server

EXPOSE 8002

CMD ["python", "-m", "uvicorn", "mcp_gateway.app:app", "--host", "0.0.0.0", "--port", "8002"]
