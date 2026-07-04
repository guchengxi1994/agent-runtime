---
name: web-search-quark
description: Fallback no-API-key web search using Quark/Shenma HTML results. Use when the primary web-search skill cannot proceed because Tavily, Serper, or Bing API keys are missing, invalid, rate-limited, or otherwise unavailable.
metadata:
  owner: runtime
  category: research
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        query:
          type: string
          description: Search query.
        count:
          type: integer
          description: Number of results to request.
          default: 10
        freshness:
          type: string
          description: "Optional freshness hint: day, week, month, or year."
      required:
        - query
      additionalProperties: false
    execution_policy:
      timeout_ms: 60000
      idle_timeout_ms: 20000
      packages:
        - requests==2.32.3
    required_secrets: {}
---

# Quark Web Search

Use this executable skill as a best-effort fallback when `web-search` cannot run because a configured API provider is missing or failing.

It does not require an API key. It fetches Quark/Shenma HTML search result pages and parses embedded hydration JSON. Results may be less stable than API-backed search and may be blocked by CAPTCHA or rate limits.

Prefer `web-search` when a configured provider is available. Use `web-search-quark` to keep research moving when API-backed search is unavailable.
