---
name: web-fetch
description: Fetch a web page and extract readable text, metadata, and links for research workflows.
metadata:
  owner: runtime
  category: research
  agent_runtime:
    executable: true
    entrypoint: skill.py
    parameters_schema:
      type: object
      properties:
        url:
          type: string
          description: The absolute URL to fetch.
        max_chars:
          type: integer
          description: Maximum extracted text characters to return.
          default: 12000
      required:
        - url
      additionalProperties: false
    execution_policy:
      timeout_ms: 60000
      idle_timeout_ms: 20000
      packages:
        - requests==2.32.3
        - beautifulsoup4==4.12.3
---

# Web Fetch

Use this executable skill to inspect a specific source URL during research. Prefer primary sources and preserve the returned `url`, `title`, `description`, and extracted text for citation work.
