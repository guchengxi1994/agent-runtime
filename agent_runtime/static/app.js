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
  button.disabled = true;
  button.textContent = "执行中";

  try {
    const response = await fetchJson("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequest(message)),
    });
    state.conversationId = response.conversation_id;
    $("conversationId").textContent = `conversation: ${state.conversationId}`;
    const suffix = response.status === "waiting_for_user" ? "\n\n状态：等待用户补充信息。" : "";
    addMessage("assistant", `${response.message || "(empty response)"}${suffix}`, response.steps || []);
  } catch (error) {
    addMessage("assistant", `请求失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "发送";
    input.focus();
  }
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
