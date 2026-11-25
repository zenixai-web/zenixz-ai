(function () {
  const scriptTag = document.currentScript;
  const clientId = (scriptTag && scriptTag.getAttribute("data-client-id")) || "DEFAULT";
  const sheetUrl = (scriptTag && scriptTag.getAttribute("data-sheet-url")) || "";
  const apiBase =
    (scriptTag && scriptTag.getAttribute("data-api-base")) ||
    "http://127.0.0.1:8000";

  let chatLang = "si";
  let languageLocked = false;
  let orderState = { step: "IDLE", order_id: null, order_data: {} };
  let history = [];

  // Floating Chat Button
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "💬 Chat";
  btn.style.cssText =
    "position:fixed;bottom:20px;right:20px;z-index:99999;padding:12px 18px;border-radius:999px;border:none;background:#2563eb;color:#fff;font-weight:700;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,0.25);";

  // Chat Box
  const box = document.createElement("div");
  box.style.cssText =
    "position:fixed;bottom:76px;right:20px;z-index:99999;width:340px;max-width:calc(100vw - 32px);height:440px;background:#0f172a;color:#f8fafc;border:1px solid rgba(148,163,184,0.25);border-radius:14px;display:none;flex-direction:column;overflow:hidden;box-shadow:0 16px 40px rgba(0,0,0,0.45);font-family:sans-serif;";

  box.innerHTML = `
    <div style="padding:12px 14px;background:#1e293b;font-weight:700;font-size:14px;display:flex;justify-content:space-between;align-items:center;">
      <span>Store Assistant (${clientId})</span>
      <button id="zxCloseBtn" type="button" style="background:none;border:none;color:#cbd5e1;font-size:16px;cursor:pointer;">✕</button>
    </div>
    <div id="zxMsgs" style="flex:1;padding:12px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;font-size:13px;"></div>
    <form id="zxForm" style="display:flex;border-top:1px solid rgba(148,163,184,0.2);padding:8px;gap:6px;background:#0f172a;">
      <input id="zxInput" type="text" placeholder="Type message..." style="flex:1;padding:8px 10px;border-radius:8px;border:1px solid rgba(148,163,184,0.3);background:#020617;color:#fff;font-size:13px;" />
      <button type="submit" style="padding:8px 12px;border-radius:8px;border:none;background:#2563eb;color:#fff;font-weight:600;cursor:pointer;">Send</button>
    </form>
  `;

  document.body.appendChild(btn);
  document.body.appendChild(box);

  const msgsEl = box.querySelector("#zxMsgs");
  const formEl = box.querySelector("#zxForm");
  const inputEl = box.querySelector("#zxInput");
  const closeEl = box.querySelector("#zxCloseBtn");

  function appendMsg(text, who) {
    const div = document.createElement("div");
    div.textContent = text;
    div.style.cssText =
      who === "user"
        ? "align-self:flex-end;background:#2563eb;color:#fff;padding:8px 11px;border-radius:10px;max-width:82%;white-space:pre-line;"
        : "align-self:flex-start;background:#1e293b;color:#f1f5f9;padding:8px 11px;border-radius:10px;max-width:85%;white-space:pre-line;";
    msgsEl.appendChild(div);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  appendMsg(
    "ආයුබෝවන්! ඔබට කැමති භාණ්ඩයක් තියෙනවා නම් ඇණවුම් කරන්න පහළින් ඒකේ නම දාන්න.",
    "bot"
  );

  btn.addEventListener("click", () => {
    box.style.display = box.style.display === "none" ? "flex" : "none";
    if (box.style.display === "flex") inputEl.focus();
  });

  closeEl.addEventListener("click", () => {
    box.style.display = "none";
  });

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = (inputEl.value || "").trim();
    if (!text) return;

    if (!languageLocked) {
      if (/[\u0B80-\u0BFF]/.test(text)) chatLang = "ta";
      else if (/[a-z]/i.test(text) && !/[\u0D80-\u0DFF]/.test(text)) chatLang = "en";
      else chatLang = "si";
      languageLocked = true;
    }

    appendMsg(text, "user");
    inputEl.value = "";

    try {
      const res = await fetch(`${apiBase}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          lang: chatLang,
          client_sheet_url: sheetUrl || undefined,
          order_state: orderState,
          history: history.slice(-6),
        }),
      });
      const data = await res.json();
      if (data.order_state) orderState = data.order_state;
      const reply = data.reply || "OK";
      appendMsg(reply, "bot");
      history.push({ role: "user", text });
      history.push({ role: "model", text: reply });
    } catch (_) {
      appendMsg("Server connection error.", "bot");
    }
  });
})();