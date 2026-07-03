definition = {
    "name": "env_and_workspace_demo",
    "description": "检查任务工作目录、临时目录和环境变量白名单是否生效",
    "category": "demo",
    "parameters": [
        {
            "name": "env_key",
            "type": "string",
            "description": "想读取的环境变量名，默认读取 DEMO_ALLOWED",
            "required": False,
            "default": "DEMO_ALLOWED",
        }
    ],
}


def execute(params: dict):
    import os
    from pathlib import Path

    env_key = str(params.get("env_key", "DEMO_ALLOWED")).strip() or "DEMO_ALLOWED"
    cwd = Path.cwd()
    tmp_dir = Path(os.environ.get("TMPDIR") or os.environ.get("TMP") or cwd)
    home_dir = Path(os.environ.get("HOME") or cwd)

    probe_file = cwd / "workspace_probe.txt"
    probe_file.write_text("workspace is isolated\n", encoding="utf-8")

    visible_keys = [
        "ARTISAN_OPENAI_API_KEY",
        "DEMO_SECRET_TOKEN",
        "INTERNAL_SERVICE_TOKEN",
        env_key,
        "TMPDIR",
        "HOME",
        "VIRTUAL_ENV",
    ]

    env_snapshot = {}
    for key in visible_keys:
        value = os.environ.get(key)
        env_snapshot[key] = value if value is not None else "<missing>"

    print(f"cwd={cwd}")
    print(f"tmp_dir={tmp_dir}")
    print(f"home_dir={home_dir}")
    print(f"probe_file={probe_file}")

    return {
        "cwd": str(cwd),
        "tmp_dir": str(tmp_dir),
        "home_dir": str(home_dir),
        "probe_file_exists": probe_file.exists(),
        "probe_file_size": probe_file.stat().st_size if probe_file.exists() else 0,
        "env_snapshot": env_snapshot,
    }
