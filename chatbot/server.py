import base64
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROOFS_DIR = DATA_DIR / "payment_proofs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROOFS_DIR.mkdir(parents=True, exist_ok=True)

USAGE_FILE = DATA_DIR / "usage.json"
CLIENTS_FILE = DATA_DIR / "clients.json"
ORDERS_FILE = DATA_DIR / "orders.json"
STOCK_DEDUCTION_LOG_FILE = DATA_DIR / "stock_deductions.json"

DEFAULT_FONT_STACK = (
    '"Google Sans", "Inter", system-ui, -apple-system, BlinkMacSystemFont, '
    '"Segoe UI", Roboto, sans-serif'
)

# Make.com scenario webhook URL එක (Google Sheet / scenario වෙත data යවන URL එක):
MAKE_WEBHOOK_URL = os.environ.get(
    "MAKE_WEBHOOK_URL",
    "",
)

# Google Apps Script / Stock webhook URL (අවශ්‍ය නම් env හෝ මෙතන යොදන්න):
APPS_SCRIPT_STOCK_URL = os.environ.get(
    "APPS_SCRIPT_STOCK_URL",
    "",
)

ADMIN_SECRET = os.environ.get("ZENIX_ADMIN_SECRET", "").strip()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-4-neon").strip()
OPENROUTER_CHAT_URL = os.environ.get(
    "OPENROUTER_CHAT_URL",
    "https://openrouter.ai/api/v1/chat/completions",
).strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _is_placeholder_url(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return True
    if "මෙතනට webhook url එක දාන්න" in u or "මෙන්න මෙතන" in u:
        return True
    if not (u.startswith("http://") or u.startswith("https://")):
        return True
    return False


def _verify_admin_if_configured(x_admin_secret: Optional[str]) -> None:
    if ADMIN_SECRET and (x_admin_secret or "").strip() != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid or missing admin secret")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if data is not None else default
    except Exception:
        return default


def _save_json(path: Path, payload: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _default_usage_store() -> Dict[str, Any]:
    return {
        "date": _today_key(),
        "messages_today": 0,
        "orders_today": 0,
        "revenue_today": 0.0,
        "recent_orders": [],
        "by_client": {},
    }


def _load_usage() -> Dict[str, Any]:
    store = _load_json(USAGE_FILE, _default_usage_store())
    if not isinstance(store, dict):
        store = _default_usage_store()
    today = _today_key()
    if store.get("date") != today:
        store["date"] = today
        store["messages_today"] = 0
        store["orders_today"] = 0
        store["revenue_today"] = 0.0
        store["recent_orders"] = []
        store["by_client"] = {}
        _save_json(USAGE_FILE, store)
    store.setdefault("messages_today", 0)
    store.setdefault("orders_today", 0)
    store.setdefault("revenue_today", 0.0)
    store.setdefault("recent_orders", [])
    store.setdefault("by_client", {})
    return store


def _load_clients() -> List[Dict[str, Any]]:
    data = _load_json(CLIENTS_FILE, [])
    return data if isinstance(data, list) else []


def _save_clients(clients: List[Dict[str, Any]]) -> None:
    _save_json(CLIENTS_FILE, clients)


def _load_orders() -> List[Dict[str, Any]]:
    data = _load_json(ORDERS_FILE, [])
    return data if isinstance(data, list) else []


def _save_orders(orders: List[Dict[str, Any]]) -> None:
    _save_json(ORDERS_FILE, orders[:5000])


def _load_stock_deduction_log() -> Dict[str, Any]:
    data = _load_json(STOCK_DEDUCTION_LOG_FILE, {"processed_keys": []})
    if not isinstance(data, dict):
        return {"processed_keys": []}
    if not isinstance(data.get("processed_keys"), list):
        data["processed_keys"] = []
    return data


def _save_stock_deduction_log(log_data: Dict[str, Any]) -> None:
    log_data["processed_keys"] = log_data.get("processed_keys", [])[-5000:]
    _save_json(STOCK_DEDUCTION_LOG_FILE, log_data)


def _find_client(
    client_key: Optional[str] = None,
    client_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clients = _load_clients()
    key_clean = (client_key or "").strip()
    id_clean = (client_id or "").strip()

    if key_clean:
        for c in clients:
            if str(c.get("client_key") or "").strip() == key_clean:
                return c
    if id_clean:
        for c in clients:
            if str(c.get("client_id") or "").strip() == id_clean:
                return c
    return None


def _build_default_widget_config(
    client: Optional[Dict[str, Any]] = None,
    client_id: str = "",
    client_key: str = "",
) -> Dict[str, Any]:
    c_id = (client.get("client_id") if client else "") or client_id or ""
    c_key = (client.get("client_key") if client else "") or client_key or ""
    b_name = (client.get("business_name") if client else "") or ""
    is_active = True
    auth_reason = ""
    if client is not None:
        status_val = str(client.get("status") or "ACTIVE").upper()
        if status_val not in ("ACTIVE", "APPROVED", "ENABLED"):
            is_active = False
            auth_reason = f"Client status is {status_val}"

    cfg = client.get("config", {}) if (client and isinstance(client.get("config"), dict)) else {}

    return {
        "client_id": c_id,
        "client_key": c_key,
        "business_name": b_name or cfg.get("business_name") or "Zenix Store",
        "bot_name": cfg.get("bot_name") or (f"{b_name} AI Assistant" if b_name else "Zenix AI Assistant"),
        "welcome_message": cfg.get("welcome_message")
        or "ආයුබෝවන්! මම ඔබේ AI සහායකයා. මට ඔබට කොහොමද උදව් කරන්න පුළුවන්?",
        "primary_color": cfg.get("primary_color") or "#2563eb",
        "secondary_color": cfg.get("secondary_color") or "#1e40af",
        "position": cfg.get("position") or "bottom-right",
        "theme": cfg.get("theme") or "light",
        "font_family": cfg.get("font_family") or DEFAULT_FONT_STACK,
        "font_size": int(cfg.get("font_size") or 14),
        "allow_voice": bool(cfg.get("allow_voice", True)),
        "allow_image": bool(cfg.get("allow_image", True)),
        "allow_file_upload": bool(cfg.get("allow_file_upload", True)),
        "show_powered_by": bool(cfg.get("show_powered_by", False)),
        "quick_replies": cfg.get("quick_replies")
        or ["මිල ගණන් දැනගන්න", "Order එකක් දාන්න", "Contact Info"],
        "currency": cfg.get("currency") or "LKR",
        "language": cfg.get("language") or "si",
        "business_type": cfg.get("business_type") or "general",
        "bank_details": cfg.get("bank_details") or "",
        "contact_info": cfg.get("contact_info") or "",
        "delivery_info": cfg.get("delivery_info") or "",
        "custom_instructions": cfg.get("custom_instructions") or "",
        "products": cfg.get("products") if isinstance(cfg.get("products"), list) else [],
        "faqs": cfg.get("faqs") if isinstance(cfg.get("faqs"), list) else [],
        "payment_rules": cfg.get("payment_rules") if isinstance(cfg.get("payment_rules"), dict) else {},
        "order_flow_enabled": bool(cfg.get("order_flow_enabled", True)),
        "max_order_qty": int(cfg.get("max_order_qty") or 50),
        "botpress_webhook_url": cfg.get("botpress_webhook_url")
        or ("" if _is_placeholder_url(MAKE_WEBHOOK_URL) else MAKE_WEBHOOK_URL),
        "auth_verified": is_active,
        "auth_status": "ACTIVE" if is_active else "INACTIVE",
        "auth_reason": auth_reason,
    }


def _save_payment_proof_file(
    order_id: str,
    proof_name: str,
    data_url: Optional[str] = None,
    raw_base64: Optional[str] = None,
) -> Dict[str, str]:
    safe_order = re.sub(r"[^A-Za-z0-9_-]", "_", (order_id or "ORD").strip())
    mime_type = ""
    b64_payload = ""

    if data_url and isinstance(data_url, str) and data_url.startswith("data:"):
        header_part, _, encoded_part = data_url.partition(",")
        b64_payload = encoded_part.strip()
        mime_match = re.match(
            r"^data:([A-Za-z0-9.+/-]+);base64$", header_part.strip(), re.IGNORECASE
        )
        if mime_match:
            mime_type = mime_match.group(1).lower()
    elif raw_base64 and isinstance(raw_base64, str):
        b64_payload = raw_base64.strip()

    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "application/pdf": ".pdf",
    }

    ext = ext_map.get(mime_type, "")
    if not ext and proof_name:
        orig_ext = Path(proof_name).suffix.lower()
        if orig_ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"):
            ext = ".jpg" if orig_ext == ".jpeg" else orig_ext
    if not ext:
        ext = ".png"

    filename = f"{safe_order}{ext}"
    if not b64_payload:
        return {
            "payment_proof_name": proof_name or filename,
            "payment_proof_url": "",
            "payment_proof_data_url": data_url or "",
        }

    try:
        binary_data = base64.b64decode(b64_payload, validate=False)
        file_path = PROOFS_DIR / filename
        with file_path.open("wb") as f:
            f.write(binary_data)
        return {
            "payment_proof_name": filename,
            "payment_proof_url": f"/api/payment-proofs/{urllib.parse.quote(filename)}",
            "payment_proof_data_url": data_url
            or f"data:{mime_type or 'image/png'};base64,{b64_payload}",
        }
    except Exception:
        return {
            "payment_proof_name": proof_name or filename,
            "payment_proof_url": "",
            "payment_proof_data_url": data_url or "",
        }


def _post_json_webhook(url: str, payload: Dict[str, Any], timeout: int = 8) -> bool:
    if _is_placeholder_url(url):
        return False
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _push_stock_deduction_once(
    order_id: str,
    item: str,
    quantity: int,
    client_id: str = "",
    site_id: str = "",
) -> Dict[str, Any]:
    clean_order = (order_id or "").strip()
    clean_item = (item or "").strip()
    qty = max(1, int(quantity or 1))
    dedup_key = (
        f"order:{clean_order}"
        if clean_order
        else f"manual:{clean_item.lower()}:{qty}:{int(time.time() // 5)}"
    )

    log_data = _load_stock_deduction_log()
    processed_set = set(log_data.get("processed_keys", []))

    if dedup_key in processed_set:
        return {
            "deducted": False,
            "already_processed": True,
            "dedup_key": dedup_key,
            "item": clean_item,
            "quantity": qty,
        }

    processed_set.add(dedup_key)
    log_data["processed_keys"] = list(processed_set)
    _save_stock_deduction_log(log_data)

    pushed_to_webhook = False
    if not _is_placeholder_url(APPS_SCRIPT_STOCK_URL):
        pushed_to_webhook = _post_json_webhook(
            APPS_SCRIPT_STOCK_URL,
            {
                "action": "deduct_stock",
                "order_id": clean_order,
                "item": clean_item,
                "quantity": qty,
                "client_id": client_id,
                "site_id": site_id,
                "timestamp": _utc_now_iso(),
            },
        )

    return {
        "deducted": True,
        "already_processed": False,
        "dedup_key": dedup_key,
        "item": clean_item,
        "quantity": qty,
        "pushed_to_webhook": pushed_to_webhook,
    }


def _record_order_usage_and_store(order_dict: Dict[str, Any]) -> Dict[str, Any]:
    usage = _load_usage()
    all_orders = _load_orders()

    order_id = str(order_dict.get("order_id") or "").strip()
    existing_idx = next(
        (i for i, o in enumerate(all_orders) if str(o.get("order_id") or "").strip() == order_id),
        -1,
    )

    is_new_order = existing_idx == -1
    if existing_idx >= 0:
        merged = dict(all_orders[existing_idx])
        merged.update(order_dict)
        all_orders[existing_idx] = merged
        final_order = merged
    else:
        all_orders.insert(0, order_dict)
        final_order = order_dict

    _save_orders(all_orders)

    if is_new_order:
        total_val = float(final_order.get("total") or 0.0)
        usage["orders_today"] = int(usage.get("orders_today") or 0) + 1
        usage["revenue_today"] = float(usage.get("revenue_today") or 0.0) + total_val

        recent = usage.get("recent_orders") or []
        summary_entry = {
            "order_id": final_order.get("order_id"),
            "client_id": final_order.get("client_id") or "",
            "item": final_order.get("item") or "",
            "quantity": int(final_order.get("quantity") or 1),
            "total": total_val,
            "currency": final_order.get("currency") or "LKR",
            "customer_name": final_order.get("customer_name") or "",
            "phone": final_order.get("phone") or "",
            "address": final_order.get("address") or "",
            "payment_method": final_order.get("payment_method") or "COD",
            "payment_status": final_order.get("payment_status") or "Pending",
            "payment_proof_name": final_order.get("payment_proof_name") or "",
            "payment_proof_url": final_order.get("payment_proof_url") or "",
            "status": final_order.get("status") or "Pending",
            "created_at": final_order.get("created_at") or _utc_now_iso(),
        }
        recent.insert(0, summary_entry)
        usage["recent_orders"] = recent[:100]

        c_id = str(final_order.get("client_id") or "default").strip() or "default"
        by_client = usage.get("by_client") or {}
        c_stats = by_client.get(
            c_id,
            {"messages_today": 0, "orders_today": 0, "revenue_today": 0.0},
        )
        c_stats["orders_today"] = int(c_stats.get("orders_today") or 0) + 1
        c_stats["revenue_today"] = float(c_stats.get("revenue_today") or 0.0) + total_val
        by_client[c_id] = c_stats
        usage["by_client"] = by_client

        _save_json(USAGE_FILE, usage)

    return final_order


def _resolve_openrouter_model(config_context: Dict[str, Any]) -> str:
    requested = (
        config_context.get("openrouter_model")
        or config_context.get("model")
        or ""
    )
    requested_str = str(requested).strip()
    if requested_str:
        return requested_str
    return OPENROUTER_MODEL or "google/gemini-4-neon"


def _generate_openrouter_reply(
    message: str,
    history: List[Dict[str, Any]],
    config_context: Dict[str, Any],
    image_base64: Optional[str] = None,
    image_name: Optional[str] = None,
) -> str:
    b_name = config_context.get("business_name") or "අපගේ ආයතනය"
    currency = config_context.get("currency") or "LKR"
    products = config_context.get("products") or []
    faqs = config_context.get("faqs") or []
    bank_details = config_context.get("bank_details") or ""
    contact_info = config_context.get("contact_info") or ""
    delivery_info = config_context.get("delivery_info") or ""
    custom_instructions = config_context.get("custom_instructions") or ""

    if not OPENROUTER_API_KEY:
        msg_l = (message or "").lower()
        if any(k in msg_l for k in ("price", "මිල", "ගණන්", "rate")):
            if products:
                lines = [
                    f"• **{p.get('name', 'Item')}**: {p.get('price', '')} {currency}"
                    for p in products[:15]
                ]
                return f"**{b_name}** — මිල ගණන්:\n" + "\n".join(lines)
            return (
                f"මිල ගණන් පිළිබඳ විස්තර සඳහා කරුණාකර අපව සම්බන්ධ කරගන්න. {contact_info}".strip()
            )

        if any(k in msg_l for k in ("bank", "account", "payment", "බැංකු", "ගෙවීම")):
            if bank_details:
                return f"**Bank / Payment Details:**\n{bank_details}"
            return "Payment ක්‍රම ලෙස Bank Transfer (Slip upload සමඟ) හෝ Cash on Delivery (COD) ලබා ගත හැක."

        if any(k in msg_l for k in ("contact", "phone", "number", "දුරකථන", "විස්තර")):
            return (
                contact_info
                or f"**{b_name}** වෙත සාදරයෙන් පිළිගනිමු. ඔබට අවශ්‍ය සේවාව හෝ item එක කුමක්ද?"
            )

        if delivery_info and any(k in msg_l for k in ("delivery", "shipping", "ඩිලිවරි")):
            return f"**Delivery Info:**\n{delivery_info}"

        return (
            f"ආයුබෝවන්! මම **{b_name}** AI සහායකයා. "
            f"ඔබට මිල ගණන් දැනගැනීමට, Order එකක් place කිරීමට හෝ විස්තර ලබා ගැනීමට මට උදව් කළ හැක."
        )

    system_prompt = (
        f"You are a helpful AI assistant for '{b_name}'. "
        f"Default currency: {currency}. Respond clearly in the user's language (Sinhala, English, or Tamil). "
        f"Bank details: {bank_details}\nContact info: {contact_info}\nDelivery info: {delivery_info}\n"
        f"Products: {json.dumps(products, ensure_ascii=False)}\n"
        f"FAQs: {json.dumps(faqs, ensure_ascii=False)}\n"
        f"Extra instructions: {custom_instructions}"
    )

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    for item in (history or [])[-8:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        txt = str(item.get("content") or "").strip()
        if txt:
            messages.append({"role": role, "content": txt})

    if image_base64:
        mime = "image/jpeg"
        if image_name and image_name.lower().endswith(".png"):
            mime = "image/png"
        elif image_name and image_name.lower().endswith(".webp"):
            mime = "image/webp"

        user_content: List[Dict[str, Any]] = []
        if message:
            user_content.append({"type": "text", "text": message})
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image_base64}"},
            }
        )
        messages.append({"role": "user", "content": user_content})
    elif message:
        messages.append({"role": "user", "content": message})

    model_to_use = _resolve_openrouter_model(config_context)
    req_body = {
        "model": model_to_use,
        "messages": messages,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "https://zenixai.online"),
        "X-Title": os.environ.get("OPENROUTER_APP_NAME", b_name),
    }

    try:
        req = urllib.request.Request(
            OPENROUTER_CHAT_URL,
            data=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            choices = parsed.get("choices") or []
            if choices:
                msg_obj = choices[0].get("message") or {}
                content = msg_obj.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    texts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("text")
                    ]
                    if texts:
                        return "\n".join(texts).strip()
    except Exception:
        pass

    return "දැනට AI පිළිතුර ලබා ගැනීමේ තාවකාලික ගැටලුවක් ඇත. කරුණාකර නැවත උත්සාහ කරන්න."


# --- Pydantic Models ---


class OrderPayload(BaseModel):
    order_id: Optional[str] = None
    client_id: Optional[str] = ""
    client_key: Optional[str] = ""
    site_id: Optional[str] = ""
    session_id: Optional[str] = ""
    item: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1)
    unit_price: Optional[float] = 0.0
    total: Optional[float] = 0.0
    currency: Optional[str] = "LKR"
    customer_name: Optional[str] = ""
    phone: Optional[str] = ""
    address: Optional[str] = ""
    payment_method: Optional[str] = "COD"
    payment_status: Optional[str] = ""
    payment_proof_name: Optional[str] = ""
    payment_proof_url: Optional[str] = ""
    payment_proof_data_url: Optional[str] = ""
    payment_proof_base64: Optional[str] = ""
    status: Optional[str] = "Pending"
    skip_stock_deduction: Optional[bool] = False
    stock_deducted_once: Optional[bool] = False
    created_at: Optional[str] = None


class StockDeductPayload(BaseModel):
    order_id: Optional[str] = ""
    item: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1)
    client_id: Optional[str] = ""
    site_id: Optional[str] = ""


class ChatPayload(BaseModel):
    message: Optional[str] = ""
    session_id: Optional[str] = ""
    site_id: Optional[str] = ""
    client_id: Optional[str] = ""
    client_key: Optional[str] = ""
    history: List[Dict[str, Any]] = Field(default_factory=list)
    image_base64: Optional[str] = None
    image_name: Optional[str] = None
    config_context: Dict[str, Any] = Field(default_factory=dict)


class ClientRegisterPayload(BaseModel):
    business_name: str = Field(..., min_length=1)
    client_id: Optional[str] = None
    client_key: Optional[str] = None
    status: str = "ACTIVE"
    config: Dict[str, Any] = Field(default_factory=dict)


class OrderStatusUpdatePayload(BaseModel):
    status: str = Field(..., min_length=1)
    payment_status: Optional[str] = None


# --- FastAPI App ---

app = FastAPI(title="Zenix AI Server", version="2.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {
        "ok": True,
        "status": "healthy",
        "timestamp": _utc_now_iso(),
        "make_webhook_configured": not _is_placeholder_url(MAKE_WEBHOOK_URL),
        "stock_webhook_configured": not _is_placeholder_url(APPS_SCRIPT_STOCK_URL),
    }


@app.get("/api/widget-config")
def get_widget_config(
    client_key: Optional[str] = Query(default=None),
    client_id: Optional[str] = Query(default=None),
    site_id: Optional[str] = Query(default=None),
    x_client_key: Optional[str] = Header(default=None),
    x_client_id: Optional[str] = Header(default=None),
):
    effective_key = (client_key or x_client_key or "").strip()
    effective_id = (client_id or x_client_id or "").strip()

    client = _find_client(client_key=effective_key, client_id=effective_id)
    cfg = _build_default_widget_config(
        client=client,
        client_id=effective_id,
        client_key=effective_key,
    )
    if site_id:
        cfg["site_id"] = site_id

    return {"ok": True, "config": cfg}


@app.post("/api/clients")
def register_or_update_client(
    payload: ClientRegisterPayload,
    x_admin_secret: Optional[str] = Header(default=None),
):
    _verify_admin_if_configured(x_admin_secret)

    clients = _load_clients()
    c_id = (payload.client_id or "").strip() or f"client_{secrets.token_hex(4)}"
    c_key = (payload.client_key or "").strip() or f"zk_{secrets.token_hex(12)}"

    merged_cfg = dict(payload.config or {})
    merged_cfg.setdefault("font_family", DEFAULT_FONT_STACK)
    merged_cfg.setdefault("font_size", 14)

    record = {
        "client_id": c_id,
        "client_key": c_key,
        "business_name": payload.business_name.strip(),
        "status": payload.status.strip().upper() or "ACTIVE",
        "config": merged_cfg,
        "updated_at": _utc_now_iso(),
    }

    updated = False
    for idx, item in enumerate(clients):
        if item.get("client_id") == c_id or item.get("client_key") == c_key:
            clients[idx] = {**item, **record}
            updated = True
            break

    if not updated:
        record["created_at"] = _utc_now_iso()
        clients.append(record)

    _save_clients(clients)
    return {"ok": True, "updated": updated, "client": record}


@app.get("/api/clients")
def list_clients(x_admin_secret: Optional[str] = Header(default=None)):
    _verify_admin_if_configured(x_admin_secret)
    clients = _load_clients()
    return {"ok": True, "clients": clients, "count": len(clients)}


@app.post("/api/deduct-stock")
def deduct_stock_endpoint(
    payload: StockDeductPayload,
    x_client_key: Optional[str] = Header(default=None),
    x_client_id: Optional[str] = Header(default=None),
):
    c_id = (payload.client_id or x_client_id or "").strip()
    c_key = (x_client_key or "").strip()
    client = _find_client(client_key=c_key, client_id=c_id)
    if client:
        c_id = str(client.get("client_id") or c_id).strip()

    result = _push_stock_deduction_once(
        order_id=payload.order_id or "",
        item=payload.item,
        quantity=payload.quantity,
        client_id=c_id,
        site_id=payload.site_id or "",
    )
    return {"ok": True, **result}


@app.post("/api/orders")
def create_or_sync_order(
    payload: OrderPayload,
    x_client_key: Optional[str] = Header(default=None),
    x_client_id: Optional[str] = Header(default=None),
):
    now_str = _utc_now_iso()
    order_id = (payload.order_id or "").strip() or (
        "ORD-"
        + datetime.now(timezone.utc).strftime("%y%m%d")
        + f"-{secrets.randbelow(9000) + 1000}"
    )

    qty = max(1, int(payload.quantity or 1))
    unit_price = float(payload.unit_price or 0.0)
    total = float(payload.total or 0.0)
    if total <= 0 and unit_price > 0:
        total = unit_price * qty

    proof_info = _save_payment_proof_file(
        order_id=order_id,
        proof_name=payload.payment_proof_name or "",
        data_url=payload.payment_proof_data_url,
        raw_base64=payload.payment_proof_base64,
    )

    method_str = (payload.payment_method or "COD").strip()
    is_bank = any(k in method_str.lower() for k in ("bank", "transfer", "slip"))
    payment_status = (payload.payment_status or "").strip()
    if not payment_status:
        if is_bank:
            payment_status = (
                "Proof Submitted (Pending Verification)"
                if (proof_info["payment_proof_name"] or proof_info["payment_proof_data_url"])
                else "Pending Bank Transfer"
            )
        else:
            payment_status = "Cash on Delivery"

    c_id = (payload.client_id or x_client_id or "").strip()
    c_key = (payload.client_key or x_client_key or "").strip()
    client = _find_client(client_key=c_key, client_id=c_id)
    if client:
        c_id = str(client.get("client_id") or c_id).strip()

    order_record = {
        "order_id": order_id,
        "client_id": c_id,
        "client_key": c_key,
        "site_id": payload.site_id or "",
        "session_id": payload.session_id or "",
        "item": payload.item.strip(),
        "quantity": qty,
        "unit_price": unit_price,
        "total": total,
        "currency": payload.currency or "LKR",
        "customer_name": (payload.customer_name or "").strip(),
        "phone": (payload.phone or "").strip(),
        "address": (payload.address or "").strip(),
        "payment_method": method_str,
        "payment_status": payment_status,
        "payment_proof_name": proof_info["payment_proof_name"],
        "payment_proof_url": proof_info["payment_proof_url"],
        "payment_proof_data_url": proof_info["payment_proof_data_url"],
        "status": (payload.status or "Pending").strip(),
        "created_at": payload.created_at or now_str,
        "updated_at": now_str,
    }

    saved_order = _record_order_usage_and_store(order_record)

    stock_result = {"deducted": False, "skipped_duplicate": True}
    if not payload.skip_stock_deduction and not payload.stock_deducted_once:
        stock_result = _push_stock_deduction_once(
            order_id=order_id,
            item=saved_order["item"],
            quantity=saved_order["quantity"],
            client_id=c_id,
            site_id=payload.site_id or "",
        )

    webhook_sent = False
    if not _is_placeholder_url(MAKE_WEBHOOK_URL):
        webhook_payload = {
            "event": "order_created",
            "order": {
                k: v for k, v in saved_order.items() if k != "payment_proof_data_url"
            },
            "has_payment_proof": bool(saved_order.get("payment_proof_name")),
            "skip_stock_deduction": True,
            "timestamp": now_str,
        }
        webhook_sent = _post_json_webhook(MAKE_WEBHOOK_URL, webhook_payload)

    return {
        "ok": True,
        "order": saved_order,
        "stock": stock_result,
        "webhook_sent": webhook_sent,
    }


@app.get("/api/orders")
def list_orders(
    client_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    x_admin_secret: Optional[str] = Header(default=None),
):
    _verify_admin_if_configured(x_admin_secret)
    orders = _load_orders()
    if client_id and client_id.strip():
        target = client_id.strip()
        orders = [o for o in orders if str(o.get("client_id") or "").strip() == target]
    return {"ok": True, "orders": orders[:limit], "total_count": len(orders)}


@app.patch("/api/orders/{order_id}/status")
def update_order_status(
    order_id: str,
    payload: OrderStatusUpdatePayload,
    x_admin_secret: Optional[str] = Header(default=None),
):
    _verify_admin_if_configured(x_admin_secret)
    orders = _load_orders()
    target_id = order_id.strip()
    found = None
    for idx, o in enumerate(orders):
        if str(o.get("order_id") or "").strip() == target_id:
            orders[idx]["status"] = payload.status.strip()
            if payload.payment_status:
                orders[idx]["payment_status"] = payload.payment_status.strip()
            orders[idx]["updated_at"] = _utc_now_iso()
            found = orders[idx]
            break

    if not found:
        raise HTTPException(status_code=404, detail="Order not found")

    _save_orders(orders)
    return {"ok": True, "order": found}


@app.get("/api/payment-proofs/{filename}")
def get_payment_proof(
    filename: str,
    x_admin_secret: Optional[str] = Header(default=None),
):
    _verify_admin_if_configured(x_admin_secret)
    clean_name = Path(urllib.parse.unquote(filename)).name
    file_path = (PROOFS_DIR / clean_name).resolve()
    if PROOFS_DIR.resolve() not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Payment proof not found")

    ext = file_path.suffix.lower()
    media_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
    }
    media_type = media_map.get(ext, "application/octet-stream")
    with file_path.open("rb") as f:
        content = f.read()

    return JSONResponse(
        content={
            "ok": True,
            "filename": clean_name,
            "media_type": media_type,
            "data_url": f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}",
        }
    )


@app.post("/api/chat")
def chat_endpoint(
    payload: ChatPayload,
    x_client_key: Optional[str] = Header(default=None),
    x_client_id: Optional[str] = Header(default=None),
):
    msg_text = (payload.message or "").strip()
    if not msg_text and not payload.image_base64:
        raise HTTPException(status_code=400, detail="Message or image is required")

    raw_id = (payload.client_id or x_client_id or "").strip()
    raw_key = (payload.client_key or x_client_key or "").strip()

    client = _find_client(client_key=raw_key, client_id=raw_id)
    c_id = (
        (str(client.get("client_id") or "").strip() if client else "")
        or raw_id
        or "default"
    )
    c_key = (
        (str(client.get("client_key") or "").strip() if client else "")
        or raw_key
    )

    base_cfg = _build_default_widget_config(client=client, client_id=c_id, client_key=c_key)
    ctx = dict(base_cfg)
    if isinstance(payload.config_context, dict):
        ctx.update({k: v for k, v in payload.config_context.items() if v is not None})

    usage = _load_usage()
    usage["messages_today"] = int(usage.get("messages_today") or 0) + 1
    by_client = usage.get("by_client") or {}
    c_stats = by_client.get(
        c_id,
        {"messages_today": 0, "orders_today": 0, "revenue_today": 0.0},
    )
    c_stats["messages_today"] = int(c_stats.get("messages_today") or 0) + 1
    by_client[c_id] = c_stats
    usage["by_client"] = by_client
    _save_json(USAGE_FILE, usage)

    reply = _generate_openrouter_reply(
        message=msg_text,
        history=payload.history,
        config_context=ctx,
        image_base64=payload.image_base64,
        image_name=payload.image_name,
    )

    return {
        "ok": True,
        "reply": reply,
        "session_id": payload.session_id or "",
        "client_id": c_id,
        "timestamp": _utc_now_iso(),
    }


@app.get("/api/usage")
def get_usage(
    client_id: Optional[str] = Query(default=None),
    x_admin_secret: Optional[str] = Header(default=None),
):
    _verify_admin_if_configured(x_admin_secret)
    usage = _load_usage()
    if client_id and client_id.strip():
        c_id = client_id.strip()
        c_stats = (usage.get("by_client") or {}).get(
            c_id,
            {"messages_today": 0, "orders_today": 0, "revenue_today": 0.0},
        )
        all_orders = [o for o in _load_orders() if str(o.get("client_id") or "").strip() == c_id]
        return {
            "ok": True,
            "date": usage.get("date"),
            "client_id": c_id,
            "messages_today": int(c_stats.get("messages_today") or 0),
            "orders_today": int(c_stats.get("orders_today") or 0),
            "revenue_today": float(c_stats.get("revenue_today") or 0.0),
            "recent_orders": all_orders[:20],
        }

    return {
        "ok": True,
        "date": usage.get("date"),
        "messages_today": int(usage.get("messages_today") or 0),
        "orders_today": int(usage.get("orders_today") or 0),
        "revenue_today": float(usage.get("revenue_today") or 0.0),
        "recent_orders": (usage.get("recent_orders") or [])[:50],
        "by_client": usage.get("by_client") or {},
    }


if __name__ == "__main__":
    import uvicorn

    module_name = f"{Path(__file__).stem}:app"
    uvicorn.run(module_name, host="0.0.0.0", port=8000, reload=True)