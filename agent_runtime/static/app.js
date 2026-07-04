const state = {
  conversationId: null,
};

const $ = (id) => document.getElementById(id);

function escapeText(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
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

function addMessage(role, text, steps = [], requestedInputs = [], liveStream = null) {
  const messages = $("messages");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  const avatar = role === "user" ? "U" : "AR";
  item.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div class="message-body">
      <div class="bubble">${escapeText(text)}</div>
      ${role === "assistant" && liveStream ? renderLiveStream(liveStream) : ""}
      ${role === "assistant" && requestedInputs.length ? renderRequestedInputs(requestedInputs) : ""}
      ${role === "assistant" && steps.length ? renderRunLog(steps) : ""}
    </div>
  `;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}

function updateAssistantMessage(item, text, steps = [], requestedInputs = [], liveStream = null) {
  const bubble = item.querySelector(".bubble");
  const body = item.querySelector(".message-body");
  bubble.textContent = text;
  for (const existing of body.querySelectorAll(".live-stream, .requested-inputs, .run-log")) {
    existing.remove();
  }
  if (liveStream) {
    body.insertAdjacentHTML("beforeend", renderLiveStream(liveStream));
  }
  if (requestedInputs.length) {
    body.insertAdjacentHTML("beforeend", renderRequestedInputs(requestedInputs));
  }
  if (steps.length) {
    body.insertAdjacentHTML("beforeend", renderRunLog(steps));
  }
  $("messages").scrollTop = $("messages").scrollHeight;
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
  const statusText = waitingStep
    ? "等待用户输入"
    : toolSteps.length
      ? `使用工具 ${toolSteps.map((step) => step.label).join(", ")}`
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

function buildRequest(message) {
  const body = { message };
  if (state.conversationId) {
    body.conversation_id = state.conversationId;
  }
  return body;
}

async function sendMessage(event) {
  event.preventDefault();
  const input = $("messageInput");
  const button = $("sendButton");
  const message = input.value.trim();
  if (!message) {
    return;
  }

  input.value = "";
  addMessage("user", message);
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
    );
  }

  try {
    const response = await fetch("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequest(message)),
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
        $("conversationId").textContent = `conversation: ${state.conversationId}`;
        const suffix = payload.status === "waiting_for_user" ? "\n\n状态：等待用户补充信息。" : "";
        const requestedInputs = payload.status === "waiting_for_user" ? payload.requested_inputs || [] : [];
        updateAssistantMessage(
          assistantItem,
          `${payload.message || "(empty response)"}${suffix}`,
          payload.steps || streamedSteps,
          requestedInputs,
          null,
        );
      },
      error(payload) {
        throw new Error(payload.error || "stream error");
      },
    });
  } catch (error) {
    updateAssistantMessage(assistantItem, `请求失败：${error.message}`, streamedSteps);
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

function resetConversation() {
  state.conversationId = null;
  $("conversationId").textContent = "conversation: new";
  $("messages").innerHTML = `
    <div class="message assistant">
      <div class="avatar">AR</div>
      <div class="message-body">
        <div class="bubble">新会话已创建。输入任务后，运行日志会折叠在回复下方。</div>
      </div>
    </div>
  `;
}

function init() {
  $("chatForm").addEventListener("submit", sendMessage);
  $("resetConversation").addEventListener("click", resetConversation);
  $("messageInput").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      $("chatForm").requestSubmit();
    }
  });
  $("messageInput").focus();
}

init();
