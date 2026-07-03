definition = {
    "name": "baidu_search_bs4",
    "description": "使用 requests + BeautifulSoup 抓取百度搜索结果页面",
    "category": "search",
    "parameters": [
        {
            "name": "query",
            "type": "string",
            "description": "搜索关键词",
            "required": True,
        },
        {
            "name": "limit",
            "type": "integer",
            "description": "返回结果条数",
            "required": False,
            "default": 10,
        },
        {
            "name": "debug",
            "type": "boolean",
            "description": "是否返回调试信息",
            "required": False,
            "default": True,
        },
    ],
}


def _extract_result_link(node):
    link = node.select_one("h3 a") or node.select_one("a")
    if not link:
        return None
    href = (link.get("href") or "").strip()
    title = " ".join(link.stripped_strings).strip()
    if not href or not title:
        return None
    return {
        "title": title,
        "url": href,
    }


def _collect_result_nodes(soup):
    selectors = [
        "div.result",
        "div.c-container",
        "div[data-log]",
    ]

    counts = {}
    nodes = []
    seen_node_ids = set()

    for selector in selectors:
        matched = soup.select(selector)
        counts[selector] = len(matched)
        for node in matched:
            node_id = id(node)
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)
            nodes.append(node)

    return nodes, counts


def _fallback_extract_from_content_left(soup, limit):
    results = []
    seen_urls = set()

    for link in soup.select("#content_left h3 a"):
        href = (link.get("href") or "").strip()
        title = " ".join(link.stripped_strings).strip()
        if not href or not title or href in seen_urls:
            continue
        seen_urls.add(href)
        results.append(
            {
                "title": title,
                "url": href,
                "summary": "",
            }
        )
        if len(results) >= limit:
            break

    return results


def execute(params: dict):
    import requests
    from bs4 import BeautifulSoup

    query = str(params.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")

    try:
        limit = int(params.get("limit", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 20))
    debug = bool(params.get("debug", True))

    url = "https://www.baidu.com/s"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    print(f"requesting baidu: query={query}, limit={limit}")
    response = requests.get(
        url,
        params={"wd": query},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    page_title = soup.title.string.strip() if soup.title and soup.title.string else ""
    print(
        "response:"
        f" status={response.status_code}, final_url={response.url}, title={page_title!r}, html_len={len(response.text)}"
    )

    nodes, selector_counts = _collect_result_nodes(soup)
    print(f"selector counts: {selector_counts}, merged_nodes={len(nodes)}")

    results = []
    seen_urls = set()

    for node in nodes:
        item = _extract_result_link(node)
        if not item:
            continue
        if item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        summary_node = (
            node.select_one(".c-abstract")
            or node.select_one(".content-right_8Zs40")
            or node.select_one(".c-span-last")
        )
        summary = ""
        if summary_node:
            summary = " ".join(summary_node.stripped_strings).strip()
        item["summary"] = summary
        results.append(item)
        if len(results) >= limit:
            break

    if not results:
        print("primary selectors returned 0 results, trying #content_left h3 a fallback")
        results = _fallback_extract_from_content_left(soup, limit)

    print(f"parsed results: {len(results)}")
    payload = {
        "query": query,
        "count": len(results),
        "results": results,
    }
    if debug or not results:
        payload["debug"] = {
            "status_code": response.status_code,
            "final_url": response.url,
            "page_title": page_title,
            "selector_counts": selector_counts,
            "content_left_h3_a": len(soup.select("#content_left h3 a")),
            "head_snippet": response.text[:1000].replace("\n", " "),
        }
    return payload
