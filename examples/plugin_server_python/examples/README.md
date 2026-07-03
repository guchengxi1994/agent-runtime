# Python Plugin Runtime Demos

这些示例主要用于验证 `plugin_server_python` 的隔离和限制是否生效，不是业务型插件。

## 文件说明

- `hello_runtime_demo.py`
  - 最基础的成功执行样例
  - 参数文件：`hello_runtime_demo.json`
- `web_page_extract_markdown_demo.py`
  - 依赖 `requests + beautifulsoup4 + markdownify`
  - 抓取网页并转成 Markdown 片段
  - 参数文件：`web_page_extract_markdown_demo.json`
- `pandas_sales_analytics_demo.py`
  - 依赖 `pandas`
  - 对销售明细做聚合分析
  - 参数文件：`pandas_sales_analytics_demo.json`
- `numpy_svd_demo.py`
  - 依赖 `numpy`
  - 随机生成方阵并做奇异值分解，返回矩阵、奇异值、`U`、`V^T` 和重建误差
  - 参数文件：`numpy_svd_demo.json`
- `yaml_contract_validator_demo.py`
  - 依赖 `PyYAML + pydantic`
  - 解析 YAML 并做结构化校验
  - 参数文件：`yaml_contract_validator_demo.json`
- `image_card_pillow_demo.py`
  - 依赖 `Pillow`
  - 生成 PNG 卡片并返回 base64
  - 参数文件：`image_card_pillow_demo.json`
- `env_and_workspace_demo.py`
  - 验证任务目录隔离、`TMPDIR` / `HOME` 路径、环境变量白名单
  - 参数文件：`env_and_workspace_demo.json`
- `process_tree_timeout_demo.py`
  - 验证超时后是否整棵进程树一起被清理
  - 参数文件：`process_tree_timeout_demo.json`
- `stdout_flood_demo.py`
  - 验证 `ARTISAN_PLUGIN_MAX_STDOUT_BYTES` 是否拦截大日志输出
  - 参数文件：`stdout_flood_demo.json`
- `memory_pressure_demo.py`
  - 验证 `ARTISAN_PLUGIN_PROCESS_MEMORY_BYTES` 是否拦截大内存申请
  - 参数文件：`memory_pressure_demo.json`

## 依赖安装类示例

### 1. 抓取网页并抽取 Markdown

脚本：`web_page_extract_markdown_demo.py`

```json
{
  "packages": [
    "requests==2.32.3",
    "beautifulsoup4==4.12.3",
    "markdownify==0.13.1"
  ]
}
```

### 2. pandas 聚合分析

脚本：`pandas_sales_analytics_demo.py`

```json
{
  "packages": [
    "pandas==2.2.3"
  ]
}
```

### 3. YAML 契约校验

脚本：`yaml_contract_validator_demo.py`

```json
{
  "packages": [
    "PyYAML==6.0.2",
    "pydantic==2.11.4"
  ]
}
```

### 4. numpy 奇异值分解

脚本：`numpy_svd_demo.py`

```json
{
  "packages": [
    "numpy==2.1.3"
  ]
}
```

### 5. 动态生成图片

脚本：`image_card_pillow_demo.py`

```json
{
  "packages": [
    "Pillow==10.4.0"
  ]
}
```

## 建议测试方式

### 1. 基础执行

使用 `hello_runtime_demo.py`，确认返回成功。

直接可用：

- 脚本：`hello_runtime_demo.py`
- 参数：`hello_runtime_demo.json`

### 2. 环境变量与工作目录隔离

使用 `env_and_workspace_demo.py`，并在 `execution_policy.env` 中注入：

```json
{
  "env": {
    "DEMO_ALLOWED": "visible"
  }
}
```

预期：

- `DEMO_ALLOWED` 可见
- `DEMO_SECRET_TOKEN`、`INTERNAL_SERVICE_TOKEN`、`ARTISAN_OPENAI_API_KEY` 默认不可见
- `cwd`、`tmp_dir`、`home_dir` 都应落在单次任务目录内

### 3. 进程树清理

使用 `process_tree_timeout_demo.py`，给一个小超时，例如：

```json
{
  "timeout_ms": 3000
}
```

预期：

- 请求在 3 秒左右超时失败
- 父进程和子进程都被清理，不应残留孤儿子进程

### 4. stdout 限流

使用 `stdout_flood_demo.py`，默认参数通常就会超过 1MB。

预期：

- 执行失败
- 错误类似 `stdout exceeded ... bytes`

### 5. 内存限制

使用 `memory_pressure_demo.py`，逐步提高 `megabytes`，例如从 `128` 到 `768`。

预期：

- 低于限制时成功
- 超过限制后失败，通常表现为 `MemoryError` 或子进程异常退出

## 使用方式

所有 `.json` 文件都已经是完整请求体，结构统一如下：

```json
{
  "params": {},
  "execution_policy": {}
}
```

如果你走 `POST /execute`，把示例脚本内容放到 `script` 字段，再把对应 `.json` 里的 `params` 和 `execution_policy` 合进去即可。

如果你走上层 backend 的插件执行代理接口，可以先把脚本存进 backend 的插件表，再由 backend 转发到运行时。
