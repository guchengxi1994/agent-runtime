const state = {
  conversationId: null,
  workspaceId: "",
  chartRenderer: "auto",
};

const $ = (id) => document.getElementById(id);
const CHART_RENDERER_STORAGE_KEY = "agent_runtime_chart_renderer";
const CHART_COLORS = ["#0f766e", "#2563eb", "#ea580c", "#7c3aed", "#dc2626", "#0891b2", "#65a30d", "#4f46e5"];
const CHART_LABEL_MAP = {
  case_count: "案件数量",
  record_count: "记录数量",
  company_count: "涉及企业数量",
  judicial_case_count: "司法案件数量",
  administrative_penalty_count: "行政处罚数量",
  share_percent: "占比",
  risk_type: "案件事项类型",
  case_category: "案件大类",
  case_type_sub1: "案件子类一",
  case_type_sub2: "案件子类二",
  event_source: "事件来源",
  month: "月份",
  year: "年份",
  region: "属地",
  industry: "行业",
  company_name: "企业名称",
  ownership_nature: "所有权性质",
  org_form: "组织形式",
  judicial_case: "司法案件",
  administrative_penalty: "行政处罚",
  total: "总量",
  count: "数量",
};
const CHART_TITLE_SEMANTICS = [
  {
    key: "source",
    phrases: ["来源构成", "来源分布", "案件来源", "来源", "构成", "占比", "司法案件", "行政处罚"],
  },
  {
    key: "trend",
    phrases: ["月度趋势", "年度趋势", "按月统计", "按受理月份统计", "按月", "月度", "月份", "趋势"],
  },
  {
    key: "industry",
    phrases: ["行业分布", "行业领域", "行业"],
  },
  {
    key: "region",
    phrases: ["区内属地", "属地分布", "属地", "街道", "镇", "区域分布", "区域", "板块"],
  },
  {
    key: "size",
    phrases: ["企业规模", "规模分布", "规模"],
  },
  {
    key: "ownership",
    phrases: ["所有权性质", "所有制性质", "所有制", "所有权", "产权性质"],
  },
  {
    key: "case_type",
    phrases: ["案件事项类型", "事项类型", "案件类型", "案件类别", "案由分布", "案由"],
  },
];

function normalizeChartRenderer(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ["auto", "tailwind", "echarts"].includes(normalized) ? normalized : "auto";
}

function loadChartRendererPreference() {
  const explicit = normalizeChartRenderer(window.__AGENT_RUNTIME_CHART_RENDERER__);
  if (explicit !== "auto" || window.__AGENT_RUNTIME_CHART_RENDERER__ === "auto") {
    return explicit;
  }
  try {
    return normalizeChartRenderer(window.localStorage.getItem(CHART_RENDERER_STORAGE_KEY));
  } catch {
    return "auto";
  }
}

function saveChartRendererPreference(value) {
  try {
    window.localStorage.setItem(CHART_RENDERER_STORAGE_KEY, normalizeChartRenderer(value));
  } catch {
    // Ignore localStorage failures in restricted browser modes.
  }
}

function escapeText(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function renderInlineMarkdown(value) {
  const parts = String(value ?? "").split(/(`[^`]+`)/g);
  return parts
    .map((part) => {
      if (/^`[^`]+`$/.test(part)) {
        return `<code>${escapeText(part.slice(1, -1))}</code>`;
      }
      let html = escapeText(part);
      html = html.replace(
        /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        (_, label, url) => `<a href="${url}" target="_blank" rel="noreferrer noopener">${label}</a>`,
      );
      html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
      html = html.replace(/~~([^~]+)~~/g, "<del>$1</del>");
      html = html.replace(/(^|[^\*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
      html = html.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
      html = html.replace(/\n/g, "<br>");
      return html;
    })
    .join("");
}

function isTableHeader(line, nextLine) {
  return (
    line.includes("|") &&
    /^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/.test(nextLine || "")
  );
}

function isListLine(line) {
  return /^\s*(?:[-*+]\s+|\d+\.\s+)/.test(line);
}

function startsMarkdownBlock(line, nextLine) {
  return (
    /^```/.test(line) ||
    /^(#{1,6})\s+/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line) ||
    isListLine(line) ||
    isTableHeader(line, nextLine)
  );
}

function parseTableCells(line) {
  let normalized = String(line ?? "").trim();
  if (normalized.startsWith("|")) {
    normalized = normalized.slice(1);
  }
  if (normalized.endsWith("|")) {
    normalized = normalized.slice(0, -1);
  }
  return normalized.split("|").map((cell) => cell.trim());
}

function parseTableAlignments(line) {
  return parseTableCells(line).map((cell) => {
    const value = cell.trim();
    if (value.startsWith(":") && value.endsWith(":")) {
      return "center";
    }
    if (value.endsWith(":")) {
      return "right";
    }
    return "left";
  });
}

function renderMarkdown(value) {
  const source = String(value ?? "").replace(/\r\n?/g, "\n").trimEnd();
  if (!source.trim()) {
    return "";
  }

  const lines = source.split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const nextLine = lines[index + 1] || "";

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fenceMatch = line.match(/^```([\w-]+)?\s*$/);
    if (fenceMatch) {
      const language = fenceMatch[1] || "";
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length && /^```/.test(lines[index])) {
        index += 1;
      }
      const label = language ? `<div class="code-block-label">${escapeText(language)}</div>` : "";
      blocks.push(
        `<div class="code-block">${label}<pre><code>${escapeText(codeLines.join("\n"))}</code></pre></div>`,
      );
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      blocks.push("<hr>");
      index += 1;
      continue;
    }

    if (isTableHeader(line, nextLine)) {
      const headers = parseTableCells(line);
      const alignments = parseTableAlignments(nextLine);
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        rows.push(parseTableCells(lines[index]));
        index += 1;
      }
      const headerHtml = headers
        .map((cell, cellIndex) => `<th class="align-${alignments[cellIndex] || "left"}">${renderInlineMarkdown(cell)}</th>`)
        .join("");
      const rowHtml = rows
        .map(
          (row) =>
            `<tr>${row
              .map(
                (cell, cellIndex) =>
                  `<td class="align-${alignments[cellIndex] || "left"}">${renderInlineMarkdown(cell)}</td>`,
              )
              .join("")}</tr>`,
        )
        .join("");
      blocks.push(
        `<div class="table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${rowHtml}</tbody></table></div>`,
      );
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }

    if (isListLine(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const tag = ordered ? "ol" : "ul";
      const items = [];
      while (index < lines.length) {
        const current = lines[index];
        const match = ordered
          ? current.match(/^\s*\d+\.\s+(.*)$/)
          : current.match(/^\s*[-*+]\s+(.*)$/);
        if (!match) {
          break;
        }
        items.push(`<li>${renderInlineMarkdown(match[1])}</li>`);
        index += 1;
      }
      blocks.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines[index], lines[index + 1] || "")) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${renderInlineMarkdown(paragraphLines.join("\n"))}</p>`);
  }

  return blocks.join("");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }
  if (!response.ok) {
    const detail = data.detail || data.error || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function renderUploadedFiles(files) {
  if (!files || !files.length) {
    return "";
  }
  const rows = files
    .map((file) => {
      const sizeKb = Math.max(1, Math.round((file.size || 0) / 1024));
      return `<div class="uploaded-file"><strong>${escapeText(file.name || "unnamed")}</strong><small>${sizeKb} KB</small></div>`;
    })
    .join("");
  return `<section class="uploaded-files" aria-label="已上传文件"><div class="uploaded-files-title">附件</div>${rows}</section>`;
}

function addMessage(role, text, steps = [], requestedInputs = [], liveStream = null, uploadedFiles = []) {
  const messages = $("messages");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  const avatar = role === "user" ? "U" : "AR";
  item.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div class="message-body">
      <div class="bubble markdown-body">${renderMarkdown(text)}</div>
      ${role === "user" && uploadedFiles.length ? renderUploadedFiles(uploadedFiles) : ""}
      ${role === "assistant" && liveStream ? renderLiveStream(liveStream) : ""}
      ${role === "assistant" && requestedInputs.length ? renderRequestedInputs(requestedInputs) : ""}
      ${role === "assistant" && steps.length ? renderRunLog(steps) : ""}
    </div>
  `;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}

function updateAssistantMessage(item, text, steps = [], requestedInputs = [], liveStream = null, toolCalls = [], options = {}) {
  const bubble = item.querySelector(".bubble");
  const body = item.querySelector(".message-body");
  disposeCharts(body);
  const reportLike = isReportLikeResponse(text);
  bubble.innerHTML = renderMarkdown(text);
  for (const existing of body.querySelectorAll(".live-stream, .requested-inputs, .run-log, .tool-results")) {
    existing.remove();
  }
  const hiddenChartIndexes = injectInlineCharts(bubble, toolCalls);
  if (liveStream) {
    body.insertAdjacentHTML("beforeend", renderLiveStream(liveStream));
  }
  if (requestedInputs.length) {
    body.insertAdjacentHTML("beforeend", renderRequestedInputs(requestedInputs));
  }
  if (toolCalls.length) {
    const toolResultsHtml = renderToolResults(toolCalls, { reportLike, hiddenChartIndexes });
    if (toolResultsHtml) {
      body.insertAdjacentHTML("beforeend", toolResultsHtml);
      hydrateToolResults(body, toolCalls);
    }
  }
  if (steps.length) {
    body.insertAdjacentHTML("beforeend", renderRunLog(steps));
  }
  item._renderState = {
    text,
    steps,
    requestedInputs,
    liveStream,
    toolCalls,
  };
  if (options.scroll !== false) {
    $("messages").scrollTop = $("messages").scrollHeight;
  }
}

function createLiveStreamState() {
  return {
    thinking: "",
    reasoningRedacted: false,
    toolCalls: {},
  };
}

function liveStreamHasContent(liveStream) {
  return Boolean(
    liveStream &&
      (liveStream.thinking ||
        liveStream.reasoningRedacted ||
        Object.keys(liveStream.toolCalls || {}).length),
  );
}

function renderLiveStream(liveStream) {
  if (!liveStreamHasContent(liveStream)) {
    return "";
  }
  const thinking = liveStream.thinking
    ? `<div class="live-block"><strong>Thinking</strong><p>${escapeText(liveStream.thinking)}</p></div>`
    : liveStream.reasoningRedacted
      ? `<div class="live-block muted"><strong>Thinking</strong><p>模型正在规划，reasoning 已按配置隐藏。</p></div>`
      : "";
  const toolRows = Object.values(liveStream.toolCalls || {})
    .map((tool) => {
      const name = tool.name || "选择工具中";
      const args = tool.arguments || "";
      const preview = args.length > 900 ? `${args.slice(0, 900)}...` : args;
      return `
        <div class="live-block tool">
          <strong>Tool Call · ${escapeText(name)}</strong>
          ${preview ? `<pre>${escapeText(preview)}</pre>` : `<p>正在生成调用参数...</p>`}
        </div>
      `;
    })
    .join("");
  return `
    <section class="live-stream" aria-label="实时规划">
      <div class="live-stream-title">实时规划</div>
      ${thinking}
      ${toolRows}
    </section>
  `;
}

function renderRequestedInputs(inputs) {
  const rows = inputs
    .map((field) => {
      const label = field.label || field.name || "补充信息";
      const required = field.required === false ? "可选" : "必填";
      return `
        <div class="requested-field">
          <div class="requested-field-head">
            <strong>${escapeText(label)}</strong>
            <span>${escapeText(required)}</span>
          </div>
          <p>${escapeText(field.description || "请补充该字段的具体要求。")}</p>
          <small>${escapeText(field.name || "")}${field.type ? ` · ${escapeText(field.type)}` : ""}</small>
        </div>
      `;
    })
    .join("");

  return `
    <section class="requested-inputs" aria-label="需要补充的信息">
      <div class="requested-inputs-title">需要补充</div>
      ${rows}
    </section>
  `;
}

function renderRunLog(steps) {
  const toolSteps = steps.filter((step) => step.kind === "sandbox_execution");
  const waitingStep = steps.find((step) => step.kind === "waiting_for_user");
  const uniqueToolLabels = Array.from(new Set(toolSteps.map((step) => step.label).filter(Boolean)));
  const statusText = waitingStep
    ? "等待用户输入"
    : toolSteps.length
      ? `已执行 ${toolSteps.length} 次工具调用${uniqueToolLabels.length ? `，涉及 ${uniqueToolLabels.slice(0, 3).join("、")}${uniqueToolLabels.length > 3 ? " 等" : ""}` : ""}`
      : "未执行 sandbox 工具";
  const summaryClass = toolSteps.length ? "used-tool" : waitingStep ? "waiting" : "no-tool";
  const rows = steps
    .map((step) => {
      const payload = {
        step_id: step.step_id,
        kind: step.kind,
        label: step.label,
        status: step.status,
        detail: step.detail,
        metadata: step.metadata,
        tool_call_id: step.tool_call_id,
        execution_id: step.execution_id,
        created_at: step.created_at,
      };
      return `
        <div class="run-step step-${escapeText(step.kind || "unknown")}">
          <div class="run-step-line">
            <span class="step-kind">${escapeText(step.kind || "unknown")}</span>
            <strong>${escapeText(step.label || "")}</strong>
            <small>${escapeText(step.status || "")}</small>
          </div>
          ${step.detail ? `<p>${escapeText(step.detail)}</p>` : ""}
          <pre>${escapeText(JSON.stringify(payload, null, 2))}</pre>
        </div>
      `;
    })
    .join("");

  return `
    <details class="run-log ${summaryClass}">
      <summary>
        <span>运行日志</span>
        <strong>${escapeText(statusText)}</strong>
      </summary>
      <div class="run-log-body">${rows}</div>
    </details>
  `;
}

function normalizeToolData(toolCall) {
  if (!toolCall || typeof toolCall !== "object") {
    return null;
  }
  const result = toolCall.result && typeof toolCall.result === "object" ? toolCall.result : {};
  const data = result.data && typeof result.data === "object" ? result.data : null;
  if (!data) {
    return null;
  }
  return {
    toolName: String(toolCall.tool_name || toolCall.toolName || "tool"),
    data,
  };
}

function extractToolResults(toolCalls) {
  const charts = [];
  const tables = [];
  for (const toolCall of toolCalls || []) {
    const normalized = normalizeToolData(toolCall);
    if (!normalized) {
      continue;
    }
    const { toolName, data } = normalized;
    const chartData =
      data.chart_spec && typeof data.chart_spec === "object"
        ? data.chart_spec
        : data.option && typeof data.option === "object"
          ? data
          : null;
    if (chartData && chartData.option && typeof chartData.option === "object") {
      charts.push({
        toolName,
        title: chartData.title || data.title || "图表",
        chartType: chartData.chart_type || data.chart_type || "chart",
        option: chartData.option,
        rendererHint: chartData.renderer || data.renderer || "",
        summary: data.summary || "",
      });
    }
    if (Array.isArray(data.rows) && data.rows.length && Array.isArray(data.columns) && data.columns.length) {
      tables.push({
        toolName,
        title:
          data.report_question ||
          data.query ||
          (toolName === "pg-case-search" ? "案例检索结果" : "查询结果"),
        summary: data.summary || "",
        rowCount: Number(data.row_count || data.rows.length || 0),
        columns: data.columns,
        rows: data.rows,
      });
    }
  }
  return { charts, tables };
}

function normalizeChartTitle(value) {
  return String(value || "")
    .replace(/\[\[chart:(.+?)\]\]/gi, "$1")
    .replace(/^图\s*\d+\s*(?:[：:]\s*|\s+)/i, "")
    .replace(/\s*(?:（|\()?\s*(?:饼图|折线图|柱状图|堆叠柱状图|前\d+位|top\s*\d+)\s*(?:）|\))?\s*$/gi, "")
    .toLowerCase()
    .replace(/[\s\u3000]+/g, "")
    .replace(/[：:，,。．.、()[\]{}（）【】"'`“”‘’\-_/]/g, "");
}

function collectChartTitleSignals(value) {
  const normalized = normalizeChartTitle(value);
  const semantics = new Set();
  const phrases = new Set();
  for (const rule of CHART_TITLE_SEMANTICS) {
    for (const phrase of rule.phrases) {
      const phraseKey = normalizeChartTitle(phrase);
      if (!phraseKey || !normalized.includes(phraseKey)) {
        continue;
      }
      semantics.add(rule.key);
      phrases.add(phraseKey);
    }
  }
  return { normalized, semantics, phrases };
}

function scoreChartTitleMatch(markerSignals, chartTitle) {
  const chartSignals = collectChartTitleSignals(chartTitle);
  if (!markerSignals.normalized || !chartSignals.normalized) {
    return 0;
  }

  let score = 0;
  if (chartSignals.normalized === markerSignals.normalized) {
    score += 120;
  } else if (
    chartSignals.normalized.includes(markerSignals.normalized) ||
    markerSignals.normalized.includes(chartSignals.normalized)
  ) {
    score += 90;
  }

  const sharedSemantics = Array.from(markerSignals.semantics).filter((key) => chartSignals.semantics.has(key));
  const sharedPhrases = Array.from(markerSignals.phrases).filter((phrase) => chartSignals.phrases.has(phrase));
  score += sharedSemantics.length * 28;
  score += sharedPhrases.length * 12;

  if (markerSignals.semantics.size && !sharedSemantics.length && score < 90) {
    return 0;
  }
  if (markerSignals.phrases.size && !sharedPhrases.length && !sharedSemantics.length && score < 90) {
    return 0;
  }
  return score;
}

function localizeChartLabel(value, fallback = "") {
  const source = String(value ?? "").trim();
  if (!source) {
    return fallback;
  }
  if (/[\u4e00-\u9fff]/.test(source)) {
    return source;
  }
  const normalized = source.toLowerCase();
  return CHART_LABEL_MAP[normalized] || source;
}

function parseInlineChartMarker(text) {
  const source = String(text || "").replace(/\s+/g, " ").trim();
  if (!source) {
    return null;
  }
  const explicitMatch = source.match(/\[\[chart:(.+?)\]\]/i);
  if (explicitMatch) {
    const rawTarget = explicitMatch[1].trim();
    const numericMatch = rawTarget.match(/^\d+$/);
    return {
      type: "explicit",
      raw: explicitMatch[0],
      chartIndex: numericMatch ? Math.max(0, Number(rawTarget) - 1) : null,
      title: numericMatch ? "" : rawTarget,
    };
  }
  const figureMatch = source.match(/^图\s*(\d+)\s*(?:[：:]\s*|\s+)(.+)$/);
  if (!figureMatch) {
    return null;
  }
  return {
    type: "figure",
    raw: figureMatch[0],
    chartIndex: Math.max(0, Number(figureMatch[1]) - 1),
    title: figureMatch[2].trim(),
  };
}

function resolveInlineChartIndex(marker, charts, usedIndexes) {
  if (!marker || !Array.isArray(charts) || !charts.length) {
    return null;
  }
  const markerSignals = collectChartTitleSignals(marker.title || "");
  if (markerSignals.normalized) {
    const rankedMatches = charts
      .map((chart, index) => ({
        index,
        score: usedIndexes.has(index) ? 0 : scoreChartTitleMatch(markerSignals, chart.title || ""),
      }))
      .filter((item) => item.score > 0)
      .sort((left, right) => right.score - left.score || left.index - right.index);
    if (rankedMatches.length && rankedMatches[0].score >= 24) {
      return rankedMatches[0].index;
    }
  }
  if (
    Number.isInteger(marker.chartIndex) &&
    marker.chartIndex >= 0 &&
    marker.chartIndex < charts.length &&
    !usedIndexes.has(marker.chartIndex)
  ) {
    if (!markerSignals.normalized) {
      return marker.chartIndex;
    }
    const ordinalScore = scoreChartTitleMatch(markerSignals, charts[marker.chartIndex]?.title || "");
    if (!markerSignals.semantics.size || ordinalScore >= 24) {
      return marker.chartIndex;
    }
  }
  return null;
}

function cleanupExplicitChartMarker(node, marker) {
  if (!node || !marker || !marker.raw) {
    return;
  }
  node.innerHTML = node.innerHTML.split(marker.raw).join("").trim();
}

function removeEmptyAnchor(node) {
  if (!node) {
    return;
  }
  if (!node.textContent.trim() && !node.children.length) {
    node.remove();
  }
}

function isValidChartIndex(index, charts) {
  return Number.isInteger(index) && index >= 0 && index < charts.length;
}

function hasAdjacentInlineChart(anchor) {
  return Boolean(
    anchor?.previousElementSibling?.classList?.contains("markdown-inline-chart") ||
      anchor?.nextElementSibling?.classList?.contains("markdown-inline-chart"),
  );
}

function insertInlineChart(anchor, chartIndex) {
  const wrapper = document.createElement("div");
  wrapper.className = "markdown-inline-chart";
  wrapper.innerHTML = `<div class="chart-canvas" data-chart-index="${chartIndex}"></div>`;
  anchor.insertAdjacentElement("afterend", wrapper);
}

function isChartSpecCodeBlock(node) {
  const codeNode = node?.querySelector("pre code");
  if (!codeNode) {
    return false;
  }
  const source = String(codeNode.textContent || "").trim();
  if (!source.startsWith("{")) {
    return false;
  }
  try {
    const parsed = JSON.parse(source);
    return Boolean(
      parsed &&
        typeof parsed === "object" &&
        (parsed.option || parsed.chart_type || parsed.renderer || parsed.title),
    );
  } catch {
    return false;
  }
}

function cleanupInlineChartSpecs(root) {
  for (const block of root.querySelectorAll(".code-block")) {
    if (!isChartSpecCodeBlock(block)) {
      continue;
    }
    const previous = block.previousElementSibling;
    const next = block.nextElementSibling;
    const adjacentInlineChart = [previous, next].some((node) => node?.classList?.contains("markdown-inline-chart"));
    if (adjacentInlineChart) {
      block.remove();
    }
  }
}

function injectInlineCharts(root, toolCalls) {
  const { charts } = extractToolResults(toolCalls);
  if (!charts.length) {
    return new Set();
  }
  const insertedIndexes = new Set();
  const hiddenIndexes = new Set();
  const usedFigureOrdinals = new Set();
  const anchors = Array.from(root.querySelectorAll("h1, h2, h3, h4, h5, h6, p, li"));
  const entries = anchors
    .map((anchor) => ({ anchor, marker: parseInlineChartMarker(anchor.textContent || "") }))
    .filter((entry) => entry.marker);

  for (const { anchor, marker } of entries.filter((entry) => entry.marker.type === "figure")) {
    if (anchor.nextElementSibling?.classList?.contains("markdown-inline-chart")) {
      continue;
    }
    const chartIndex = resolveInlineChartIndex(marker, charts, insertedIndexes);
    if (chartIndex == null) {
      continue;
    }
    insertInlineChart(anchor, chartIndex);
    insertedIndexes.add(chartIndex);
    hiddenIndexes.add(chartIndex);
    if (isValidChartIndex(marker.chartIndex, charts)) {
      usedFigureOrdinals.add(marker.chartIndex);
      if (marker.chartIndex !== chartIndex) {
        hiddenIndexes.add(marker.chartIndex);
      }
    }
  }

  for (const { anchor, marker } of entries.filter((entry) => entry.marker.type === "explicit")) {
    if (hasAdjacentInlineChart(anchor)) {
      const nearbyChartIndex = resolveInlineChartIndex(marker, charts, insertedIndexes);
      if (nearbyChartIndex != null) {
        hiddenIndexes.add(nearbyChartIndex);
      }
      if (isValidChartIndex(marker.chartIndex, charts)) {
        hiddenIndexes.add(marker.chartIndex);
      }
      cleanupExplicitChartMarker(anchor, marker);
      removeEmptyAnchor(anchor);
      continue;
    }
    if (isValidChartIndex(marker.chartIndex, charts) && usedFigureOrdinals.has(marker.chartIndex)) {
      hiddenIndexes.add(marker.chartIndex);
      cleanupExplicitChartMarker(anchor, marker);
      removeEmptyAnchor(anchor);
      continue;
    }
    const chartIndex = resolveInlineChartIndex(marker, charts, insertedIndexes);
    if (chartIndex == null) {
      cleanupExplicitChartMarker(anchor, marker);
      removeEmptyAnchor(anchor);
      continue;
    }
    cleanupExplicitChartMarker(anchor, marker);
    insertInlineChart(anchor, chartIndex);
    insertedIndexes.add(chartIndex);
    hiddenIndexes.add(chartIndex);
    if (isValidChartIndex(marker.chartIndex, charts) && marker.chartIndex !== chartIndex) {
      hiddenIndexes.add(marker.chartIndex);
    }
    removeEmptyAnchor(anchor);
  }

  if (hiddenIndexes.size) {
    cleanupInlineChartSpecs(root);
  }
  return hiddenIndexes;
}

function isReportLikeResponse(text) {
  const source = String(text || "");
  if (source.length < 900) {
    return false;
  }
  return (
    (source.includes("报告") || source.includes("执行摘要") || source.includes("治理建议")) &&
    (/[一二三四五六七八九十]+、/.test(source) || /(?:^|\n)##?\s*[一二三四五六七八九十]+[、.]/m.test(source))
  );
}

function isTechnicalResultTable(table) {
  const source = `${table.title || ""} ${table.summary || ""} ${table.columns.join(" ")}`.toLowerCase();
  return /完整列名|关键字段|表结构|schema|列名|字段|enterprise_risk_events|source table/.test(source);
}

function localizeChartType(chartType) {
  const normalized = String(chartType || "").trim().toLowerCase();
  return (
    {
      line: "折线图",
      bar: "柱状图",
      stacked_bar: "堆叠柱状图",
      pie: "饼图",
      heatmap: "热力图",
      chart: "图表",
    }[normalized] || "图表"
  );
}

function renderTableCard(table, options = {}) {
  const reportLike = Boolean(options.reportLike);
  const limitedRows = table.rows.slice(0, 12);
  const head = table.columns.map((column) => `<th>${escapeText(column)}</th>`).join("");
  const body = limitedRows
    .map(
      (row) =>
        `<tr>${table.columns
          .map((column) => `<td>${renderInlineMarkdown(String(row[column] ?? ""))}</td>`)
          .join("")}</tr>`,
    )
    .join("");
  const footer =
    table.rows.length > limitedRows.length
      ? `<p class="chart-meta">仅展示前 ${limitedRows.length} 行，共 ${table.rowCount || table.rows.length} 行。</p>`
      : `<p class="chart-meta">共 ${table.rowCount || table.rows.length} 行。</p>`;
  return `
    <article class="tool-card">
      <div class="tool-card-head">
        <div>
          <strong>${escapeText(table.title || "查询结果")}</strong>
          ${reportLike ? "" : `<span>${escapeText(table.toolName)}</span>`}
        </div>
        <span class="tool-result-badge">${escapeText(`${table.rowCount || table.rows.length} 行`)}</span>
      </div>
      <div class="tool-card-body">
        ${table.summary ? `<p class="tool-card-summary">${escapeText(table.summary)}</p>` : ""}
        <div class="tool-result-table-wrap">
          <table class="tool-result-table">
            <thead><tr>${head}</tr></thead>
            <tbody>${body}</tbody>
          </table>
        </div>
        ${footer}
      </div>
    </article>
  `;
}

function renderDisclosure(label, description, content) {
  return `
    <details class="tool-results-disclosure">
      <summary>
        <span>${escapeText(label)}</span>
        <strong>${escapeText(description)}</strong>
      </summary>
      <div class="tool-results-disclosure-body">${content}</div>
    </details>
  `;
}

function renderToolResults(toolCalls, options = {}) {
  const { charts, tables } = extractToolResults(toolCalls);
  if (!charts.length && !tables.length) {
    return "";
  }
  const reportLike = Boolean(options.reportLike);
  const hiddenChartIndexes = options.hiddenChartIndexes instanceof Set ? options.hiddenChartIndexes : new Set();
  const visibleCharts = charts
    .map((chart, index) => ({ chart, index }))
    .filter(({ index }) => !hiddenChartIndexes.has(index));
  const chartHtml = visibleCharts
    .map(
      ({ chart, index }) => `
        <article class="tool-card" data-report-like="${reportLike ? "1" : "0"}">
          <div class="tool-card-head">
            <div>
              <strong>${escapeText(chart.title || "图表")}</strong>
              ${reportLike ? "" : `<span>${escapeText(chart.toolName)}</span>`}
            </div>
            <span class="tool-result-badge">${escapeText(localizeChartType(chart.chartType || "chart"))}</span>
          </div>
          <div class="tool-card-body">
            ${chart.summary ? `<p class="tool-card-summary">${escapeText(chart.summary)}</p>` : ""}
            <div class="chart-canvas" data-chart-index="${index}"></div>
            <p class="chart-meta" data-chart-meta-index="${index}"></p>
          </div>
        </article>
      `,
    )
    .join("");
  const visibleTables = reportLike ? tables.filter((table) => !isTechnicalResultTable(table)) : tables;
  const technicalTables = reportLike ? tables.filter((table) => isTechnicalResultTable(table)) : [];
  const tableHtml = visibleTables.map((table) => renderTableCard(table, { reportLike })).join("");
  const technicalTableHtml = technicalTables.map((table) => renderTableCard(table, { reportLike })).join("");
  if (!visibleCharts.length && !visibleTables.length && !technicalTables.length) {
    return "";
  }

  return `
    <section class="tool-results" aria-label="结构化结果">
      ${visibleCharts.length ? `<div class="tool-result-group"><div class="tool-result-heading">图表结果</div>${chartHtml}</div>` : ""}
      ${
        !reportLike && visibleTables.length
          ? `<div class="tool-result-group"><div class="tool-result-heading">查询结果</div>${tableHtml}</div>`
          : ""
      }
      ${
        reportLike && visibleTables.length
          ? `<div class="tool-result-group"><div class="tool-result-heading">分析明细</div>${renderDisclosure("分析明细", `共 ${visibleTables.length} 组数据结果，可展开核对`, tableHtml)}</div>`
          : ""
      }
      ${
        reportLike && technicalTables.length
          ? `<div class="tool-result-group"><div class="tool-result-heading">技术明细</div>${renderDisclosure("技术明细", `共 ${technicalTables.length} 组，通常可忽略`, technicalTableHtml)}</div>`
          : ""
      }
    </section>
  `;
}

function formatMetric(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) {
    return "0";
  }
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: numeric >= 100 ? 0 : 1,
  }).format(numeric);
}

function formatPercent(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) {
    return "0%";
  }
  return `${numeric.toFixed(numeric >= 10 ? 0 : 1)}%`;
}

function normalizeChartValue(value) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : 0;
  }
  if (value && typeof value === "object") {
    if (Array.isArray(value) && value.length) {
      return normalizeChartValue(value[value.length - 1]);
    }
    if ("value" in value) {
      return normalizeChartValue(value.value);
    }
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function buildChartLegend(series) {
  if (!Array.isArray(series) || !series.length) {
    return "";
  }
  return `
    <div class="chart-legend">
      ${series
        .map(
          (item) => `
            <span class="chart-legend-item">
              <span class="chart-swatch" style="background:${item.color};"></span>
              <span>${escapeText(localizeChartLabel(item.name, "系列"))}</span>
            </span>
          `,
        )
        .join("")}
    </div>
  `;
}

function buildTailwindChartModel(chart) {
  const option = chart && chart.option && typeof chart.option === "object" ? chart.option : null;
  if (!option) {
    return null;
  }
  const chartType = String(chart.chartType || "").trim().toLowerCase();
  if (chartType === "pie") {
    const pieSeries = Array.isArray(option.series) ? option.series[0] : null;
    const segments = Array.isArray(pieSeries?.data)
      ? pieSeries.data.map((item, index) => ({
          name: localizeChartLabel(item?.name, `分类 ${index + 1}`),
          value: normalizeChartValue(item?.value),
          color: CHART_COLORS[index % CHART_COLORS.length],
        }))
      : [];
    if (!segments.length) {
      return null;
    }
    return {
      type: "pie",
      title: chart.title || option.title?.text || "图表",
      segments,
      total: segments.reduce((sum, item) => sum + Math.max(0, item.value), 0),
    };
  }

  if (!["bar", "stacked_bar", "line"].includes(chartType)) {
    return null;
  }
  const rawSeries = Array.isArray(option.series) ? option.series : [];
  const categories = Array.isArray(option.xAxis?.data) ? option.xAxis.data.map((item) => String(item ?? "")) : [];
  if (!categories.length || !rawSeries.length) {
    return null;
  }
  const pointCount = Math.max(categories.length, ...rawSeries.map((item) => (Array.isArray(item?.data) ? item.data.length : 0)));
  const normalizedCategories = Array.from({ length: pointCount }, (_, index) =>
    localizeChartLabel(categories[index], String(categories[index] ?? index + 1)),
  );
  const series = rawSeries
    .map((item, index) => ({
      name: localizeChartLabel(item?.name, `系列 ${index + 1}`),
      values: Array.from({ length: pointCount }, (_, itemIndex) =>
        normalizeChartValue(Array.isArray(item?.data) ? item.data[itemIndex] : 0),
      ),
      stack: item?.stack || null,
      color: CHART_COLORS[index % CHART_COLORS.length],
    }))
    .filter((item) => item.values.some((value) => Number.isFinite(value)));
  if (!series.length) {
    return null;
  }
  const stacked = chartType === "stacked_bar" || series.some((item) => item.stack);
  const maxValue = stacked
    ? Math.max(
        1,
        ...Array.from({ length: pointCount }, (_, index) =>
          series.reduce((sum, item) => sum + Math.max(0, item.values[index] || 0), 0),
        ),
      )
    : Math.max(1, ...series.flatMap((item) => item.values.map((value) => Math.max(0, value))));
  return {
    type: stacked ? "stacked_bar" : chartType,
    title: chart.title || option.title?.text || "图表",
    categories: normalizedCategories,
    series,
    maxValue,
  };
}

function canRenderWithTailwind(chart) {
  return Boolean(buildTailwindChartModel(chart));
}

function resolveChartRenderer(chart) {
  const preferred = normalizeChartRenderer(state.chartRenderer);
  const tailwindReady = canRenderWithTailwind(chart);
  const echartsReady = Boolean(window.echarts && chart.option);
  if (preferred === "tailwind") {
    return tailwindReady ? "tailwind" : echartsReady ? "echarts" : "unsupported";
  }
  if (preferred === "echarts") {
    return echartsReady ? "echarts" : tailwindReady ? "tailwind" : "unsupported";
  }
  if (tailwindReady) {
    return "tailwind";
  }
  if (echartsReady) {
    return "echarts";
  }
  return "unsupported";
}

function buildChartMeta(chart, renderer) {
  const preferred = normalizeChartRenderer(state.chartRenderer);
  const source = chart.rendererHint || "spec";
  const rendererLabel =
    renderer === "tailwind" ? "Tailwind / SVG" : renderer === "echarts" ? "ECharts" : "Unavailable";
  const preferredLabel = preferred === "auto" ? "Auto" : preferred === "tailwind" ? "Tailwind" : "ECharts";
  const fallbackNote =
    preferred !== "auto" && ((preferred === "tailwind" && renderer !== "tailwind") || (preferred === "echarts" && renderer !== "echarts"))
      ? ` · 已从 ${preferredLabel} 回退`
      : "";
  return `渲染：${rendererLabel}${fallbackNote} · 规格：${source}`;
}

function renderChartGridLines() {
  return Array.from({ length: 4 }, () => `<div class="chart-grid-line"></div>`).join("");
}

function renderTailwindBarChart(model) {
  const minWidth = Math.max(360, model.categories.length * (model.series.length > 1 ? 78 : 66));
  const showValueLabels = model.categories.length <= 8 && model.series.length <= 4;
  const bars = model.categories
    .map((category, categoryIndex) => {
      const seriesMarkup = model.series
        .map((series) => {
          const value = Math.max(0, series.values[categoryIndex] || 0);
          const height = value <= 0 ? 0 : Math.max(4, (value / model.maxValue) * 100);
          return `
            <div class="chart-bar-column">
              ${showValueLabels ? `<span class="chart-bar-value">${escapeText(formatMetric(value))}</span>` : ""}
            <div
              class="chart-bar"
              style="height:${height}%;background:${series.color};"
              title="${escapeText(`${localizeChartLabel(series.name, "系列")} · ${localizeChartLabel(category, category)}: ${value}`)}"
              ></div>
            </div>
          `;
        })
        .join("");
      return `
        <div class="chart-category">
          <div class="chart-bar-group">${seriesMarkup}</div>
          <div class="chart-category-label" title="${escapeText(category)}">${escapeText(category)}</div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="chart-shell">
      ${buildChartLegend(model.series)}
      <div class="chart-scroll">
        <div class="chart-scale" style="min-width:${minWidth}px;">
          <span>${escapeText(formatMetric(model.maxValue))}</span>
          <span>0</span>
        </div>
        <div class="chart-plot">
          <div class="chart-plot-frame" style="min-width:${minWidth}px;">
            <div class="chart-grid">${renderChartGridLines()}</div>
            <div class="chart-bars" style="grid-template-columns:repeat(${model.categories.length}, minmax(0, 1fr));">
              ${bars}
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderTailwindStackedBarChart(model) {
  const minWidth = Math.max(360, model.categories.length * 78);
  const showValueLabels = model.categories.length <= 8;
  const bars = model.categories
    .map((category, categoryIndex) => {
      const total = model.series.reduce((sum, series) => sum + Math.max(0, series.values[categoryIndex] || 0), 0);
      const barHeight = total <= 0 ? 0 : Math.max(4, (total / model.maxValue) * 100);
      const segments = model.series
        .map((series) => {
          const value = Math.max(0, series.values[categoryIndex] || 0);
          const segmentHeight = total > 0 ? (value / total) * 100 : 0;
          return `
            <div
              class="chart-bar-segment"
              style="height:${segmentHeight}%;background:${series.color};"
              title="${escapeText(`${localizeChartLabel(series.name, "系列")} · ${localizeChartLabel(category, category)}: ${value}`)}"
            ></div>
          `;
        })
        .join("");
      return `
        <div class="chart-category">
          <div class="chart-bar-group">
            <div class="chart-bar-column">
              ${showValueLabels ? `<span class="chart-bar-value">${escapeText(formatMetric(total))}</span>` : ""}
              <div class="chart-bar-stack" style="height:${barHeight}%;">
                ${segments}
              </div>
            </div>
          </div>
          <div class="chart-category-label" title="${escapeText(category)}">${escapeText(category)}</div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="chart-shell">
      ${buildChartLegend(model.series)}
      <div class="chart-scroll">
        <div class="chart-scale" style="min-width:${minWidth}px;">
          <span>${escapeText(formatMetric(model.maxValue))}</span>
          <span>0</span>
        </div>
        <div class="chart-plot">
          <div class="chart-plot-frame" style="min-width:${minWidth}px;">
            <div class="chart-grid">${renderChartGridLines()}</div>
            <div class="chart-bars" style="grid-template-columns:repeat(${model.categories.length}, minmax(0, 1fr));">
              ${bars}
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function buildLinePath(values, maxValue, width, height) {
  const top = 16;
  const bottom = 18;
  const left = 16;
  const right = 16;
  const innerHeight = height - top - bottom;
  const innerWidth = width - left - right;
  return values.map((value, index) => {
    const x =
      values.length === 1 ? left + innerWidth / 2 : left + (innerWidth * index) / Math.max(1, values.length - 1);
    const y = top + innerHeight - (Math.max(0, value) / Math.max(1, maxValue)) * innerHeight;
    return { x, y };
  });
}

function renderTailwindLineChart(model) {
  const width = Math.max(360, model.categories.length * 78);
  const height = 224;
  const lines = Array.from({ length: 5 }, (_, index) => {
    const y = 16 + ((height - 34) * index) / 4;
    return `<line x1="0" y1="${y}" x2="${width}" y2="${y}" stroke="rgba(212,212,216,0.8)" stroke-dasharray="4 6" />`;
  }).join("");
  const seriesMarkup = model.series
    .map((series) => {
      const points = buildLinePath(series.values, model.maxValue, width, height);
      const path = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
      const pointNodes = points
        .map(
          (point, index) => `
            <circle cx="${point.x}" cy="${point.y}" r="4" fill="${series.color}">
              <title>${escapeText(`${localizeChartLabel(series.name, "系列")} · ${localizeChartLabel(model.categories[index], model.categories[index])}: ${series.values[index]}`)}</title>
            </circle>
          `,
        )
        .join("");
      return `
        <g>
          <path d="${path}" fill="none" stroke="${series.color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>
          ${pointNodes}
        </g>
      `;
    })
    .join("");
  const labels = model.categories
    .map((category) => `<div class="chart-axis-label" title="${escapeText(category)}">${escapeText(category)}</div>`)
    .join("");
  return `
    <div class="chart-shell chart-line-wrap">
      ${buildChartLegend(model.series)}
      <div class="chart-scroll">
        <div class="chart-scale" style="min-width:${width}px;">
          <span>${escapeText(formatMetric(model.maxValue))}</span>
          <span>0</span>
        </div>
        <svg class="chart-line-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${escapeText(model.title)}">
          ${lines}
          ${seriesMarkup}
        </svg>
        <div class="chart-axis-grid" style="min-width:${width}px;grid-template-columns:repeat(${model.categories.length}, minmax(0, 1fr));">
          ${labels}
        </div>
      </div>
    </div>
  `;
}

function renderTailwindPieChart(model) {
  if (model.total <= 0) {
    return `<div class="chart-empty">暂无可绘制的数据。</div>`;
  }
  let offset = 0;
  const gradient = model.segments
    .map((segment) => {
      const size = (Math.max(0, segment.value) / model.total) * 100;
      const start = offset;
      offset += size;
      return `${segment.color} ${start.toFixed(2)}% ${offset.toFixed(2)}%`;
    })
    .join(", ");
  const list = model.segments
    .map((segment) => {
      const percent = model.total > 0 ? (Math.max(0, segment.value) / model.total) * 100 : 0;
      return `
        <div class="chart-segment-item">
          <div class="chart-segment-main">
            <span class="chart-swatch" style="background:${segment.color};"></span>
            <span class="chart-segment-label" title="${escapeText(segment.name)}">${escapeText(segment.name)}</span>
          </div>
          <div class="chart-segment-metrics">
            <div>${escapeText(formatMetric(segment.value))}</div>
            <div>${escapeText(formatPercent(percent))}</div>
          </div>
        </div>
      `;
    })
    .join("");
  return `
    <div class="chart-shell">
      <div class="chart-pie-layout">
        <div class="chart-pie-wrap">
          <div class="chart-pie" style="background:conic-gradient(${gradient});">
            <div class="chart-pie-hole">
              <div class="chart-pie-caption">总量</div>
              <div class="chart-pie-total">${escapeText(formatMetric(model.total))}</div>
            </div>
          </div>
        </div>
        <div class="chart-segment-list">${list}</div>
      </div>
    </div>
  `;
}

function renderTailwindChart(container, chart) {
  const model = buildTailwindChartModel(chart);
  if (!model) {
    container.innerHTML = `<div class="chart-empty">当前图表规格无法用 Tailwind 模式渲染。</div>`;
    return false;
  }
  if (model.type === "pie") {
    container.innerHTML = renderTailwindPieChart(model);
    return true;
  }
  if (model.type === "line") {
    container.innerHTML = renderTailwindLineChart(model);
    return true;
  }
  if (model.type === "stacked_bar") {
    container.innerHTML = renderTailwindStackedBarChart(model);
    return true;
  }
  container.innerHTML = renderTailwindBarChart(model);
  return true;
}

function cloneChartOptionWithLocalizedText(option) {
  if (!option || typeof option !== "object") {
    return option;
  }
  let cloned = option;
  try {
    cloned = JSON.parse(JSON.stringify(option));
  } catch {
    return option;
  }
  const xAxes = Array.isArray(cloned.xAxis) ? cloned.xAxis : cloned.xAxis ? [cloned.xAxis] : [];
  for (const axis of xAxes) {
    if (Array.isArray(axis?.data)) {
      axis.data = axis.data.map((item) => localizeChartLabel(item, String(item ?? "")));
    }
    if (typeof axis?.name === "string") {
      axis.name = localizeChartLabel(axis.name, axis.name);
    }
  }
  const yAxes = Array.isArray(cloned.yAxis) ? cloned.yAxis : cloned.yAxis ? [cloned.yAxis] : [];
  for (const axis of yAxes) {
    if (typeof axis?.name === "string") {
      axis.name = localizeChartLabel(axis.name, axis.name);
    }
  }
  if (cloned.legend && Array.isArray(cloned.legend.data)) {
    cloned.legend.data = cloned.legend.data.map((item) => localizeChartLabel(item, String(item ?? "")));
  }
  if (Array.isArray(cloned.series)) {
    cloned.series = cloned.series.map((series) => {
      const nextSeries = { ...series };
      if (typeof nextSeries.name === "string") {
        nextSeries.name = localizeChartLabel(nextSeries.name, nextSeries.name);
      }
      if (Array.isArray(nextSeries.data)) {
        nextSeries.data = nextSeries.data.map((item, index) => {
          if (item && typeof item === "object" && !Array.isArray(item)) {
            return {
              ...item,
              name: localizeChartLabel(item.name, item.name || `分类 ${index + 1}`),
            };
          }
          return item;
        });
      }
      return nextSeries;
    });
  }
  return cloned;
}

function renderEchartsChart(container, chart) {
  if (!window.echarts || !chart.option) {
    return false;
  }
  const instance = window.echarts.init(container, null, { renderer: "canvas" });
  instance.setOption(cloneChartOptionWithLocalizedText(chart.option));
  const resize = () => instance.resize();
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    container._chartObserver = observer;
  }
  container._chartInstance = instance;
  requestAnimationFrame(resize);
  return true;
}

function disposeChartContainer(container) {
  if (container._chartObserver) {
    container._chartObserver.disconnect();
    delete container._chartObserver;
  }
  if (window.echarts) {
    const instance = container._chartInstance || window.echarts.getInstanceByDom(container);
    if (instance) {
      instance.dispose();
    }
  }
  delete container._chartInstance;
  container.innerHTML = "";
}

function hydrateToolResults(body, toolCalls) {
  const { charts } = extractToolResults(toolCalls);
  if (!charts.length) {
    return;
  }
  const containers = Array.from(body.querySelectorAll(".chart-canvas[data-chart-index]"));
  containers.forEach((container) => {
    const chartIndex = Number(container.dataset.chartIndex);
    if (!Number.isInteger(chartIndex) || chartIndex < 0) {
      return;
    }
    const chart = charts[chartIndex];
    const meta = body.querySelector(`[data-chart-meta-index="${chartIndex}"]`);
    if (!chart) {
      return;
    }
    const reportLike = container.closest(".tool-card")?.dataset.reportLike === "1";
    disposeChartContainer(container);
    const renderer = resolveChartRenderer(chart);
    if (renderer === "tailwind") {
      renderTailwindChart(container, chart);
    } else if (renderer === "echarts") {
      renderEchartsChart(container, chart);
    } else {
      container.innerHTML = `<div class="chart-empty">当前结果缺少可渲染的图表规格。</div>`;
    }
    if (meta) {
      meta.textContent = reportLike ? "" : buildChartMeta(chart, renderer);
    }
  });
}

function disposeCharts(root) {
  for (const container of root.querySelectorAll(".chart-canvas")) {
    disposeChartContainer(container);
  }
}

function buildRequest(message, uploadedFiles = []) {
  const body = new FormData();
  body.append("message", message);
  if (state.conversationId) {
    body.append("conversation_id", state.conversationId);
  }
  const workspaceId = ($("workspaceInput").value || "").trim() || state.workspaceId;
  if (workspaceId) {
    body.append("workspace_id", workspaceId);
  }
  for (const file of uploadedFiles) {
    body.append("files", file);
  }
  return body;
}

function syncSelectedFiles() {
  const list = $("selectedFiles");
  const files = Array.from($("fileInput").files || []);
  if (!files.length) {
    list.innerHTML = "";
    return;
  }
  list.innerHTML = files
    .map((file) => {
      const sizeKb = Math.max(1, Math.round((file.size || 0) / 1024));
      return `<span class="selected-file">${escapeText(file.name)} · ${sizeKb} KB</span>`;
    })
    .join("");
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $("messageInput");
  const fileInput = $("fileInput");
  const button = $("sendButton");
  const message = input.value.trim();
  const selectedFiles = Array.from(fileInput.files || []);
  if (!message && !selectedFiles.length) {
    return;
  }

  input.value = "";
  fileInput.value = "";
  syncSelectedFiles();
  addMessage("user", message || "已上传文件，请处理。", [], [], null, selectedFiles);
  const assistantItem = addMessage("assistant", "运行中...");
  const streamedSteps = [];
  const liveStream = createLiveStreamState();
  let streamedText = "";
  button.disabled = true;
  button.textContent = "执行中";

  function renderLive() {
    updateAssistantMessage(
      assistantItem,
      streamedText || "运行中...",
      streamedSteps,
      [],
      liveStreamHasContent(liveStream) ? liveStream : null,
      [],
    );
  }

  try {
    const response = await fetch("/chat/stream", {
      method: "POST",
      body: buildRequest(message, selectedFiles),
    });
    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new Error(text || response.statusText);
    }
    await consumeEventStream(response.body, {
      step(step) {
        streamedSteps.push(step);
        renderLive();
      },
      delta(payload) {
        if (payload.kind === "assistant" && payload.delta) {
          streamedText += payload.delta;
        } else if (payload.kind === "thinking") {
          if (payload.redacted) {
            liveStream.reasoningRedacted = true;
          } else if (payload.delta) {
            liveStream.thinking += payload.delta;
          }
        } else if (payload.kind === "tool_call") {
          const key = String(payload.tool_call_index ?? payload.tool_call_id ?? "0");
          const tool = liveStream.toolCalls[key] || { name: "", arguments: "" };
          if (payload.phase === "name") {
            tool.name = payload.name || `${tool.name}${payload.delta || ""}`;
          } else if (payload.phase === "arguments") {
            tool.name = payload.name || tool.name;
            tool.arguments += payload.delta || "";
          }
          liveStream.toolCalls[key] = tool;
        }
        renderLive();
      },
      message(payload) {
        state.conversationId = payload.conversation_id;
        state.workspaceId = payload.workspace_id || state.workspaceId;
        if (state.workspaceId) {
          $("workspaceInput").value = state.workspaceId;
        }
        syncSessionMeta();
        const suffix = payload.status === "waiting_for_user" ? "\n\n状态：等待用户补充信息。" : "";
        const requestedInputs = payload.status === "waiting_for_user" ? payload.requested_inputs || [] : [];
        updateAssistantMessage(
          assistantItem,
          `${payload.message || "(empty response)"}${suffix}`,
          payload.steps || streamedSteps,
          requestedInputs,
          null,
          payload.tool_calls || [],
        );
      },
      error(payload) {
        throw new Error(payload.error || "stream error");
      },
    });
  } catch (error) {
    updateAssistantMessage(assistantItem, `请求失败：${error.message}`, streamedSteps, [], null, []);
  } finally {
    button.disabled = false;
    button.textContent = "发送";
    input.focus();
  }
}

async function consumeEventStream(body, handlers) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (!event) {
        continue;
      }
      if (event.type === "step" && handlers.step) {
        handlers.step(event.data);
      } else if (event.type === "delta" && handlers.delta) {
        handlers.delta(event.data);
      } else if (event.type === "message" && handlers.message) {
        handlers.message(event.data);
      } else if (event.type === "error" && handlers.error) {
        handlers.error(event.data);
      }
    }
  }
}

function parseSseChunk(chunk) {
  const lines = chunk.split("\n");
  const typeLine = lines.find((line) => line.startsWith("event:"));
  const dataLines = lines.filter((line) => line.startsWith("data:"));
  if (!typeLine || !dataLines.length) {
    return null;
  }
  const type = typeLine.slice("event:".length).trim();
  const rawData = dataLines.map((line) => line.slice("data:".length).trim()).join("\n");
  return { type, data: JSON.parse(rawData) };
}

function syncSessionMeta() {
  const conversationText = state.conversationId ? state.conversationId : "new";
  const workspaceText = ($("workspaceInput").value || "").trim() || state.workspaceId || "auto";
  $("sessionMeta").textContent = `conversation: ${conversationText} · workspace: ${workspaceText}`;
}

function resetConversation() {
  state.conversationId = null;
  syncSessionMeta();
  $("messages").innerHTML = `
    <div class="message assistant">
      <div class="avatar">AR</div>
      <div class="message-body">
        <div class="bubble markdown-body">新会话已创建。输入任务后，运行日志会折叠在回复下方。</div>
      </div>
    </div>
  `;
}

function rerenderAssistantMessages() {
  for (const item of document.querySelectorAll("#messages .message.assistant")) {
    if (!item._renderState) {
      continue;
    }
    const renderState = item._renderState;
    updateAssistantMessage(
      item,
      renderState.text,
      renderState.steps,
      renderState.requestedInputs,
      renderState.liveStream,
      renderState.toolCalls,
      { scroll: false },
    );
  }
}

function setChartRendererPreference(value, { rerender = true } = {}) {
  state.chartRenderer = normalizeChartRenderer(value);
  saveChartRendererPreference(state.chartRenderer);
  if ($("chartRendererSelect")) {
    $("chartRendererSelect").value = state.chartRenderer;
  }
  if (rerender) {
    rerenderAssistantMessages();
  }
}

function init() {
  state.chartRenderer = loadChartRendererPreference();
  $("chatForm").addEventListener("submit", sendMessage);
  $("resetConversation").addEventListener("click", resetConversation);
  $("chartRendererSelect").addEventListener("change", (event) => {
    setChartRendererPreference(event.target.value);
  });
  $("fileInput").addEventListener("change", syncSelectedFiles);
  $("workspaceInput").addEventListener("input", syncSessionMeta);
  $("messageInput").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      $("chatForm").requestSubmit();
    }
  });
  setChartRendererPreference(state.chartRenderer, { rerender: false });
  syncSessionMeta();
  $("messageInput").focus();
}

init();
