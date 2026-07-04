definition = {
    "name": "web-search-quark",
    "description": "Search Quark/Shenma HTML results without an API key. Fallback for web-search provider failures.",
}

import html
import json
import re
from datetime import datetime
from urllib.parse import urlencode

import requests


TIME_RANGE_MAP = {"day": "4", "week": "3", "month": "2", "year": "1"}
CAPTCHA_PATTERN = r'\{[^{]*?"action"\s*:\s*"captcha"\s*,\s*"url"\s*:\s*"([^"]+)"[^{]*?\}'
HYDRATION_PATTERN = r'<script\s+type="application/json"\s+id="s-data-[^"]+"\s+data-used-by="hydrate">(.*?)</script>'


def execute(params):
    query = str(params.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")

    count = int(params.get("count") or 10)
    count = max(1, min(count, 20))
    freshness = str(params.get("freshness", "")).strip().lower()

    results = []
    warnings = []
    pages = 1 if count <= 10 else 2
    for page in range(1, pages + 1):
        page_html = fetch_quark_page(query, page, freshness)
        if is_alibaba_captcha(page_html):
            return {
                "provider": "quark",
                "query": query,
                "success": False,
                "error_type": "captcha",
                "error": "Quark returned an Alibaba CAPTCHA page. Retry later or use an API-backed search provider.",
                "results": results,
            }
        page_results = parse_quark_general_html(page_html)
        if not page_results:
            warnings.append(f"page {page} returned no parseable results")
        results.extend(page_results)
        results = dedupe_results(results)
        if len(results) >= count:
            break

    return {
        "provider": "quark",
        "query": query,
        "success": bool(results),
        "results": results[:count],
        "warnings": warnings,
        "note": "No API key required; parsed from Quark/Shenma HTML hydration data.",
    }


def fetch_quark_page(query, page, freshness):
    params = {
        "q": query,
        "layout": "html",
        "page": page,
    }
    if freshness in TIME_RANGE_MAP:
        params["tl_request"] = TIME_RANGE_MAP[freshness]

    response = requests.get(
        f"https://quark.sm.cn/s?{urlencode(params)}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def parse_quark_general_html(page_html):
    results = []
    for raw_match in re.findall(HYDRATION_PATTERN, page_html, re.DOTALL):
        data = parse_json_script(raw_match)
        if not isinstance(data, dict):
            continue

        initial_data = as_dict(as_dict(data.get("data")).get("initialData"))
        extra_data = as_dict(data.get("extraData"))
        source_category = extra_data.get("sc")

        try:
            parsed = parse_by_source_category(source_category, initial_data)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            results.extend(parsed)
        elif isinstance(parsed, dict):
            results.append(parsed)

    return [item for item in (normalize_result(item) for item in results) if item]


def parse_json_script(raw):
    for candidate in (raw, html.unescape(raw)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def parse_by_source_category(source_category, data):
    data = as_dict(data)
    if source_category == "ai_page":
        return parse_ai_page(data)
    if source_category == "news_uchq":
        return parse_news_uchq(data)
    if source_category in {
        "ss_doc",
        "ss_kv",
        "ss_pic",
        "ss_text",
        "ss_video",
        "baike",
        "structure_web_novel",
    }:
        return parse_ss_doc(data)
    if source_category == "ss_note":
        return parse_ss_note(data)
    if source_category == "addition":
        return {
            "title": textify(data.get("title", {}).get("content")),
            "url": data.get("source", {}).get("url"),
            "snippet": textify(data.get("summary", {}).get("content")),
        }
    if source_category == "nature_result":
        return {
            "title": textify(data.get("title")),
            "url": data.get("url"),
            "snippet": textify(data.get("desc")),
        }
    return parse_generic_result(data)


def parse_ai_page(data):
    results = []
    for item in data.get("list", []):
        item = as_dict(item)
        content = item.get("content")
        if isinstance(content, list):
            content = " | ".join(str(part) for part in content)
        source = as_dict(item.get("source"))
        results.append(
            {
                "title": textify(item.get("title")),
                "url": item.get("url"),
                "snippet": textify(content),
                "published_date": timestamp_to_iso(source.get("time")),
            }
        )
    return results


def parse_news_uchq(data):
    results = []
    for item in data.get("feed", []):
        item = as_dict(item)
        results.append(
            {
                "title": textify(item.get("title")),
                "url": item.get("url"),
                "snippet": textify(item.get("summary")),
                "published_date": parse_date(item.get("time")),
                "thumbnail": secure_url(item.get("image")),
            }
        )
    return results


def parse_ss_doc(data):
    thumbnail = None
    pic_list = data.get("picListProps")
    if isinstance(pic_list, list) and pic_list:
        thumbnail = secure_url(as_dict(pic_list[0]).get("src"))

    title_props = as_dict(data.get("titleProps"))
    source_props = as_dict(data.get("sourceProps"))
    summary_props = as_dict(data.get("summaryProps"))
    message = as_dict(data.get("message"))

    return {
        "title": textify(title_props.get("content") or data.get("title")),
        "url": (
            source_props.get("dest_url")
            or data.get("normal_url")
            or data.get("url")
        ),
        "snippet": textify(
            summary_props.get("content")
            or message.get("replyContent")
            or data.get("show_body")
            or data.get("desc")
        ),
        "published_date": timestamp_to_iso(source_props.get("time")),
        "thumbnail": thumbnail,
    }


def parse_ss_note(data):
    title = as_dict(data.get("title"))
    source = as_dict(data.get("source"))
    summary = as_dict(data.get("summary"))
    return {
        "title": textify(title.get("content")),
        "url": source.get("dest_url"),
        "snippet": textify(summary.get("content")),
        "published_date": timestamp_to_iso(source.get("time")),
    }


def parse_generic_result(data):
    if not isinstance(data, dict):
        return None

    title_props = as_dict(data.get("titleProps"))
    source_props = as_dict(data.get("sourceProps"))
    summary_props = as_dict(data.get("summaryProps"))

    title = data.get("title") or data.get("name") or title_props.get("content")
    url = (
        data.get("url")
        or data.get("link")
        or data.get("normal_url")
        or source_props.get("dest_url")
    )
    snippet = (
        data.get("desc")
        or data.get("summary")
        or data.get("abstract")
        or summary_props.get("content")
    )
    if title or url or snippet:
        return {"title": textify(title), "url": url, "snippet": textify(snippet)}
    return None


def normalize_result(item):
    if not isinstance(item, dict):
        return None
    title = textify(item.get("title"))
    url = str(item.get("url") or "").strip()
    snippet = textify(item.get("snippet") or item.get("content"))
    if not title and not url:
        return None
    result = {
        "title": title or url,
        "url": secure_url(url),
        "snippet": snippet,
        "score": None,
    }
    if item.get("published_date"):
        result["published_date"] = item["published_date"]
    if item.get("thumbnail"):
        result["thumbnail"] = item["thumbnail"]
    return result


def dedupe_results(results):
    seen = set()
    deduped = []
    for item in results:
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def is_alibaba_captcha(page_html):
    return bool(re.search(CAPTCHA_PATTERN, page_html))


def as_dict(value):
    return value if isinstance(value, dict) else {}


def textify(value):
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def secure_url(value):
    if not value:
        return ""
    return str(value).strip().replace("http://", "https://", 1)


def timestamp_to_iso(value):
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp).date().isoformat()


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None
