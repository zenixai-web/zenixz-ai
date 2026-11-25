(function () {
  if (window.__ZENIX_WIDGET_LOADED__) return;
  window.__ZENIX_WIDGET_LOADED__ = true;

  const scriptTag =
    document.currentScript ||
    document.querySelector('script[src*="widget.js"]');
  const clientId =
    (scriptTag && scriptTag.getAttribute("data-client-id")) || "DEFAULT";
  const apiBase =
    (scriptTag && scriptTag.getAttribute("data-api-base")) ||
    "http://127.0.0.1:8000";

  const safeClientId = String(clientId || "DEFAULT")
    .replace(/[^A-Za-z0-9_-]/g, "")
    .slice(0, 40);

  let chatLang = "si";
  let isLightMode = false;

  try {
    isLightMode = localStorage.getItem("store_chat_theme_mode") === "light";
  } catch (_) {}

  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "💬 Chat";
  btn.setAttribute("aria-label", "Open Chat");
  btn.style.cssText =
    "position:fixed;bottom:20px;right:20px;z-index:99998;padding:12px 18px;border-radius:999px;border:none;background:#2563eb;color:#ffffff;font-weight:700;font-size:14px;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,0.28);";

  const box = document.createElement("div");
  box.style.cssText =
    "position:fixed;bottom:76px;right:20px;z-index:99999;width:360px;max-width:calc(100vw - 32px);height:480px;max-height:calc(100vh - 104px);border-radius:16px;display:none;flex-direction:column;overflow:hidden;box-shadow:0 18px 46px rgba(0,0,0,0.45);font-family:system-ui,-apple-system,sans-serif;";

  const header = document.createElement("div");
  header.style.cssText =
    "padding:12px 14px;font-weight:700;font-size:14px;display:flex;justify-content:space-between;align-items:center;gap:8px;";

  const titleSpan = document.createElement("span");
  titleSpan.textContent = `Store Assistant (${safeClientId})`;

  const rightBtns = document.createElement("div");
  rightBtns.style.cssText = "display:flex;align-items:center;gap:6px;";

  const themeBtn = document.createElement("button");
  themeBtn.type = "button";
  themeBtn.title = "Switch Theme";
  themeBtn.style.cssText =
    "background:transparent;border:1px solid rgba(148,163,184,0.3);border-radius:8px;padding:3px 8px;cursor:pointer;font-size:13px;";

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.textContent = "✕";
  closeBtn.title = "Close";
  closeBtn.style.cssText =
    "background:transparent;border:none;font-size:15px;font-weight:700;cursor:pointer;padding:2px 6px;";

  rightBtns.appendChild(themeBtn);
  rightBtns.appendChild(closeBtn);
  header.appendChild(titleSpan);
  header.appendChild(rightBtns);

  const msgsEl = document.createElement("div");
  msgsEl.style.cssText =
    "flex:1;padding:12px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;font-size:13.5px;";

  const formEl = document.createElement("form");
  formEl.style.cssText = "display:flex;padding:8px;gap:6px;";

  const inputEl = document.createElement("input");
  inputEl.type = "text";
  inputEl.placeholder = "Type message...";
  inputEl.maxLength = 1500;
  inputEl.autocomplete = "off";
  inputEl.style.cssText =
    "flex:1;padding:8px 11px;border-radius:9px;font-size:13.5px;outline:none;";

  const sendBtnEl = document.createElement("button");
  sendBtnEl.type = "submit";
  sendBtnEl.textContent = "Send";
  sendBtnEl.style.cssText =
    "padding:8px 13px;border-radius:9px;border:none;background:#2563eb;color:#ffffff;font-weight:600;font-size:13px;cursor:pointer;";

  formEl.appendChild(inputEl);
  formEl.appendChild(sendBtnEl);

  box.appendChild(header);
  box.appendChild(msgsEl);
  box.appendChild(formEl);

  document.body.appendChild(btn);
  document.body.appendChild(box);

  function paintTheme() {
    box.style.background = isLightMode ? "#ffffff" : "#0f172a";
    box.style.color = isLightMode ? "#0f172a" : "#f8fafc";
    box.style.border = isLightMode
      ? "1px solid rgba(15,23,42,0.15)"
      : "1px solid rgba(148,163,184,0.25)";
    header.style.background = isLightMode ? "#f8fafc" : "#1e293b";
    header.style.borderBottom = isLightMode
      ? "1px solid rgba(15,23,42,0.1)"
      : "1px solid rgba(148,163,184,0.15)";
    closeBtn.style.color = isLightMode ? "#0f172a" : "#cbd5e1";
    themeBtn.style.color = isLightMode ? "#0f172a" : "#f8fafc";
    themeBtn.textContent = isLightMode ? "🌙" : "☀️";
    formEl.style.background = isLightMode ? "#f8fafc" : "#0f172a";
    formEl.style.borderTop = isLightMode
      ? "1px solid rgba(15,23,42,0.1)"
      : "1px solid rgba(148,163,184,0.2)";
    inputEl.style.background = isLightMode ? "#ffffff" : "#020617";
    inputEl.style.color = isLightMode ? "#0f172a" : "#ffffff";
    inputEl.style.border = isLightMode
      ? "1px solid rgba(15,23,42,0.2)"
      : "1px solid rgba(148,163,184,0.3)";
    Array.from(msgsEl.children).forEach((m) => {
      if (m.dataset.who === "bot") {
        m.style.background = isLightMode ? "#f1f5f9" : "#1e293b";
        m.style.color = isLightMode ? "#0f172a" : "#f1f5f9";
      }
    });
  }

  paintTheme();

  themeBtn.addEventListener("click", () => {
    isLightMode = !isLightMode;
    try {
      localStorage.setItem(
        "store_chat_theme_mode",
        isLightMode ? "light" : "dark"
      );
    } catch (_) {}
    paintTheme();
  });

  function appendMsg(text, who) {
    const m = document.createElement("div");
    m.dataset.who = who;
    m.textContent = String(text || "");
    m.style.cssText =
      who === "user"
        ? "align-self:flex-end;background:#2563eb;color:#ffffff;padding:8px 11px;border-radius:10px;max-width:82%;white-space:pre-line;word-break:break-word;"
        : `align-self:flex-start;background:${
            isLightMode ? "#f1f5f9" : "#1e293b"
          };color:${
            isLightMode ? "#0f172a" : "#f1f5f9"
          };padding:8px 11px;border-radius:10px;max-width:85%;white-space:pre-line;word-break:break-word;`;
    msgsEl.appendChild(m);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  appendMsg(
    "ආයුබෝවන්! ඔබට මාත් සමඟ සිංහලෙන් කතා කරන්න පුළුවන්.\nYou can also talk to me in English.",
    "bot"
  );

  btn.addEventListener("click", () => {
    const open = box.style.display === "flex";
    box.style.display = open ? "none" : "flex";
    if (!open) inputEl.focus();
  });

  closeBtn.addEventListener("click", () => {
    box.style.display = "none";
  });

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = (inputEl.value || "").trim().slice(0, 1500);
    if (!msg) return;
    appendMsg(msg, "user");
    inputEl.value = "";
    sendBtnEl.disabled = true;

    try {
      const base = String(apiBase || "").replace(/\/+$/, "");
      const res = await fetch(`${base}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          lang: chatLang,
          history: [],
          order_state: { step: "IDLE", order_id: null, order_data: {} },
        }),
      });
      const data = await res.json();
      appendMsg(
        data && data.reply
          ? data.reply
          : (data && data.detail) || "Please try again.",
        "bot"
      );
    } catch (_) {
      appendMsg("Unable to connect to server.", "bot");
    } finally {
      sendBtnEl.disabled = false;
      inputEl.focus();
    }
  });
})();