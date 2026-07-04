---
name: web-search
description: API-backed web search for research sources using Tavily, Serper, or Bing when configured. If API keys are missing, invalid, rate-limited, or this skill otherwise cannot proceed, use the no-API-key web-search-quark skill as an alternate search capability.
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
          description: Optional freshness hint such as day, week, month, year.
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

# Web Search

Use this executable skill for broad source discovery when an API-backed provider is configured. Prefer `count=20` for exhaustive research phases. If the result reports `missing_search_provider` or a provider error, continue with another suitable search skill from the available catalog such as `web-search-quark`, or ask the operator to configure `TAVILY_API_KEY`, `SERPER_API_KEY`, or `BING_SEARCH_API_KEY`.
