(() => {
  const qs = (sel) => document.querySelector(sel);
  const root = qs("#lectChatRoot");
  if (!root) return;

  const fab = qs("#lectChatFab");
  const panel = qs("#lectChatPanel");
  const closeBtn = qs("#lectChatClose");
  const msgs = qs("#lectChatMsgs");
  const input = qs("#lectChatInput");
  const helpBtn = qs("#lectChatHelp");
  const quick = qs("#lectChatQuick");
  const sendBtn = qs("#lectChatSend");

  const API_URL = "/lecturer/api/chat";
  const userName = (root.dataset.userName || "").trim();

  const escapeHtml = (s) =>
    String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");

  function scrollToBottom() {
    if (!msgs) return;
    msgs.scrollTop = msgs.scrollHeight;
  }

  function addBubble({ who, text }) {
    const wrap = document.createElement("div");
    wrap.className = `flex ${who === "me" ? "justify-end" : "justify-start"}`;

    const bubble = document.createElement("div");
    bubble.className =
      who === "me"
        ? "max-w-[85%] bg-indigo-700 text-white rounded-2xl rounded-br-md px-4 py-2 text-sm shadow"
        : "max-w-[85%] bg-white border border-slate-200 text-slate-800 rounded-2xl rounded-bl-md px-4 py-2 text-sm shadow-sm";
    bubble.innerHTML = `<div class="whitespace-pre-wrap">${escapeHtml(text)}</div>`;

    wrap.appendChild(bubble);
    msgs.appendChild(wrap);
    scrollToBottom();
  }

  function addActions(actions) {
    if (!actions?.length) return;
    const wrap = document.createElement("div");
    wrap.className = "flex flex-wrap gap-2";

    actions.forEach((a) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "text-[12px] font-semibold px-3 py-1.5 rounded-full bg-slate-900 text-white hover:bg-slate-800 transition-colors";
      btn.textContent = a.label || a.value || a.type || "Action";
      btn.addEventListener("click", () => handleAction(a));
      wrap.appendChild(btn);
    });

    msgs.appendChild(wrap);
    scrollToBottom();
  }

  function addCards(cards) {
    if (!cards?.length) return;
    cards.forEach((c) => {
      if (c.type === "leave_confirm") {
        const card = document.createElement("div");
        card.className =
          "bg-white border border-slate-200 rounded-2xl p-4 shadow-sm";
        const fields = (c.fields || [])
          .map(
            (f) => `
            <div class="flex items-start justify-between gap-3 py-1">
              <div class="text-[11px] font-bold uppercase tracking-wide text-slate-400">${escapeHtml(
                f.label
              )}</div>
              <div class="text-sm text-slate-800 font-semibold text-right">${escapeHtml(
                f.value
              )}</div>
            </div>`
          )
          .join("");
        card.innerHTML = `
          <div class="flex items-center justify-between mb-2">
            <div class="font-extrabold text-slate-900">${escapeHtml(
              c.title || "Confirm"
            )}</div>
            <div class="text-[11px] font-bold text-indigo-700 bg-indigo-50 px-2 py-1 rounded-full">Preview</div>
          </div>
          <div class="divide-y divide-slate-100">${fields}</div>
        `;
        msgs.appendChild(card);
        scrollToBottom();
      }
    });
  }

  async function postChat({ message, action, payload }) {
    const res = await fetch(API_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(message ? { message } : {}),
        ...(action ? { action } : {}),
        payload: payload || {},
      }),
    });
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    const isJson = ct.includes("application/json");
    const data = isJson ? await res.json().catch(() => null) : null;
    if (!res.ok || !data?.ok) {
      const fallbackText = !isJson ? await res.text().catch(() => "") : "";
      const err = new Error(data?.message || `HTTP ${res.status}`);
      err.status = res.status;
      err.data = data;
      err.fallbackText = fallbackText?.slice?.(0, 240);
      throw err;
    }
    return data;
  }

  async function handleAction(a) {
    if (a.type === "navigate" && a.url) {
      window.location.href = a.url;
      return;
    }

    // Normalize: server expects action string + payload
    const action = a.type;
    const payload = { ...a };

    try {
      const data = await postChat({ action, payload });
      if (data.text) addBubble({ who: "bot", text: data.text });
      addCards(data.cards);
      addActions(data.actions);
    } catch (e) {
      const msg =
        e?.status === 401
          ? "Your session expired. Please login again."
          : "Sorry — something went wrong. Please try again.";
      addBubble({ who: "bot", text: msg });
      if (e?.data?.actions) addActions(e.data.actions);
      console.error(e);
    }
  }

  async function sendMessage(text) {
    const msg = (text ?? input.value ?? "").trim();
    if (!msg) return;
    input.value = "";
    addBubble({ who: "me", text: msg });

    try {
      const data = await postChat({ message: msg });
      if (data.text) addBubble({ who: "bot", text: data.text });
      addCards(data.cards);
      addActions(data.actions);
    } catch (e) {
      const msg2 =
        e?.status === 401
          ? "Your session expired. Please login again."
          : "Sorry — I couldn’t respond. Please refresh and try again.";
      addBubble({ who: "bot", text: msg2 });
      if (e?.data?.actions) addActions(e.data.actions);
      console.error(e);
    }
  }

  function openPanel() {
    panel.classList.remove("hidden");
    setTimeout(() => input?.focus(), 50);
    if (!msgs.dataset.welcomed) {
      msgs.dataset.welcomed = "1";
      const hi = userName ? `Hello, ${userName}!` : "Hello!";
      addBubble({ who: "bot", text: `${hi} What can I help you with today?` });
      addBubble({ who: "bot", text: "Tip: Click “Help” to see examples and quick buttons." });
    }
  }

  function closePanel() {
    panel.classList.add("hidden");
  }

  fab?.addEventListener("click", () => {
    if (panel.classList.contains("hidden")) openPanel();
    else closePanel();
  });
  closeBtn?.addEventListener("click", closePanel);

  helpBtn?.addEventListener("click", async () => {
    if (!quick) return;
    quick.classList.toggle("hidden");
    if (quick.classList.contains("hidden")) return;

    if (!quick.dataset.loaded) {
      quick.dataset.loaded = "1";
      try {
        const data = await postChat({ message: "" });
        if (data.text) addBubble({ who: "bot", text: data.text });
        addCards(data.cards);
        addActions(data.actions);
      } catch (e) {
        // ignore; help chips are still visible
        console.error(e);
      }
    }
  });

  sendBtn?.addEventListener("click", () => sendMessage());
  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage();
  });

  root.querySelectorAll("button[data-quick]").forEach((btn) => {
    btn.addEventListener("click", () => sendMessage(btn.getAttribute("data-quick")));
  });
})();

