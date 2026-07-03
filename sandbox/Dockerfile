FROM python:3.13-slim

WORKDIR /app

ARG ARTISAN_UID=10001
ARG ARTISAN_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${ARTISAN_GID}" artisan \
    && useradd --uid "${ARTISAN_UID}" --gid "${ARTISAN_GID}" --create-home --shell /usr/sbin/nologin artisan

COPY sandbox/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

COPY sandbox/app.py ./app.py
COPY sandbox/runner.py ./runner.py
COPY sandbox/runtime ./runtime

RUN mkdir -p /app/plugins_runtime/venvs \
    /app/plugins_runtime/scripts \
    /app/plugins_runtime/pip_cache \
    /app/plugins_runtime/tmp \
    && chown -R "${ARTISAN_UID}:${ARTISAN_GID}" /app /home/artisan

USER artisan

ENV ARTISAN_PLUGIN_SERVER_PORT=8001

EXPOSE 8001

CMD ["python", "app.py"]
