from __future__ import annotations

import json
from io import BytesIO
import importlib.util
import sys
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient


def load_sandbox_app_module():
    sandbox_root = Path("sandbox").resolve()
    if str(sandbox_root) not in sys.path:
        sys.path.insert(0, str(sandbox_root))
    module_path = sandbox_root / "app.py"
    spec = importlib.util.spec_from_file_location("sandbox_app_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def build_bundle() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"runtime": "python", "entrypoint": "skill.py"}, ensure_ascii=False),
        )
        archive.writestr(
            "skill.py",
            "\n".join(
                [
                    "definition = {'name': 'bundle-demo', 'description': 'demo'}",
                    "def execute(params):",
                    "    return {'echo': params.get('value'), 'workspace': __import__('os').getenv('AGENT_RUNTIME_WORKSPACE_ID', '')}",
                ]
            ),
        )
    return buffer.getvalue()


def test_bundle_execute_accepts_skill_context_and_returns_execution_metadata(monkeypatch):
    sandbox_app = load_sandbox_app_module()

    async def fake_run_bundle_execute(plugin_id, bundle_bytes, params, policy, stdout_queue=None):
        assert plugin_id
        assert bundle_bytes
        assert params["value"] == 7
        assert policy["env"]["AGENT_RUNTIME_WORKSPACE_ID"] == "ws_bundle"
        return {"success": True, "data": {"ok": True}, "phase": "execute"}

    monkeypatch.setattr(sandbox_app, "run_bundle_execute", fake_run_bundle_execute)
    client = TestClient(sandbox_app.app)

    response = client.post(
        "/bundle/execute",
        data={
            "params": json.dumps({"value": 7}, ensure_ascii=False),
            "skill": json.dumps(
                {
                    "name": "bundle-demo",
                    "entrypoint": "skill.py",
                    "execution_policy": {"packages": []},
                    "required_secrets": {},
                },
                ensure_ascii=False,
            ),
            "context": json.dumps(
                {
                    "agent_id": "default",
                    "conversation_id": "conv_bundle",
                    "workspace_id": "ws_bundle",
                    "run_id": "run_bundle",
                    "user_id": "user_bundle",
                },
                ensure_ascii=False,
            ),
            "base_policy": json.dumps({"env": {"CUSTOM_FLAG": "1"}}, ensure_ascii=False),
        },
        files={"bundle": ("bundle.zip", build_bundle(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["execution"]["workspace_id"] == "ws_bundle"
    assert payload["execution"]["conversation_id"] == "conv_bundle"
    assert payload["execution"]["skill_name"] == "bundle-demo"
