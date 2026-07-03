# Agent Runtime MVP

这是一个服务端 agent runtime：用户通过 `POST /chat` 对话，服务端加载企业可控的 skill/tool 注册表，用 OpenAI SDK 做工具调用决策，并把实际工具代码交给 `examples/plugin_server_python` 的隔离 runner 执行。

## 架构

- `agent_runtime`: FastAPI 对话服务，负责会话、skill discovery、skill activation、tool calling、权限占位。
- `registry/skills`: skill 注册表。skill 是 `SKILL.md` harness 文档，不是代码，不绑定工具。
- `registry/tools`: tool 注册表。tool 包含 OpenAI function schema、Python 脚本和执行策略。
- `examples/plugin_server_python`: 已有隔离执行服务，负责临时 venv、依赖安装、超时和 stdout/stderr 限制。

## 启动

先启动隔离 runner：

```bash
cd examples/plugin_server_python
pip install -r requirements.txt
python app.py
```

再启动 agent runtime：

```bash
cd /Users/guchengxi/Desktop/projects/agent-runtime
pip install -r requirements.txt
export OPENAI_API_KEY="你的 OpenAI API Key"
python -m agent_runtime
```

默认端口：

- runner: `http://127.0.0.1:8001`
- agent runtime: `http://127.0.0.1:8010`

浏览器打开 `http://127.0.0.1:8010/` 可以使用内置前端入口。页面会展示当前 skill harness catalog、tool catalog、对话区和 tool trace。

## 对话调用

```bash
curl -s http://127.0.0.1:8010/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "计算 (123 + 456) / 3"
  }'
```

运行时会把 skill catalog 的 `name + description` 暴露给模型。模型认为某个 skill 相关时，会先调用内置 `activate_skill` 读取完整 `SKILL.md` harness，再根据 harness 和可用 tool catalog 自己决定是否调用工具。

## 注册 Tool

MVP 支持通过管理接口写入文件注册表。生产环境应设置 `AGENT_RUNTIME_ADMIN_TOKEN` 并在网关侧补充认证、审计和租户隔离。

```bash
curl -s http://127.0.0.1:8010/admin/tools \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer your-admin-token' \
  -d @registry/tools/calculator.json
```

tool 的关键字段：

- `name`: OpenAI function name，只允许 `A-Z a-z 0-9 _ -`。
- `parameters_schema`: JSON Schema，给模型生成 tool arguments 用。
- `script`: 交给隔离 runner 执行的 Python 代码，必须包含 `definition` 和 `execute(params)`。
- `execution_policy`: 传给 runner 的超时、依赖、环境变量策略。
- `permissions`: 权限占位，支持 `tenant_ids`、`roles`、`scopes`。

## Skill Harness

skill 使用目录式包：

```text
registry/skills/math-assistant/
  SKILL.md
  references/
    optional-domain-notes.md
```

`SKILL.md` 必须包含极小 YAML frontmatter：

```markdown
---
name: math-assistant
description: Use deterministic calculation practices for arithmetic and percentages.
metadata:
  owner: runtime-example
---

# Math Assistant

这里写完整 harness：什么时候使用、分析步骤、如何判断是否需要工具、工具调用顺序、行业术语和输出要求。
```

后端只读取 `name` 和 `description` 做 discovery，不解析正文里的“什么时候调用什么工具”。这些决策由模型在激活 skill 后根据 harness 自己完成。`metadata.agent_runtime.permissions` 可作为本 runtime 的私有扩展，用于企业权限控制。

## 企业权限预留

当前 MVP 做了轻量权限占位：

- `ChatRequest.user.tenant_id` 对应 `permissions.tenant_ids`。
- `ChatRequest.user.roles` 对应 `permissions.roles`。
- `ChatRequest.user.scopes` 必须覆盖 `permissions.scopes`。

后续建议把权限检查接到企业 IdP、租户数据目录、工具级审计日志和审批流。MVP 不做数据面强隔离，不应直接暴露到公网。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 无 | OpenAI SDK 使用 |
| `AGENT_RUNTIME_HOST` | `0.0.0.0` | 服务监听地址 |
| `AGENT_RUNTIME_PORT` | `8010` | 服务端口 |
| `AGENT_RUNTIME_MODEL` | `gpt-4.1-mini` | OpenAI 模型 |
| `AGENT_RUNTIME_PLUGIN_SERVER_URL` | `http://127.0.0.1:8001` | 隔离 runner 地址 |
| `AGENT_RUNTIME_REGISTRY_DIR` | `./registry` | tool/skill 文件注册表 |
| `AGENT_RUNTIME_ADMIN_TOKEN` | 空 | 设置后管理接口要求 Bearer token |
| `AGENT_RUNTIME_MAX_TOOL_ROUNDS` | `6` | 单轮对话最大工具调用轮次 |
