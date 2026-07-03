definition = {
    "name": "stdout_flood_demo",
    "description": "连续输出大文本，用于验证 stdout 总量限制是否生效",
    "category": "demo",
    "parameters": [
        {
            "name": "line_count",
            "type": "integer",
            "description": "输出行数",
            "required": False,
            "default": 2000,
        },
        {
            "name": "line_size",
            "type": "integer",
            "description": "每行字符数",
            "required": False,
            "default": 1024,
        },
    ],
}


def execute(params: dict):
    line_count = max(1, int(params.get("line_count", 2000) or 2000))
    line_size = max(16, int(params.get("line_size", 1024) or 1024))

    chunk = "X" * line_size
    for index in range(line_count):
        print(f"{index:05d}:{chunk}")

    return {
        "lines_printed": line_count,
        "line_size": line_size,
        "estimated_bytes": line_count * (line_size + 8),
    }
