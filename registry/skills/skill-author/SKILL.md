---
name: skill-author
description: Design and create validated agent-runtime skill packages from a user's natural-language capability request.
metadata:
  owner: runtime
  category: authoring
  agent_runtime:
    capabilities:
      - skill-authoring
      - executable-skill-design
      - mcp-composition
---

# Skill Author

Create the smallest reusable agent-runtime skill package that satisfies the requested capability. Prefer a harness skill; add executable Python only for deterministic local processing or an isolated integration.

## Workflow

1. Check the skill catalog for an existing capability before creating a duplicate.
2. Choose harness or executable. Ask for only blocking information: external side effects, authentication, a required MCP tool, or required input semantics.
3. Build the complete package. Call `create_skill_package`; do not only paste proposed files into chat.
4. Default to `overwrite: false`. Ask for explicit confirmation before replacing an existing package.

`SKILL.md` is required. Its frontmatter `name` is also the directory name and must match `^[a-z0-9][a-z0-9-]{0,63}$`. The Markdown body must state when and how the agent should use the skill.

## Frontmatter Contract

Use `metadata.agent_runtime` for runtime behavior. `owner` and `category` are descriptive only; omit optional fields that do not change behavior.

| Field | Meaning | Use |
| --- | --- | --- |
| `name` | Unique package and tool name. | Required; equals directory name. |
| `description` | Short model-facing purpose. | Required; describe what the skill does. |
| `metadata.owner`, `metadata.category` | Human ownership and grouping. | Optional. |
| `agent_runtime.enabled` | Whether the skill can be exposed. | Default `true`; set `false` only to disable it. |
| `agent_runtime.capabilities` | String capability hints for discovery. | Optional list, for example `text-normalization`. |
| `agent_runtime.permissions` | Access limits: `tenant_ids`, `roles`, `scopes`. | Omit unless the operator specifies an access policy. |
| `agent_runtime.executable` | Makes the skill an OpenAI-callable sandbox function. | Default `false`. |
| `agent_runtime.entrypoint` | Relative UTF-8 Python file in the package. | Default `skill.py`; required as a supplied file when executable. |
| `agent_runtime.parameters_schema` | JSON Schema shown to the model for an executable tool call. | Required for executable skills; use object type and `additionalProperties: false`. |
| `agent_runtime.execution_policy` | Sandbox timeout, dependencies, and non-secret environment. | Only for executable skills. |
| `agent_runtime.required_secrets` | Maps script environment variables to sandbox host environment variables. | Never include secret values in package files. |
| `agent_runtime.mcp_dependencies` | Declared typed MCP tools for a harness skill. | Never combine with `executable: true`. |

Use `agent_runtime` rather than the legacy `agent-runtime` spelling. Do not add unknown runtime fields merely as notes; put prose in the Markdown body.

## Harness And MCP Skills

A harness does not need `skill.py`. It guides the model and can bind atomic MCP tools from one or more configured servers. Each dependency must have a unique `alias`; the alias becomes the callable tool name after activation.

```yaml
---
name: customer-triage
description: Triage a customer request with configured customer and case MCP tools.
metadata:
  owner: support-platform
  category: support
  agent_runtime:
    capabilities: [customer-triage]
    mcp_dependencies:
      - alias: lookup_customer
        server_id: customer-data
        tool_name: lookup
        required: true
      - alias: open_case
        server_id: case-management
        tool_name: create_case
        required: false
---

# Customer Triage

Call `lookup_customer` before proposing an account-specific action. Call `open_case` only after the user explicitly requests a case.
```

`server_id` and `tool_name` must already be registered and allowlisted by the runtime. Do not make `skill.py` connect to the MCP gateway; the runtime performs discovery, permissions checks, and typed calls.

## Executable Skills

An executable skill needs two compatible contracts:

1. `parameters_schema` controls the tool arguments the model may send.
2. `skill.py` exposes `definition` for sandbox validation and `execute(params)` for the implementation.

Keep the names, types, required flags, defaults, and descriptions aligned across both. `parameters_schema` is the source for model invocation; `definition.parameters` is required runtime metadata, not a replacement schema.

### `parameters_schema`

Use a JSON Schema object with `properties`, `required`, and `additionalProperties: false`. Support the normal JSON value types. Put defaults on optional properties when a stable default exists.

```yaml
parameters_schema:
  type: object
  properties:
    text:
      type: string
      description: Text to normalize.
    uppercase:
      type: boolean
      description: Return upper-case text after whitespace normalization.
      default: false
  required: [text]
  additionalProperties: false
```

### `skill.py`

`definition` must be a top-level object with non-empty `name`, `description`, and a `parameters` list. Every parameter item must include:

| Field | Meaning |
| --- | --- |
| `name` | Matches a `parameters_schema.properties` key. |
| `type` | One of `string`, `number`, `integer`, `boolean`, `array`, or `object`. |
| `description` | Non-empty explanation of the accepted value. |
| `required` | Boolean matching the schema's `required` list. |
| `enum` | Optional list of allowed values. |

`category` is optional but recommended. `execute(params)` may be synchronous or async, must validate runtime values defensively, and must return JSON-compatible data. Raise `ValueError` for invalid input; do not run shell commands or install packages at execution time.

```python
definition = {
    "name": "text-normalizer",
    "description": "Normalize whitespace and optionally upper-case text.",
    "category": "text",
    "parameters": [
        {
            "name": "text",
            "type": "string",
            "description": "Text to normalize.",
            "required": True,
        },
        {
            "name": "uppercase",
            "type": "boolean",
            "description": "Whether to return upper-case text.",
            "required": False,
        },
    ],
}


def execute(params):
    text = str(params.get("text", "")).strip()
    if not text:
        raise ValueError("text is required")
    normalized = " ".join(text.split())
    if bool(params.get("uppercase", False)):
        normalized = normalized.upper()
    return {"text": normalized}
```

### Complete Executable Package

Submit this package shape to `create_skill_package` as `skill_markdown` plus the `skill.py` file above:

```yaml
---
name: text-normalizer
description: Normalize whitespace in a text value and optionally return upper-case output.
metadata:
  owner: runtime
  category: text
  agent_runtime:
    executable: true
    entrypoint: skill.py
    capabilities: [text-normalization]
    parameters_schema:
      type: object
      properties:
        text:
          type: string
          description: Text to normalize.
        uppercase:
          type: boolean
          description: Return upper-case text after whitespace normalization.
          default: false
      required: [text]
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Text Normalizer

Use this skill when deterministic whitespace cleanup is required. Do not alter wording other than whitespace or the requested case conversion.
```

## Sandbox Dependencies And Secrets

Declare third-party Python packages in `execution_policy.packages`; the sandbox creates or reuses an isolated virtual environment and runs `pip install` before execution. Use exact versions, for example `requests==2.32.3`. Standard-library-only code uses `packages: []`.

```yaml
execution_policy:
  timeout_ms: 60000           # Positive milliseconds; server limits still apply.
  idle_timeout_ms: 20000      # Maximum quiet time before the sandbox stops work.
  packages:
    - requests==2.32.3
    - beautifulsoup4==4.12.3
  # Optional operator-controlled package index settings. Normally omit these.
  # pip_index_url: https://packages.example.internal/simple
  # pip_extra_index_url: https://pypi.org/simple
  # pip_trusted_host: packages.example.internal
```

Do not put `pip install`, package bootstrap code, or unpinned dependencies in `skill.py`. Do not use `execution_policy.env` for credentials. It is only for non-secret, static environment values when the operator has specified them.

For a secret, map the name consumed by Python to an environment variable configured on the sandbox host. The runtime checks it before execution and injects the value only into the sandbox process:

```yaml
required_secrets:
  CUSTOMER_API_KEY: env:CUSTOMER_API_KEY
```

Read it from Python with `os.environ["CUSTOMER_API_KEY"]`; never place the value in `SKILL.md`, `skill.py`, `files`, or tool arguments.

Supporting UTF-8 text files may be submitted as `files`, such as `references/*.md`, templates, JSON, SQL, and Python modules. Never put `SKILL.md` in `files`, and do not submit hidden files, path traversal, binary assets, certificates, or key files.

After creation, state the skill name, type, input contract, files created, dependencies, MCP bindings, and any operator action still required.
