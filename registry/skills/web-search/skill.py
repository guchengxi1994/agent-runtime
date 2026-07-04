definition = {
    "name": "web-search",
    "description": "API-backed web search through Tavily, Serper, or Bing when configured.",
}

import os

import requests


def execute(params):
    query = str(params.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    count = int(params.get("count") or 10)
    count = max(1, min(count, 20))
    freshness = str(params.get("freshness", "")).strip() or None

    try:
        if os.getenv("TAVILY_API_KEY"):
            return search_tavily(query, count, freshness)
        if os.getenv("SERPER_API_KEY"):
            return search_serper(query, count)
        if os.getenv("BING_SEARCH_API_KEY"):
            return search_bing(query, count, freshness)
    except Exception as exc:
        return {
            "success": False,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "query": query,
            "results": [],
        }

    return {
        "success": False,
        "error_type": "missing_search_provider",
        "error": "Configure TAVILY_API_KEY, SERPER_API_KEY, or BING_SEARCH_API_KEY in the sandbox environment.",
        "query": query,
        "results": [],
    }


def search_tavily(query, count, freshness):
    payload = {
        "query": query,
        "max_results": count,
        "search_depth": "advanced",
        "include_answer": False,
        "include_raw_content": False,
    }
    if freshness:
        payload["time_range"] = freshness
    response = requests.post(
        "https://api.tavily.com/search",
        json=payload,
        headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "provider": "tavily",
        "query": query,
        "results": [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "score": item.get("score"),
            }
            for item in data.get("results", [])
        ],
    }


def search_serper(query, count):
    response = requests.post(
        "https://google.serper.dev/search",
        json={"q": query, "num": count},
        headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    organic = data.get("organic") or []
    return {
        "provider": "serper",
        "query": query,
        "results": [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "score": None,
            }
            for item in organic[:count]
        ],
    }


def search_bing(query, count, freshness):
    params = {"q": query, "count": count, "textDecorations": False, "textFormat": "Raw"}
    if freshness:
        params["freshness"] = freshness
    response = requests.get(
        "https://api.bing.microsoft.com/v7.0/search",
        params=params,
        headers={"Ocp-Apim-Subscription-Key": os.environ["BING_SEARCH_API_KEY"]},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    pages = (data.get("webPages") or {}).get("value") or []
    return {
        "provider": "bing",
        "query": query,
        "results": [
            {
                "title": item.get("name", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "score": None,
            }
            for item in pages[:count]
        ],
    }
