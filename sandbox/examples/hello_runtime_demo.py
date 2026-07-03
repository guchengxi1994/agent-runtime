definition = {
    "name": "hello_runtime_demo",
    "description": "最基础的 Python 插件 demo，用于确认执行链路正常",
    "category": "demo",
    "parameters": [
        {
            "name": "name",
            "type": "string",
            "description": "问候对象",
            "required": False,
            "default": "Artisan",
        }
    ],
}


def execute(params: dict):
    name = str(params.get("name", "Artisan")).strip() or "Artisan"
    print(f"hello_runtime_demo running for {name}")
    return {
        "message": f"Hello, {name}!",
        "plugin": definition["name"],
    }
