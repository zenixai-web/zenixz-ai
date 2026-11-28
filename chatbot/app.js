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

  const I18N = {
    si: {
      welcome:
        "ආයුබෝවන්! මම ඔබේ AI සහායකයා. ඔබට මොනවාද අවශ්‍ය කරන්නේ? නිෂ්පාදන විස්තර හෝ ඇනවුම් සඳහා අහන්න.",
      placeholder: "ඔබේ පණිවිඩය මෙතන ලියන්න...",
      send: "Send",
      typing: "ලියමින් සිටී...",
      askWhatNeeded: "ඔබට මොනවාද අවශ්‍ය කරන්නේ? අපගේ නිෂ්පාදන හෝ ඇනවුම් ගැන විමසන්න.",
      cardOrder: "🛒 ඇනවුම් කිරීමට කැමති නම්",
      cardOrderSub: "Order එකක් දාන්න",
      cardOtherItem: "📦 වෙනත් භාණ්ඩයක් ගැන",
      cardOtherItemSub: "තවත් විස්තර බලන්න",
      offTopicPopup:
        "මට උදව් කරන්න පුළුවන් මෙම ව්‍යාපාරයේ නිෂ්පාදන සහ ගනුදෙනු/ඇනවුම් සම්බන්ධයෙන් පමණයි. කරුණාකර ඒ සම්බන්ධ දෙයක් පමණක් මගෙන් අහන්න."
    },
    en: {
      welcome:
        "Hello! I am your AI store assistant. What would you like help with? Ask about our products or place an order.",
      placeholder: "Type your message here...",
      send: "Send",
      typing: "Typing...",
      askWhatNeeded: "What would you like? Ask about products or place an order.",
      cardOrder: "🛒 Place an Order",
      cardOrderSub: "Order this item",
      cardOtherItem: "📦 Another Product",
      cardOtherItemSub: "Explore more items",
      offTopicPopup:
        "I can only help with this store's products and order transactions. Please ask only about products or orders."
    },
    ta: {
      welcome:
        "வணக்கம்! நான் உங்கள் AI உதவியாளர். உங்களுக்கு என்ன தேவை? பொருட்கள் அல்லது ஆர்டர்கள் பற்றி கேளுங்கள்.",
      placeholder: "உங்கள் செய்தியை இங்கே தட்டச்சு செய்க...",
      send: "Send",
      typing: "எழுதுகிறது...",
      askWhatNeeded: "உங்களுக்கு என்ன தேவை? பொருட்கள் அல்லது ஆர்டர் பற்றி கேளுங்கள்.",
      cardOrder: "🛒 ஆர்டர் செய்ய விரும்பினால்",
      cardOrderSub: "ஆர்டர் பதிவு செய்க",
      cardOtherItem: "📦 வேறு பொருள் பற்றி",
      cardOtherItemSub: "மேலும் விவரம் பார்க்க",
      offTopicPopup:
        "இந்த வணிகத்தின் பொருட்கள் மற்றும் ஆர்டர்கள் தொடர்பாக மட்டுமே என்னால் உதவ முடியும். அது தொடர்பான கேள்விகளை மட்டும் கேளுங்கள்."
    }
  };

  const DEFAULT_CONFIG = {
    botName: ACTIVE_CLIENT_NAME ? `${ACTIVE_CLIENT_NAME} AI Assistant` : "Zenix AI Assistant",
    primaryColor: "#2563eb",
    secondaryColor: "#1e40af",
    theme: "dark",
    language: "si",
    orderFlowEnabled: true,
    maxOrderQty: 50,
    toggles: {
      showLanguageSwitcher: true,
      showThemeToggle: true,
      showChoiceCards: true,
      strictOffTopic: true
    }
  };

  let config = Object.assign({}, DEFAULT_CONFIG, {
    toggles: Object.assign({}, DEFAULT_CONFIG.toggles)
  });
  let isOpen = false;
  let isSending = false;
  let conversationHistory = [];
  let messageCounter = 0;
  let domRefs = {};
  let attachedSlipData = null;
  let lastMentionedItem = "";

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

  function getLangPack() {
    return I18N[config.language] || I18N.si;
  }

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
    const lightRecord = Object.assign({}, record, {
      payment_proof: "",
      client_id: ACTIVE_CLIENT_ID,
      created_at: record.created_at || new Date().toISOString()
    });
    const current = loadLocalOrders();
    const filtered = current.filter(
      (o) => String(o.order_id || "").trim() !== String(lightRecord.order_id || "").trim()
    );
    const updated = [lightRecord, ...filtered];
    saveLocalOrders(updated);
    broadcastOrderUpdate(lightRecord);
    return updated;
  }

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

      const preferredLang = String(data.default_language || data.language || "").trim().toLowerCase();
      if (I18N[preferredLang]) {
        config.language = preferredLang;
      }

      const widgetOpts =
        (data.widget_options && typeof data.widget_options === "object" && data.widget_options) ||
        (data.toggles && typeof data.toggles === "object" && data.toggles) ||
        {};
      if (typeof widgetOpts.show_language_switcher === "boolean") {
        config.toggles.showLanguageSwitcher = widgetOpts.show_language_switcher;
      }
      if (typeof widgetOpts.show_theme_toggle === "boolean") {
        config.toggles.showThemeToggle = widgetOpts.show_theme_toggle;
      }
      if (typeof widgetOpts.show_choice_cards === "boolean") {
        config.toggles.showChoiceCards = widgetOpts.show_choice_cards;
      }
      if (typeof widgetOpts.strict_off_topic === "boolean") {
        config.toggles.strictOffTopic = widgetOpts.strict_off_topic;
      }
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

  async function loadStockCatalogFromCsv() {
    if (!STOCK_CSV_URL) return [];
    try {
      const res = await fetch(STOCK_CSV_URL, { cache: "no-store" });
      if (!res.ok) return [];
      const csvText = await res.text();
      const lines = csvText
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean);
      if (lines.length < 2) return [];
      const items = [];
      for (let i = 1; i < lines.length; i++) {
        const cols = parseCsvRow(lines[i]);
        const name = String(cols[0] || "").trim();
        if (!name) continue;
        const rawStock = parseInt(String(cols[1] || "0").replace(/[^0-9-]/g, ""), 10);
        const qty = Number.isNaN(rawStock) ? 0 : rawStock;
        const price = String(cols[2] || "").trim();
        items.push({ name, qty, price });
      }
      return items;
    } catch (_) {
      return [];
    }
  }

  async function checkStockFromCsv(itemName, qty) {
    const catalog = await loadStockCatalogFromCsv();
    if (!catalog.length) return { checked: false, available: true, stockQty: null, matches: [] };
    const target = String(itemName || "").trim().toLowerCase();
    for (const row of catalog) {
      const rName = row.name.toLowerCase();
      if (rName && (rName === target || rName.includes(target) || target.includes(rName))) {
        lastMentionedItem = row.name;
        return {
          checked: true,
          available: row.qty >= Math.max(1, Number(qty || 1)),
          stockQty: row.qty,
          price: row.price,
          matches: [row]
        };
      }
    }
    return { checked: false, available: true, stockQty: null, matches: catalog };
  }

  async function handleStockInquiryIfApplicable(text) {
    const low = String(text || "").toLowerCase();
    const asksStock =
      low.includes("stock") ||
      low.includes("available") ||
      low.includes("price") ||
      low.includes("තියෙනවද") ||
      low.includes("ස්ටොක්") ||
      low.includes("මිල") ||
      low.includes("කීයද");

    if (!asksStock || !STOCK_CSV_URL) return null;
    const catalog = await loadStockCatalogFromCsv();
    if (!catalog.length) return null;

    const matched = catalog.filter(
      (it) =>
        low.includes(it.name.toLowerCase()) ||
        it.name.toLowerCase().split(/\s+/).some((w) => w.length >= 3 && low.includes(w))
    );

    const listToShow = matched.length ? matched : catalog.slice(0, 8);
    if (matched.length === 1) {
      lastMentionedItem = matched[0].name;
    }

    const lines = listToShow.map((it) => {
      const stText = it.qty > 0 ? `In Stock (${it.qty})` : "Out of Stock";
      const prText = it.price ? ` — ${it.price}` : "";
      return `• **${it.name}**: ${stText}${prText}`;
    });

    return `📦 **Stock Details**\n${lines.join("\n")}`;
  }

  function isOffTopicMessage(text) {
    if (!config.toggles.strictOffTopic) return false;
    const raw = String(text || "").trim();
    const low = raw.toLowerCase();
    if (!low) return false;

    const businessKeywords = [
      "order", "buy", "price", "cost", "stock", "item", "product", "delivery",
      "ship", "cod", "bank", "slip", "payment", "pay", "quantity", "qty",
      "address", "phone", "name", "store", "shop", "help", "support",
      "ඕඩර්", "ඇනවුම්", "මිල", "ස්ටොක්", "භාණ්ඩ", "ඩිලිවරි", "බෑන්ක්",
      "ස්ලිප්", "ගෙවීම", "නම", "ෆෝන්", "ඇඩ්‍රස්", "උදව්", "සහය",
      "ஆர்டர்", "விலை", "பொருள்", "பணம்", "டெலிவரி"
    ];
    if (businessKeywords.some((k) => low.includes(k))) {
      return false;
    }

    const offTopicPatterns = [
      "කන්නේ මොනවද", "කන්න මොනවද", "උදේට කෑවද", "දවල්ට කෑවද", "රෑට කෑවද",
      "සින්දුවක්", "song", "sing", "joke", "funny", "movie", "film",
      "cricket", "football", "politics", "election", "weather", "news",
      "love", "girlfriend", "boyfriend", "breakfast", "lunch", "dinner",
      "what to eat", "tell me a joke", "பாட்டு", "சாப்பாடு"
    ];
    return offTopicPatterns.some((p) => low.includes(p));
  }

  function isGreetingOnly(text) {
    const low = String(text || "").trim().toLowerCase();
    return [
      "hi", "hello", "hey", "ayubowan", "ආයුබෝවන්", "හායි", "හෙලෝ", "வணக்கம்"
    ].includes(low);
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
        width: 380px;
        max-width: calc(100vw - 24px);
        height: 545px;
        max-height: calc(100vh - 100px);
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
      .zenix-chat-window.light-theme {
        background: #ffffff;
        color: #0f172a;
        border: 1px solid #cbd5e1;
      }
      .zenix-chat-header {
        padding: 11px 12px;
        color: #ffffff;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
      }
      .zenix-header-controls {
        display: flex;
        align-items: center;
        gap: 5px;
        flex-wrap: wrap;
      }
      .zenix-lang-group {
        display: inline-flex;
        background: rgba(15, 23, 42, 0.35);
        border-radius: 8px;
        padding: 2px;
        gap: 2px;
      }
      .zenix-lang-btn {
        border: none;
        background: transparent;
        color: rgba(255, 255, 255, 0.82);
        border-radius: 6px;
        padding: 3px 6px;
        font-size: 10.5px;
        font-weight: 700;
        cursor: pointer;
      }
      .zenix-lang-btn.active {
        background: #ffffff;
        color: #0f172a;
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
      .zenix-chat-window.light-theme .zenix-messages {
        background: #f8fafc;
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
      .zenix-chat-window.light-theme .zenix-msg.bot {
        background: #ffffff;
        color: #0f172a;
        border: 1px solid #e2e8f0;
      }
      .zenix-msg.user {
        align-self: flex-end;
        color: #ffffff;
      }
      .zenix-choice-popup {
        display: none;
        gap: 8px;
        padding: 8px 12px;
        background: #0f172a;
        border-top: 1px solid #1e293b;
      }
      .zenix-chat-window.light-theme .zenix-choice-popup {
        background: #f1f5f9;
        border-top: 1px solid #e2e8f0;
      }
      .zenix-choice-card {
        flex: 1;
        background: #1e293b;
        color: #f8fafc;
        border: 1px solid #3b82f6;
        border-radius: 11px;
        padding: 9px 10px;
        cursor: pointer;
        text-align: left;
        font-size: 12px;
        font-weight: 600;
        transition: transform 0.12s ease, background 0.15s ease;
      }
      .zenix-chat-window.light-theme .zenix-choice-card {
        background: #ffffff;
        color: #0f172a;
        border: 1px solid #2563eb;
      }
      .zenix-choice-card:hover {
        background: #2563eb;
        color: #ffffff;
      }
      .zenix-choice-sub {
        display: block;
        font-size: 10.5px;
        opacity: 0.8;
        margin-top: 2px;
        font-weight: 400;
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
      .zenix-chat-window.light-theme .zenix-input-area {
        background: #ffffff;
        border-top: 1px solid #e2e8f0;
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
      .zenix-chat-window.light-theme .zenix-text-input {
        background: #f1f5f9;
        color: #0f172a;
        border: 1px solid #cbd5e1;
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
        padding: 5px 8px;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
      }
      .zenix-attach-btn {
        background: #1e293b;
        border: 1px solid #334155;
        color: #e2e8f0;
        border-radius: 10px;
        width: 36px;
        height: 36px;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
      }
      .zenix-chat-window.light-theme .zenix-attach-btn {
        background: #f1f5f9;
        border: 1px solid #cbd5e1;
        color: #0f172a;
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

  function hideChoiceCardsPopup() {
    if (domRefs.choicePopupEl) {
      domRefs.choicePopupEl.style.display = "none";
      domRefs.choicePopupEl.innerHTML = "";
    }
  }

  function showTwoOptionCardsPopup(mode) {
    if (!config.toggles.showChoiceCards || !domRefs.choicePopupEl) return;
    const pack = getLangPack();
    domRefs.choicePopupEl.innerHTML = "";
    domRefs.choicePopupEl.style.display = "flex";

    if (mode === "payment") {
      const bankBtn = document.createElement("button");
      bankBtn.type = "button";
      bankBtn.className = "zenix-choice-card";
      bankBtn.innerHTML = `🏦 Bank Transfer<span class="zenix-choice-sub">Slip Upload (Pending)</span>`;
      bankBtn.addEventListener("click", () => {
        hideChoiceCardsPopup();
        selectPaymentMethod("Bank Transfer");
      });

      const codBtn = document.createElement("button");
      codBtn.type = "button";
      codBtn.className = "zenix-choice-card";
      codBtn.innerHTML = `🚚 Cash on Delivery<span class="zenix-choice-sub">Pay on Arrival (Confirmed)</span>`;
      codBtn.addEventListener("click", () => {
        hideChoiceCardsPopup();
        selectPaymentMethod("Cash on Delivery");
      });

      domRefs.choicePopupEl.appendChild(bankBtn);
      domRefs.choicePopupEl.appendChild(codBtn);
      return;
    }

    const opt1 = document.createElement("button");
    opt1.type = "button";
    opt1.className = "zenix-choice-card";
    opt1.innerHTML = `${escapeHtml(pack.cardOrder)}<span class="zenix-choice-sub">${escapeHtml(pack.cardOrderSub)}</span>`;
    opt1.addEventListener("click", () => {
      hideChoiceCardsPopup();
      appendMessage("user", pack.cardOrder);
      startOrderFlow(lastMentionedItem);
    });

    const opt2 = document.createElement("button");
    opt2.type = "button";
    opt2.className = "zenix-choice-card";
    opt2.innerHTML = `${escapeHtml(pack.cardOtherItem)}<span class="zenix-choice-sub">${escapeHtml(pack.cardOtherItemSub)}</span>`;
    opt2.addEventListener("click", () => {
      hideChoiceCardsPopup();
      appendMessage("user", pack.cardOtherItem);
      appendMessage("bot", pack.askWhatNeeded);
    });

    domRefs.choicePopupEl.appendChild(opt1);
    domRefs.choicePopupEl.appendChild(opt2);
  }

  function applyTheme() {
    if (!domRefs.win) return;
    domRefs.win.classList.toggle("light-theme", config.theme === "light");
    if (domRefs.themeBtn) {
      domRefs.themeBtn.textContent = config.theme === "light" ? "🌙" : "☀️";
    }
  }

  function setLanguage(langCode) {
    const nextLang = I18N[langCode] ? langCode : "si";
    config.language = nextLang;
    const pack = getLangPack();

    if (domRefs.textInput) domRefs.textInput.placeholder = pack.placeholder;
    if (domRefs.sendBtn) domRefs.sendBtn.textContent = pack.send;

    if (domRefs.langBtns) {
      Object.entries(domRefs.langBtns).forEach(([code, btn]) => {
        if (btn) btn.classList.toggle("active", code === nextLang);
      });
    }
  }

  function renderBankDetailsCard() {
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

  function selectPaymentMethod(method) {
    hideChoiceCardsPopup();
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
    hideChoiceCardsPopup();
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
    await sendChatToServer(`Order captured: ${orderObj.order_id}`, orderObj, true);

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
      showTwoOptionCardsPopup("payment");
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

  async function sendChatToServer(messageText, orderPayload, skipAiReply) {
    if (!ACTIVE_CLIENT_ID || !PLAN_IS_ACTIVE) {
      destroyWidgetIfInactive();
      return;
    }

    const pack = getLangPack();
    const typingId = skipAiReply ? null : appendMessage("bot", pack.typing);
    const localizedPrompt = `[Language: ${config.language}] ${messageText}`;

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
          message: localizedPrompt,
          history: conversationHistory.slice(-10),
          order: orderPayload || null
        })
      });
      if (typingId) removeMessageById(typingId);
      if (!res.ok) {
        if (!skipAiReply) appendMessage("bot", "Server එකට සම්බන්ධ වීමට නොහැකි විය.");
        return;
      }
      const data = await res.json();
      if (data.blocked || data.plan_active === false) {
        destroyWidgetIfInactive();
        return;
      }
      if (data.reply && !skipAiReply) {
        appendMessage("bot", data.reply);
        conversationHistory.push({ role: "assistant", content: data.reply });
        showTwoOptionCardsPopup("next");
      }
    } catch (_) {
      if (typingId) removeMessageById(typingId);
      if (!skipAiReply) appendMessage("bot", "Server එකට සම්බන්ධ වීමට නොහැකි විය.");
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
    hideChoiceCardsPopup();

    try {
      if (orderDraft.active) {
        await handleOrderStep(text);
        return;
      }

      const pack = getLangPack();
      if (text && isOffTopicMessage(text)) {
        appendMessage("bot", pack.offTopicPopup);
        return;
      }

      if (text && isGreetingOnly(text)) {
        appendMessage("bot", pack.askWhatNeeded);
        return;
      }

      if (config.orderFlowEnabled && looksLikeOrderIntent(text)) {
        startOrderFlow(lastMentionedItem);
        return;
      }

      const stockReply = text ? await handleStockInquiryIfApplicable(text) : null;
      if (stockReply) {
        appendMessage("bot", stockReply);
        showTwoOptionCardsPopup("next");
        return;
      }

      await sendChatToServer(text || displayMsg, null, false);
    } finally {
      isSending = false;
    }
  }

  function buildWidget() {
    createStyles();
    if (document.getElementById("zenix-chat-widget-root")) return;

    const pack = getLangPack();
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
    title.style.fontSize = "13.5px";
    title.style.whiteSpace = "nowrap";
    title.style.overflow = "hidden";
    title.style.textOverflow = "ellipsis";
    title.textContent = config.botName;

    const controls = document.createElement("div");
    controls.className = "zenix-header-controls";

    const langBtns = {};
    if (config.toggles.showLanguageSwitcher) {
      const langGroup = document.createElement("div");
      langGroup.className = "zenix-lang-group";
      [
        ["si", "සිං"],
        ["en", "EN"],
        ["ta", "தமிழ்"]
      ].forEach(([code, label]) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "zenix-lang-btn" + (config.language === code ? " active" : "");
        b.textContent = label;
        b.addEventListener("click", () => setLanguage(code));
        langGroup.appendChild(b);
        langBtns[code] = b;
      });
      controls.appendChild(langGroup);
    }

    let themeBtn = null;
    if (config.toggles.showThemeToggle) {
      themeBtn = document.createElement("button");
      themeBtn.type = "button";
      themeBtn.className = "zenix-icon-btn";
      themeBtn.title = "Toggle Dark / Light";
      themeBtn.textContent = config.theme === "light" ? "🌙" : "☀️";
      themeBtn.addEventListener("click", () => {
        config.theme = config.theme === "dark" ? "light" : "dark";
        applyTheme();
      });
      controls.appendChild(themeBtn);
    }

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "zenix-icon-btn";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => toggleWidget(false));
    controls.appendChild(closeBtn);

    header.appendChild(title);
    header.appendChild(controls);

    const messagesEl = document.createElement("div");
    messagesEl.className = "zenix-messages";

    const choicePopupEl = document.createElement("div");
    choicePopupEl.className = "zenix-choice-popup";

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
    attachBtn.className = "zenix-attach-btn";
    attachBtn.title = "Attach PNG, JPG, or PDF";
    attachBtn.innerHTML = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>`;
    attachBtn.addEventListener("click", () => fileInputEl.click());

    const textInput = document.createElement("input");
    textInput.type = "text";
    textInput.className = "zenix-text-input";
    textInput.placeholder = pack.placeholder;
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
    sendBtn.textContent = pack.send;
    sendBtn.addEventListener("click", handleSubmit);

    inputArea.appendChild(fileInputEl);
    inputArea.appendChild(attachBtn);
    inputArea.appendChild(textInput);
    inputArea.appendChild(sendBtn);

    win.appendChild(header);
    win.appendChild(messagesEl);
    win.appendChild(choicePopupEl);
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
      choicePopupEl,
      slipBarEl,
      slipNameEl,
      fileInputEl,
      textInput,
      sendBtn,
      themeBtn,
      langBtns,
      launcherBtn
    };
    setLanguage(config.language);
    applyTheme();
    appendMessage("bot", pack.welcome);
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