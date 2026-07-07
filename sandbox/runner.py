from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

RUNNER_RESULT_PREFIX = "__ARTISAN_PLUGIN_RESULT__="


def load_module(script_path: Path):
    script_dir = str(script_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec = importlib.util.spec_from_file_location(f"plugin_module_{script_path.stem}", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load plugin script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def format_exception_payload(exc: Exception) -> dict[str, Any]:
    error_type = exc.__class__.__name__
    message = str(exc).strip()
    if message:
        return {
            "success": False,
            "error": f"{error_type}: {message}",
            "error_type": error_type,
        }
    return {
        "success": False,
        "error": error_type,
        "error_type": error_type,
    }


async def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"{RUNNER_RESULT_PREFIX}"
            + json.dumps({"success": False, "error": "Usage: runner.py <script.py> <payload.json>"}, ensure_ascii=False)
        )
        return 2

    script_path = Path(sys.argv[1]).resolve()
    payload_path = Path(sys.argv[2]).resolve()

    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        module = load_module(script_path)
        action = payload.get("action")
        params = payload.get("params", {})

        if action == "getDefinition":
            definition = getattr(module, "definition", None)
            if definition is None:
                raise RuntimeError("Plugin script must define `definition`")
            print(f"{RUNNER_RESULT_PREFIX}" + json.dumps({"definition": definition}, ensure_ascii=False))
            return 0

        if action != "execute":
            raise RuntimeError(f"Unsupported action: {action}")

        execute = getattr(module, "execute", None)
        if execute is None or not callable(execute):
            raise RuntimeError("Plugin script must define callable `execute(params)`")

        result = await maybe_await(execute(params))
        print(f"{RUNNER_RESULT_PREFIX}" + json.dumps({"success": True, "data": result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            f"{RUNNER_RESULT_PREFIX}"
            + json.dumps(format_exception_payload(exc), ensure_ascii=False)
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
