(function () {
  if (window.__ZENIX_WIDGET_LOADED__) return;
  window.__ZENIX_WIDGET_LOADED__ = true;

  function installInspectGuard() {
    if (window.__ZENIX_INSPECT_GUARD_INSTALLED__) return;
    window.__ZENIX_INSPECT_GUARD_INSTALLED__ = true;

    document.addEventListener("contextmenu", function (e) {
      e.preventDefault();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "F12" || e.keyCode === 123) {
        e.preventDefault();
        return false;
      }
      if (
        e.ctrlKey &&
        e.shiftKey &&
        ["I", "J", "C", "K", "i", "j", "c", "k"].includes(e.key)
      ) {
        e.preventDefault();
        return false;
      }
      if (
        e.metaKey &&
        e.altKey &&
        ["I", "J", "C", "i", "j", "c"].includes(e.key)
      ) {
        e.preventDefault();
        return false;
      }
      if (
        (e.ctrlKey || e.metaKey) &&
        ["U", "u", "S", "s"].includes(e.key)
      ) {
        e.preventDefault();
        return false;
      }
    });
  }

  installInspectGuard();

  function removeLegacyStoreAssistant() {
    const legacySelectors = [
      "[data-zenix-legacy]",
      "#legacy-chatbot",
      "#store-assistant-widget",
      ".legacy-chat-widget",
      ".store-assistant-container",
      "#chat-container"
    ];
    legacySelectors.forEach((sel) => {
      document.querySelectorAll(sel).forEach((el) => {
        if (el && !el.closest("#zenix-chat-widget-root")) {
          el.remove();
        }
      });
    });
  }

  const CHAT_FONT_STACK =
    '"Google Sans", "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

  function injectChatFont() {
    try {
      if (!document.getElementById("zenix-google-font-link")) {
        const link = document.createElement("link");
        link.id = "zenix-google-font-link";
        link.rel = "stylesheet";
        link.href =
          "https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap";
        (document.head || document.documentElement).appendChild(link);
      }
      if (!document.getElementById("zenix-font-style")) {
        const st = document.createElement("style");
        st.id = "zenix-font-style";
        st.textContent = `
          .zenix-widget-root, .zenix-widget-root * {
            font-family: ${CHAT_FONT_STACK} !important;
          }
        `;
        (document.head || document.documentElement).appendChild(st);
      }
    } catch (_) {}
  }

  const scriptEl =
    document.currentScript ||
    Array.from(document.querySelectorAll("script")).find((s) =>
      (s.src || "").includes("app")
    );

  function resolveApiBase() {
    const attrBase = scriptEl && scriptEl.getAttribute("data-api-base");
    if (attrBase && attrBase.trim()) return attrBase.trim().replace(/\/+$/, "");
    if (window.ZENIX_API_BASE && String(window.ZENIX_API_BASE).trim()) {
      return String(window.ZENIX_API_BASE).trim().replace(/\/+$/, "");
    }
    const host = window.location.hostname;
    if (host === "127.0.0.1" || host === "localhost") {
      return "http://127.0.0.1:8000";
    }
    return "https://zenixz-ai.onrender.com";
  }

  const API_BASE = resolveApiBase();
  const urlParams = new URLSearchParams(window.location.search);

  const CLIENT_ID_FROM_SCRIPT = (
    (scriptEl && scriptEl.getAttribute("data-client-id")) ||
    urlParams.get("client_id") ||
    window.ZENIX_CLIENT_ID ||
    ""
  )
    .trim()
    .toUpperCase();

  const CLIENT_NAME_FROM_SCRIPT = (
    (scriptEl && scriptEl.getAttribute("data-client-name")) ||
    window.ZENIX_CLIENT_NAME ||
    ""
  ).trim();

  const CLIENT_KEY_FROM_SCRIPT = (
    (scriptEl && scriptEl.getAttribute("data-client-key")) ||
    window.ZENIX_CLIENT_KEY ||
    ""
  ).trim();

  const SITE_ID =
    (scriptEl && scriptEl.getAttribute("data-site-id")) ||
    window.ZENIX_SITE_ID ||
    CLIENT_ID_FROM_SCRIPT ||
    window.location.hostname ||
    "default-site";

  const STORAGE_SCOPE = `${SITE_ID}::${CLIENT_ID_FROM_SCRIPT || "default"}`;

  let ACTIVE_CLIENT_KEY = CLIENT_KEY_FROM_SCRIPT;
  let ACTIVE_CLIENT_ID = CLIENT_ID_FROM_SCRIPT;
  let ACTIVE_CLIENT_NAME = CLIENT_NAME_FROM_SCRIPT;
  let PLAN_IS_ACTIVE = false;

  const SESSION_KEY = `zenix_sid_${STORAGE_SCOPE}`;
  let sessionId =
    sessionStorage.getItem(SESSION_KEY) ||
    "sess_" + Math.random().toString(36).slice(2) + "_" + Date.now().toString(36);
  sessionStorage.setItem(SESSION_KEY, sessionId);

  const ORDERS_LOCAL_KEY = `zenix_client_orders_v1_${STORAGE_SCOPE}`;
  const orderChannel =
    typeof BroadcastChannel !== "undefined"
      ? new BroadcastChannel(`zenix_orders_channel_${STORAGE_SCOPE}`)
      : null;

  function loadLocalOrders() {
    try {
      const raw = localStorage.getItem(ORDERS_LOCAL_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    } catch (_) {
      return [];
    }
  }

  function saveLocalOrders(list) {
    try {
      localStorage.setItem(ORDERS_LOCAL_KEY, JSON.stringify(list.slice(0, 100)));
    } catch (_) {}
  }

  function broadcastOrderUpdate(record) {
    try {
      if (orderChannel) {
        orderChannel.postMessage({
          type: "ORDER_CREATED",
          client_id: ACTIVE_CLIENT_ID,
          site_id: SITE_ID,
          order: record,
          orders: loadLocalOrders(),
          timestamp: Date.now()
        });
      }
    } catch (_) {}
  }

  function persistOrderLocally(record) {
    const scopedRecord = Object.assign({}, record, {
      client_id: ACTIVE_CLIENT_ID
    });
    const current = loadLocalOrders();
    const filtered = current.filter(
      (o) => String(o.order_id || "").trim() !== String(scopedRecord.order_id || "").trim()
    );
    const updated = [scopedRecord, ...filtered];
    saveLocalOrders(updated);
    broadcastOrderUpdate(scopedRecord);
    return updated;
  }

  const DEFAULT_CONFIG = {
    botName: ACTIVE_CLIENT_NAME ? `${ACTIVE_CLIENT_NAME} AI Assistant` : "Zenix AI Assistant",
    welcomeMessage:
      "Hello! I'm your AI Assistant. How can I help you?\nආයුබෝවන්! මම ඔබේ AI සහායකයා. ඔබට කොහොමද උදව් කරන්න පුළුවන්?",
    primaryColor: "#2563eb",
    secondaryColor: "#1e40af",
    position: "bottom-right",
    theme: "dark",
    fontFamily: CHAT_FONT_STACK,
    fontSize: 14,
    currency: "LKR",
    language: "si",
    orderFlowEnabled: true,
    maxOrderQty: 50
  };

  let config = Object.assign({}, DEFAULT_CONFIG);
  let isOpen = false;
  let isSending = false;
  let conversationHistory = [];
  let messageCounter = 0;
  let domRefs = {};

  let orderDraft = {
    active: false,
    step: null,
    item: "",
    quantity: 1,
    customerName: "",
    phone: "",
    address: "",
    paymentMethod: ""
  };

  function generateOrderId() {
    const now = new Date();
    const y = String(now.getFullYear()).slice(-2);
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    const rnd = Math.floor(1000 + Math.random() * 9000);
    return `ORD-${y}${m}${d}-${rnd}`;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    const amp = String.fromCharCode(38);
    const lt = String.fromCharCode(60);
    const gt = String.fromCharCode(62);
    const quot = String.fromCharCode(34);
    const apos = String.fromCharCode(39);
    return String(str)
      .split(amp).join(amp + "amp;")
      .split(lt).join(amp + "lt;")
      .split(gt).join(amp + "gt;")
      .split(quot).join(amp + "quot;")
      .split(apos).join(amp + "#039;");
  }

  function formatRichText(text) {
    if (!text) return "";
    let safe = escapeHtml(String(text));
    safe = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/\n/g, "<br>");
    return safe;
  }

  async function verifyPlanStatus() {
    if (!ACTIVE_CLIENT_ID) return false;
    try {
      const url = `${API_BASE}/api/widget/status?client_id=${encodeURIComponent(ACTIVE_CLIENT_ID)}`;
      const res = await fetch(url, {
        method: "GET",
        headers: {
          ...(ACTIVE_CLIENT_KEY ? { "X-Client-Key": ACTIVE_CLIENT_KEY } : {}),
          "X-Client-Id": ACTIVE_CLIENT_ID
        },
        cache: "no-store"
      });
      if (!res.ok) return false;
      const data = await res.json();
      if (data.maintenance_mode) return false;
      if (data.allowed !== true || data.plan_active !== true) return false;
      if (data.store_name) {
        ACTIVE_CLIENT_NAME = data.store_name;
        config.botName = `${data.store_name} AI Assistant`;
      }
      return true;
    } catch (_) {
      return false;
    }
  }

  function createStyles() {
    if (document.getElementById("zenix-chat-widget-styles")) return;
    const style = document.createElement("style");
    style.id = "zenix-chat-widget-styles";
    style.textContent = `
      .zenix-widget-root {
        position: fixed;
        bottom: 20px;
        right: 20px;
        z-index: 2147483000;
        font-family: ${CHAT_FONT_STACK};
        font-size: 14px;
      }
      .zenix-launcher-btn {
        width: 58px;
        height: 58px;
        border-radius: 50%;
        border: none;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #ffffff;
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.28);
      }
      .zenix-chat-window {
        position: absolute;
        bottom: 72px;
        right: 0;
        width: 370px;
        max-width: calc(100vw - 28px);
        height: 520px;
        max-height: calc(100vh - 105px);
        border-radius: 18px;
        overflow: hidden;
        display: none;
        flex-direction: column;
        background: #0f172a;
        color: #f8fafc;
        border: 1px solid rgba(51, 65, 85, 0.85);
        box-shadow: 0 20px 50px rgba(15, 23, 42, 0.35);
      }
      .zenix-chat-window.open {
        display: flex;
      }
      .zenix-chat-header {
        padding: 12px 14px;
        color: #ffffff;
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .zenix-messages {
        flex: 1;
        overflow-y: auto;
        padding: 14px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        background: #020617;
      }
      .zenix-msg {
        max-width: 85%;
        padding: 10px 13px;
        border-radius: 14px;
        word-wrap: break-word;
      }
      .zenix-msg.bot {
        align-self: flex-start;
        background: #1e293b;
        color: #f8fafc;
        border: 1px solid #334155;
      }
      .zenix-msg.user {
        align-self: flex-end;
        color: #ffffff;
      }
      .zenix-input-area {
        padding: 10px 12px;
        border-top: 1px solid #1e293b;
        background: #0f172a;
        display: flex;
        gap: 6px;
      }
      .zenix-text-input {
        flex: 1;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 9px 12px;
        font-size: 13.5px;
        outline: none;
        background: #1e293b;
        color: #f8fafc;
      }
      .zenix-send-btn {
        border: none;
        border-radius: 10px;
        padding: 9px 14px;
        color: #ffffff;
        font-weight: 600;
        cursor: pointer;
      }
      .zenix-icon-btn {
        background: rgba(255, 255, 255, 0.18);
        border: none;
        color: #ffffff;
        border-radius: 7px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function appendMessage(role, text) {
    if (!domRefs.messagesEl) return null;
    const id = `msg_${++messageCounter}`;
    const el = document.createElement("div");
    el.id = id;
    el.className = `zenix-msg ${role === "user" ? "user" : "bot"}`;
    if (role === "user") el.style.background = config.primaryColor;
    el.innerHTML = formatRichText(text);
    domRefs.messagesEl.appendChild(el);
    domRefs.messagesEl.scrollTop = domRefs.messagesEl.scrollHeight;
    return id;
  }

  function removeMessageById(id) {
    const el = id && document.getElementById(id);
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function toggleWidget(forceState) {
    if (!domRefs.win) return;
    isOpen = typeof forceState === "boolean" ? forceState : !isOpen;
    domRefs.win.classList.toggle("open", isOpen);
    if (isOpen && domRefs.textInput) {
      setTimeout(() => domRefs.textInput.focus(), 50);
    }
  }

  function resetOrderDraft() {
    orderDraft = {
      active: false,
      step: null,
      item: "",
      quantity: 1,
      customerName: "",
      phone: "",
      address: "",
      paymentMethod: ""
    };
  }

  function startOrderFlow() {
    resetOrderDraft();
    orderDraft.active = true;
    orderDraft.step = "item";
    appendMessage("bot", "හරි! ඔබට අවශ්‍ය Product / Item එකේ නම ලියන්න.");
  }

  async function handleOrderStep(text) {
    const t = String(text || "").trim();
    const low = t.toLowerCase();
    if (low === "cancel" || t.includes("අවලංගු")) {
      resetOrderDraft();
      appendMessage("bot", "Order එක cancel කරන ලදී.");
      return true;
    }
    if (orderDraft.step === "item") {
      orderDraft.item = t || "Item";
      orderDraft.step = "quantity";
      appendMessage("bot", `"${orderDraft.item}" සඳහා Quantity එක කීයද?`);
      return true;
    }
    if (orderDraft.step === "quantity") {
      const q = parseInt(t.replace(/[^0-9]/g, ""), 10);
      orderDraft.quantity = !isNaN(q) && q > 0 ? Math.min(q, config.maxOrderQty || 50) : 1;
      orderDraft.step = "name";
      appendMessage("bot", "ඔබේ සම්පූර්ණ නම ඇතුළත් කරන්න.");
      return true;
    }
    if (orderDraft.step === "name") {
      orderDraft.customerName = t;
      orderDraft.step = "phone";
      appendMessage("bot", "ඔබේ දුරකථන අංකය (Phone Number) ඇතුළත් කරන්න.");
      return true;
    }
    if (orderDraft.step === "phone") {
      orderDraft.phone = t;
      orderDraft.step = "address";
      appendMessage("bot", "Delivery Address එක ඇතුළත් කරන්න.");
      return true;
    }
    if (orderDraft.step === "address") {
      orderDraft.address = t;
      orderDraft.step = "payment";
      appendMessage("bot", "Payment ක්‍රමය (Bank Transfer හෝ COD) තෝරන්න.");
      return true;
    }
    if (orderDraft.step === "payment") {
      orderDraft.paymentMethod = t || "COD";
      const orderObj = {
        order_id: generateOrderId(),
        client_id: ACTIVE_CLIENT_ID,
        customer_name: orderDraft.customerName,
        phone: orderDraft.phone,
        address: orderDraft.address,
        item: orderDraft.item,
        quantity: orderDraft.quantity,
        payment_method: orderDraft.paymentMethod,
        status: "Pending",
        created_at: new Date().toISOString()
      };
      persistOrderLocally(orderObj);
      await sendChatToServer(
        `New order captured: ${orderObj.order_id}`,
        orderObj
      );
      appendMessage(
        "bot",
        `✅ **Order Confirmed!**\n• **Order ID:** ${orderObj.order_id}\n• **Item:** ${orderObj.item}\n• **Qty:** ${orderObj.quantity}\n• **Name:** ${orderObj.customer_name}\n• **Phone:** ${orderObj.phone}\n• **Payment:** ${orderObj.payment_method}`
      );
      resetOrderDraft();
      return true;
    }
    return false;
  }

  function looksLikeOrderIntent(text) {
    const t = String(text || "").toLowerCase();
    return (
      t.includes("order") ||
      t.includes("buy") ||
      t.includes("ඕඩර්") ||
      t.includes("මිලදී")
    );
  }

  async function sendChatToServer(messageText, orderPayload) {
    const typingId = appendMessage("bot", "ලියමින් සිටී...");
    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(ACTIVE_CLIENT_KEY ? { "X-Client-Key": ACTIVE_CLIENT_KEY } : {}),
          "X-Client-Id": ACTIVE_CLIENT_ID
        },
        body: JSON.stringify({
          client_id: ACTIVE_CLIENT_ID,
          session_id: sessionId,
          message: messageText,
          history: conversationHistory.slice(-10),
          order: orderPayload || null
        })
      });
      removeMessageById(typingId);
      if (!res.ok) {
        appendMessage("bot", "Server එකට සම්බන්ධ වීමට නොහැකි විය.");
        return;
      }
      const data = await res.json();
      if (data.blocked) {
        appendMessage("bot", data.reply || "Service unavailable.");
        return;
      }
      if (data.reply && !orderPayload) {
        appendMessage("bot", data.reply);
        conversationHistory.push({ role: "assistant", content: data.reply });
      }
    } catch (_) {
      removeMessageById(typingId);
      appendMessage("bot", "Server එකට සම්බන්ධ වීමට නොහැකි විය.");
    }
  }

  async function handleSubmit() {
    if (isSending || !domRefs.textInput) return;
    const text = domRefs.textInput.value.trim();
    if (!text) return;
    domRefs.textInput.value = "";

    isSending = true;
    appendMessage("user", text);
    conversationHistory.push({ role: "user", content: text });

    try {
      if (orderDraft.active) {
        await handleOrderStep(text);
        return;
      }
      if (config.orderFlowEnabled && looksLikeOrderIntent(text)) {
        startOrderFlow();
        return;
      }
      await sendChatToServer(text, null);
    } finally {
      isSending = false;
    }
  }

  function buildWidget() {
    createStyles();
    if (document.getElementById("zenix-chat-widget-root")) return;

    const root = document.createElement("div");
    root.id = "zenix-chat-widget-root";
    root.className = "zenix-widget-root";

    const win = document.createElement("div");
    win.className = "zenix-chat-window";

    const header = document.createElement("div");
    header.className = "zenix-chat-header";
    header.style.background = `linear-gradient(135deg, ${config.primaryColor}, ${config.secondaryColor})`;

    const title = document.createElement("div");
    title.style.fontWeight = "600";
    title.textContent = config.botName;

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "zenix-icon-btn";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => toggleWidget(false));

    header.appendChild(title);
    header.appendChild(closeBtn);

    const messagesEl = document.createElement("div");
    messagesEl.className = "zenix-messages";

    const inputArea = document.createElement("div");
    inputArea.className = "zenix-input-area";

    const textInput = document.createElement("input");
    textInput.type = "text";
    textInput.className = "zenix-text-input";
    textInput.placeholder = "ඔබේ පණිවිඩය මෙතන ලියන්න...";
    textInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    });

    const sendBtn = document.createElement("button");
    sendBtn.type = "button";
    sendBtn.className = "zenix-send-btn";
    sendBtn.style.background = config.primaryColor;
    sendBtn.textContent = "Send";
    sendBtn.addEventListener("click", handleSubmit);

    inputArea.appendChild(textInput);
    inputArea.appendChild(sendBtn);

    win.appendChild(header);
    win.appendChild(messagesEl);
    win.appendChild(inputArea);

    const launcherBtn = document.createElement("button");
    launcherBtn.type = "button";
    launcherBtn.className = "zenix-launcher-btn";
    launcherBtn.style.background = `linear-gradient(135deg, ${config.primaryColor}, ${config.secondaryColor})`;
    launcherBtn.innerHTML = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
    launcherBtn.addEventListener("click", () => toggleWidget());

    root.appendChild(win);
    root.appendChild(launcherBtn);
    (document.body || document.documentElement).appendChild(root);

    domRefs = { root, win, messagesEl, textInput, sendBtn, launcherBtn };
    appendMessage("bot", config.welcomeMessage);
  }

  async function init() {
    removeLegacyStoreAssistant();
    if (!ACTIVE_CLIENT_ID) return;
    PLAN_IS_ACTIVE = await verifyPlanStatus();
    if (!PLAN_IS_ACTIVE) return;
    injectChatFont();
    buildWidget();
    removeLegacyStoreAssistant();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();