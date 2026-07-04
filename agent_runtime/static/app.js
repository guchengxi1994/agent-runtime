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

function addMessage(role, text, steps = []) {
  const messages = $("messages");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  const avatar = role === "user" ? "U" : "AR";
  item.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div class="message-body">
      <div class="bubble">${escapeText(text)}</div>
      ${role === "assistant" && steps.length ? renderRunLog(steps) : ""}
    </div>
  `;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}

function updateAssistantMessage(item, text, steps = []) {
  const bubble = item.querySelector(".bubble");
  const body = item.querySelector(".message-body");
  bubble.textContent = text;
  const existing = body.querySelector(".run-log");
  if (existing) {
    existing.remove();
  }
  if (steps.length) {
    body.insertAdjacentHTML("beforeend", renderRunLog(steps));
  }
  $("messages").scrollTop = $("messages").scrollHeight;
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
  button.disabled = true;
  button.textContent = "执行中";

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
        updateAssistantMessage(assistantItem, "运行中...", streamedSteps);
      },
      message(payload) {
        state.conversationId = payload.conversation_id;
        $("conversationId").textContent = `conversation: ${state.conversationId}`;
        const suffix = payload.status === "waiting_for_user" ? "\n\n状态：等待用户补充信息。" : "";
        updateAssistantMessage(
          assistantItem,
          `${payload.message || "(empty response)"}${suffix}`,
          payload.steps || streamedSteps,
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
