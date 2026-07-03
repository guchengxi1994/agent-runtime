example_execution_policy = {
    "packages": [
        "requests==2.32.3",
        "beautifulsoup4==4.12.3",
        "markdownify==0.13.1",
    ]
}


definition = {
    "name": "web_page_extract_markdown_demo",
    "description": "抓取网页并抽取标题、正文和 Markdown 片段，需要 requests + bs4 + markdownify",
    "category": "demo",
    "parameters": [
        {
            "name": "url",
            "type": "string",
            "description": "要抓取的页面 URL",
            "required": True,
        },
        {
            "name": "max_paragraphs",
            "type": "integer",
            "description": "最多返回多少段正文",
            "required": False,
            "default": 5,
        },
        {
            "name": "timeout_seconds",
            "type": "integer",
            "description": "HTTP 超时秒数",
            "required": False,
            "default": 15,
        },
    ],
}


def execute(params: dict):
    import requests
    from bs4 import BeautifulSoup
    from markdownify import markdownify as to_markdown

    recommended_packages = [
        "requests==2.32.3",
        "beautifulsoup4==4.12.3",
        "markdownify==0.13.1",
    ]
    url = str(params.get("url", "")).strip()
    if not url:
        raise ValueError("url is required")

    max_paragraphs = max(1, min(int(params.get("max_paragraphs", 5) or 5), 20))
    timeout_seconds = max(3, min(int(params.get("timeout_seconds", 15) or 15), 60))

    print(f"fetching url={url}")
    response = requests.get(
        url,
        timeout=timeout_seconds,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    headings = [
        node.get_text(" ", strip=True)
        for node in soup.select("h1, h2, h3")[:10]
        if node.get_text(" ", strip=True)
    ]

    paragraphs = []
    for node in soup.select("article p, main p, p"):
        text = node.get_text(" ", strip=True)
        if len(text) < 40:
            continue
        paragraphs.append(text)
        if len(paragraphs) >= max_paragraphs:
            break

    main_node = soup.select_one("article") or soup.select_one("main") or soup.body or soup
    markdown = to_markdown(str(main_node), heading_style="ATX")
    markdown_excerpt = markdown[:4000]

    print(
        f"page parsed status={response.status_code} title={title!r} "
        f"paragraphs={len(paragraphs)} headings={len(headings)}"
    )

    return {
        "url": response.url,
        "status_code": response.status_code,
        "title": title,
        "headings": headings,
        "paragraphs": paragraphs,
        "markdown_excerpt": markdown_excerpt,
        "content_length": len(response.text),
        "recommended_packages": recommended_packages,
    }
