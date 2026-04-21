/* Agent Console — app logic. The UI has one chat path: AgentLoop v2. */

(() => {
  // ---------- state ----------
  const state = {
    conversations: [],
    currentId: null,
    history: [],          // [{role, content}]
    pendingImages: [],    // [{base64, mediaType, name}]
    sending: false,
  };

  // ---------- DOM ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = {
    stream:        $("#chat-stream"),
    empty:         $("#chat-empty"),
    input:         $("#chat-input"),
    send:          $("#chat-send"),
    mode:          $("#chat-mode"),
    status:        $("#chat-status"),
    title:         $("#conv-title"),
    newChat:       $("#new-chat-btn"),
    convList:      $("#conversation-list"),
    imageInput:    $("#image-file-input"),
    imagePreview:  $("#image-preview-container"),
    imageList:     $("#image-preview-list"),
    memoryList:    $("#memory-list"),
    memoryEmpty:   $("#memory-empty"),
    memoryCount:   $("#memory-count"),
    memoryTokens:  $("#memory-tokens"),
    kbBadge:       $("#kb-badge"),
  };

  // ---------- util ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  const setStatus = (text, cls = "") => {
    el.status.textContent = text;
    el.status.className = `chat-status ${cls}`;
  };
  const CHAT_ENDPOINT = "/api/agent_chat_v2";
  const autoGrow = () => {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 240)}px`;
  };
  // Conversation timestamps are ISO strings (e.g. "2026-04-20T12:34:56") —
  // raw Date parses them fine, but a few legacy rows also use " " separators,
  // so normalise first. Returns "" when unparseable.
  function formatConvDate(raw) {
    if (!raw) return "";
    const iso = typeof raw === "string" ? raw.replace(" ", "T") : raw;
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString();
  }

  // ---------- modals ----------
  const openModal = (name) => {
    const m = document.getElementById(`${name}-modal`);
    if (!m) return;
    m.classList.add("open");
    m.setAttribute("aria-hidden", "false");
    if (name === "memory") loadMemories();
  };
  const closeModal = (m) => {
    m.classList.remove("open");
    m.setAttribute("aria-hidden", "true");
  };
  $$("[data-open]").forEach((b) =>
    b.addEventListener("click", () => openModal(b.dataset.open))
  );
  $$("[data-close]").forEach((b) =>
    b.addEventListener("click", () => {
      const m = b.closest(".modal");
      if (m) closeModal(m);
    })
  );
  $$(".modal").forEach((m) =>
    m.addEventListener("click", (e) => {
      if (e.target === m) closeModal(m);
    })
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $$(".modal.open").forEach(closeModal);
  });

  // deep-link: ?modal=memory auto-opens
  const bodyModal = document.body.dataset.modal;
  if (bodyModal) openModal(bodyModal);

  // ---------- conversations ----------
  async function loadConversations() {
    try {
      const res = await fetch("/api/conversations");
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "load failed");
      state.conversations = data.conversations || [];
      renderConversations();
    } catch (err) {
      console.error("loadConversations", err);
    }
  }

  function renderConversations() {
    if (!state.conversations.length) {
      el.convList.innerHTML = `<p class="side-empty">No conversations yet.</p>`;
      return;
    }
    el.convList.innerHTML = state.conversations.map((c) => {
      const active = c.id === state.currentId ? " is-active" : "";
      const title = esc(c.title || "Untitled");
      const when = formatConvDate(c.updated_at || c.created_at);
      return `
        <button class="conv-item${active}" data-conv-id="${c.id}">
          <span class="conv-item-title">${title}</span>
          ${when ? `<span class="conv-item-sub">${esc(when)}</span>` : ""}
        </button>`;
    }).join("");
    $$(".conv-item", el.convList).forEach((b) =>
      b.addEventListener("click", () => openConversation(b.dataset.convId))
    );
  }

  async function openConversation(id) {
    try {
      const res = await fetch(`/api/conversations/${id}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "load failed");
      const conv = data.conversation || {};
      state.currentId = id;
      state.history = (conv.messages || []).map((m) => ({
        role: m.role,
        content: m.content || "",
      }));
      el.title.textContent = conv.title || "Conversation";
      renderHistory();
      renderConversations();
    } catch (err) {
      setStatus(err.message, "err");
    }
  }

  function renderHistory() {
    el.stream.innerHTML = "";
    if (!state.history.length) {
      el.stream.appendChild(el.empty);
      return;
    }
    state.history.forEach((m) => {
      const turn = document.createElement("article");
      turn.className = `turn turn-${m.role === "user" ? "user" : "assistant"}`;
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = m.content || "";
      turn.appendChild(bubble);
      el.stream.appendChild(turn);
    });
    el.stream.scrollTop = el.stream.scrollHeight;
  }

  async function startNewConversation() {
    state.currentId = null;
    state.history = [];
    state.pendingImages = [];
    el.title.textContent = "New conversation";
    el.stream.innerHTML = "";
    el.stream.appendChild(el.empty);
    renderPreview();
    renderConversations();
    el.input.focus();
  }

  el.newChat?.addEventListener("click", startNewConversation);

  async function persistConversationIfNeeded(firstText) {
    if (state.currentId) return state.currentId;
    const title = firstText.slice(0, 40).replace(/\n/g, " ");
    const res = await fetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "create failed");
    const convId = data.id || data.conversation_id || data.conversation?.id;
    if (!convId) throw new Error("create failed: missing conversation id");
    state.currentId = convId;
    el.title.textContent = title;
    return convId;
  }

  async function persistMessage(role, content) {
    if (!state.currentId) return;
    try {
      await fetch(`/api/conversations/${state.currentId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, content }),
      });
    } catch (err) {
      console.warn("persistMessage", err);
    }
  }

  // ---------- activity (tool trace) ----------
  class ActivityView {
    constructor(host) {
      this.events = [];
      this.start = Date.now();
      this.expanded = false;
      this.toolCallCount = 0;
      this.host = host;

      this.bar = document.createElement("div");
      this.bar.className = "activity-bar";
      this.bar.dataset.status = "thinking";
      this.bar.dataset.expanded = "false";
      this.bar.innerHTML = `
        <span class="activity-icon">●</span>
        <span class="activity-summary">Thinking…</span>
        <span class="activity-time">0s</span>
        <span class="activity-arrow">›</span>`;
      this.bar.onclick = () => this.toggle();

      this.panel = document.createElement("div");
      this.panel.className = "activity-panel";
      this.panel.hidden = true;
      this.panel.innerHTML = `
        <div class="activity-header">
          <span>Activity <span class="activity-duration">· 0s</span></span>
          <button class="activity-close" type="button">✕</button>
        </div>
        <div class="activity-title">Thinking</div>
        <ul class="activity-list"></ul>`;
      this.list = this.panel.querySelector(".activity-list");
      this.panel.querySelector(".activity-close").onclick = (e) => {
        e.stopPropagation();
        this.collapse();
      };

      host.appendChild(this.bar);
      host.appendChild(this.panel);

      this.tick = setInterval(() => {
        const s = Math.round((Date.now() - this.start) / 1000);
        this.bar.querySelector(".activity-time").textContent = `${s}s`;
        this.panel.querySelector(".activity-duration").textContent = `· ${s}s`;
      }, 1000);
    }
    toggle() {
      this.expanded = !this.expanded;
      this.bar.dataset.expanded = this.expanded ? "true" : "false";
      this.panel.hidden = !this.expanded;
      if (this.expanded) {
        requestAnimationFrame(() => {
          this.host.scrollIntoView({ block: "nearest", behavior: "smooth" });
        });
      }
    }
    collapse() { if (this.expanded) this.toggle(); }
    addEvent(event) {
      const existing = this.events.findIndex((e) => e.id === event.id);
      const isThinking = (event.type || "").startsWith("thinking");
      const isToolCall = event.type === "tool_call";
      const isToolResult = event.type === "tool_result";
      const isManifest = event.type === "tool_manifest";
      const detailText = event.detail || "";

      let detailHtml = `<div class="activity-item-detail">${esc(detailText)}</div>`;
      if (isToolCall) {
        this.toolCallCount += existing >= 0 ? 0 : 1;
        detailHtml = `<div class="activity-item-detail activity-tool-args"><code>${esc(detailText)}</code></div>`;
      } else if (isToolResult) {
        const cls = event.status === "error" ? "activity-tool-error" : "activity-tool-success";
        detailHtml = `<div class="activity-item-detail ${cls}"><code>${esc(detailText)}</code></div>`;
      } else if (isThinking) {
        detailHtml = `<div class="activity-item-detail activity-reasoning">${esc(detailText)}</div>`;
      } else if (isManifest) {
        const meta = event.meta || {};
        const tools = Array.isArray(meta.tools) ? meta.tools : [];
        detailHtml = `<div class="activity-item-detail activity-tool-args"><code>${esc(tools.join(", ") || detailText)}</code></div>`;
      }

      let iconHtml = `<span class="dot ${event.status === "start" || event.status === "progress" ? "pending" : ""}"></span>`;
      if (isToolCall) iconHtml = `<span class="tool-icon">🔧</span>`;
      else if (isToolResult) iconHtml = event.status === "error" ? `<span class="tool-icon">⚠</span>` : `<span class="tool-icon">✓</span>`;

      if (existing >= 0) {
        this.events[existing] = event;
        const li = this.list.querySelector(`[data-id="${event.id}"]`);
        if (li) {
          li.querySelector(".activity-item-title span:last-child").textContent = event.title || "";
          const oldDetail = li.querySelector(".activity-item-detail");
          if (oldDetail) oldDetail.outerHTML = detailHtml.replace(/^<div class="activity-item-detail/, `<div class="activity-item-detail`);
        }
      } else {
        this.events.push(event);
        const li = document.createElement("li");
        li.dataset.id = event.id;
        li.innerHTML = `
          <div class="activity-item-title">${iconHtml}<span>${esc(event.title || "")}</span></div>
          ${detailHtml}`;
        this.list.appendChild(li);
      }

      // Update top bar summary
      const latest = event.title || (isToolCall ? "Calling tool" : "Working");
      this.bar.querySelector(".activity-summary").textContent = latest;
    }
    finish(totalMs, payload) {
      clearInterval(this.tick);
      if (!payload?.error && this.toolCallCount === 0) {
        this.addEvent({
          id: `no_tools_${this.start}`,
          type: "tool_result",
          title: "No tools used",
          detail: "The model completed this turn without emitting a tool call.",
          status: "done",
        });
      }
      const s = totalMs ? (totalMs / 1000).toFixed(1) : Math.round((Date.now() - this.start) / 1000);
      this.bar.dataset.status = payload && payload.error ? "error" : "done";
      this.bar.querySelector(".activity-icon").textContent = payload && payload.error ? "⚠" : "✓";
      this.bar.querySelector(".activity-summary").textContent = payload && payload.error
        ? "Failed"
        : (this.toolCallCount
          ? `Done · ${this.toolCallCount} tool call${this.toolCallCount === 1 ? "" : "s"}`
          : "Done · no tools");
      this.bar.querySelector(".activity-time").textContent = `${s}s`;
    }
  }

  // ---------- image upload ----------
  function renderPreview() {
    if (!state.pendingImages.length) {
      el.imagePreview.hidden = true;
      el.imageList.innerHTML = "";
      return;
    }
    el.imagePreview.hidden = false;
    const hasImageModel = Boolean(document.body.dataset.imageGenModel);
    const acceptsMultimodal = CHAT_ENDPOINT === "/api/agent_chat_v2";
    const warnBanner = hasImageModel || acceptsMultimodal
      ? ""
      : `<p class="status-inline warn composer-preview-warn">⚠ 当前 profile 未配置图像模型，发送时图片会被丢弃。</p>`;
    el.imageList.innerHTML = warnBanner + state.pendingImages.map((img, i) =>
      `<img src="data:${img.mediaType};base64,${img.base64}" data-idx="${i}" title="${esc(img.name)}" />`
    ).join("");
    $$("img[data-idx]", el.imageList).forEach((im) =>
      im.addEventListener("click", () => {
        state.pendingImages.splice(Number(im.dataset.idx), 1);
        renderPreview();
      })
    );
  }

  async function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result).split(",")[1]);
      fr.onerror = reject;
      fr.readAsDataURL(file);
    });
  }

  async function addImageFiles(files) {
    for (const f of files) {
      if (!f.type.startsWith("image/")) continue;
      try {
        const base64 = await fileToBase64(f);
        state.pendingImages.push({ base64, mediaType: f.type, name: f.name });
      } catch (err) {
        console.error(err);
      }
    }
    renderPreview();
  }

  el.imageInput?.addEventListener("change", (e) => {
    addImageFiles(e.target.files);
    e.target.value = "";
  });

  el.input?.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length) {
      e.preventDefault();
      addImageFiles(files);
    }
  });

  ["dragover", "drop"].forEach((ev) =>
    el.input?.addEventListener(ev, (e) => {
      e.preventDefault();
      if (ev === "drop") addImageFiles(Array.from(e.dataTransfer.files || []));
    })
  );

  // ---------- send ----------
  async function sendMessage() {
    if (state.sending) return;
    const text = el.input.value.trim();
    if (!text) return;

    const endpoint = CHAT_ENDPOINT;
    // AgentLoop v2 accepts image blocks directly as multimodal input.
    const imageGenModel = document.body.dataset.imageGenModel || "";
    if (endpoint === "/api/agent_chat" && state.pendingImages.length > 0 && !imageGenModel) {
      const proceed = confirm(
        "当前 profile 未配置图像模型，附加的图片将被忽略。\n继续发送纯文本吗？\n\n（如需处理图片，请在 Settings → + New profile 中配置 Image generation。）"
      );
      if (!proceed) return;
      state.pendingImages = [];
      renderPreview();
    }

    state.sending = true;
    el.send.disabled = true;
    setStatus("");

    // hide empty state
    if (el.empty.parentElement) el.empty.remove();

    // render user bubble
    const userTurn = document.createElement("article");
    userTurn.className = "turn turn-user";
    const userBubble = document.createElement("div");
    userBubble.className = "bubble";
    userBubble.textContent = text;
    userTurn.appendChild(userBubble);
    el.stream.appendChild(userTurn);

    // assistant scaffold
    const asstTurn = document.createElement("article");
    asstTurn.className = "turn turn-assistant";
    const activity = new ActivityView(asstTurn);
    const asstBubble = document.createElement("div");
    asstBubble.className = "bubble";
    asstTurn.appendChild(asstBubble);
    el.stream.appendChild(asstTurn);
    el.stream.scrollTop = el.stream.scrollHeight;

    // reset composer
    el.input.value = "";
    autoGrow();

    try {
      await persistConversationIfNeeded(text);
      await persistMessage("user", text);
      await loadConversations();

      const payload = {
        message: text,
        mode: el.mode.value,
        history: state.history,
        conversation_id: state.currentId,
      };
      if (state.pendingImages.length) {
        payload.images = state.pendingImages.map((img) => ({
          base64: img.base64, media_type: img.mediaType, name: img.name,
        }));
      }

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const errText = await res.text();
        let detail = errText;
        try {
          const errJson = JSON.parse(errText);
          detail = errJson.error || errJson.detail || errText;
        } catch {}
        throw new Error(detail || `Stream connection failed (${res.status})`);
      }
      if (!res.body) throw new Error("Stream connection failed: empty response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let fullText = "";
      let sources = [];

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          if (!part.trim() || part.startsWith(":")) continue;
          let event = "", data = "";
          for (const line of part.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) data = line.slice(5).trim();
          }
          if (!event || !data) continue;
          let payload;
          try { payload = JSON.parse(data); } catch { continue; }

          if (event === "activity") {
            activity.addEvent(payload);
          } else if (event === "token") {
            fullText += payload.text || "";
            asstBubble.textContent = fullText;
            el.stream.scrollTop = el.stream.scrollHeight;
          } else if (event === "done") {
            sources = payload.sources || [];
            activity.finish(payload.total_time_ms || 0, payload);
          }
        }
      }

      state.pendingImages = [];
      renderPreview();

      if (sources.length) {
        const sourceEl = document.createElement("div");
        sourceEl.className = "chat-sources";
        sourceEl.textContent = "Sources: " + sources.map((s, i) =>
          `[${i + 1}] ${s.kb ? s.kb + ": " : ""}${s.path || "unknown"}`
        ).join(" · ");
        asstBubble.appendChild(sourceEl);
      }
      // update history, persist
      state.history.push({ role: "user", content: text });
      state.history.push({ role: "assistant", content: fullText });
      await persistMessage("assistant", fullText);
      await loadConversations();
    } catch (err) {
      console.error(err);
      setStatus(err.message, "err");
      asstBubble.textContent = `[error] ${err.message}`;
      activity.finish(0, { error: true });
    } finally {
      state.sending = false;
      el.send.disabled = false;
      el.input.focus();
    }
  }

  el.send?.addEventListener("click", sendMessage);
  el.input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage();
    }
  });
  el.input?.addEventListener("input", autoGrow);

  // suggestion chips
  $$(".chip").forEach((c) =>
    c.addEventListener("click", () => {
      el.input.value = c.dataset.prompt || c.textContent;
      autoGrow();
      el.input.focus();
    })
  );

  // ---------- memories ----------
  async function loadMemories() {
    try {
      const res = await fetch("/api/memories");
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "load failed");
      const items = data.memories || [];
      el.memoryCount.textContent = items.length;
      el.memoryTokens.textContent = data.total_tokens || 0;
      if (!items.length) {
        el.memoryList.innerHTML = `<p class="muted">No memories yet.</p>`;
        return;
      }
      el.memoryList.innerHTML = items.map((m) => `
        <div class="list-row">
          <div>
            <p class="list-title">${esc(m.key || m.id)}</p>
            <p class="list-sub">${esc((m.value || "").slice(0, 140))}</p>
          </div>
          <div class="list-actions">
            <button class="btn btn-ghost btn-sm" data-mem-del="${esc(m.id)}">Delete</button>
          </div>
        </div>`).join("");
      $$("[data-mem-del]", el.memoryList).forEach((b) =>
        b.addEventListener("click", async () => {
          await fetch(`/api/memories/${b.dataset.memDel}`, { method: "DELETE" });
          loadMemories();
        })
      );
    } catch (err) {
      el.memoryList.innerHTML = `<p class="status-inline warn">${esc(err.message)}</p>`;
    }
  }

  // ---------- add profile ----------
  const addForm = $("#profile-add-form");
  const addToggle = $("#profile-add-toggle");
  const addCancel = $("#profile-add-cancel");
  const addStatus = $("#profile-add-status");
  const imageEnable = $("#image-gen-enable");
  const imageBody = $("#image-gen-fieldset .fieldset-body");

  // Static fallback catalogs. Users without a working key still get sensible
  // options; Detect merges real results from the vendor on top.
  const LLM_CATALOG = {
    openai: ["gpt-5.4", "gpt-5.4-mini", "gpt-5.2", "gpt-5-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3", "o3-mini", "o1", "o1-mini"],
    deepseek: ["deepseek-chat", "deepseek-reasoner"],
    zhipu: ["glm-4.7", "glm-4.6", "glm-4-plus", "glm-4-flash", "glm-4-air"],
    openai_compat: [],
  };
  const IMAGE_CATALOG = {
    openai: ["gpt-image-1", "dall-e-3", "dall-e-2"],
    zhipu: ["cogview-3-plus", "cogview-3"],
  };
  const VENDOR_BASE_URLS = {
    openai: "https://api.openai.com/v1",
    deepseek: "https://api.deepseek.com/v1",
    zhipu: "https://open.bigmodel.cn/api/paas/v4",
    openai_compat: "",
  };

  function fillSelect(select, items, current) {
    if (!select) return;
    select.innerHTML = "";
    if (!items.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "— enter key, then Detect —";
      opt.disabled = true;
      opt.selected = true;
      select.appendChild(opt);
      return;
    }
    items.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (m === current) opt.selected = true;
      select.appendChild(opt);
    });
  }

  function setBaseUrlDefault(input, vendor) {
    if (!input || input.value) return;  // don't overwrite user edits
    input.value = VENDOR_BASE_URLS[vendor] || "";
  }

  async function detectModels(section) {
    const isLlm = section === "llm";
    const vendor = $(isLlm ? "#llm-vendor" : "#image-vendor")?.value || "";
    const key = $(isLlm ? "#llm-api-key" : "#image-api-key")?.value || "";
    const baseUrl = $(isLlm ? "#llm-base-url" : "#image-base-url")?.value || "";
    const statusEl = $(isLlm ? "#llm-detect-status" : "#image-detect-status");
    const select = $(isLlm ? "#llm-model" : "#image-model");
    if (!statusEl || !select) return;
    if (!vendor || !key) {
      statusEl.textContent = "Fill vendor and API key first.";
      statusEl.className = "status-inline warn";
      return;
    }
    statusEl.textContent = "Detecting…";
    statusEl.className = "status-inline";
    try {
      const res = await fetch("/api/list_models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vendor, key, base_url: baseUrl, section }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Detect failed");
      const existing = Array.from(select.options).map((o) => o.value).filter(Boolean);
      const merged = Array.from(new Set([...(data.models || []), ...existing])).sort();
      const prior = select.value;
      fillSelect(select, merged, prior);
      statusEl.textContent = `Detected ${data.models.length} model(s).`;
      statusEl.className = "status-inline ok";
    } catch (err) {
      statusEl.textContent = `${err.message}. Using fallback list.`;
      statusEl.className = "status-inline warn";
    }
  }

  // Initialize selects and vendor-change bindings.
  const llmVendor = $("#llm-vendor");
  const llmModel = $("#llm-model");
  const llmBase = $("#llm-base-url");
  const llmDetect = $("#llm-detect");
  if (llmVendor && llmModel) {
    fillSelect(llmModel, LLM_CATALOG[llmVendor.value] || []);
    setBaseUrlDefault(llmBase, llmVendor.value);
    llmVendor.addEventListener("change", () => {
      fillSelect(llmModel, LLM_CATALOG[llmVendor.value] || []);
      if (llmBase) llmBase.value = VENDOR_BASE_URLS[llmVendor.value] || "";
    });
  }
  llmDetect?.addEventListener("click", () => detectModels("llm"));

  const imgVendor = $("#image-vendor");
  const imgModel = $("#image-model");
  const imgBase = $("#image-base-url");
  const imgDetect = $("#image-detect");
  if (imgVendor && imgModel) {
    fillSelect(imgModel, IMAGE_CATALOG[imgVendor.value] || []);
    setBaseUrlDefault(imgBase, imgVendor.value);
    imgVendor.addEventListener("change", () => {
      fillSelect(imgModel, IMAGE_CATALOG[imgVendor.value] || []);
      if (imgBase) imgBase.value = VENDOR_BASE_URLS[imgVendor.value] || "";
    });
  }
  imgDetect?.addEventListener("click", () => detectModels("image_gen"));

  function setAddOpen(open) {
    if (!addForm || !addToggle) return;
    addForm.hidden = !open;
    addToggle.textContent = open ? "Close" : "+ New profile";
  }
  addToggle?.addEventListener("click", () => setAddOpen(addForm?.hidden));
  addCancel?.addEventListener("click", () => {
    addForm?.reset();
    if (imageBody) imageBody.hidden = true;
    if (addStatus) { addStatus.textContent = ""; addStatus.className = "status-inline"; }
    setAddOpen(false);
  });
  imageEnable?.addEventListener("change", () => {
    if (imageBody) imageBody.hidden = !imageEnable.checked;
  });

  addForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!addStatus) return;
    const fd = new FormData(addForm);
    const body = {
      name: String(fd.get("name") || "").trim(),
      llm: {
        vendor: String(fd.get("llm_vendor") || ""),
        model: String(fd.get("llm_model") || "").trim(),
        api_key: String(fd.get("llm_api_key") || "").trim(),
        base_url: String(fd.get("llm_base_url") || "").trim(),
      },
    };
    if (imageEnable?.checked) {
      body.image_gen = {
        vendor: String(fd.get("image_vendor") || ""),
        model: String(fd.get("image_model") || "").trim(),
        api_key: String(fd.get("image_api_key") || "").trim(),
        base_url: String(fd.get("image_base_url") || "").trim(),
      };
    }
    addStatus.textContent = "Creating…";
    addStatus.className = "status-inline";
    try {
      const res = await fetch("/api/profiles/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      addStatus.textContent = `Created "${data.name}". Reloading…`;
      addStatus.className = "status-inline ok";
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      addStatus.textContent = err.message;
      addStatus.className = "status-inline warn";
    }
  });

  // ---------- boot ----------
  loadConversations();
  autoGrow();
})();
