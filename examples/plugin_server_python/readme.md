# Artisan Python Plugin Server

基于 FastAPI + 子进程 + 临时 `venv` 的无状态 Python 插件执行服务。

运行时本身不访问数据库，也不依赖 plugin 表。它只负责：

- 校验调用方上传的 Python 脚本或 bundle
- 按请求创建隔离任务目录和临时 `venv`
- 安装 `execution_policy.packages` 指定的依赖
- 执行插件并把 `stdout/stderr` 通过 SSE 回传

如果上层还保留“插件存 DB”的产品形态，也应该由 backend 先查库，再把脚本源码转发到这里执行。

## Python 插件书写规范

单文件 Python 插件至少包含：

- 全局变量 `definition`
- 可调用的 `execute(params)` 函数

最小示例：

```python
definition = {
    "name": "hello_world",
    "description": "简单问候插件",
    "category": "utility",
    "parameters": [
        {"name": "name", "type": "string", "description": "名称", "required": False}
    ],
}


def execute(params: dict):
    print("running plugin...")
    return {"message": f"Hello, {params.get('name', 'Artisan')}!"}
```

要求：

- 必须定义全局变量 `definition`
- 必须定义 `execute(params)`，支持同步或异步函数
- 返回值必须可 JSON 序列化
- 普通 `print()` 会作为 `stdout` SSE 事件返回

推荐的 `definition` 结构：

```python
definition = {
    "name": "my_tool",
    "description": "描述这个工具的功能",
    "category": "utility",
    "parameters": [
        {
            "name": "input",
            "type": "string",
            "description": "输入参数",
            "required": True,
            "default": "",
            "enum": ["a", "b"],
        }
    ],
}
```

依赖应声明在：

- 插件的 `python_packages`
- 或单次执行的 `execution_policy.packages`

不建议在脚本里自行 `pip install`。

## API

接口与 TypeScript 运行时保持相同的无状态风格：

- `GET /health`
- `POST /validate`
- `POST /execute`
- `POST /execute/stream`
- `POST /bundle/validate`
- `POST /bundle/execute`
- `POST /bundle/execute/stream`

其中流式接口 SSE 事件包括：

- `started`
- `phase`
- `activity`
- `pip`
- `venv`
- `stdout`
- `stderr`
- `ping`
- `result`
- `error`

`result` 事件结构：

```json
{
  "success": true,
  "data": {},
  "phase": "execute"
}
```

`error` 事件和普通 HTTP 错误现在也会返回结构化信息，例如：

```json
{
  "success": false,
  "error": "dep-install idle timeout (30000ms)",
  "status": 500,
  "code": "phase_idle_timeout",
  "phase": "dep-install",
  "details": {
    "step": "pip",
    "phase_elapsed_ms": 30124,
    "idle_timeout_ms": 30000,
    "effective_idle_timeout_ms": 60000,
    "compile_guard_active": true,
    "recent_output": "pip 正在 building wheel for ...",
    "last_output_hash": "c14b2c9e7f8d2d6a"
  }
}
```

## 分阶段执行模型

执行和流式输出统一按 4 个阶段组织：

- `pre-execute`: 校验 bundle、创建任务目录、解压、读取 `manifest.json`、生成 payload、创建 venv
- `dep-install`: bootstrap pip、安装 `execution_policy.packages`
- `execute`: 启动 `runner.py` 并执行插件
- `post-execute`: 删除任务目录或保留调试 venv

SSE 会在阶段切换时发送 `phase` 事件：

```json
{
  "plugin_id": "demo",
  "phase": "dep-install",
  "status": "in_progress",
  "timestamp": 1777981078681,
  "packages": ["numpy==2.1.3"]
}
```

阶段结束时同样会发送 `phase` 事件，并带上 `elapsed_ms`。

## 活跃度检测与慢安装处理

之前的空闲超时只依赖“是否读到完整的一行输出”，对 `pip` 进度条、回车刷新、长时间编译都不稳。现在的 runtime 改为：

- 按字节流持续读取 `stdout/stderr`，不再只靠 `readline()`
- 持续计算 `stdout/stderr` 哈希，并通过 `activity` 事件回传
- `idle_timeout_ms` 基于“最近一次真实字节输出”判断，而不是“最近一次换行”
- `dep-install` 阶段如果检测到 `building wheel`、`cargo`、`cmake`、`gcc` 等编译信号，会自动加一段编译宽限时间

`activity` 事件示例：

```json
{
  "plugin_id": "demo",
  "phase": "dep-install",
  "stream": "pip",
  "stdout_hash": "2b0c4b7c9d71a82f",
  "stderr_hash": "da39a3ee5e6b4b0d",
  "combined_hash": "f7851a4e6d7a5029",
  "stdout_bytes": 8192,
  "stderr_bytes": 0,
  "idle_elapsed_ms": 213
}
```

说明：

- 如果哈希持续变化，说明 subprocess 仍有输出活动，不应该被误判为卡死
- 如果进入真正无输出的编译阶段，仍然可能触发 idle timeout，所以保留总超时和编译宽限双层保护
- 前端可以把 `phase + code + details.recent_output` 直接展示出来，定位会比过去容易很多

## Bundle 上传格式

bundle 使用 zip 文件，根目录必须包含 `manifest.json`：

```json
{
  "runtime": "python",
  "entrypoint": "src/main.py"
}
```

规则：

- `runtime` 必须是 `python`
- `entrypoint` 必须是 bundle 内相对路径
- 入口文件必须存在且是 `.py`
- 不做自动入口猜测，统一以 `manifest.json` 为准

bundle 接口使用 multipart/form-data：

- `bundle`: zip 文件
- `id`: 可选
- `params`: 可选，仅执行接口需要，必须是 JSON 对象字符串
- `execution_policy`: 可选 JSON 对象字符串

运行时会做这些限制：

- 拒绝路径穿越
- 限制 bundle 文件数
- 限制解压后总大小
- 只在当前任务目录中解压和执行

## execution_policy

Python 版本的 `execution_policy` 用来描述环境准备与执行限制：

```json
{
  "timeout_ms": 60000,
  "idle_timeout_ms": 30000,
  "packages": ["requests==2.32.3", "pandas"],
  "pip_index_url": "https://pypi.tuna.tsinghua.edu.cn/simple",
  "pip_extra_index_url": "",
  "pip_trusted_host": "pypi.tuna.tsinghua.edu.cn",
  "keep_venv": false,
  "env": {
    "MY_API_KEY": "xxx"
  }
}
```

字段说明：

- `timeout_ms`: 单次执行总超时，覆盖 `pre-execute`、`dep-install`、`execute`、`post-execute`
- `idle_timeout_ms`: 阶段空闲超时；长时间没有新的输出字节时判定为 hang
- `packages`: 本次执行安装到临时 `venv` 的依赖
- `pip_index_url`: 主 pip 源
- `pip_extra_index_url`: 额外 pip 源
- `pip_trusted_host`: trusted host
- `keep_venv`: 调试用，默认执行后删除
- `env`: 传给插件进程的额外环境变量

## 缓存策略

- `venv` 是一次性的，默认执行后删除
- `pip` 下载缓存保留在 `ARTISAN_PLUGIN_PIP_CACHE_DIR`

这保证了环境隔离，同时避免重复下载依赖。

## 隔离与限制

当前实现默认包含这些保护：

- 每次执行创建独立任务目录，脚本、payload、venv、tmp 都在任务目录内
- 插件子进程使用独立进程组，超时会终止整棵进程树
- Linux 下会对插件执行进程施加 `RLIMIT_CPU`、`RLIMIT_AS`、`RLIMIT_FSIZE`、`RLIMIT_NPROC`、`RLIMIT_NOFILE`
- 插件默认只继承白名单环境变量，宿主敏感变量不会自动传入
- `stdout` / `stderr` 总量有上限，超出会直接终止执行
- bundle 上传会校验 `manifest.json`、文件路径、文件数量和解压大小

说明：

- `keep_venv=true` 仅用于调试
- 如果需要额外环境变量，使用 `execution_policy.env`

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8001` | 服务端口 |
| `ARTISAN_PLUGIN_SERVER_PORT` | `8001` | 服务端口，优先于 `PORT` |
| `ARTISAN_PLUGIN_RUNTIME_DIR` | `./plugins_runtime` | 运行时目录根路径 |
| `ARTISAN_PLUGIN_VENV_ROOT` | `./plugins_runtime/venvs` | 临时 venv 根目录 |
| `ARTISAN_PLUGIN_SCRIPT_ROOT` | `./plugins_runtime/scripts` | 临时脚本目录 |
| `ARTISAN_PLUGIN_PIP_CACHE_DIR` | `./plugins_runtime/pip_cache` | pip 缓存目录 |
| `ARTISAN_PLUGIN_TIMEOUT` | `300000` | 默认执行总超时 |
| `ARTISAN_PLUGIN_VALIDATE_TIMEOUT` | `60000` | 默认验证总超时 |
| `ARTISAN_PLUGIN_IDLE_TIMEOUT` | `120000` | 默认执行空闲超时 |
| `ARTISAN_PLUGIN_VALIDATE_IDLE_TIMEOUT` | `30000` | 默认验证空闲超时 |
| `ARTISAN_PLUGIN_MAX_TIMEOUT` | `900000` | 单次执行最大总超时 |
| `ARTISAN_PLUGIN_MAX_IDLE_TIMEOUT` | `300000` | 单次执行最大空闲超时 |
| `ARTISAN_PLUGIN_MAX_BODY_BYTES` | `1048576` | 请求体大小限制 |
| `ARTISAN_PLUGIN_MAX_CONCURRENCY` | `8` | 最大并发数 |
| `ARTISAN_PLUGIN_PIP_INDEX_URL` | `https://pypi.tuna.tsinghua.edu.cn/simple` | 默认 pip 源 |
| `ARTISAN_PLUGIN_PIP_EXTRA_INDEX_URL` | 空 | 额外 pip 源 |
| `ARTISAN_PLUGIN_PIP_TRUSTED_HOST` | `pypi.tuna.tsinghua.edu.cn` | 默认 trusted host |
| `ARTISAN_PLUGIN_LOG_LEVEL` | `INFO` | 主服务日志级别 |
| `ARTISAN_PLUGIN_MAX_STDOUT_BYTES` | `1048576` | 单次执行 stdout 总输出上限 |
| `ARTISAN_PLUGIN_MAX_STDERR_BYTES` | `1048576` | 单次执行 stderr 总输出上限 |
| `ARTISAN_PLUGIN_PROCESS_CPU_SECONDS` | `120` | 插件进程 CPU 时间上限 |
| `ARTISAN_PLUGIN_PROCESS_MEMORY_BYTES` | `1610612736` | 插件进程地址空间上限 |
| `ARTISAN_PLUGIN_PROCESS_FSIZE_BYTES` | `52428800` | 插件进程可写文件大小上限 |
| `ARTISAN_PLUGIN_PROCESS_NPROC` | `64` | 插件进程可创建进程数上限 |
| `ARTISAN_PLUGIN_PROCESS_NOFILE` | `512` | 插件进程可打开文件数上限 |

## 本地运行

```bash
cd plugin_server_python
pip install -r requirements.txt
python app.py
```

## Docker

```bash
docker build -f plugin_server_python/Dockerfile -t artisan-plugin-server-python .
docker run --rm -p 8001:8001 artisan-plugin-server-python
```

## 说明

当前实现采用“每次请求一个新 venv”的保守模型，优点是隔离清晰、清理简单。代价是首次安装依赖会慢一些，但 `pip cache` 会降低重复下载成本。

流式执行时，子进程的 `phase/activity/stdout/stderr/pip/venv` 输出会同时进入 SSE 和主服务控制台日志。非流式接口仍会返回 phase-aware 的错误结构，但不会逐行回放控制台输出。
