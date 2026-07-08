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


def build_bundle_with_dotenv() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"runtime": "python", "entrypoint": "skill.py"}, ensure_ascii=False),
        )
        archive.writestr(".env", "PGHOST=bundle-pg\nPGPORT=5432\n")
        archive.writestr(
            "skill.py",
            "\n".join(
                [
                    "import os",
                    "definition = {'name': 'bundle-env-demo', 'description': 'demo'}",
                    "def execute(params):",
                    "    return {'pghost': os.getenv('PGHOST', ''), 'pgport': os.getenv('PGPORT', '')}",
                ]
            ),
        )
    return buffer.getvalue()


def build_bundle_reads_env() -> bytes:
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
                    "import os",
                    "definition = {'name': 'bundle-env-reader', 'description': 'demo'}",
                    "def execute(params):",
                    "    return {'pghost': os.getenv('PGHOST', ''), 'pgport': os.getenv('PGPORT', '')}",
                ]
            ),
        )
    return buffer.getvalue()


def test_runner_returns_argument_keys_trace(tmp_path):
    sandbox_root = Path("sandbox").resolve()
    if str(sandbox_root) not in sys.path:
        sys.path.insert(0, str(sandbox_root))
    runner_module = importlib.util.spec_from_file_location("sandbox_runner_test_module", sandbox_root / "runner.py")
    runner = importlib.util.module_from_spec(runner_module)
    assert runner_module and runner_module.loader
    runner_module.loader.exec_module(runner)

    script_path = tmp_path / "skill.py"
    payload_path = tmp_path / "payload.json"
    script_path.write_text(
        "definition = {'name': 'demo'}\n"
        "def execute(params):\n"
        "    return {'ok': params.get('value')}\n",
        encoding="utf-8",
    )
    payload_path.write_text(json.dumps({"action": "execute", "params": {"value": 9}}, ensure_ascii=False), encoding="utf-8")

    old_argv = sys.argv[:]
    try:
        sys.argv = ["runner.py", str(script_path), str(payload_path)]
        exit_code = __import__("asyncio").run(runner.main())
    finally:
        sys.argv = old_argv

    assert exit_code == 0


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
                    "tool_call_id": "call_bundle",
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
    assert payload["execution"]["tool_call_id"] == "call_bundle"
    assert payload["trace"]["argument_keys"] == ["value"]
    assert payload["trace"]["arguments_preview"]["value"] == 7


def test_bundle_execute_can_read_dotenv_from_bundle(monkeypatch):
    sandbox_app = load_sandbox_app_module()
    client = TestClient(sandbox_app.app)
    monkeypatch.setenv("PGHOST", "parent-pg")
    monkeypatch.setenv("PGPORT", "6543")

    response = client.post(
        "/bundle/execute",
        data={
            "params": json.dumps({}, ensure_ascii=False),
            "skill": json.dumps(
                {
                    "name": "bundle-env-demo",
                    "entrypoint": "skill.py",
                    "execution_policy": {"packages": []},
                    "required_secrets": {},
                },
                ensure_ascii=False,
            ),
            "context": json.dumps(
                {
                    "agent_id": "default",
                    "conversation_id": "conv_env",
                    "workspace_id": "ws_env",
                    "run_id": "run_env",
                    "user_id": "user_env",
                    "tool_call_id": "call_env",
                },
                ensure_ascii=False,
            ),
        },
        files={"bundle": ("bundle.zip", build_bundle_with_dotenv(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["pghost"] == "bundle-pg"
    assert payload["data"]["pgport"] == "5432"


def test_bundle_execute_inherits_parent_env(monkeypatch):
    sandbox_app = load_sandbox_app_module()
    client = TestClient(sandbox_app.app)
    monkeypatch.setenv("PGHOST", "parent-pg")
    monkeypatch.setenv("PGPORT", "6543")

    response = client.post(
        "/bundle/execute",
        data={
            "params": json.dumps({}, ensure_ascii=False),
            "skill": json.dumps(
                {
                    "name": "bundle-env-demo",
                    "entrypoint": "skill.py",
                    "execution_policy": {"packages": []},
                    "required_secrets": {},
                },
                ensure_ascii=False,
            ),
            "context": json.dumps(
                {
                    "agent_id": "default",
                    "conversation_id": "conv_env_parent",
                    "workspace_id": "ws_env_parent",
                    "run_id": "run_env_parent",
                    "user_id": "user_env_parent",
                    "tool_call_id": "call_env_parent",
                },
                ensure_ascii=False,
            ),
        },
        files={"bundle": ("bundle.zip", build_bundle_reads_env(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["pghost"] == "parent-pg"
    assert payload["data"]["pgport"] == "6543"


def test_bundle_execute_env_precedence(monkeypatch):
    sandbox_app = load_sandbox_app_module()
    client = TestClient(sandbox_app.app)
    monkeypatch.setenv("PGHOST", "parent-pg")
    monkeypatch.setenv("PGPORT", "6543")

    response = client.post(
        "/bundle/execute",
        data={
            "params": json.dumps({}, ensure_ascii=False),
            "skill": json.dumps(
                {
                    "name": "bundle-env-demo",
                    "entrypoint": "skill.py",
                    "execution_policy": {"packages": []},
                    "required_secrets": {},
                },
                ensure_ascii=False,
            ),
            "context": json.dumps(
                {
                    "agent_id": "default",
                    "conversation_id": "conv_env_override",
                    "workspace_id": "ws_env_override",
                    "run_id": "run_env_override",
                    "user_id": "user_env_override",
                    "tool_call_id": "call_env_override",
                },
                ensure_ascii=False,
            ),
            "base_policy": json.dumps({"env": {"PGHOST": "policy-pg", "PGPORT": "7654"}}, ensure_ascii=False),
        },
        files={"bundle": ("bundle.zip", build_bundle_with_dotenv(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["pghost"] == "policy-pg"
    assert payload["data"]["pgport"] == "7654"
