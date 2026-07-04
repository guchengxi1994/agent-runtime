definition = {
    "name": "web-fetch",
    "description": "Fetch a URL and extract readable text, metadata, and links.",
}

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


def execute(params):
    url = str(params.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be an absolute http(s) URL")
    max_chars = int(params.get("max_chars") or 12000)
    max_chars = max(1000, min(max_chars, 50000))

    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": "agent-runtime-research/0.1 (+https://example.local)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
        },
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    text = response.text
    if "html" not in content_type.lower():
        return {
            "url": response.url,
            "status_code": response.status_code,
            "content_type": content_type,
            "title": "",
            "description": "",
            "text": text[:max_chars],
            "links": [],
        }

    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description_tag = soup.find("meta", attrs={"name": "description"})
    description = description_tag.get("content", "").strip() if description_tag else ""
    extracted = " ".join(soup.get_text("\n").split())
    links = []
    for anchor in soup.find_all("a", href=True)[:80]:
        label = anchor.get_text(" ", strip=True)
        href = urljoin(response.url, anchor["href"])
        if label or href:
            links.append({"text": label[:160], "url": href})

    return {
        "url": response.url,
        "status_code": response.status_code,
        "content_type": content_type,
        "title": title,
        "description": description,
        "text": extracted[:max_chars],
        "links": links,
    }
