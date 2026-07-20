# Agent Runtime MVP

这是一个 harness-driven agent runtime。主服务只负责规划、候选 skill 路由、权限、secret 声明、审计和会话状态；所有 Python 代码执行交给独立的 `sandbox` 服务。

## 核心口径

- `Skill = harness and/or executable capability`。不再维护单独的 `registry/tools`。
- Harness skill 是 `SKILL.md` 里的流程、约束、领域知识和规划提示，模型通过 `activate_skill` 按需加载全文。
- Executable skill 是带 `metadata.agent_runtime.executable=true` 的 skill 包，包含 `parameters_schema`、`execution_policy`、`required_secrets` 和 Python `entrypoint`。
- OpenAI 接口层仍使用 function tool call 机制，但可调用对象来自 executable skill，而不是独立 tool registry。
- `agent_runtime` 不创建 venv、不安装依赖、不执行脚本；它只把已授权 skill 的脚本、参数和执行策略发给 `sandbox /skills/execute`。
- `sandbox` 是 compose 内部服务，负责 venv 缓存、依赖安装、超时、stdout/stderr 限制和执行结果。
- `request_user_input` 是 runtime 内置动作，不进 sandbox。模型缺少必要输入时必须暂停并返回 `waiting_for_user`。

## 调用流程

1. 客户端调用 `POST /chat`，可指定 `agent_id`、`conversation_id`、`skill_ids` 和 `user` 上下文。
2. Runtime 按 agent、user 权限和显式 skill 选择生成安全候选 skill 集。
3. 模型可以直接调用 executable skill，也可以先调用 `activate_skill` 加载 harness，再按 harness 继续规划。
4. executable skill 调用会被转发到 sandbox 的 `POST /skills/execute`。
5. 如果信息不足，模型调用 `request_user_input`，runtime 返回 `status=waiting_for_user` 和 `requested_inputs`。
6. 用户补充信息时沿用同一个 `conversation_id` 发送下一条消息，runtime 会把它视为对上一次输入请求的回答并继续规划。

## Docker Compose

推荐用 compose 启动主 runtime 和内部 sandbox：

```bash
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY、AGENT_RUNTIME_MODEL、OPENAI_BASE_URL 等
docker compose up --build
```

服务边界：

- `agent-runtime`: 暴露 `http://127.0.0.1:8010`
- `sandbox`: 只在 compose 网络内暴露 `http://sandbox:8001`
- `registry`: 通过 volume 挂载到主 runtime
- `sandbox-runtime`: sandbox 的 venv、pip cache 和临时执行目录 volume

## 本地启动

开发环境建议用 `.env` 管理变量：

```bash
cp .env.example .env
# 编辑 .env
```

`agent_runtime` 和 `sandbox` 都会从当前目录或父目录自动加载 `.env`。本地 `.env` 会覆盖同名继承环境变量，避免 IDE/终端进程里残留的旧 key 悄悄生效。真实 `.env` 已被 `.gitignore` 忽略，不要提交。

先启动 sandbox：

```bash
cd sandbox
pip install -r requirements.txt
python app.py
```

再启动 agent runtime：

```bash
pip install -r requirements.txt
python -m agent_runtime
```

浏览器打开 `http://127.0.0.1:8010/` 可以使用内置调试前端。

## Executable Skill

skill 使用目录式包：

```text
registry/skills/calculator/
  SKILL.md
  skill.py
```

`SKILL.md` 示例：

```markdown
---
name: calculator
description: Execute deterministic arithmetic expressions.
metadata:
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        expression:
          type: string
      required:
        - expression
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
    required_secrets:
      API_TOKEN: env:UPSTREAM_API_TOKEN
---

# Calculator

Use this executable skill for deterministic arithmetic. Do not invent missing numbers.
```

`skill.py` 必须定义 `definition` 和 `execute(params)`。依赖包写在 `execution_policy.packages`；API token/header 等 secret 只声明在 `required_secrets`，由 sandbox 从自身环境变量解析并注入进程环境。

## Research Skills

仓库包含 `academic-deep-research` harness skill，以及两个研究原子能力：

- `web-search`: 通过 `TAVILY_API_KEY`、`SERPER_API_KEY` 或 `BING_SEARCH_API_KEY` 中任意一个搜索 provider 做 source discovery。
- `web-fetch`: 抓取指定 URL 并提取正文、metadata 和链接。

如果未配置搜索 provider，`web-search` 会返回 `missing_search_provider`，模型应要求用户或 operator 配置 `.env` 后再继续研究。

## Harness Skill

Harness skill 不需要 `executable=true`，只提供规划说明：

```markdown
---
name: math-assistant
description: Plan deterministic arithmetic work and call calculator when execution is needed.
metadata:
  owner: runtime-example
---

# Math Assistant

1. 复杂计算优先调用 `calculator`。
2. 如果缺少数字或公式，先调用 `request_user_input`。
3. 根据执行结果用用户语言解释答案。
```

## 用户输入请求

`request_user_input` 是内置 runtime function：

- 模型发现缺少必要数据时调用它，而不是猜测参数。
- Runtime 返回 `ChatResponse.status = "waiting_for_user"`。
- `ChatResponse.requested_inputs` 描述需要补充的字段。
- 当前 conversation 会记录 `pending_input_request`。
- 下一次同 `conversation_id` 的用户消息会被当作补充输入，runtime 清除 pending 状态并继续规划。
- 这个动作会进入 trace，但不会进入 sandbox，也不会产生代码执行。

## Step Trace

`ChatResponse` 同时返回 `steps` 和 `tool_calls`。前端和控制台都应优先使用 `steps` 判断当前阶段类型：

- `thinking`: runtime 正在调用模型规划下一步。默认不暴露隐藏推理文本；如果兼容模型返回 `reasoning_content`，且 `AGENT_RUNTIME_EXPOSE_REASONING_CONTENT=true`，才会把它放进 `detail`。
- `runtime_call`: runtime 内置动作，例如 `activate_skill`、`read_skill_resource` 或权限拒绝。
- `sandbox_execution`: executable skill 已转发到 sandbox 执行。
- `waiting_for_user`: 通过 `request_user_input` 暂停，等待用户补充字段。
- `final`: 生成最终回复或 runtime 达到轮次上限。

控制台默认输出高信号日志：

- `TOOL_USED run_id=... tool=calculator ...`: 已执行 sandbox 工具。
- `RUN_DONE run_id=... used_tool=true tools=['calculator'] ...`: 本轮是否用了工具的总结。
- `INPUT_REQUIRED run_id=...`: runtime 暂停等待用户输入。

完整 step 明细会降到 DEBUG：设置 `AGENT_RUNTIME_LOG_LEVEL=DEBUG` 后可以看到 `runtime_step step_id=... kind=...`。

## API

- `GET /health`: runtime 状态和 executable skill 数量
- `GET /skills`: 当前用户可见 skill summary
- `GET /agents`: 当前用户可见 agent summary
- `GET /executions`: 最近 sandbox execution metadata
- `POST /chat`: 对话入口
- `POST /admin/reload`: 重载 registry
- `POST /admin/agents`: 写入 agent 定义
- `POST /admin/skills`: 写入 skill 包的 `SKILL.md`

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 无 | OpenAI-compatible provider API key |
| `OPENAI_BASE_URL` | 空 | OpenAI-compatible provider base URL |
| `AGENT_RUNTIME_HOST` | `0.0.0.0` | 服务监听地址 |
| `AGENT_RUNTIME_PORT` | `8010` | 服务端口 |
| `AGENT_RUNTIME_MODEL` | `gpt-4.1-mini` | OpenAI 模型 |
| `AGENT_RUNTIME_MEMORY_MODEL` | 空（回退到主模型） | 可选的低成本 memory 增量提取模型；复用主模型的 API key 和 base URL |
| `AGENT_RUNTIME_MODEL_CONTEXT_TOKENS` | `131072` | 当前模型的上下文窗口长度；按实际 provider/model 配置 |
| `AGENT_RUNTIME_CONTEXT_COMPACTION_THRESHOLD` | `0.8` | 下一次请求预计占用达到上下文窗口比例时，先压缩旧历史再调用模型 |
| `AGENT_RUNTIME_MEMORY_ENABLED` | `true` | 是否启用 workspace 增量记忆 |
| `AGENT_RUNTIME_MEMORY_CONTEXT_TOKENS` | `4000` | 新 conversation 注入 durable memory 的最大估算 token |
| `AGENT_RUNTIME_MEMORY_MAX_ENTRIES` | `200` | 单 workspace 最多保留的 memory 条目数 |
| `AGENT_RUNTIME_REASONING_EFFORT` | 空 | 可选，仅在 provider/model 支持时传给 Chat Completions |
| `AGENT_RUNTIME_EXPOSE_REASONING_CONTENT` | `false` | 是否在 `steps.kind=thinking` 中展示兼容接口返回的 reasoning 文本 |
| `AGENT_RUNTIME_SANDBOX_URL` | `http://127.0.0.1:8001` | sandbox 地址 |
| `AGENT_RUNTIME_REGISTRY_DIR` | `./registry` | skill/agent 文件注册表 |
| `AGENT_RUNTIME_ADMIN_TOKEN` | 空 | 设置后管理接口要求 Bearer token |
| `AGENT_RUNTIME_MAX_RUNTIME_ROUNDS` | `6` | 单轮对话最大 runtime 调用轮次 |
| `AGENT_RUNTIME_REQUEST_TIMEOUT_SECONDS` | `600` | 调用 sandbox 的 HTTP 超时 |
| `AGENT_RUNTIME_LOG_LEVEL` | `INFO` | runtime 控制台日志等级 |
| `AGENT_RUNTIME_HTTP_LOG_LEVEL` | `WARNING` | `httpx/httpcore` 请求日志等级 |

启动时 runtime 会输出一条 OpenAI client 配置日志，包含 `model`、`base_url`、`base_url_source`、`api_key_present`、`api_key_source`、脱敏后的 `api_key_masked`、key 长度、`api_key_sha256` 短指纹和 `env_file`。不会输出完整 API key。

上下文压缩由 runtime 控制，不依赖模型自行判断。每轮模型返回的 `prompt_tokens/completion_tokens` 会累计到 conversation 状态；上一轮真实 `prompt_tokens` 用来校准下一轮本地估算。压缩触发判断使用“下一次预计 prompt tokens / 模型上下文长度”，而不是累计计费 token。达到阈值后，runtime 调用同一模型把当前轮之前的旧消息压成可恢复摘要，保留当前用户输入和本轮 tool-call 链。

每个 workspace 的长期记忆保存在 `artifacts/workspaces/<workspace_id>/MEMORY.md`，其目标是让新模型对话恢复用户任务，而不是保存报告正文。runtime 会在回复前确定性写入原始目标和当前用户问题；`AGENT_RUNTIME_MEMORY_MODEL`（未配置则使用主模型）在回复后异步补充约束、决策、进度、待办和 artifact 指针。后台 job 先持久化到 `memory_jobs/*.json`，按 workspace 串行执行并自动重试 3 次；失败状态可通过 `GET /workspaces/{workspace_id}/memory` 查看，并通过 `POST /workspaces/{workspace_id}/memory/retry` 或前端按钮重试。新 conversation 只注入带明确 `use_when` 的 resume context，旧的无用途条目保留审计但不注入模型。

## 优化细则

- 候选集必须先由 runtime 基于 agent、user、permission 过滤，模型只能在安全候选集中规划。
- Harness skill 只负责告诉模型“如何做”，不要在 loader 中硬编码“什么时候必须调用哪个工具”。
- Executable skill 是唯一可执行能力入口；小工具、小 skill、业务脚本都统一按 executable skill 暴露。
- Sandbox 必须是独立服务，主 runtime 不能内嵌 venv、pip install 或 subprocess 执行。
- 依赖、secret、timeout、venv cache key 都属于执行策略，不属于 prompt 文本。
- 用户输入请求必须是显式 runtime 状态，而不是普通闲聊回复；否则复杂 harness 无法稳定暂停和恢复。
- thinking 必须作为可观测阶段单独标记；默认只展示阶段状态，不把隐藏推理混到最终回答或 sandbox 日志里。
- 审计记录要包含 `agent_id`、`conversation_id`、`run_id`、`skill_name`、执行耗时、依赖和错误阶段。
