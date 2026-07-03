const state = {
  conversationId: null,
  selectedSkills: new Set(),
  skills: [],
};

const $ = (id) => document.getElementById(id);

function splitCsv(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

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

async function loadHealth() {
  const healthText = $("healthText");
  const pulse = document.querySelector(".pulse");
  try {
    const health = await fetchJson("/health");
    pulse.classList.remove("offline");
    const baseUrl = health.openai_base_url ? ` · ${health.openai_base_url}` : "";
    healthText.textContent = `${health.model}${baseUrl} · ${health.skills} skills · ${health.executable_skills} executable`;
  } catch (error) {
    pulse.classList.add("offline");
    healthText.textContent = `连接失败：${error.message}`;
  }
}

async function loadCatalog() {
  const skills = await fetchJson("/skills");
  state.skills = skills.skills || [];
  renderSkills();
  await loadHealth();
}

function renderSkills() {
  const list = $("skillList");
  if (!state.skills.length) {
    list.innerHTML = '<div class="empty">暂无 skill harness。请在 registry/skills 下添加 SKILL.md。</div>';
    return;
  }
  list.innerHTML = "";
  for (const skill of state.skills) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `skill-card ${state.selectedSkills.has(skill.name) ? "selected" : ""}`;
    const badge = skill.executable ? "executable" : "harness";
    button.innerHTML = `<strong>${escapeText(skill.name)}</strong><span>${escapeText(skill.description)}</span><small>${badge}</small>`;
    button.addEventListener("click", () => {
      if (state.selectedSkills.has(skill.name)) {
        state.selectedSkills.delete(skill.name);
      } else {
        state.selectedSkills.add(skill.name);
      }
      renderSkills();
    });
    list.appendChild(button);
  }
}

function addMessage(role, text) {
  const messages = $("messages");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  const avatar = role === "user" ? "U" : "AR";
  item.innerHTML = `<div class="avatar">${avatar}</div><div class="bubble">${escapeText(text)}</div>`;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

function renderTrace(steps, toolCalls) {
  const traceList = $("traceList");
  if (steps && steps.length) {
    traceList.innerHTML = "";
    for (const step of steps) {
      const item = document.createElement("details");
      item.className = `trace-item step-${escapeText(step.kind || "unknown")}`;
      item.open = step.kind === "waiting_for_user" || step.kind === "sandbox_execution";
      item.innerHTML = `
        <summary>
          <span class="step-kind">${escapeText(step.kind || "unknown")}</span>
          <strong>${escapeText(step.label || "")}</strong>
          <span>${escapeText(step.status || "")}</span>
        </summary>
        <p class="trace-detail">${escapeText(step.detail || "")}</p>
        <pre>${escapeText(JSON.stringify({
          step_id: step.step_id,
          metadata: step.metadata,
          tool_call_id: step.tool_call_id,
          execution_id: step.execution_id,
          created_at: step.created_at,
        }, null, 2))}</pre>
      `;
      traceList.appendChild(item);
    }
    return;
  }
  if (!toolCalls || !toolCalls.length) {
    traceList.innerHTML = '<p class="hint">本轮没有 runtime 调用。</p>';
    return;
  }
  traceList.innerHTML = "";
  for (const call of toolCalls) {
    const item = document.createElement("details");
    item.className = "trace-item";
    item.open = call.tool_name === "activate_skill";
    item.innerHTML = `
      <summary><strong>${escapeText(call.tool_name)}</strong> · ${escapeText(call.tool_call_id)}</summary>
      <pre>${escapeText(JSON.stringify({ arguments: call.arguments, result: call.result }, null, 2))}</pre>
    `;
    traceList.appendChild(item);
  }
}

function buildRequest(message) {
  const user = {
    id: $("userId").value.trim() || "anonymous",
    tenant_id: $("tenantId").value.trim() || null,
    roles: splitCsv($("roles").value),
    scopes: splitCsv($("scopes").value),
  };
  const body = {
    message,
    user,
  };
  if (state.conversationId) {
    body.conversation_id = state.conversationId;
  }
  if (state.selectedSkills.size) {
    body.skill_ids = Array.from(state.selectedSkills);
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
    addMessage("assistant", `${response.message || "(empty response)"}${suffix}`);
    renderTrace(response.steps, response.tool_calls);
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
      <div class="bubble">新会话已创建。你可以让模型自动选择 skill，也可以在左侧固定一个 skill。</div>
    </div>
  `;
  $("traceList").innerHTML = '<p class="hint">skill 激活、用户输入请求和 sandbox 执行结果会显示在这里。</p>';
}

function init() {
  $("chatForm").addEventListener("submit", sendMessage);
  $("resetConversation").addEventListener("click", resetConversation);
  $("refreshCatalog").addEventListener("click", loadCatalog);
  $("clearTrace").addEventListener("click", () => {
    $("traceList").innerHTML = '<p class="hint">skill 激活、用户输入请求和 sandbox 执行结果会显示在这里。</p>';
  });
  $("messageInput").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      $("chatForm").requestSubmit();
    }
  });
  loadCatalog().catch((error) => {
    $("skillList").innerHTML = `<div class="empty">加载 catalog 失败：${escapeText(error.message)}</div>`;
  });
}

init();

