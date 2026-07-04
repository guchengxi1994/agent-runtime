# Agent Runtime Sandbox

> 这个sandbox是基于 [Artisan Plugin](https://github.com/AI-change-the-world/Artisan) 修改的

这是独立的 Python skill 执行服务。它不负责规划、不读取 `registry/skills`，也不保存工具目录；调用方必须把要执行的 skill 脚本、参数、上下文和执行策略通过 HTTP 传进来。

## 职责边界

- 接收 `POST /skills/execute` 请求。
- 为每个 agent + skill + packages 组合生成可复用 `venv_key`。
- 创建隔离任务目录，安装 `execution_policy.packages`。
- 从 `required_secrets` 解析环境变量并注入子进程。
- 执行 Python `entrypoint`，限制总超时、idle timeout、stdout/stderr 大小和进程资源。
- 返回结构化 `execution` metadata，供主 runtime 审计。

主 `agent_runtime` 只做规划和路由，不能在自身进程里执行 skill 代码。

## Skill 脚本规范

Python entrypoint 至少包含：

```python
definition = {
    "name": "hello",
    "description": "Return a greeting.",
}


def execute(params: dict):
    return {"message": f"Hello, {params.get('name', 'world')}!"}
```

要求：

- 必须定义全局变量 `definition`。
- 必须定义同步或异步 `execute(params)`。
- 返回值必须可 JSON 序列化。
- 不要在脚本里自行 `pip install`，依赖写到 skill 的 `execution_policy.packages`。
- API token/header 等敏感值通过 `required_secrets` 声明，由 sandbox 从环境变量解析。

## `POST /skills/execute`

请求结构：

```json
{
  "skill": {
    "name": "calculator",
    "entrypoint": "skill.py",
    "execution_policy": {
      "timeout_ms": 30000,
      "idle_timeout_ms": 10000,
      "packages": []
    },
    "required_secrets": {}
  },
  "script": "definition = {...}\ndef execute(params): ...",
  "arguments": {
    "expression": "1 + 2"
  },
  "context": {
    "agent_id": "default",
    "conversation_id": "conv_xxx",
    "user_id": "anonymous",
    "run_id": "run_xxx"
  },
  "base_policy": {}
}
```

成功响应会包含 runner 结果和审计 metadata：

```json
{
  "success": true,
  "data": {
    "value": 3
  },
  "phase": "execute",
  "execution": {
    "execution_id": "exec_xxx",
    "agent_id": "default",
    "conversation_id": "conv_xxx",
    "run_id": "run_xxx",
    "user_id": "anonymous",
    "skill_name": "calculator",
    "entrypoint": "skill.py",
    "elapsed_ms": 123,
    "timeout_ms": 30000,
    "packages": []
  }
}
```

如果缺少 secret：

```json
{
  "success": false,
  "error_type": "missing_required_secrets",
  "error": "Missing required secret(s): env:UPSTREAM_API_TOKEN"
}
```

## 其他接口

- `GET /health`: sandbox 状态
- `POST /validate`: 兼容的单文件脚本校验入口
- `POST /execute`: 兼容的单文件脚本执行入口
- `POST /execute/stream`: 兼容的 SSE 执行入口
- `POST /bundle/validate`: zip bundle 校验
- `POST /bundle/execute`: zip bundle 执行
- `POST /bundle/execute/stream`: zip bundle SSE 执行

主 runtime 只依赖 `/skills/execute`。其他入口保留用于本地调试和底层 runner 验证，不再表示上层 tool 抽象。

## execution_policy

```json
{
  "timeout_ms": 60000,
  "idle_timeout_ms": 30000,
  "packages": ["requests==2.32.3"],
  "pip_index_url": "https://mirrors.aliyun.com/pypi/simple",
  "pip_extra_index_url": "",
  "pip_trusted_host": "mirrors.aliyun.com",
  "env": {
    "NON_SECRET_FLAG": "1"
  }
}
```

字段说明：

- `timeout_ms`: 单次执行总超时。
- `idle_timeout_ms`: 阶段无输出超时。
- `packages`: 安装到 skill venv 的 Python 依赖。
- `pip_index_url` / `pip_extra_index_url` / `pip_trusted_host`: pip 源配置。
- `env`: 非敏感环境变量；secret 使用 `required_secrets`。
- `venv_key`: 可选。未传时 sandbox 会按 `agent_id + skill_name + packages` 自动生成。

## 分阶段执行

执行过程按阶段返回错误和日志：

- `pre-execute`: 创建任务目录、写入脚本和 payload、创建或复用 venv
- `dep-install`: 用 `ensurepip` 离线 bootstrap pip，然后按 `packages` 安装依赖
- `execute`: 启动 runner 并调用 `execute(params)`
- `post-execute`: 清理任务目录

## 隔离与限制

- 每次执行创建独立任务目录。
- venv 按 skill 依赖缓存，任务目录仍按次清理。
- cached venv 会写入 `.agent_runtime_dependencies.json`；如果 marker 缺失或与当前 `packages` 不匹配，sandbox 会在复用前自动重新同步依赖。
- 子进程使用独立进程组，超时会终止整棵进程树。
- Linux 下会施加 `RLIMIT_CPU`、`RLIMIT_AS`、`RLIMIT_FSIZE`、`RLIMIT_NPROC`、`RLIMIT_NOFILE`。
- 默认只继承白名单环境变量。
- stdout/stderr 总量有上限，超出会终止执行。
- bundle 上传会校验 manifest、路径穿越、文件数和解压大小。

## 本地运行

从仓库根目录创建 `.env`：

```bash
cp .env.example .env
```

sandbox 启动时会从当前目录或父目录自动加载 `.env`。executable skill 的 `required_secrets` 会从 sandbox 进程环境中解析，例如 `API_TOKEN: env:UPSTREAM_API_TOKEN`。

```bash
cd sandbox
pip install -r requirements.txt
python app.py
```

## Docker

```bash
docker build -f sandbox/Dockerfile -t agent-runtime-sandbox .
docker run --rm -p 8001:8001 agent-runtime-sandbox
```

在完整项目中推荐从仓库根目录执行：

```bash
docker compose up --build
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8001` | 服务端口 |
| `ARTISAN_PLUGIN_SERVER_PORT` | `8001` | 服务端口，优先于 `PORT` |
| `ARTISAN_PLUGIN_RUNTIME_DIR` | `./plugins_runtime` | 运行时目录根路径 |
| `ARTISAN_PLUGIN_VENV_ROOT` | `./plugins_runtime/venvs` | venv 根目录 |
| `ARTISAN_PLUGIN_PIP_CACHE_DIR` | `./plugins_runtime/pip_cache` | pip 缓存目录 |
| `ARTISAN_PLUGIN_TEMP_DIR` | `./plugins_runtime/tmp` | 临时任务目录 |
| `ARTISAN_PLUGIN_TIMEOUT` | `300000` | 默认执行总超时 |
| `ARTISAN_PLUGIN_VALIDATE_TIMEOUT` | `60000` | 默认验证总超时 |
| `ARTISAN_PLUGIN_IDLE_TIMEOUT` | `120000` | 默认执行空闲超时 |
| `ARTISAN_PLUGIN_VALIDATE_IDLE_TIMEOUT` | `30000` | 默认验证空闲超时 |
| `ARTISAN_PLUGIN_MAX_TIMEOUT` | `900000` | 单次执行最大总超时 |
| `ARTISAN_PLUGIN_MAX_IDLE_TIMEOUT` | `300000` | 单次执行最大空闲超时 |
| `ARTISAN_PLUGIN_MAX_BODY_BYTES` | `1048576` | 请求体大小限制 |
| `ARTISAN_PLUGIN_MAX_CONCURRENCY` | `8` | 最大并发数 |
| `ARTISAN_PLUGIN_PIP_INDEX_URL` | `https://mirrors.aliyun.com/pypi/simple` | 默认 pip 源 |
| `ARTISAN_PLUGIN_MAX_STDOUT_BYTES` | `1048576` | stdout 上限 |
| `ARTISAN_PLUGIN_MAX_STDERR_BYTES` | `1048576` | stderr 上限 |
