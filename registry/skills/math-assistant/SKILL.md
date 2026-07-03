---
name: math-assistant
description: Use deterministic tools for arithmetic, percentages, numeric comparison, and aggregation.
metadata:
  owner: runtime-example
  category: reasoning
---

# Math Assistant

Use this skill when the user asks for arithmetic, numeric comparison, percentages, averaging, summation, or other deterministic calculations.

## Procedure

1. When the `calculator` tool is available, call it for non-trivial arithmetic instead of calculating mentally.
2. Use the exact numeric expression from the user when possible.
3. If the user request is ambiguous, ask for the missing numbers or formula before calling the tool.
4. After tool execution, explain the result briefly in the user's language.

## Constraints

- Do not invent numbers that were not provided by the user.
- Do not claim a calculation succeeded unless the tool result reports success.
- If the tool returns an error, explain the error and ask the user to revise the expression.
