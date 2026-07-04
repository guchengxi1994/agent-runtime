---
name: web-search
description: Search the web for research sources using a configured search provider.
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

Use this executable skill for broad source discovery. Prefer `count=20` for exhaustive research phases. If the result reports `missing_search_provider`, ask the user or operator to configure one of `TAVILY_API_KEY`, `SERPER_API_KEY`, or `BING_SEARCH_API_KEY` in the sandbox environment.
