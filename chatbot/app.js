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
    return "https://zenixz-ai-1.onrender.com";
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
  let CLIENT_PLAN_TYPE = "normal";
  let STOCK_CSV_URL = "";
  let STOCK_WEBAPP_URL = "";

  const SESSION_KEY = `zenix_sid_${STORAGE_SCOPE}`;
  let sessionId =
    sessionStorage.getItem(SESSION_KEY) ||
    "sess_" + Math.random().toString(36).slice(2) + "_" + Date.now().toString(36);
  sessionStorage.setItem(SESSION_KEY, sessionId);

  const ORDERS_LOCAL_KEY = `zenix_client_orders_v1_${STORAGE_SCOPE}`;
  const TWENTY_FOUR_HOURS_MS = 24 * 60 * 60 * 1000;

  const orderChannel =
    typeof BroadcastChannel !== "undefined"
      ? new BroadcastChannel(`zenix_orders_channel_${STORAGE_SCOPE}`)
      : null;

  function isWithin24Hours(isoStr) {
    if (!isoStr) return true;
    const ts = Date.parse(String(isoStr));
    if (Number.isNaN(ts)) return true;
    return Date.now() - ts <= TWENTY_FOUR_HOURS_MS;
  }

  function loadLocalOrders() {
    try {
      const raw = localStorage.getItem(ORDERS_LOCAL_KEY);
      if (!raw) return [];
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return [];
      const valid = arr.filter((o) => isWithin24Hours(o && o.created_at));
      if (valid.length !== arr.length) {
        localStorage.setItem(ORDERS_LOCAL_KEY, JSON.stringify(valid.slice(0, 100)));
      }
      return valid;
    } catch (_) {
      return [];
    }
  }

  function saveLocalOrders(list) {
    try {
      const valid = (Array.isArray(list) ? list : []).filter((o) =>
        isWithin24Hours(o && o.created_at)
      );
      localStorage.setItem(ORDERS_LOCAL_KEY, JSON.stringify(valid.slice(0, 100)));
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
      client_id: ACTIVE_CLIENT_ID,
      created_at: record.created_at || new Date().toISOString()
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
      "ආයුබෝවන්! මම ඔබේ AI සහායකයා. ඔබට කොහොමද උදව් කරන්න පුළුවන්?",
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
  let attachedSlipData = null;

  let orderDraft = {
    active: false,
    step: null,
    item: "",
    quantity: 1,
    customerName: "",
    phone: "",
    address: "",
    paymentMethod: "",
    paymentProof: ""
  };

  function generateOrderId() {
    const now = new Date();
    const y = String(now.getFullYear()).slice(-2);
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    const rnd = Math.floor(100000 + Math.random() * 900000);
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

  function destroyWidgetIfInactive() {
    PLAN_IS_ACTIVE = false;
    const root = document.getElementById("zenix-chat-widget-root");
    if (root && root.parentNode) {
      root.parentNode.removeChild(root);
    }
    const shell = document.getElementById("chatShell");
    if (shell) {
      shell.classList.add("hidden-by-plan");
    }
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
      CLIENT_PLAN_TYPE = String(data.plan || "normal").trim().toLowerCase();
      STOCK_CSV_URL = String(data.stock_csv_url || "").trim();
      STOCK_WEBAPP_URL = String(data.stock_webapp_url || "").trim();
      return true;
    } catch (_) {
      return false;
    }
  }

  function parseCsvRow(line) {
    const out = [];
    let cur = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        inQuotes = !inQuotes;
      } else if (ch === "," && !inQuotes) {
        out.push(cur.trim());
        cur = "";
      } else {
        cur += ch;
      }
    }
    out.push(cur.trim());
    return out;
  }

  async function checkStockFromCsv(itemName, qty) {
    if (!STOCK_CSV_URL) return { checked: false, available: true, stockQty: null };
    try {
      const res = await fetch(STOCK_CSV_URL, { cache: "no-store" });
      if (!res.ok) return { checked: false, available: true, stockQty: null };
      const csvText = await res.text();
      const lines = csvText
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean);
      if (lines.length < 2) return { checked: false, available: true, stockQty: null };

      const target = String(itemName || "").trim().toLowerCase();
      for (let i = 1; i < lines.length; i++) {
        const cols = parseCsvRow(lines[i]);
        const nameCol = String(cols[0] || "").trim().toLowerCase();
        if (nameCol && (nameCol.includes(target) || target.includes(nameCol))) {
          const rawStock = parseInt(String(cols[1] || "0").replace(/[^0-9-]/g, ""), 10);
          const stockQty = Number.isNaN(rawStock) ? 0 : rawStock;
          return {
            checked: true,
            available: stockQty >= Math.max(1, Number(qty || 1)),
            stockQty
          };
        }
      }
      return { checked: false, available: true, stockQty: null };
    } catch (_) {
      return { checked: false, available: true, stockQty: null };
    }
  }

  async function deductStockPremiumWebApp(orderObj) {
    if (CLIENT_PLAN_TYPE !== "premium" || !STOCK_WEBAPP_URL) return;
    try {
      await fetch(STOCK_WEBAPP_URL, {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "deduct_stock",
          client_id: ACTIVE_CLIENT_ID,
          order_id: orderObj.order_id,
          item: orderObj.item,
          quantity: orderObj.quantity
        })
      });
    } catch (_) {}
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
        width: 375px;
        max-width: calc(100vw - 28px);
        height: 530px;
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
        line-height: 1.55;
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
      .zenix-pay-popup {
        display: flex;
        gap: 8px;
        padding: 8px 12px;
        background: #0f172a;
        border-top: 1px solid #1e293b;
      }
      .zenix-pay-choice {
        flex: 1;
        background: #1e293b;
        color: #f8fafc;
        border: 1px solid #3b82f6;
        border-radius: 10px;
        padding: 9px 10px;
        cursor: pointer;
        text-align: left;
        font-size: 12.5px;
        font-weight: 600;
      }
      .zenix-pay-choice:hover {
        background: #2563eb;
      }
      .zenix-slip-bar {
        display: none;
        align-items: center;
        justify-content: space-between;
        padding: 6px 12px;
        background: #172554;
        color: #bfdbfe;
        font-size: 12px;
      }
      .zenix-input-area {
        padding: 10px 12px;
        border-top: 1px solid #1e293b;
        background: #0f172a;
        display: flex;
        gap: 6px;
        align-items: center;
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

  function renderBankDetailsCard() {
    // Rendered dynamically in chat UI rather than exposed as a static global variable
    const details = [
      ["Bank", "Commercial Bank of Ceylon"],
      ["Account Name", ACTIVE_CLIENT_NAME || "Zenix AI Store"],
      ["Account No", "8012-3456-7890"],
      ["Branch", "Colombo"]
    ];
    const lines = details.map(([k, v]) => `• **${k}:** ${v}`).join("\n");
    appendMessage(
      "bot",
      `🏦 **Bank Transfer Details**\n${lines}\n\nකරුණාකර ඔබේ **සම්පූර්ණ නම** ලියන්න (පසුව Phone, Address සහ Slip PNG/JPG/PDF attach කරන්න).`
    );
  }

  function showPaymentCardsPopup() {
    if (!domRefs.payPopupEl) return;
    domRefs.payPopupEl.innerHTML = "";
    domRefs.payPopupEl.style.display = "flex";

    const bankBtn = document.createElement("button");
    bankBtn.type = "button";
    bankBtn.className = "zenix-pay-choice";
    bankBtn.innerHTML = "🏦 Bank Transfer<br><span style='font-size:11px;opacity:0.8'>Slip Upload (Pending)</span>";
    bankBtn.addEventListener("click", () => selectPaymentMethod("Bank Transfer"));

    const codBtn = document.createElement("button");
    codBtn.type = "button";
    codBtn.className = "zenix-pay-choice";
    codBtn.innerHTML = "🚚 Cash on Delivery<br><span style='font-size:11px;opacity:0.8'>Pay on Arrival (Confirmed)</span>";
    codBtn.addEventListener("click", () => selectPaymentMethod("Cash on Delivery"));

    domRefs.payPopupEl.appendChild(bankBtn);
    domRefs.payPopupEl.appendChild(codBtn);
  }

  function hidePaymentCardsPopup() {
    if (domRefs.payPopupEl) {
      domRefs.payPopupEl.style.display = "none";
      domRefs.payPopupEl.innerHTML = "";
    }
  }

  function selectPaymentMethod(method) {
    hidePaymentCardsPopup();
    orderDraft.paymentMethod = method;
    orderDraft.step = "name";
    if (method === "Bank Transfer") {
      renderBankDetailsCard();
    } else {
      appendMessage(
        "bot",
        "🚚 **Cash on Delivery** තෝරාගත්තා. කරුණාකර ඔබේ **සම්පූර්ණ නම** ඇතුළත් කරන්න."
      );
    }
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
    hidePaymentCardsPopup();
    attachedSlipData = null;
    if (domRefs.slipBarEl) domRefs.slipBarEl.style.display = "none";
    if (domRefs.fileInputEl) domRefs.fileInputEl.value = "";
    orderDraft = {
      active: false,
      step: null,
      item: "",
      quantity: 1,
      customerName: "",
      phone: "",
      address: "",
      paymentMethod: "",
      paymentProof: ""
    };
  }

  function startOrderFlow(prefillItem) {
    resetOrderDraft();
    orderDraft.active = true;
    if (prefillItem) {
      orderDraft.item = prefillItem;
      orderDraft.step = "quantity";
      appendMessage("bot", `"${orderDraft.item}" සඳහා Quantity එක කීයද?`);
    } else {
      orderDraft.step = "item";
      appendMessage("bot", "හරි! ඔබට අවශ්‍ය Product / Item එකේ නම ලියන්න.");
    }
  }

  async function finalizeOrder() {
    const orderId = generateOrderId();
    const isCod = /cod|cash/i.test(orderDraft.paymentMethod);
    const statusLabel = isCod ? "Confirmed" : "Pending";
    const proofPayload =
      attachedSlipData && attachedSlipData.dataUrl
        ? attachedSlipData.dataUrl
        : orderDraft.paymentProof || "";

    const orderObj = {
      order_id: orderId,
      client_id: ACTIVE_CLIENT_ID,
      customer_name: orderDraft.customerName,
      phone: orderDraft.phone,
      address: orderDraft.address,
      item: orderDraft.item,
      quantity: orderDraft.quantity,
      payment_method: orderDraft.paymentMethod || "Cash on Delivery",
      payment_proof: proofPayload,
      payment_proof_name: attachedSlipData ? attachedSlipData.name : "",
      status: statusLabel,
      created_at: new Date().toISOString()
    };

    persistOrderLocally(orderObj);
    await deductStockPremiumWebApp(orderObj);
    await sendChatToServer(`Order captured: ${orderObj.order_id}`, orderObj);

    appendMessage(
      "bot",
      `✅ **Order Placed!**\n• **Order ID:** ${orderObj.order_id}\n• **Item:** ${orderObj.item}\n• **Qty:** ${orderObj.quantity}\n• **Payment Method:** ${orderObj.payment_method}\n• **Status:** ${orderObj.status}`
    );
    resetOrderDraft();
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
      const qty = !isNaN(q) && q > 0 ? Math.min(q, config.maxOrderQty || 50) : 1;
      orderDraft.quantity = qty;

      const stockCheck = await checkStockFromCsv(orderDraft.item, qty);
      if (stockCheck.checked && !stockCheck.available) {
        appendMessage(
          "bot",
          `❌ සමාවන්න, "${orderDraft.item}" සඳහා ප්‍රමාණවත් stock නැත (Available: ${stockCheck.stockQty}). වෙනත් item එකක් හෝ quantity එකක් ලියන්න.`
        );
        orderDraft.step = "item";
        return true;
      }

      orderDraft.step = "payment";
      appendMessage(
        "bot",
        `✅ මේක ගන්න පුළුවන්! ඔබ ඕඩර් කරන්න කැමති මොකෙන්ද? පහළ cards වලින් **Bank Transfer** හෝ **Cash on Delivery** තෝරන්න.`
      );
      showPaymentCardsPopup();
      return true;
    }

    if (orderDraft.step === "payment") {
      selectPaymentMethod(t || "Cash on Delivery");
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
      if (orderDraft.paymentMethod === "Bank Transfer" && !attachedSlipData) {
        orderDraft.step = "slip";
        appendMessage(
          "bot",
          "📎 කරුණාකර ඔබේ Payment Slip එක (`PNG`, `JPG`, හෝ `PDF`) attach කරන්න (නැත්නම් slip reference / 'done' ලියන්න)."
        );
        return true;
      }
      await finalizeOrder();
      return true;
    }

    if (orderDraft.step === "slip") {
      if (!attachedSlipData && t) {
        orderDraft.paymentProof = t;
      }
      await finalizeOrder();
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
    const stillActive = await verifyPlanStatus();
    if (!stillActive) {
      destroyWidgetIfInactive();
      return;
    }

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
      if (data.blocked || data.plan_active === false) {
        destroyWidgetIfInactive();
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

  function handleFileSelected(file) {
    if (!file) return;
    const allowed = ["image/png", "image/jpeg", "application/pdf"];
    if (!allowed.includes(file.type)) {
      appendMessage("bot", "කරුණාකර PNG, JPG, හෝ PDF file එකක් පමණක් attach කරන්න.");
      return;
    }
    const reader = new FileReader();
    reader.onload = async function (ev) {
      attachedSlipData = {
        name: file.name,
        type: file.type,
        dataUrl: String((ev && ev.target && ev.target.result) || "")
      };
      if (domRefs.slipNameEl) domRefs.slipNameEl.textContent = `📎 ${file.name}`;
      if (domRefs.slipBarEl) domRefs.slipBarEl.style.display = "flex";

      if (orderDraft.active && orderDraft.step === "slip") {
        await finalizeOrder();
      }
    };
    reader.readAsDataURL(file);
  }

  async function handleSubmit() {
    if (isSending || !domRefs.textInput) return;
    const text = domRefs.textInput.value.trim();
    if (!text && !attachedSlipData) return;
    domRefs.textInput.value = "";

    isSending = true;
    const displayMsg = text || (attachedSlipData ? `Attached: ${attachedSlipData.name}` : "");
    appendMessage("user", displayMsg);
    if (text) conversationHistory.push({ role: "user", content: text });

    try {
      if (orderDraft.active) {
        await handleOrderStep(text);
        return;
      }
      if (config.orderFlowEnabled && looksLikeOrderIntent(text)) {
        startOrderFlow("");
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

    const payPopupEl = document.createElement("div");
    payPopupEl.className = "zenix-pay-popup";
    payPopupEl.style.display = "none";

    const slipBarEl = document.createElement("div");
    slipBarEl.className = "zenix-slip-bar";
    const slipNameEl = document.createElement("span");
    const removeSlipBtn = document.createElement("button");
    removeSlipBtn.type = "button";
    removeSlipBtn.className = "zenix-icon-btn";
    removeSlipBtn.textContent = "✕";
    removeSlipBtn.addEventListener("click", () => {
      attachedSlipData = null;
      slipBarEl.style.display = "none";
      if (fileInputEl) fileInputEl.value = "";
    });
    slipBarEl.appendChild(slipNameEl);
    slipBarEl.appendChild(removeSlipBtn);

    const inputArea = document.createElement("div");
    inputArea.className = "zenix-input-area";

    const fileInputEl = document.createElement("input");
    fileInputEl.type = "file";
    fileInputEl.accept = ".png,.jpg,.jpeg,.pdf,image/png,image/jpeg,application/pdf";
    fileInputEl.style.display = "none";
    fileInputEl.addEventListener("change", (e) => {
      const f = e.target && e.target.files && e.target.files[0];
      handleFileSelected(f);
    });

    const attachBtn = document.createElement("button");
    attachBtn.type = "button";
    attachBtn.className = "zenix-icon-btn";
    attachBtn.style.padding = "9px 10px";
    attachBtn.title = "Attach PNG, JPG, or PDF";
    attachBtn.textContent = "📎";
    attachBtn.addEventListener("click", () => fileInputEl.click());

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

    inputArea.appendChild(fileInputEl);
    inputArea.appendChild(attachBtn);
    inputArea.appendChild(textInput);
    inputArea.appendChild(sendBtn);

    win.appendChild(header);
    win.appendChild(messagesEl);
    win.appendChild(payPopupEl);
    win.appendChild(slipBarEl);
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

    domRefs = {
      root,
      win,
      messagesEl,
      payPopupEl,
      slipBarEl,
      slipNameEl,
      fileInputEl,
      textInput,
      sendBtn,
      launcherBtn
    };
    appendMessage("bot", config.welcomeMessage);
  }

  async function init() {
    removeLegacyStoreAssistant();
    if (!ACTIVE_CLIENT_ID) {
      destroyWidgetIfInactive();
      return;
    }
    PLAN_IS_ACTIVE = await verifyPlanStatus();
    if (!PLAN_IS_ACTIVE) {
      destroyWidgetIfInactive();
      return;
    }
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