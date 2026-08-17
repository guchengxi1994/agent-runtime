# Test MCP Server

This stdio MCP server is used by the runtime's default `test-tools` registry entry. It exposes four deterministic tools:

- `add(a, b)`
- `echo(text, uppercase=false)`
- `reverse_text(text)`
- `server_info()`

The Docker Compose `mcp-gateway` service starts this server as a child process when the agent activates `test-tools`; do not start it separately when using Compose.

For local testing without Docker:

```bash
pip install -r mcp_gateway/requirements.txt
python -m uvicorn mcp_gateway.app:app --host 127.0.0.1 --port 8002
```

Then start the agent runtime with `AGENT_RUNTIME_MCP_GATEWAY_URL=http://127.0.0.1:8002`. The gateway launches `python mcp_server/server.py` on demand.
