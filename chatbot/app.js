(function () {
  if (window.__ZENIX_WIDGET_LOADED__) return;
  window.__ZENIX_WIDGET_LOADED__ = true;

  const CHAT_FONT_STACK = '"Google Sans", "Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

  function injectChatFont() {
    try {
      if (!document.getElementById("zenix-google-font-link")) {
        const link = document.createElement("link");
        link.id = "zenix-google-font-link";
        link.rel = "stylesheet";
        link.href = "https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap";
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
  injectChatFont();

  const scriptEl =
    document.currentScript ||
    Array.from(document.querySelectorAll("script")).find((s) =>
      (s.src || "").includes("app.js")
    );

  let scriptUrl = null;
  try {
    if (scriptEl && scriptEl.src) {
      scriptUrl = new URL(scriptEl.src, window.location.href);
    }
  } catch (_) {}

  const inferredOrigin =
    scriptUrl && scriptUrl.origin && scriptUrl.origin !== "null"
      ? scriptUrl.origin
      : window.location.origin && window.location.origin !== "null"
      ? window.location.origin
      : "https://zenixz-ai-1.onrender.com/";

  const API_BASE =
    (scriptEl && scriptEl.getAttribute("data-api-base")) ||
    window.ZENIX_API_BASE ||
    inferredOrigin;

  const BOTPRESS_WEBHOOK_URL =
    (scriptEl && scriptEl.getAttribute("data-botpress-webhook")) ||
    window.ZENIX_BOTPRESS_WEBHOOK_URL ||
    "";

  const CLIENT_ID_FROM_SCRIPT =
    (scriptEl && scriptEl.getAttribute("data-client-id")) ||
    window.ZENIX_CLIENT_ID ||
    "";

  const CLIENT_NAME_FROM_SCRIPT =
    (scriptEl && scriptEl.getAttribute("data-client-name")) ||
    window.ZENIX_CLIENT_NAME ||
    "";

  const CLIENT_KEY_FROM_SCRIPT =
    (scriptEl && scriptEl.getAttribute("data-client-key")) ||
    window.ZENIX_CLIENT_KEY ||
    "";

  const SITE_ID =
    (scriptEl && scriptEl.getAttribute("data-site-id")) ||
    window.ZENIX_SITE_ID ||
    CLIENT_ID_FROM_SCRIPT ||
    window.location.hostname ||
    "default-site";

  const STORAGE_SCOPE = `${SITE_ID}::${CLIENT_ID_FROM_SCRIPT || "default"}`;

  let ACTIVE_CLIENT_KEY = CLIENT_KEY_FROM_SCRIPT || "";
  let ACTIVE_CLIENT_ID = CLIENT_ID_FROM_SCRIPT || "";
  let ACTIVE_CLIENT_NAME = CLIENT_NAME_FROM_SCRIPT || "";
  let AUTH_VERIFIED = false;
  let AUTH_STATUS = "PENDING";
  let AUTH_REASON = "";

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
      localStorage.setItem(ORDERS_LOCAL_KEY, JSON.stringify(list.slice(0, 200)));
    } catch (_) {}
  }

  function broadcastOrderUpdate(record) {
    try {
      if (orderChannel) {
        orderChannel.postMessage({
          type: "ORDER_CREATED",
          client_id: ACTIVE_CLIENT_ID || CLIENT_ID_FROM_SCRIPT || "",
          site_id: SITE_ID,
          order: record,
          orders: loadLocalOrders(),
          timestamp: Date.now()
        });
      }
    } catch (_) {}
    try {
      window.dispatchEvent(
        new CustomEvent("zenix:order-created", {
          detail: {
            client_id: ACTIVE_CLIENT_ID || CLIENT_ID_FROM_SCRIPT || "",
            site_id: SITE_ID,
            order: record,
            orders: loadLocalOrders()
          }
        })
      );
    } catch (_) {}
  }

  function persistOrderLocally(record) {
    const current = loadLocalOrders();
    const filtered = current.filter(
      (o) => String(o.order_id || "").trim() !== String(record.order_id || "").trim()
    );
    const updated = [record, ...filtered];
    saveLocalOrders(updated);
    broadcastOrderUpdate(record);
    return updated;
  }

  function updateLocalOrderFields(orderId, patch) {
    if (!orderId) return;
    const current = loadLocalOrders();
    let changedRecord = null;
    const updated = current.map((o) => {
      if (String(o.order_id || "").trim() === String(orderId).trim()) {
        changedRecord = Object.assign({}, o, patch);
        return changedRecord;
      }
      return o;
    });
    if (changedRecord) {
      saveLocalOrders(updated);
      broadcastOrderUpdate(changedRecord);
    }
  }

  const DEFAULT_CONFIG = {
    botName: ACTIVE_CLIENT_NAME ? `${ACTIVE_CLIENT_NAME} AI Assistant` : "Zenix AI Assistant",
    welcomeMessage: "ආයුබෝවන්! මම ඔබේ AI සහායකයා. මට ඔබට කොහොමද උදව් කරන්න පුළුවන්?",
    primaryColor: "#2563eb",
    secondaryColor: "#1e40af",
    position: "bottom-right",
    theme: "light",
    fontFamily: CHAT_FONT_STACK,
    fontSize: 14,
    allowVoice: true,
    allowImage: true,
    allowFileUpload: true,
    showPoweredBy: false,
    quickReplies: ["මිල ගණන් දැනගන්න", "Order එකක් දාන්න", "Contact Info"],
    currency: "LKR",
    language: "si",
    businessType: "general",
    businessName: ACTIVE_CLIENT_NAME || "",
    bankDetails: "",
    contactInfo: "",
    deliveryInfo: "",
    customInstructions: "",
    products: [],
    faqs: [],
    paymentRules: {},
    orderFlowEnabled: true,
    maxOrderQty: 50,
    apiKey: "",
    botpressWebhookUrl: BOTPRESS_WEBHOOK_URL,
    clientKey: ACTIVE_CLIENT_KEY,
    clientId: ACTIVE_CLIENT_ID,
    authStatus: AUTH_STATUS,
    authVerified: AUTH_VERIFIED,
    authReason: AUTH_REASON
  };

  let config = Object.assign({}, DEFAULT_CONFIG);
  let isOpen = false;
  let isSending = false;
  let isRecording = false;
  let recognition = null;
  let pendingImageBase64 = null;
  let pendingImageName = null;
  let pendingImageDataUrl = null;
  let pendingPaymentProofBase64 = null;
  let pendingPaymentProofName = null;
  let pendingPaymentProofDataUrl = null;

  let conversationHistory = [];
  let messageCounter = 0;

  let orderDraft = {
    active: false,
    step: null,
    item: "",
    quantity: 1,
    unitPrice: 0,
    totalPrice: 0,
    customerName: "",
    phone: "",
    address: "",
    paymentMethod: "",
    paymentProofName: "",
    paymentProofUrl: "",
    paymentProofDataUrl: "",
    orderId: ""
  };

  const processedStockOrderIds = new Set();

  const UI_STRINGS = {
    si: {
      placeholder: "ඔබේ පණිවිඩය මෙතන ලියන්න...",
      send: "යවන්න",
      typing: "AI සහායකයා ලියමින් සිටී...",
      voiceListening: "කතා කරන්න... (සවන් දෙමින්)",
      voiceNotSupported: "ඔබේ Browser එක Voice input සඳහා සහය නොදක්වයි.",
      imageAttached: "පින්තූරය තෝරා ඇත",
      proofAttached: "Payment slip/proof තෝරා ඇත",
      removeFile: "ඉවත් කරන්න",
      orderStarted: "හරි! අපි ඔබේ Order එක සකසමු. ඔබට අවශ්‍ය Product / Item එකේ නම කියන්න.",
      orderAskQty: (item, price) =>
        `"${item}" (Unit price: ${price ? price.toLocaleString() : "0"}) සඳහා ඔබට අවශ්‍ය ප්‍රමාණය (Quantity) කීයද?`,
      orderAskName: "ඔබේ සම්පූර්ණ නම (Full Name) ඇතුළත් කරන්න.",
      orderAskPhone: "ඔබේ දුරකථන අංකය (Phone Number) ඇතුළත් කරන්න.",
      orderAskAddress: "Delivery කරන්න ඕනෑ ලිපිනය (Delivery Address) ඇතුළත් කරන්න.",
      orderAskPayment: (summaryText) =>
        `${summaryText}\n\nPayment ක්‍රමය තෝරන්න:\n1. Bank Transfer / Slip Upload\n2. Cash on Delivery (COD)\n\n"Bank Transfer" හෝ "COD" ලෙස type කරන්න (Bank transfer නම් slip/proof photo එකක්/PDF එකක් attach කරන්න).`,
      orderConfirmed: (ord) =>
        `✅ **Order එක සාර්ථකව තහවුරු වුණා!**\n\n• **Order ID:** ${ord.order_id}\n• **Item:** ${ord.item}\n• **Quantity:** ${ord.quantity}\n• **Total:** ${Number(ord.total || 0).toLocaleString()} ${ord.currency || "LKR"}\n• **Name:** ${ord.customer_name}\n• **Phone:** ${ord.phone}\n• **Address:** ${ord.address}\n• **Payment:** ${ord.payment_method}${ord.payment_proof_name ? `\n• **Proof:** ${ord.payment_proof_name}` : ""}\n\nඉක්මනින් අපේ team එක ඔබව සම්බන්ධ කරගනී.`,
      orderCancelled: "Order එක cancel කරන ලදී. වෙනත් උදව්වක් අවශ්‍යද?",
      clearChat: "Chat Clear",
      newOrderBtn: "New Order",
      uploadProofBtn: "Upload Proof",
      uploadImageBtn: "Photo",
      voiceBtn: "Voice",
      offlineMsg: "Server එකට සම්බන්ධ වීමට නොහැකි විය. කරුණාකර නැවත උත්සාහ කරන්න."
    },
    en: {
      placeholder: "Type your message here...",
      send: "Send",
      typing: "AI Assistant is typing...",
      voiceListening: "Listening... speak now",
      voiceNotSupported: "Voice input is not supported in your browser.",
      imageAttached: "Image attached",
      proofAttached: "Payment proof attached",
      removeFile: "Remove",
      orderStarted: "Great! Let's place your order. Please tell me the product or item name you want.",
      orderAskQty: (item, price) =>
        `How many units of "${item}"${price ? ` (${Number(price).toLocaleString()} each)` : ""} would you like?`,
      orderAskName: "Please enter your full name.",
      orderAskPhone: "Please enter your phone number.",
      orderAskAddress: "Please enter your delivery address.",
      orderAskPayment: (summaryText) =>
        `${summaryText}\n\nChoose payment method:\n1. Bank Transfer (attach slip/proof image or PDF)\n2. Cash on Delivery (COD)\n\nType "Bank Transfer" or "COD".`,
      orderConfirmed: (ord) =>
        `✅ **Order Confirmed!**\n\n• **Order ID:** ${ord.order_id}\n• **Item:** ${ord.item}\n• **Quantity:** ${ord.quantity}\n• **Total:** ${Number(ord.total || 0).toLocaleString()} ${ord.currency || "LKR"}\n• **Name:** ${ord.customer_name}\n• **Phone:** ${ord.phone}\n• **Address:** ${ord.address}\n• **Payment:** ${ord.payment_method}${ord.payment_proof_name ? `\n• **Proof:** ${ord.payment_proof_name}` : ""}\n\nOur team will contact you shortly.`,
      orderCancelled: "Order cancelled. How else can I help you?",
      clearChat: "Clear",
      newOrderBtn: "Order",
      uploadProofBtn: "Proof",
      uploadImageBtn: "Photo",
      voiceBtn: "Voice",
      offlineMsg: "Unable to reach the server right now. Please try again."
    },
    ta: {
      placeholder: "உங்கள் செய்தியை இங்கே தட்டச்சு செய்யவும்...",
      send: "அனுப்பு",
      typing: "AI உதவியாளர் எழுதுகிறார்...",
      voiceListening: "பேசுங்கள்... கேட்கிறது",
      voiceNotSupported: "உங்கள் உலாவி குரல் உள்ளீட்டை ஆதரிக்கவில்லை.",
      imageAttached: "படம் இணைக்கப்பட்டது",
      proofAttached: "பணம் செலுத்திய ஆவணம் இணைக்கப்பட்டது",
      removeFile: "நீக்கு",
      orderStarted: "சரி! உங்கள் ஆர்டரைத் தொடங்குவோம். உங்களுக்கு தேவையான பொருளின் பெயரைச் சொல்லவும்.",
      orderAskQty: (item, price) =>
        `"${item}"${price ? ` (${Number(price).toLocaleString()} ஒவ்வொன்றும்)` : ""} எத்தனை எண்ணிக்கை வேண்டும்?`,
      orderAskName: "உங்கள் முழுப் பெயரை உள்ளிடவும்.",
      orderAskPhone: "உங்கள் தொலைபேசி எண்ணை உள்ளிடவும்.",
      orderAskAddress: "டெலிவரி முகவரியை உள்ளிடவும்.",
      orderAskPayment: (summaryText) =>
        `${summaryText}\n\nபணம் செலுத்தும் முறையைத் தேர்ந்தெடுக்கவும்:\n1. Bank Transfer (ரசீது/படம் இணைக்கவும்)\n2. Cash on Delivery (COD)`,
      orderConfirmed: (ord) =>
        `✅ **ஆர்டர் உறுதி செய்யப்பட்டது!**\n\n• **Order ID:** ${ord.order_id}\n• **Item:** ${ord.item}\n• **Quantity:** ${ord.quantity}\n• **Total:** ${Number(ord.total || 0).toLocaleString()} ${ord.currency || "LKR"}\n• **Name:** ${ord.customer_name}\n• **Phone:** ${ord.phone}\n• **Address:** ${ord.address}\n• **Payment:** ${ord.payment_method}`,
      orderCancelled: "ஆர்டர் ரத்து செய்யப்பட்டது. வேறு உதவி தேவையா?",
      clearChat: "அழி",
      newOrderBtn: "Order",
      uploadProofBtn: "Proof",
      uploadImageBtn: "Photo",
      voiceBtn: "Voice",
      offlineMsg: "சர்வருடன் இணைக்க முடியவில்லை. மீண்டும் முயற்சிக்கவும்."
    }
  };

  function getLangStrings() {
    const lang = (config.language || "si").toLowerCase();
    if (lang.startsWith("en")) return UI_STRINGS.en;
    if (lang.startsWith("ta")) return UI_STRINGS.ta;
    return UI_STRINGS.si;
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
    safe = safe.replace(/\*(.+?)\*/g, "<em>$1</em>");
    safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");
    safe = safe.replace(/\n/g, "<br>");
    return safe;
  }

  function generateOrderId() {
    const now = new Date();
    const y = String(now.getFullYear()).slice(-2);
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    const rnd = Math.floor(1000 + Math.random() * 9000);
    return `ORD-${y}${m}${d}-${rnd}`;
  }

  function findProductPrice(itemName) {
    if (!itemName || !Array.isArray(config.products)) return 0;
    const q = String(itemName).trim().toLowerCase();
    if (!q) return 0;
    let exact = config.products.find(
      (p) => String(p.name || p.title || "").trim().toLowerCase() === q
    );
    if (!exact) {
      exact = config.products.find(
        (p) =>
          String(p.name || p.title || "").trim().toLowerCase().includes(q) ||
          q.includes(String(p.name || p.title || "").trim().toLowerCase())
      );
    }
    if (exact) {
      const rawPrice = exact.price ?? exact.unit_price ?? exact.amount ?? 0;
      const num = parseFloat(String(rawPrice).replace(/[^0-9.]/g, ""));
      return isNaN(num) ? 0 : num;
    }
    return 0;
  }

  function normalizeOrderPayload(ord) {
    const qty = Math.max(1, parseInt(ord.quantity, 10) || 1);
    const unitPrice = parseFloat(ord.unit_price ?? ord.unitPrice ?? 0) || 0;
    const total =
      parseFloat(ord.total ?? ord.totalPrice ?? 0) ||
      (unitPrice > 0 ? unitPrice * qty : 0);

    return {
      order_id: ord.order_id || ord.orderId || generateOrderId(),
      client_id: ACTIVE_CLIENT_ID || CLIENT_ID_FROM_SCRIPT || ord.client_id || "",
      client_key: ACTIVE_CLIENT_KEY || CLIENT_KEY_FROM_SCRIPT || "",
      site_id: SITE_ID,
      session_id: sessionId,
      item: ord.item || "Item",
      quantity: qty,
      unit_price: unitPrice,
      total: total,
      currency: ord.currency || config.currency || "LKR",
      customer_name: ord.customer_name || ord.customerName || "",
      phone: ord.phone || "",
      address: ord.address || "",
      payment_method: ord.payment_method || ord.paymentMethod || "COD",
      payment_status:
        (ord.payment_method || ord.paymentMethod || "").toLowerCase().includes("bank") ||
        (ord.payment_method || ord.paymentMethod || "").toLowerCase().includes("transfer") ||
        (ord.payment_method || ord.paymentMethod || "").toLowerCase().includes("slip")
          ? ord.payment_proof_name || ord.paymentProofName
            ? "Proof Submitted (Pending Verification)"
            : "Pending Bank Transfer"
          : "Cash on Delivery",
      payment_proof_name: ord.payment_proof_name || ord.paymentProofName || "",
      payment_proof_url: ord.payment_proof_url || ord.paymentProofUrl || "",
      payment_proof_data_url: ord.payment_proof_data_url || ord.paymentProofDataUrl || "",
      status: "Pending",
      created_at: ord.created_at || new Date().toISOString()
    };
  }

  async function deductStockOnce(record) {
    const orderId = String(record.order_id || "").trim();
    const item = String(record.item || "").trim();
    const qty = Math.max(1, parseInt(record.quantity, 10) || 1);
    if (!item || qty <= 0) return;

    const dedupKey = orderId ? `order:${orderId}` : `item:${item.toLowerCase()}:qty:${qty}:${Date.now()}`;
    if (processedStockOrderIds.has(dedupKey)) {
      return;
    }
    processedStockOrderIds.add(dedupKey);

    try {
      const res = await fetch(`${API_BASE.replace(/\/+$/, "")}/api/deduct-stock`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(ACTIVE_CLIENT_KEY ? { "X-Client-Key": ACTIVE_CLIENT_KEY } : {}),
          ...(ACTIVE_CLIENT_ID ? { "X-Client-Id": ACTIVE_CLIENT_ID } : {})
        },
        body: JSON.stringify({
          order_id: orderId,
          item: item,
          quantity: qty,
          client_id: ACTIVE_CLIENT_ID || CLIENT_ID_FROM_SCRIPT || "",
          site_id: SITE_ID
        })
      });
      if (res.ok) {
        try {
          const data = await res.json();
          if (data && typeof data.remaining_stock !== "undefined") {
            updateLocalOrderFields(orderId, { remaining_stock: data.remaining_stock });
          }
        } catch (_) {}
      }
    } catch (_) {}
  }

  async function sendOrderToBackendAndMake(record) {
    const payload = Object.assign({}, record, {
      skip_stock_deduction: true,
      stock_deducted_once: true
    });

    try {
      const res = await fetch(`${API_BASE.replace(/\/+$/, "")}/api/orders`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(ACTIVE_CLIENT_KEY ? { "X-Client-Key": ACTIVE_CLIENT_KEY } : {}),
          ...(ACTIVE_CLIENT_ID ? { "X-Client-Id": ACTIVE_CLIENT_ID } : {})
        },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        try {
          const data = await res.json();
          const serverOrder = data && (data.order || data.data || data);
          if (serverOrder && serverOrder.order_id) {
            updateLocalOrderFields(record.order_id, {
              order_id: serverOrder.order_id,
              status: serverOrder.status || record.status,
              payment_status: serverOrder.payment_status || record.payment_status,
              synced_at: new Date().toISOString()
            });
          }
        } catch (_) {}
      }
    } catch (_) {}

    const webhookTarget = config.botpressWebhookUrl || BOTPRESS_WEBHOOK_URL;
    if (webhookTarget) {
      try {
        await fetch(webhookTarget, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            event: "new_order",
            order: payload,
            session_id: sessionId,
            site_id: SITE_ID,
            client_id: ACTIVE_CLIENT_ID || CLIENT_ID_FROM_SCRIPT || ""
          })
        });
      } catch (_) {}
    }
  }

  async function finalizeAndSaveOrderInstant(rawOrder) {
    const record = normalizeOrderPayload(rawOrder);
    persistOrderLocally(record);
    await deductStockOnce(record);
    sendOrderToBackendAndMake(record);
    return record;
  }

  async function verifyClientKeyAndLoadConfig() {
    const keyToVerify =
      ACTIVE_CLIENT_KEY ||
      (scriptEl && scriptEl.getAttribute("data-client-key")) ||
      window.ZENIX_CLIENT_KEY ||
      "";
    const idToVerify =
      ACTIVE_CLIENT_ID ||
      (scriptEl && scriptEl.getAttribute("data-client-id")) ||
      window.ZENIX_CLIENT_ID ||
      "";

    try {
      const params = new URLSearchParams();
      if (keyToVerify) params.set("client_key", keyToVerify);
      if (idToVerify) params.set("client_id", idToVerify);
      if (SITE_ID) params.set("site_id", SITE_ID);

      const url = `${API_BASE.replace(/\/+$/, "")}/api/widget-config?${params.toString()}`;
      const res = await fetch(url, {
        method: "GET",
        headers: {
          ...(keyToVerify ? { "X-Client-Key": keyToVerify } : {}),
          ...(idToVerify ? { "X-Client-Id": idToVerify } : {})
        }
      });

      if (res.ok) {
        const data = await res.json();
        const cfg = data && (data.config || data);
        if (cfg && typeof cfg === "object") {
          AUTH_VERIFIED = cfg.auth_verified !== false;
          AUTH_STATUS = cfg.auth_status || (AUTH_VERIFIED ? "ACTIVE" : "INVALID");
          AUTH_REASON = cfg.auth_reason || "";
          if (cfg.client_id) ACTIVE_CLIENT_ID = cfg.client_id;
          if (cfg.client_key) ACTIVE_CLIENT_KEY = cfg.client_key;
          if (cfg.business_name && !ACTIVE_CLIENT_NAME) ACTIVE_CLIENT_NAME = cfg.business_name;

          config = Object.assign({}, config, {
            botName: cfg.bot_name || cfg.botName || config.botName,
            welcomeMessage: cfg.welcome_message || cfg.welcomeMessage || config.welcomeMessage,
            primaryColor: cfg.primary_color || cfg.primaryColor || config.primaryColor,
            secondaryColor: cfg.secondary_color || cfg.secondaryColor || config.secondaryColor,
            position: cfg.position || config.position,
            theme: cfg.theme || config.theme,
            fontFamily: cfg.font_family || cfg.fontFamily || CHAT_FONT_STACK,
            fontSize: parseInt(cfg.font_size || cfg.fontSize || config.fontSize, 10) || 14,
            allowVoice: cfg.allow_voice !== undefined ? Boolean(cfg.allow_voice) : config.allowVoice,
            allowImage: cfg.allow_image !== undefined ? Boolean(cfg.allow_image) : config.allowImage,
            allowFileUpload:
              cfg.allow_file_upload !== undefined ? Boolean(cfg.allow_file_upload) : config.allowFileUpload,
            showPoweredBy:
              cfg.show_powered_by !== undefined ? Boolean(cfg.show_powered_by) : config.showPoweredBy,
            quickReplies:
              Array.isArray(cfg.quick_replies) && cfg.quick_replies.length
                ? cfg.quick_replies
                : Array.isArray(cfg.quickReplies) && cfg.quickReplies.length
                ? cfg.quickReplies
                : config.quickReplies,
            currency: cfg.currency || config.currency,
            language: cfg.language || config.language,
            businessType: cfg.business_type || cfg.businessType || config.businessType,
            businessName: cfg.business_name || cfg.businessName || config.businessName,
            bankDetails: cfg.bank_details || cfg.bankDetails || config.bankDetails,
            contactInfo: cfg.contact_info || cfg.contactInfo || config.contactInfo,
            deliveryInfo: cfg.delivery_info || cfg.deliveryInfo || config.deliveryInfo,
            customInstructions:
              cfg.custom_instructions || cfg.customInstructions || config.customInstructions,
            products: Array.isArray(cfg.products) ? cfg.products : config.products,
            faqs: Array.isArray(cfg.faqs) ? cfg.faqs : config.faqs,
            paymentRules:
              cfg.payment_rules || cfg.paymentRules || config.paymentRules,
            orderFlowEnabled:
              cfg.order_flow_enabled !== undefined
                ? Boolean(cfg.order_flow_enabled)
                : config.orderFlowEnabled,
            maxOrderQty: parseInt(cfg.max_order_qty || cfg.maxOrderQty || 50, 10) || 50,
            botpressWebhookUrl:
              cfg.botpress_webhook_url || cfg.botpressWebhookUrl || config.botpressWebhookUrl,
            clientKey: ACTIVE_CLIENT_KEY,
            clientId: ACTIVE_CLIENT_ID,
            authStatus: AUTH_STATUS,
            authVerified: AUTH_VERIFIED,
            authReason: AUTH_REASON
          });
        }
      }
    } catch (_) {}
  }

  function createWidgetStyles() {
    if (document.getElementById("zenix-chat-widget-styles")) return;
    const style = document.createElement("style");
    style.id = "zenix-chat-widget-styles";
    style.textContent = `
      .zenix-widget-root {
        position: fixed;
        z-index: 2147483000;
        font-family: ${CHAT_FONT_STACK};
        font-size: 14px;
        line-height: 1.45;
      }
      .zenix-widget-root.pos-bottom-right {
        bottom: 20px;
        right: 20px;
      }
      .zenix-widget-root.pos-bottom-left {
        bottom: 20px;
        left: 20px;
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
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.25);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      .zenix-launcher-btn:hover {
        transform: translateY(-2px) scale(1.03);
        box-shadow: 0 14px 30px rgba(15, 23, 42, 0.32);
      }
      .zenix-chat-window {
        position: absolute;
        bottom: 72px;
        width: 375px;
        max-width: calc(100vw - 28px);
        height: 540px;
        max-height: calc(100vh - 105px);
        border-radius: 18px;
        overflow: hidden;
        display: none;
        flex-direction: column;
        box-shadow: 0 20px 50px rgba(15, 23, 42, 0.28);
        border: 1px solid rgba(148, 163, 184, 0.25);
        background: #ffffff;
        color: #0f172a;
      }
      .zenix-widget-root.pos-bottom-right .zenix-chat-window {
        right: 0;
      }
      .zenix-widget-root.pos-bottom-left .zenix-chat-window {
        left: 0;
      }
      .zenix-chat-window.open {
        display: flex;
      }
      .zenix-chat-window.dark {
        background: #0f172a;
        color: #f8fafc;
        border-color: rgba(51, 65, 85, 0.8);
      }
      .zenix-chat-header {
        padding: 14px 16px;
        color: #ffffff;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }
      .zenix-header-info {
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .zenix-header-title {
        font-weight: 600;
        font-size: 15px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .zenix-header-sub {
        font-size: 11px;
        opacity: 0.9;
      }
      .zenix-header-actions {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .zenix-icon-btn {
        background: rgba(255, 255, 255, 0.18);
        border: none;
        color: #ffffff;
        border-radius: 8px;
        padding: 5px 8px;
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
        transition: background 0.15s ease;
      }
      .zenix-icon-btn:hover {
        background: rgba(255, 255, 255, 0.3);
      }
      .zenix-messages {
        flex: 1;
        overflow-y: auto;
        padding: 14px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        background: #f8fafc;
      }
      .zenix-chat-window.dark .zenix-messages {
        background: #020617;
      }
      .zenix-msg {
        max-width: 84%;
        padding: 10px 13px;
        border-radius: 14px;
        word-wrap: break-word;
      }
      .zenix-msg.bot {
        align-self: flex-start;
        background: #ffffff;
        color: #0f172a;
        border: 1px solid #e2e8f0;
        border-bottom-left-radius: 4px;
      }
      .zenix-chat-window.dark .zenix-msg.bot {
        background: #1e293b;
        color: #f8fafc;
        border-color: #334155;
      }
      .zenix-msg.user {
        align-self: flex-end;
        color: #ffffff;
        border-bottom-right-radius: 4px;
      }
      .zenix-msg img {
        max-width: 100%;
        border-radius: 10px;
        margin-top: 6px;
        display: block;
      }
      .zenix-quick-replies {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        padding: 8px 14px;
        background: #f1f5f9;
        border-top: 1px solid #e2e8f0;
      }
      .zenix-chat-window.dark .zenix-quick-replies {
        background: #0f172a;
        border-top-color: #1e293b;
      }
      .zenix-chip-btn {
        border: 1px solid #cbd5e1;
        background: #ffffff;
        color: #1e293b;
        border-radius: 999px;
        padding: 5px 11px;
        font-size: 12px;
        cursor: pointer;
        transition: all 0.15s ease;
      }
      .zenix-chat-window.dark .zenix-chip-btn {
        background: #1e293b;
        color: #e2e8f0;
        border-color: #334155;
      }
      .zenix-chip-btn:hover {
        border-color: #2563eb;
        color: #2563eb;
      }
      .zenix-attachment-bar {
        display: none;
        align-items: center;
        justify-content: space-between;
        padding: 6px 14px;
        font-size: 12px;
        background: #eff6ff;
        color: #1d4ed8;
        border-top: 1px solid #bfdbfe;
      }
      .zenix-attachment-bar.active {
        display: flex;
      }
      .zenix-input-area {
        padding: 10px 12px;
        border-top: 1px solid #e2e8f0;
        background: #ffffff;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .zenix-chat-window.dark .zenix-input-area {
        background: #0f172a;
        border-top-color: #1e293b;
      }
      .zenix-toolbar {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
      }
      .zenix-tool-btn {
        border: 1px solid #e2e8f0;
        background: #f8fafc;
        color: #334155;
        border-radius: 8px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
      }
      .zenix-chat-window.dark .zenix-tool-btn {
        background: #1e293b;
        color: #cbd5e1;
        border-color: #334155;
      }
      .zenix-tool-btn.recording {
        background: #fee2e2;
        color: #b91c1c;
        border-color: #fca5a5;
      }
      .zenix-input-row {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .zenix-text-input {
        flex: 1;
        border: 1px solid #cbd5e1;
        border-radius: 10px;
        padding: 9px 12px;
        font-size: 13.5px;
        outline: none;
        background: #ffffff;
        color: #0f172a;
      }
      .zenix-chat-window.dark .zenix-text-input {
        background: #1e293b;
        color: #f8fafc;
        border-color: #334155;
      }
      .zenix-send-btn {
        border: none;
        border-radius: 10px;
        padding: 9px 14px;
        color: #ffffff;
        font-weight: 600;
        font-size: 13px;
        cursor: pointer;
      }
      .zenix-powered {
        text-align: center;
        font-size: 10px;
        opacity: 0.65;
        padding-bottom: 6px;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  let domRefs = {};

  function buildWidgetDom() {
    createWidgetStyles();
    if (document.getElementById("zenix-chat-widget-root")) return;

    const s = getLangStrings();
    const root = document.createElement("div");
    root.id = "zenix-chat-widget-root";
    root.className = `zenix-widget-root ${
      config.position === "bottom-left" ? "pos-bottom-left" : "pos-bottom-right"
    }`;

    const win = document.createElement("div");
    win.className = `zenix-chat-window ${config.theme === "dark" ? "dark" : ""}`;
    win.style.fontSize = `${config.fontSize || 14}px`;

    const header = document.createElement("div");
    header.className = "zenix-chat-header";
    header.style.background = `linear-gradient(135deg, ${config.primaryColor}, ${config.secondaryColor})`;

    const headerInfo = document.createElement("div");
    headerInfo.className = "zenix-header-info";
    const titleEl = document.createElement("div");
    titleEl.className = "zenix-header-title";
    titleEl.textContent = config.botName;
    const subEl = document.createElement("div");
    subEl.className = "zenix-header-sub";
    subEl.textContent = "Online • AI Assistant";
    headerInfo.appendChild(titleEl);
    headerInfo.appendChild(subEl);

    const headerActions = document.createElement("div");
    headerActions.className = "zenix-header-actions";

    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "zenix-icon-btn";
    clearBtn.textContent = s.clearChat;
    clearBtn.addEventListener("click", () => {
      domRefs.messagesEl.innerHTML = "";
      conversationHistory = [];
      resetOrderDraft();
      appendBotMessage(config.welcomeMessage);
    });

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "zenix-icon-btn";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => toggleWidget(false));

    headerActions.appendChild(clearBtn);
    headerActions.appendChild(closeBtn);
    header.appendChild(headerInfo);
    header.appendChild(headerActions);

    const messagesEl = document.createElement("div");
    messagesEl.className = "zenix-messages";

    const quickRepliesEl = document.createElement("div");
    quickRepliesEl.className = "zenix-quick-replies";

    const attachmentBar = document.createElement("div");
    attachmentBar.className = "zenix-attachment-bar";
    const attachmentLabel = document.createElement("span");
    const removeAttachmentBtn = document.createElement("button");
    removeAttachmentBtn.type = "button";
    removeAttachmentBtn.className = "zenix-tool-btn";
    removeAttachmentBtn.textContent = s.removeFile;
    removeAttachmentBtn.addEventListener("click", clearPendingAttachments);
    attachmentBar.appendChild(attachmentLabel);
    attachmentBar.appendChild(removeAttachmentBtn);

    const inputArea = document.createElement("div");
    inputArea.className = "zenix-input-area";

    const toolbar = document.createElement("div");
    toolbar.className = "zenix-toolbar";

    const orderBtn = document.createElement("button");
    orderBtn.type = "button";
    orderBtn.className = "zenix-tool-btn";
    orderBtn.textContent = `🛒 ${s.newOrderBtn}`;
    orderBtn.addEventListener("click", () => startOrderFlow(""));

    const proofBtn = document.createElement("button");
    proofBtn.type = "button";
    proofBtn.className = "zenix-tool-btn";
    proofBtn.textContent = `🧾 ${s.uploadProofBtn}`;

    const proofInput = document.createElement("input");
    proofInput.type = "file";
    proofInput.accept = "image/*,.pdf";
    proofInput.style.display = "none";

    proofBtn.addEventListener("click", () => proofInput.click());
    proofInput.addEventListener("change", handleProofFileSelect);

    const photoBtn = document.createElement("button");
    photoBtn.type = "button";
    photoBtn.className = "zenix-tool-btn";
    photoBtn.textContent = `🖼️ ${s.uploadImageBtn}`;

    const photoInput = document.createElement("input");
    photoInput.type = "file";
    photoInput.accept = "image/*";
    photoInput.style.display = "none";

    photoBtn.addEventListener("click", () => photoInput.click());
    photoInput.addEventListener("change", handlePhotoFileSelect);

    const voiceBtn = document.createElement("button");
    voiceBtn.type = "button";
    voiceBtn.className = "zenix-tool-btn";
    voiceBtn.textContent = `🎙️ ${s.voiceBtn}`;
    voiceBtn.addEventListener("click", toggleVoiceInput);

    toolbar.appendChild(orderBtn);
    if (config.allowFileUpload) {
      toolbar.appendChild(proofBtn);
      toolbar.appendChild(proofInput);
    }
    if (config.allowImage) {
      toolbar.appendChild(photoBtn);
      toolbar.appendChild(photoInput);
    }
    if (config.allowVoice) {
      toolbar.appendChild(voiceBtn);
    }

    const inputRow = document.createElement("div");
    inputRow.className = "zenix-input-row";

    const textInput = document.createElement("input");
    textInput.type = "text";
    textInput.className = "zenix-text-input";
    textInput.placeholder = s.placeholder;
    textInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleUserSubmit();
      }
    });

    const sendBtn = document.createElement("button");
    sendBtn.type = "button";
    sendBtn.className = "zenix-send-btn";
    sendBtn.style.background = config.primaryColor;
    sendBtn.textContent = s.send;
    sendBtn.addEventListener("click", handleUserSubmit);

    inputRow.appendChild(textInput);
    inputRow.appendChild(sendBtn);

    inputArea.appendChild(toolbar);
    inputArea.appendChild(inputRow);

    const poweredEl = document.createElement("div");
    poweredEl.className = "zenix-powered";
    poweredEl.style.display = config.showPoweredBy ? "block" : "none";
    poweredEl.textContent = "Powered by Zenix AI";

    win.appendChild(header);
    win.appendChild(messagesEl);
    win.appendChild(quickRepliesEl);
    win.appendChild(attachmentBar);
    win.appendChild(inputArea);
    win.appendChild(poweredEl);

    const launcherBtn = document.createElement("button");
    launcherBtn.type = "button";
    launcherBtn.className = "zenix-launcher-btn";
    launcherBtn.style.background = `linear-gradient(135deg, ${config.primaryColor}, ${config.secondaryColor})`;
    launcherBtn.setAttribute("aria-label", "Open Chat");
    launcherBtn.innerHTML = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>`;
    launcherBtn.addEventListener("click", () => toggleWidget());

    root.appendChild(win);
    root.appendChild(launcherBtn);
    (document.body || document.documentElement).appendChild(root);

    domRefs = {
      root,
      win,
      titleEl,
      subEl,
      messagesEl,
      quickRepliesEl,
      attachmentBar,
      attachmentLabel,
      textInput,
      sendBtn,
      voiceBtn,
      photoInput,
      proofInput,
      launcherBtn,
      poweredEl
    };

    renderQuickReplies();
    appendBotMessage(config.welcomeMessage);
  }

  function applyConfigToUi() {
    if (!domRefs.root) return;
    const s = getLangStrings();
    domRefs.root.className = `zenix-widget-root ${
      config.position === "bottom-left" ? "pos-bottom-left" : "pos-bottom-right"
    }`;
    domRefs.win.className = `zenix-chat-window ${isOpen ? "open" : ""} ${
      config.theme === "dark" ? "dark" : ""
    }`;
    domRefs.win.style.fontSize = `${config.fontSize || 14}px`;
    domRefs.titleEl.textContent = config.botName || "AI Assistant";
    domRefs.textInput.placeholder = s.placeholder;
    domRefs.sendBtn.textContent = s.send;
    domRefs.sendBtn.style.background = config.primaryColor;
    domRefs.launcherBtn.style.background = `linear-gradient(135deg, ${config.primaryColor}, ${config.secondaryColor})`;
    if (domRefs.poweredEl) {
      domRefs.poweredEl.style.display = config.showPoweredBy ? "block" : "none";
    }
    renderQuickReplies();
  }

  function renderQuickReplies() {
    if (!domRefs.quickRepliesEl) return;
    domRefs.quickRepliesEl.innerHTML = "";
    const list = Array.isArray(config.quickReplies) ? config.quickReplies : [];
    list.forEach((label) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "zenix-chip-btn";
      btn.textContent = label;
      btn.addEventListener("click", () => {
        if (isSending) return;
        processMessageText(label, null);
      });
      domRefs.quickRepliesEl.appendChild(btn);
    });
  }

  function toggleWidget(forceState) {
    if (!domRefs.win) return;
    isOpen = typeof forceState === "boolean" ? forceState : !isOpen;
    if (isOpen) {
      domRefs.win.classList.add("open");
      setTimeout(() => {
        try {
          domRefs.textInput.focus();
        } catch (_) {}
      }, 60);
    } else {
      domRefs.win.classList.remove("open");
    }
  }

  function appendMessage(role, text, imageDataUrl) {
    if (!domRefs.messagesEl) return null;
    const id = `msg_${++messageCounter}`;
    const el = document.createElement("div");
    el.id = id;
    el.className = `zenix-msg ${role === "user" ? "user" : "bot"}`;
    if (role === "user") {
      el.style.background = config.primaryColor;
    }
    if (text) {
      const p = document.createElement("div");
      p.innerHTML = formatRichText(text);
      el.appendChild(p);
    }
    if (imageDataUrl && /^data:image\//i.test(imageDataUrl)) {
      const img = document.createElement("img");
      img.src = imageDataUrl;
      img.alt = "Attachment";
      el.appendChild(img);
    }
    domRefs.messagesEl.appendChild(el);
    domRefs.messagesEl.scrollTop = domRefs.messagesEl.scrollHeight;
    return id;
  }

  function appendBotMessage(text, imageDataUrl) {
    return appendMessage("bot", text, imageDataUrl);
  }

  function appendUserMessage(text, imageDataUrl) {
    return appendMessage("user", text, imageDataUrl);
  }

  function showTypingIndicator() {
    const s = getLangStrings();
    return appendBotMessage(s.typing);
  }

  function removeMessageById(id) {
    if (!id) return;
    const el = document.getElementById(id);
    if (el && el.parentNode) {
      el.parentNode.removeChild(el);
    }
  }

  function clearPendingAttachments() {
    pendingImageBase64 = null;
    pendingImageName = null;
    pendingImageDataUrl = null;
    pendingPaymentProofBase64 = null;
    pendingPaymentProofName = null;
    pendingPaymentProofDataUrl = null;
    if (domRefs.photoInput) domRefs.photoInput.value = "";
    if (domRefs.proofInput) domRefs.proofInput.value = "";
    if (domRefs.attachmentBar) {
      domRefs.attachmentBar.classList.remove("active");
      domRefs.attachmentLabel.textContent = "";
    }
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  async function handlePhotoFileSelect(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const s = getLangStrings();
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const base64 = dataUrl.includes(",") ? dataUrl.split(",")[1] : dataUrl;
      pendingImageBase64 = base64;
      pendingImageName = file.name;
      pendingImageDataUrl = dataUrl;
      if (domRefs.attachmentBar) {
        domRefs.attachmentLabel.textContent = `🖼️ ${s.imageAttached}: ${file.name}`;
        domRefs.attachmentBar.classList.add("active");
      }
    } catch (_) {}
  }

  async function handleProofFileSelect(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const s = getLangStrings();
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const base64 = dataUrl.includes(",") ? dataUrl.split(",")[1] : dataUrl;
      pendingPaymentProofBase64 = base64;
      pendingPaymentProofName = file.name;
      pendingPaymentProofDataUrl = dataUrl;
      if (domRefs.attachmentBar) {
        domRefs.attachmentLabel.textContent = `🧾 ${s.proofAttached}: ${file.name}`;
        domRefs.attachmentBar.classList.add("active");
      }
      if (orderDraft.active && orderDraft.step === "payment") {
        await completeOrderFromDraft("Bank Transfer");
      }
    } catch (_) {}
  }

  function toggleVoiceInput() {
    const s = getLangStrings();
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) {
      appendBotMessage(s.voiceNotSupported);
      return;
    }

    if (isRecording && recognition) {
      try {
        recognition.stop();
      } catch (_) {}
      isRecording = false;
      if (domRefs.voiceBtn) domRefs.voiceBtn.classList.remove("recording");
      return;
    }

    recognition = new SpeechRec();
    const lang = (config.language || "si").toLowerCase();
    recognition.lang = lang.startsWith("en") ? "en-US" : lang.startsWith("ta") ? "ta-LK" : "si-LK";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    isRecording = true;
    if (domRefs.voiceBtn) domRefs.voiceBtn.classList.add("recording");
    if (domRefs.subEl) domRefs.subEl.textContent = s.voiceListening;

    recognition.onresult = (event) => {
      const transcript =
        event.results &&
        event.results[0] &&
        event.results[0][0] &&
        event.results[0][0].transcript
          ? event.results[0][0].transcript.trim()
          : "";
      if (transcript && domRefs.textInput) {
        domRefs.textInput.value = transcript;
        handleUserSubmit();
      }
    };

    recognition.onerror = () => {};
    recognition.onend = () => {
      isRecording = false;
      if (domRefs.voiceBtn) domRefs.voiceBtn.classList.remove("recording");
      if (domRefs.subEl) domRefs.subEl.textContent = "Online • AI Assistant";
    };

    try {
      recognition.start();
    } catch (_) {
      isRecording = false;
      if (domRefs.voiceBtn) domRefs.voiceBtn.classList.remove("recording");
    }
  }

  function resetOrderDraft() {
    orderDraft = {
      active: false,
      step: null,
      item: "",
      quantity: 1,
      unitPrice: 0,
      totalPrice: 0,
      customerName: "",
      phone: "",
      address: "",
      paymentMethod: "",
      paymentProofName: "",
      paymentProofUrl: "",
      paymentProofDataUrl: "",
      orderId: ""
    };
  }

  function startOrderFlow(preselectedItem) {
    const s = getLangStrings();
    resetOrderDraft();
    orderDraft.active = true;
    if (preselectedItem && String(preselectedItem).trim()) {
      orderDraft.item = String(preselectedItem).trim();
      orderDraft.unitPrice = findProductPrice(orderDraft.item);
      orderDraft.step = "quantity";
      appendBotMessage(s.orderAskQty(orderDraft.item, orderDraft.unitPrice));
    } else {
      orderDraft.step = "item";
      appendBotMessage(s.orderStarted);
    }
  }

  async function completeOrderFromDraft(chosenPaymentMethod) {
    const s = getLangStrings();
    const qty = Math.max(1, parseInt(orderDraft.quantity, 10) || 1);
    const unitPrice = parseFloat(orderDraft.unitPrice) || findProductPrice(orderDraft.item) || 0;
    const total = unitPrice > 0 ? unitPrice * qty : 0;
    const method = chosenPaymentMethod || orderDraft.paymentMethod || "COD";

    const created = await finalizeAndSaveOrderInstant({
      order_id: generateOrderId(),
      item: orderDraft.item || "Item",
      quantity: qty,
      unit_price: unitPrice,
      total: total,
      currency: config.currency || "LKR",
      customer_name: orderDraft.customerName || "",
      phone: orderDraft.phone || "",
      address: orderDraft.address || "",
      payment_method: method,
      payment_proof_name: pendingPaymentProofName || orderDraft.paymentProofName || "",
      payment_proof_url: orderDraft.paymentProofUrl || "",
      payment_proof_data_url:
        pendingPaymentProofDataUrl || orderDraft.paymentProofDataUrl || ""
    });

    resetOrderDraft();
    clearPendingAttachments();
    appendBotMessage(s.orderConfirmed(created));
    return created;
  }

  async function handleOrderStepInput(text) {
    const s = getLangStrings();
    const trimmed = String(text || "").trim();
    const lower = trimmed.toLowerCase();

    if (lower === "cancel" || lower.includes("cancel order") || trimmed.includes("අවලංගු")) {
      resetOrderDraft();
      appendBotMessage(s.orderCancelled);
      return true;
    }

    if (orderDraft.step === "item") {
      orderDraft.item = trimmed;
      orderDraft.unitPrice = findProductPrice(trimmed);
      orderDraft.step = "quantity";
      appendBotMessage(s.orderAskQty(orderDraft.item, orderDraft.unitPrice));
      return true;
    }

    if (orderDraft.step === "quantity") {
      const parsedQty = parseInt(trimmed.replace(/[^0-9]/g, ""), 10);
      const maxQty = config.maxOrderQty || 50;
      const qty = !isNaN(parsedQty) && parsedQty > 0 ? Math.min(parsedQty, maxQty) : 1;
      orderDraft.quantity = qty;
      orderDraft.totalPrice = (orderDraft.unitPrice || 0) * qty;
      orderDraft.step = "name";
      appendBotMessage(s.orderAskName);
      return true;
    }

    if (orderDraft.step === "name") {
      orderDraft.customerName = trimmed;
      orderDraft.step = "phone";
      appendBotMessage(s.orderAskPhone);
      return true;
    }

    if (orderDraft.step === "phone") {
      orderDraft.phone = trimmed;
      orderDraft.step = "address";
      appendBotMessage(s.orderAskAddress);
      return true;
    }

    if (orderDraft.step === "address") {
      orderDraft.address = trimmed;
      orderDraft.step = "payment";
      const totalDisplay =
        orderDraft.totalPrice > 0
          ? `${Number(orderDraft.totalPrice).toLocaleString()} ${config.currency || "LKR"}`
          : "To be confirmed";
      const summary = `📋 **Order Summary**\n• Item: ${orderDraft.item}\n• Qty: ${orderDraft.quantity}\n• Total: ${totalDisplay}\n• Name: ${orderDraft.customerName}\n• Phone: ${orderDraft.phone}\n• Address: ${orderDraft.address}`;
      appendBotMessage(s.orderAskPayment(summary));
      return true;
    }

    if (orderDraft.step === "payment") {
      let method = "COD";
      if (
        lower.includes("bank") ||
        lower.includes("transfer") ||
        lower.includes("slip") ||
        lower.includes("1")
      ) {
        method = "Bank Transfer";
      } else if (lower.includes("cod") || lower.includes("cash") || lower.includes("2")) {
        method = "Cash on Delivery";
      } else {
        method = trimmed || "COD";
      }
      orderDraft.paymentMethod = method;
      await completeOrderFromDraft(method);
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
      t.includes("මිලදී") ||
      t.includes("ගන්න ඕන")
    );
  }

  async function sendChatToServer(text, imageBase64, imageName) {
    const typingId = showTypingIndicator();
    try {
      const payload = {
        message: text,
        session_id: sessionId,
        site_id: SITE_ID,
        client_id: ACTIVE_CLIENT_ID || CLIENT_ID_FROM_SCRIPT || "",
        client_key: ACTIVE_CLIENT_KEY || CLIENT_KEY_FROM_SCRIPT || "",
        history: conversationHistory.slice(-12),
        image_base64: imageBase64 || null,
        image_name: imageName || null,
        config_context: {
          business_name: config.businessName,
          business_type: config.businessType,
          currency: config.currency,
          language: config.language,
          bank_details: config.bankDetails,
          contact_info: config.contactInfo,
          delivery_info: config.deliveryInfo,
          custom_instructions: config.customInstructions,
          products: config.products,
          faqs: config.faqs
        }
      };

      const res = await fetch(`${API_BASE.replace(/\/+$/, "")}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(ACTIVE_CLIENT_KEY ? { "X-Client-Key": ACTIVE_CLIENT_KEY } : {}),
          ...(ACTIVE_CLIENT_ID ? { "X-Client-Id": ACTIVE_CLIENT_ID } : {})
        },
        body: JSON.stringify(payload)
      });

      removeMessageById(typingId);

      if (!res.ok) {
        const s = getLangStrings();
        appendBotMessage(s.offlineMsg);
        return;
      }

      const data = await res.json();
      const replyText =
        data.reply ||
        data.response ||
        data.message ||
        (data.data && (data.data.reply || data.data.message)) ||
        "";

      if (data.order && typeof data.order === "object") {
        await finalizeAndSaveOrderInstant(data.order);
      }

      if (replyText) {
        appendBotMessage(replyText);
        conversationHistory.push({ role: "assistant", content: replyText });
      }
    } catch (_) {
      removeMessageById(typingId);
      const s = getLangStrings();
      appendBotMessage(s.offlineMsg);
    }
  }

  async function processMessageText(text, imgDataUrl) {
    if (isSending) return;
    const cleanText = String(text || "").trim();
    const imgB64 = pendingImageBase64;
    const imgName = pendingImageName;

    if (!cleanText && !imgB64) return;

    isSending = true;
    appendUserMessage(cleanText, imgDataUrl || pendingImageDataUrl || null);
    if (cleanText) {
      conversationHistory.push({ role: "user", content: cleanText });
    }
    clearPendingAttachments();

    try {
      if (orderDraft.active) {
        await handleOrderStepInput(cleanText);
        return;
      }

      if (config.orderFlowEnabled && looksLikeOrderIntent(cleanText)) {
        startOrderFlow("");
        return;
      }

      await sendChatToServer(cleanText, imgB64, imgName);
    } finally {
      isSending = false;
    }
  }

  function handleUserSubmit() {
    if (!domRefs.textInput || isSending) return;
    const val = domRefs.textInput.value;
    domRefs.textInput.value = "";
    processMessageText(val, pendingImageDataUrl);
  }

  async function init() {
    await verifyClientKeyAndLoadConfig();
    buildWidgetDom();
    applyConfigToUi();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();