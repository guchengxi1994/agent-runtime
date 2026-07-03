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
export OPENAI_API_KEY="你的 OpenAI API Key"
docker compose up --build
```

服务边界：

- `agent-runtime`: 暴露 `http://127.0.0.1:8010`
- `sandbox`: 只在 compose 网络内暴露 `http://sandbox:8001`
- `registry`: 通过 volume 挂载到主 runtime
- `sandbox-runtime`: sandbox 的 venv、pip cache 和临时执行目录 volume

## 本地启动

先启动 sandbox：

```bash
cd sandbox
pip install -r requirements.txt
python app.py
```

再启动 agent runtime：

```bash
pip install -r requirements.txt
set OPENAI_API_KEY=你的 OpenAI API Key
set AGENT_RUNTIME_SANDBOX_URL=http://127.0.0.1:8001
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
| `OPENAI_API_KEY` | 无 | OpenAI SDK 使用 |
| `AGENT_RUNTIME_HOST` | `0.0.0.0` | 服务监听地址 |
| `AGENT_RUNTIME_PORT` | `8010` | 服务端口 |
| `AGENT_RUNTIME_MODEL` | `gpt-4.1-mini` | OpenAI 模型 |
| `AGENT_RUNTIME_SANDBOX_URL` | `http://127.0.0.1:8001` | sandbox 地址 |
| `AGENT_RUNTIME_REGISTRY_DIR` | `./registry` | skill/agent 文件注册表 |
| `AGENT_RUNTIME_ADMIN_TOKEN` | 空 | 设置后管理接口要求 Bearer token |
| `AGENT_RUNTIME_MAX_RUNTIME_ROUNDS` | `6` | 单轮对话最大 runtime 调用轮次 |
| `AGENT_RUNTIME_REQUEST_TIMEOUT_SECONDS` | `600` | 调用 sandbox 的 HTTP 超时 |

## 优化细则

- 候选集必须先由 runtime 基于 agent、user、permission 过滤，模型只能在安全候选集中规划。
- Harness skill 只负责告诉模型“如何做”，不要在 loader 中硬编码“什么时候必须调用哪个工具”。
- Executable skill 是唯一可执行能力入口；小工具、小 skill、业务脚本都统一按 executable skill 暴露。
- Sandbox 必须是独立服务，主 runtime 不能内嵌 venv、pip install 或 subprocess 执行。
- 依赖、secret、timeout、venv cache key 都属于执行策略，不属于 prompt 文本。
- 用户输入请求必须是显式 runtime 状态，而不是普通闲聊回复；否则复杂 harness 无法稳定暂停和恢复。
- 审计记录要包含 `agent_id`、`conversation_id`、`run_id`、`skill_name`、执行耗时、依赖和错误阶段。
