example_execution_policy = {
    "packages": [
        "Pillow==10.4.0",
    ]
}


definition = {
    "name": "image_card_pillow_demo",
    "description": "使用 Pillow 动态生成 PNG 卡片并返回 base64，需要 Pillow",
    "category": "demo",
    "parameters": [
        {
            "name": "title",
            "type": "string",
            "description": "卡片标题",
            "required": False,
            "default": "Artisan Demo",
        },
        {
            "name": "subtitle",
            "type": "string",
            "description": "卡片副标题",
            "required": False,
            "default": "Python plugin runtime",
        },
        {
            "name": "width",
            "type": "integer",
            "description": "图片宽度",
            "required": False,
            "default": 960,
        },
        {
            "name": "height",
            "type": "integer",
            "description": "图片高度",
            "required": False,
            "default": 540,
        },
    ],
}


def execute(params: dict):
    import base64
    from io import BytesIO

    from PIL import Image, ImageDraw, ImageFont

    recommended_packages = [
        "Pillow==10.4.0",
    ]
    title = str(params.get("title", "Artisan Demo")).strip() or "Artisan Demo"
    subtitle = str(params.get("subtitle", "Python plugin runtime")).strip() or "Python plugin runtime"
    width = max(320, min(int(params.get("width", 960) or 960), 1600))
    height = max(240, min(int(params.get("height", 540) or 540), 1200))

    image = Image.new("RGB", (width, height), "#f4efe6")
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(244 * (1 - ratio) + 208 * ratio)
        g = int(239 * (1 - ratio) + 225 * ratio)
        b = int(230 * (1 - ratio) + 244 * ratio)
        draw.line((0, y, width, y), fill=(r, g, b))

    panel_margin = 36
    draw.rounded_rectangle(
        (panel_margin, panel_margin, width - panel_margin, height - panel_margin),
        radius=28,
        fill=(255, 252, 247),
        outline=(188, 170, 142),
        width=3,
    )

    title_font = ImageFont.load_default(size=28)
    subtitle_font = ImageFont.load_default(size=16)

    draw.text((72, 88), title, fill=(56, 43, 28), font=title_font)
    draw.text((72, 132), subtitle, fill=(112, 93, 71), font=subtitle_font)

    block_top = 220
    colors = [(196, 87, 62), (84, 128, 169), (104, 149, 104), (218, 181, 100)]
    for index, color in enumerate(colors):
        left = 72 + index * 150
        draw.rounded_rectangle(
            (left, block_top, left + 110, block_top + 110),
            radius=18,
            fill=color,
        )

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    print(f"generated image {width}x{height}, bytes={buffer.tell()}")

    return {
        "format": "png",
        "width": width,
        "height": height,
        "image_base64": encoded,
        "recommended_packages": recommended_packages,
    }
