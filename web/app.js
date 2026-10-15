"use strict";

const element = (id) => document.getElementById(id);
const phases = {
  idle: ["准备好，聊一聊", "点击开始，让机器人听见你。"],
  connecting: ["正在准备对话", "正在连接机器人与所选 Grok Bot。"],
  listening: ["正在听你说", "说完一句后，等待 Grok Bot 回答。"],
  thinking: ["Grok Bot 正在思考", "正在准备回答，此时暂停聆听。"],
  speaking: ["机器人正在回答", "回答结束后继续听；也可以点击停止回答，继续听。"],
  stopping: ["正在结束对话", "正在释放麦克风与播放任务。"],
  error: ["对话暂时中断", "检查连接或设置后，再试一次。"],
};
let state = null;
let available = false;
let busy = false;
let lastMessages = "";
let localError = "";
let previousFocus = null;
let botsLoading = false;
let botChoices = [];
let textSubmitting = false;
let askController = null;

async function api(path, body, requestController = null) {
  const controller = requestController || new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, path === "/api/ask" ? 210000 : 12000);
  try {
    const options = {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store", signal: controller.signal,
    };
    let response;
    for (let attempt = 0; ; attempt++) {
      try {
        response = await fetch(path, options);
        break;
      } catch (error) {
        if (controller.signal.aborted || error.name === "AbortError") throw error;
        if (!(error instanceof TypeError)) throw error;
        // Retry only a failed read, within the original request deadline.
        // A POST may already have reached the application and must not repeat.
        if (body === undefined && attempt === 0) continue;
        const delivery = body === undefined ? "" : "；操作可能已送达，请勿自动重发";
        throw new Error(`无法连接本机语音应用，请确认应用仍在运行，并从应用入口重新打开页面${delivery}。`);
      }
    }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || result.error || `请求失败 (${response.status})`);
    return result;
  } catch (error) {
    if (timedOut) throw new Error("应用响应超时，请检查连接后重试；已经发送的消息请勿自动重发。");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function renderMessages(messages) {
  const snapshot = JSON.stringify(messages);
  if (snapshot === lastMessages) return;
  lastMessages = snapshot;
  const container = element("messages");
  const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
  if (!messages.length) {
    container.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const mark = document.createElement("span");
    mark.className = "empty-mark"; mark.textContent = "“"; mark.setAttribute("aria-hidden", "true");
    const heading = document.createElement("h3"); heading.textContent = "从一句你好开始";
    const description = document.createElement("p"); description.textContent = "开始对话后，真实的语音转写会出现在这里。";
    empty.append(mark, heading, description); container.append(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const message of messages) {
    const article = document.createElement("article"); article.className = "message"; article.dataset.role = message.role;
    const role = document.createElement("span"); role.className = "message-role";
    role.textContent = message.role === "user" ? "你" : message.role === "assistant" ? "GROK" : "系统";
    const text = document.createElement("p"); text.className = "message-text"; text.textContent = message.text || "";
    article.append(role, text); fragment.append(article);
  }
  container.replaceChildren(fragment);
  if (nearBottom) container.scrollTop = container.scrollHeight;
}

function render() {
  const phase = state?.phase || "idle";
  const active = Boolean(state?.enabled);
  const textActive = Boolean(state?.text_active || textSubmitting);
  const transitioning = phase === "connecting" || phase === "stopping";
  const configured = Boolean(state?.configured);
  const cleanup = Boolean(state?.cleanup_pending && !active && phase !== "stopping");
  let [label, detail] = phases[phase] || ["等待应用状态", "等待应用服务响应。"];
  if (!available) {
    label = "应用连接已断开";
    detail = "暂时无法确认机器人状态，正在重新连接应用。";
  } else if (!configured && !active) {
    label = "为声音接上灵感";
    detail = "先在本机登录 Grok Bot，并在设置中选择一个 Bot。";
  } else if (!state.connected && !active) {
    label = "等待机器人连接";
    detail = "先连接机器人，再开始对话。";
  } else if (state.muted && active) {
    label = "聆听已暂停";
    detail = "暂时不接收新的语音输入；已有回答仍可能继续。";
  }
  if (available && textActive && !active) {
    label = "正在验证文字回复";
    detail = "只验证 Grok Bot 文字桥，不启动麦克风或播报。";
  }
  if (cleanup) {
    label = "语音资源待释放";
    detail = state.connected ? "点击重试，确认机器人语音会话已结束。" : "等待原机器人重新连接后重试释放。";
  }
  element("phase-label").textContent = label;
  element("phase-detail").textContent = detail;
  element("voice-visual").dataset.phase = available && !state?.muted ? phase : "idle";
  element("connection").textContent = !available ? "应用连接中断" : state.connected ? "机器人已连接" : "机器人未连接";
  element("connection").dataset.connected = String(available && Boolean(state?.connected));
  const main = element("main-button");
  main.replaceChildren(document.createTextNode(busy ? "处理中…" : cleanup ? "重试释放语音" : textActive && !active ? "取消文字验证" : active ? "结束对话" : configured ? "开始对话" : "选择 Grok Bot"));
  const arrow = document.createElement("span"); arrow.textContent = active || textActive ? "×" : "↗"; arrow.setAttribute("aria-hidden", "true"); main.append(arrow);
  main.disabled = !available || busy || transitioning || (configured && !active && !textActive && !state.connected);
  element("mute-button").disabled = !available || !active || busy || transitioning;
  element("mute-button").textContent = state?.muted ? "恢复聆听" : "暂停聆听";
  element("mute-button").setAttribute("aria-pressed", String(Boolean(state?.muted)));
  element("interrupt-button").disabled = !available || !active || busy || !["speaking", "thinking"].includes(phase);
  element("text-input").disabled = !available || !configured || (active && !state?.connected) || transitioning || textActive;
  element("send-button").disabled = element("text-input").disabled || busy || !element("text-input").value.trim();
  element("send-button").setAttribute("aria-label", active ? "发送文字并由机器人播报" : "文字验证（不播报）");
  element("text-label").textContent = active ? "发送文字给机器人" : "文字验证（不播报）";
  element("send-button").title = active ? "发送文字并播报" : "文字验证（不播报）";
  element("text-input").placeholder = active ? "输入一句话，由机器人播报…" : "文字验证（不播报）：输入一句话…";
  element("composer-note").textContent = active ? "文字发送后由机器人播报；这不是麦克风输入。" : "文字验证（不播报） · 无需设备在线，不计入语音轮次。";
  element("device-label").textContent = `设备 · ${state?.device_id || "等待连接"}`;
  element("bot-label").textContent = `Bot · ${state?.bot_name || state?.bot || "未选择"}`;
  const latency = state?.metrics?.agent_response_ms;
  element("latency-label").textContent = `Bot 回复耗时 · ${latency == null ? "—" : Math.round(latency) + " ms"}`;
  element("turn-count").textContent = `${state?.metrics?.turns || 0} 语音轮`;
  element("model-label").textContent = "JOYINSIDE × GROK BOT";
  element("save-settings").disabled = busy || botsLoading || textActive || !element("bot-select").value;
  const error = localError || state?.error || "";
  element("error").textContent = error;
  element("error").hidden = !error;
  renderMessages(state?.messages || []);
}

async function refresh() {
  try {
    const next = await api("/api/state");
    state = next; available = true;
  } catch {
    available = false;
  }
  render();
}

async function action(path, body = {}) {
  if (busy) return false;
  busy = true; localError = ""; render();
  try {
    await api(path, body);
    await refresh();
    return true;
  } catch (error) {
    localError = error.message;
    element("settings-feedback").textContent = error.message;
    return false;
  } finally {
    busy = false; render();
  }
}

async function loadBots() {
  if (botsLoading) return;
  botsLoading = true;
  element("refresh-bots").disabled = true;
  element("bot-select").disabled = true;
  element("bot-status").textContent = "正在读取本机 Grok Bot…";
  render();
  try {
    const result = await api("/api/bots");
    botChoices = Array.isArray(result.bots) ? result.bots : [];
    const select = element("bot-select");
    const previous = select.value;
    const placeholder = document.createElement("option");
    placeholder.value = ""; placeholder.textContent = botChoices.length ? "请选择一个 Bot" : "未找到可用 Bot";
    select.replaceChildren(placeholder);
    for (const bot of botChoices) {
      const option = document.createElement("option");
      option.value = bot.id; option.textContent = bot.name || bot.id;
      select.append(option);
    }
    const selected = botChoices.find((bot) => bot.id === state?.bot || bot.name === state?.bot_name || bot.name === state?.bot);
    select.value = selected?.id || (botChoices.some((bot) => bot.id === previous) ? previous : "");
    element("bot-status").textContent = botChoices.length ? `找到 ${botChoices.length} 个 Bot。更换 Bot 在下一次对话生效。` : "请打开 Grok Bot，确认已登录且已创建 Bot，然后刷新列表。";
  } catch (error) {
    element("bot-select").replaceChildren();
    const option = document.createElement("option"); option.value = ""; option.textContent = "无法读取 Bot 列表";
    element("bot-select").append(option);
    element("bot-status").textContent = `无法读取 Bot 列表。${error.message}`;
  } finally {
    botsLoading = false;
    element("refresh-bots").disabled = false;
    element("bot-select").disabled = false;
    render();
  }
}

function openSettings() {
  previousFocus = document.activeElement;
  element("settings-feedback").textContent = "";
  element("settings-dialog").showModal();
  loadBots();
}

element("settings-button").addEventListener("click", openSettings);
element("close-settings").addEventListener("click", () => element("settings-dialog").close());
element("settings-dialog").addEventListener("close", () => {
  previousFocus?.focus();
});
element("refresh-bots").addEventListener("click", loadBots);
element("bot-select").addEventListener("change", render);
element("main-button").addEventListener("click", async () => {
  if (state?.cleanup_pending && !state?.enabled) {
    await action("/api/stop");
    return;
  }
  if (!state?.enabled && (state?.text_active || textSubmitting)) {
    askController?.abort();
    await action("/api/stop");
    return;
  }
  if (!state?.configured && !state?.enabled) return openSettings();
  await action(state?.enabled ? "/api/stop" : "/api/start");
});
element("mute-button").addEventListener("click", () => action("/api/mute", { muted: !state?.muted }));
element("interrupt-button").addEventListener("click", () => action("/api/interrupt"));
element("text-input").addEventListener("input", render);
element("text-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = element("text-input").value.trim();
  if (!text || element("send-button").disabled) return;
  if (state?.enabled) {
    if (await action("/api/text", { text })) element("text-input").value = "";
  } else {
    textSubmitting = true;
    localError = "";
    const controller = new AbortController();
    askController = controller;
    render();
    try {
      await api("/api/ask", { text }, controller);
      if (element("text-input").value.trim() === text) element("text-input").value = "";
    } catch (error) {
      if (error.name !== "AbortError") localError = error.message;
    } finally {
      if (askController === controller) askController = null;
      textSubmitting = false;
      await refresh();
    }
  }
  render();
});
element("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const bot = element("bot-select").value;
  if (!bot) {
    element("settings-feedback").textContent = "请先选择一个 Grok Bot。";
    element("bot-select").focus(); return;
  }
  element("save-settings").disabled = true;
  if (await action("/api/settings", { bot })) {
    element("settings-feedback").textContent = "设置已保存。";
  }
  render();
});
element("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = element("pair-code").value.trim();
  if (code && await action("/api/pair", { code })) {
    element("pair-code").value = "";
    element("settings-feedback").textContent = state?.connected ? "机器人已连接。" : "配对请求已发送，等待机器人连接。";
  }
});

async function poll() {
  if (!busy) await refresh();
  setTimeout(poll, document.hidden ? 4000 : 800);
}
poll();
