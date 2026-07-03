definition = {
    "name": "memory_pressure_demo",
    "description": "申请大块内存，用于验证进程内存限制是否生效",
    "category": "demo",
    "parameters": [
        {
            "name": "megabytes",
            "type": "integer",
            "description": "尝试分配的内存大小，单位 MB",
            "required": False,
            "default": 128,
        }
    ],
}


def execute(params: dict):
    megabytes = max(1, int(params.get("megabytes", 128) or 128))
    print(f"allocating {megabytes}MB memory")

    blocks = []
    block_size = 1024 * 1024
    for index in range(megabytes):
        blocks.append(bytearray(block_size))
        if index % 32 == 0:
            print(f"allocated {index + 1}MB")

    checksum = sum(block[0] for block in blocks)
    return {
        "allocated_mb": megabytes,
        "checksum": checksum,
    }
