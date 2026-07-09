definition = {
    "name": "qwen-web-analysis",
    "description": "Use a DashScope-compatible model with native web search enabled to produce deeper Chinese analysis.",
}

import os
import re
import time
from urllib.parse import urlparse

import requests

MAX_QUESTION_CHARS = 600
MAX_EVIDENCE_CHARS = 4000


def execute(params):
    started = time.monotonic()
    mode = compact_text(params.get("mode") or "explain").lower() or "explain"
    if mode not in {"explain", "recommend"}:
        raise ValueError("mode must be one of: explain, recommend")

    question = compact_text(params.get("question"))
    if not question:
        raise ValueError("question is required")

    warnings = []
    question, question_clipped = truncate_text(question, MAX_QUESTION_CHARS)
    if question_clipped:
        warnings.append(f"Question was truncated to {MAX_QUESTION_CHARS} characters to keep web analysis focused.")

    evidence_summary = compact_text(params.get("evidence_summary"))
    evidence_summary, evidence_clipped = truncate_text(evidence_summary, MAX_EVIDENCE_CHARS)
    if evidence_clipped:
        warnings.append(
            f"Evidence summary was truncated to {MAX_EVIDENCE_CHARS} characters to control token usage."
        )

    analysis_scope = compact_text(params.get("analysis_scope"))
    recommendation_focus = compact_text(params.get("recommendation_focus"))
    institution_perspective = compact_text(params.get("institution_perspective") or "procuratorate").lower() or "procuratorate"
    if institution_perspective not in {"procuratorate", "generic"}:
        raise ValueError("institution_perspective must be one of: procuratorate, generic")
    region = compact_text(params.get("region"))
    time_range = compact_text(params.get("time_range"))
    answer_language = compact_text(params.get("answer_language") or "zh-CN") or "zh-CN"

    model_config = load_model_config()
    prompt = build_analysis_prompt(
        mode=mode,
        question=question,
        evidence_summary=evidence_summary,
        recommendation_focus=recommendation_focus,
        institution_perspective=institution_perspective,
        analysis_scope=analysis_scope,
        region=region,
        time_range=time_range,
        answer_language=answer_language,
    )

    model_started = time.monotonic()
    payload = call_dashscope_native_search(
        model_config,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一名擅长产业结构、区域治理、法治服务、资源配置研判的中文高级分析师。"
                    "你可以使用原生联网搜索能力，但它只应服务于一个核心解释问题。"
                    "不要把一次分析扩展成多主题、多轮次的搜索编排。"
                    "优先吸收本地证据摘要，联网只用于补足原因机制、公开背景、可借鉴经验和治理含义。"
                    "最终回答必须只用中文，并组织成完整分析段落。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1800,
    )
    model_ms = int((time.monotonic() - model_started) * 1000)

    answer_body = extract_text_content(payload)
    if not answer_body:
        raise RuntimeError("model response content was empty")
    sources = extract_sources(payload, answer_body)
    if not sources:
        warnings.append("DashScope response did not expose structured citations; output is preserved but sources are unavailable.")

    answer_markdown = build_final_markdown(answer_body, sources)
    total_ms = int((time.monotonic() - started) * 1000)

    return {
        "success": True,
        "mode": "dashscope_native_search",
        "analysis_mode": mode,
        "question": question,
        "answer_markdown": answer_markdown,
        "sources": sources,
        "warnings": warnings,
        "model": model_config["model"],
        "search_enabled": True,
        "summary": summarize_answer(question, sources),
        "timings_ms": {
            "model": model_ms,
            "total": total_ms,
        },
        "stats": {
            "question_chars": len(question),
            "evidence_chars": len(evidence_summary),
            "recommendation_focus_chars": len(recommendation_focus),
            "institution_perspective_chars": len(institution_perspective),
            "output_chars": len(answer_markdown),
            "source_count": len(sources),
            "question_truncated": question_clipped,
            "evidence_truncated": evidence_clipped,
        },
        "finish_reason": extract_finish_reason(payload),
    }


def load_model_config():
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    model = str(os.getenv("AGENT_RUNTIME_MODEL") or "").strip()
    base_url = str(os.getenv("OPENAI_BASE_URL") or "").strip()
    missing = [
        name
        for name, value in [
            ("OPENAI_API_KEY", api_key),
            ("AGENT_RUNTIME_MODEL", model),
            ("OPENAI_BASE_URL", base_url),
        ]
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    timeout_seconds = clamp_int(os.getenv("QWEN_WEB_ANALYSIS_MODEL_TIMEOUT_SECONDS"), 75, 20, 180)
    return {
        "api_key": api_key,
        "model": model,
        "base_url": base_url.rstrip("/"),
        "timeout_seconds": timeout_seconds,
    }


def build_analysis_prompt(*, mode, question, evidence_summary, recommendation_focus, institution_perspective, analysis_scope, region, time_range, answer_language):
    if mode == "recommend":
        if institution_perspective == "procuratorate":
            output_outline = (
                "10. 使用以下结构输出：\n"
                "    - 导语\n"
                "    - （一）第一条治理建议\n"
                "      - 党委政府主责\n"
                "      - 检察履职协同\n"
                "    - （二）第二条治理建议\n"
                "      - 党委政府主责\n"
                "      - 检察履职协同\n"
                "    - （三）第三条治理建议\n"
                "      - 党委政府主责\n"
                "      - 检察履职协同\n"
                "    - 边界与来源\n"
                "11. 导语要体现检察机关立足法律监督、服务党委政府治理决策的口径，但不要脱离本地证据另起空泛表态。\n"
                "12. 每条建议都要先概括本地问题，再吸收外部可借鉴经验，最后拆成“党委政府主责”和“检察履职协同”两部分。\n"
                "13. 建议动作必须正式、克制、可执行，不要写成咨询公司风格或互联网运营文案。\n"
            )
        else:
            output_outline = (
                "10. 使用以下结构输出：\n"
                "    - 本地问题判断\n"
                "    - 外部可借鉴经验\n"
                "    - 对策建议\n"
                "    - 边界与来源\n"
                "11. 外部可借鉴经验尽量提炼为 2-4 条，优先选择与当前本地问题相似、做法明确的经验。\n"
                "12. 对策建议必须回扣本地证据，写成简洁、正式、可执行的中文表述，不要泛泛喊口号。\n"
            )
        focus_line = f"建议聚焦：{recommendation_focus or '（未指定）'}\n"
        mode_requirements = (
            "5. 重点回答“面对当前本地问题，外部有哪些可借鉴做法，这些做法如何转化为下一步对策建议”。\n"
            "7. 联网信息优先用于寻找可借鉴治理经验、成熟做法和针对性举措，不要重复报数。\n"
        )
    else:
        output_outline = (
            "10. 使用以下结构输出：\n"
            "    - 核心判断\n"
            "    - 成因机制\n"
            "    - 治理含义\n"
            "    - 边界与来源\n"
            "11. 每个部分尽量写成 1-2 个完整自然段，内容要充分，但不要用大量项目符号堆砌。\n"
        )
        focus_line = ""
        mode_requirements = (
            "5. 重点回答“为什么会这样”“背后机制是什么”“对治理或资源配置意味着什么”。\n"
            "7. 联网信息只用于补足原因解释、行业机制、公开背景和治理含义，而不是重复报数。\n"
        )
    return (
        "请使用原生联网搜索能力，对下面的问题做解释型分析。\n"
        "要求：\n"
        "1. 最终必须只用中文。\n"
        "2. 本次只围绕一个核心问题展开，不要拆成多个并列搜索任务，不要自行扩展成多主题综述。\n"
        "3. 如果输入里含有多个子问题，也要沿同一条主线合并回答，不要分别检索、分别作答。\n"
        "4. 不要复述技术过程，不要写 SQL、JSON、表结构、API 调试信息。\n"
        f"{mode_requirements}"
        "6. 如果本地证据已经给出事实分布，要优先采用该口径；不要把已经给出的数字、排序、占比重新上网核对一遍。\n"
        "8. 如果外部信息不足，请明确写出不确定性与边界，不要硬编。\n"
        "9. 如果模型支持来源引用，请在正文中自然保留；如果没有，也照常输出完整分析。\n"
        f"{output_outline}\n"
        f"分析模式：{mode}\n"
        f"机构口径：{institution_perspective}\n"
        f"回答语言：{answer_language}\n"
        f"问题：{question}\n"
        f"{focus_line}"
        f"分析范围：{analysis_scope or '（未指定）'}\n"
        f"地区口径：{region or '（未指定）'}\n"
        f"时间范围：{time_range or '（未指定）'}\n"
        f"本地证据摘要：{evidence_summary or '（无）'}\n"
    )


def call_dashscope_native_search(model_config, *, messages, temperature, max_tokens):
    endpoint = f"{model_config['base_url']}/chat/completions"
    response = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {model_config['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "model": model_config["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "enable_search": True,
        },
        timeout=model_config["timeout_seconds"],
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("choices"):
        raise RuntimeError("model response did not contain choices")
    return payload


def extract_text_content(payload):
    choice = first_choice(payload)
    message = choice.get("message") if isinstance(choice, dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return ""


def extract_sources(payload, answer_body):
    choice = first_choice(payload)
    message = choice.get("message") if isinstance(choice, dict) else {}
    raw_candidates = []

    for key in ("annotations", "citations", "search_results", "references"):
        value = message.get(key)
        if isinstance(value, list):
            raw_candidates.extend(value)
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, list):
            raw_candidates.extend(value)

    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                raw_candidates.append(item)

    normalized = dedupe_sources([normalize_source(item) for item in raw_candidates if normalize_source(item)])
    if normalized:
        return normalized

    markdown_sources = dedupe_sources(extract_markdown_links(answer_body))
    return markdown_sources


def normalize_source(item):
    if not isinstance(item, dict):
        return None
    url = secure_url(
        item.get("url")
        or item.get("link")
        or item.get("source")
        or item.get("source_url")
    )
    if not url:
        return None
    title = textify(item.get("title") or item.get("name") or item.get("source_title") or "")
    snippet = textify(item.get("snippet") or item.get("text") or item.get("content") or item.get("description") or "")
    return {
        "title": title or url,
        "url": url,
        "snippet": snippet,
    }


def extract_markdown_links(text):
    results = []
    for label, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text or ""):
        normalized_url = secure_url(url)
        if not normalized_url:
            continue
        results.append(
            {
                "title": textify(label) or normalized_url,
                "url": normalized_url,
                "snippet": "",
            }
        )
    return results


def dedupe_sources(items):
    seen = set()
    deduped = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = secure_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(
            {
                "title": textify(item.get("title") or url),
                "url": url,
                "snippet": textify(item.get("snippet") or ""),
            }
        )
    return deduped


def build_final_markdown(answer_body, sources):
    body = str(answer_body or "").strip()
    if not sources or "## 参考来源" in body:
        return body
    rows = []
    for index, source in enumerate(sources, start=1):
        rows.append(f"{index}. [{source.get('title') or source.get('url')}]({source.get('url')})")
    return f"{body}\n\n## 参考来源\n" + "\n".join(rows)


def summarize_answer(question, sources):
    if sources:
        return f"已完成 DashScope 原生联网分析：{question}。共整理 {len(sources)} 个可识别来源。"
    return f"已完成 DashScope 原生联网分析：{question}。响应未返回可识别来源列表。"


def extract_finish_reason(payload):
    choice = first_choice(payload)
    if isinstance(choice, dict):
        return str(choice.get("finish_reason") or "").strip()
    return ""


def first_choice(payload):
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        return choices[0]
    return {}


def textify(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(textify(item) for item in value if textify(item))
    if isinstance(value, dict):
        return textify(value.get("content") or value.get("text") or value.get("title") or "")
    return " ".join(str(value).split())


def secure_url(value):
    source = str(value or "").strip()
    if not source:
        return ""
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return source


def clamp_int(value, default, minimum, maximum):
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = default
    return max(minimum, min(maximum, numeric))


def compact_text(value):
    return " ".join(str(value or "").split())


def truncate_text(value, max_chars):
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text, False
    return text[: max_chars - 1].rstrip() + "…", True
