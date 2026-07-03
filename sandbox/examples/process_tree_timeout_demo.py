definition = {
    "name": "process_tree_timeout_demo",
    "description": "生成子进程并阻塞，用于验证超时后是否整棵进程树一起被清理",
    "category": "demo",
    "parameters": [
        {
            "name": "child_sleep_seconds",
            "type": "integer",
            "description": "子进程休眠时间",
            "required": False,
            "default": 60,
        },
        {
            "name": "parent_sleep_seconds",
            "type": "integer",
            "description": "父进程休眠时间",
            "required": False,
            "default": 60,
        },
    ],
}


def execute(params: dict):
    import subprocess
    import sys
    import time

    child_sleep_seconds = int(params.get("child_sleep_seconds", 60) or 60)
    parent_sleep_seconds = int(params.get("parent_sleep_seconds", 60) or 60)

    child_code = (
        "import time\n"
        "print('child process started', flush=True)\n"
        f"time.sleep({child_sleep_seconds})\n"
        "print('child process finished', flush=True)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print(f"spawned child pid={child.pid}")
    print(
        "run this plugin with a small execution_policy.timeout_ms "
        "to verify parent and child are killed together"
    )

    time.sleep(parent_sleep_seconds)

    return {
        "child_pid": child.pid,
        "note": "If you see this result, timeout was longer than the sleep time.",
    }
