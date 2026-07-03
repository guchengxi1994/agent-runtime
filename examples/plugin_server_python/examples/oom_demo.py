definition = {
    "name": "oom_kill_demo",
    "description": "持续申请内存，直到容器被 OOM kill",
    "category": "demo",
    "parameters": [
        {
            "name": "chunk_mb",
            "type": "integer",
            "description": "每次申请多少 MB",
            "required": False,
            "default": 128,
        },
        {
            "name": "sleep_ms",
            "type": "integer",
            "description": "每轮 sleep 毫秒数",
            "required": False,
            "default": 20,
        },
    ],
}


def execute(params: dict):
    import time

    chunk_mb = max(1, int(params.get("chunk_mb", 128) or 128))
    sleep_ms = max(0, int(params.get("sleep_ms", 20) or 20))

    block_size = chunk_mb * 1024 * 1024
    blocks = []
    allocated = 0

    while True:
        blocks.append(bytearray(block_size))
        allocated += chunk_mb
        print(f"allocated ~{allocated}MB")
        if sleep_ms:
            time.sleep(sleep_ms / 1000)