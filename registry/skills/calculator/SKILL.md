---
name: calculator
description: Execute deterministic arithmetic expressions for math, percentages, comparisons, and aggregation.
metadata:
  owner: runtime-example
  category: execution
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        expression:
          type: string
          description: A Python arithmetic expression, for example 'sum([1, 2, 3]) / 3'.
      required:
        - expression
      additionalProperties: false
    execution_policy:
      timeout_ms: 30000
      idle_timeout_ms: 10000
      packages: []
---

# Calculator

Use this executable skill for deterministic arithmetic, percentages, numeric comparison, averaging, summation, and small math expressions.

Input must be a single arithmetic expression. Do not invent missing numbers.
