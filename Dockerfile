FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_RUNTIME_HOST=0.0.0.0 \
    AGENT_RUNTIME_PORT=8010 \
    AGENT_RUNTIME_REGISTRY_DIR=/app/registry \
    AGENT_RUNTIME_SANDBOX_URL=http://sandbox:8001

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

COPY agent_runtime ./agent_runtime
COPY registry ./registry

EXPOSE 8010

CMD ["python", "-m", "agent_runtime"]
