---
name: mcp-composition-demo
description: Demonstrate how a harness skill declares and composes a typed MCP tool dependency.
metadata:
  owner: runtime-example
  category: mcp-demo
  agent_runtime:
    mcp_dependencies:
      - alias: sum_numbers
        server_id: test-tools
        tool_name: add
        required: true
---

# MCP Composition Demo

The runtime binds `sum_numbers` to the allowlisted `test-tools/add` MCP tool when this skill is activated.

1. Call `sum_numbers` only when the user supplies both numbers.
2. Use the returned sum in a concise answer.
3. Do not invoke the generic `mcp` skill or choose a server manually for this task; the dependency is already declared and runtime-validated.
