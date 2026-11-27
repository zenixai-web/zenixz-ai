import base64
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import random
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import firebase_admin
from firebase_admin import credentials, firestore

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CLIENTS_FILE = DATA_DIR / "clients.json"
ORDERS_FILE = DATA_DIR / "orders.json"
SYSTEM_STATE_FILE = DATA_DIR / "system_state.json"

# Secrets/API Keys are read strictly from environment variables (never hardcoded).
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_API_URL = os.environ.get("AI_API_URL", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "").strip()

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_NAME = os.environ.get("SENDER_NAME", "Zenix AI").strip()
ALLOW_DEV_CONSOLE_OTP = os.environ.get("ALLOW_DEV_CONSOLE_OTP", "0").strip() == "1"
SESSION_TOKEN_SECRET = os.environ.get("SESSION_TOKEN_SECRET", "").strip() or secrets.token_hex(32)
ADMIN_SECRET = os.environ.get("ZENIX_ADMIN_SECRET", "").strip()

OTP_STORE: Dict[str, Dict[str, Any]] = {}
OTP_TTL_SECONDS = 300
IP_OTP_HISTORY: Dict[str, List[float]] = defaultdict(list)
SEEN_CONV_SESSIONS = set()

_FIRESTORE_LAST_ERROR = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _init_firestore():
    global _FIRESTORE_LAST_ERROR
    if firebase_admin._apps:
        return firestore.client()

    raw_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    key_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()

    try:
        if raw_json:
            cred_dict = json.loads(raw_json)
            if isinstance(cred_dict, dict) and "private_key" in cred_dict:
                cred_dict["private_key"] = str(cred_dict["private_key"]).replace("\\n", "\n")
            cred = credentials.Certificate(cred_dict)
        elif key_path and os.path.exists(key_path):
            cred = credentials.Certificate(key_path)
        else:
            cred = credentials.ApplicationDefault()

        firebase_admin.initialize_app(cred)
        _FIRESTORE_LAST_ERROR = ""
        return firestore.client()
    except Exception as exc:
        _FIRESTORE_LAST_ERROR = str(exc)
        raise


def _get_db():
    global _FIRESTORE_LAST_ERROR
    try:
        return _init_firestore()
    except Exception as exc:
        _FIRESTORE_LAST_ERROR = str(exc)
        return None


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000
    ).hex()
    return f"{salt}${digest}"


def _issue_client_token(client_id: str, email: str) -> str:
    exp_ts = int(time.time()) + 86400 * 7
    payload_obj = {
        "client_id": client_id.upper(),
        "email": (email or "").strip().lower(),
        "exp": exp_ts,
    }
    payload_b64 = (
        base64.urlsafe_b64encode(
            json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    sig = hmac.new(
        SESSION_TOKEN_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_client_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    payload_b64, sig = token.split(".", 1)
    expected_sig = hmac.new(
        SESSION_TOKEN_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        data = json.loads(
            base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        )
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        if not data.get("client_id"):
            return None
        return data
    except Exception:
        return None


def _get_authed_client_id(
    authorization: Optional[str], x_client_token: Optional[str]
) -> Optional[str]:
    token = ""
    auth_h = (authorization or "").strip()
    if auth_h.lower().startswith("bearer "):
        token = auth_h[7:].strip()
    if not token:
        token = (x_client_token or "").strip()
    verified = _verify_client_token(token)
    if verified:
        return str(verified.get("client_id") or "").strip().upper()
    return None


def _require_admin(x_admin_secret: Optional[str]) -> None:
    if not ADMIN_SECRET or (x_admin_secret or "").strip() != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin authorization required")


def _get_maintenance_state() -> Dict[str, Any]:
    st = _load_json(
        SYSTEM_STATE_FILE,
        {
            "maintenance_mode": False,
            "maintenance_message": (
                "දැනට අපේ server එක තාවකාලිකව නවත්වලා තියෙන්නේ. "
                "මෙතන ඉදලා update එකක් දෙනකන් orders එන්නේ නැහැ."
            ),
        },
    )
    return st if isinstance(st, dict) else {"maintenance_mode": False, "maintenance_message": ""}


def _save_maintenance_state(st: Dict[str, Any]) -> None:
    st["updated_at"] = _utc_now_iso()
    _save_json(SYSTEM_STATE_FILE, st)
    db = _get_db()
    if db:
        try:
            db.collection("system").document("maintenance").set(st, merge=True)
        except Exception:
            pass


def _fs_get_client(db, cid: str) -> Optional[Dict[str, Any]]:
    if not db or not cid:
        return None
    try:
        snap = db.collection("clients").document(cid.upper()).get()
        if snap.exists:
            data = snap.to_dict() or {}
            data["client_id"] = cid.upper()
            return data
    except Exception:
        pass
    return None


def _fs_find_by_email(db, email_lower: str) -> Optional[str]:
    if not db or not email_lower:
        return None
    try:
        docs = db.collection("clients").where("email", "==", email_lower).limit(1).stream()
        for d in docs:
            return str(d.id).upper()
    except Exception:
        pass
    return None


def _fs_find_by_username(db, username_val: str) -> Optional[Dict[str, Any]]:
    if not db or not username_val:
        return None
    u_clean = username_val.strip()
    try:
        for field_name in ("username", "generated_username"):
            docs = db.collection("clients").where(field_name, "==", u_clean).limit(1).stream()
            for d in docs:
                data = d.to_dict() or {}
                data["client_id"] = str(d.id).upper()
                return data
    except Exception:
        pass
    return None


def _get_client_quota(entry: Dict[str, Any]) -> Dict[str, int]:
    def _safe_int(val: Any, default: int = 0) -> int:
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return default

    return {
        "conversations_limit": _safe_int(entry.get("conversations_limit"), 0),
        "conversations_used": _safe_int(entry.get("conversations_used"), 0),
        "ai_credits": _safe_int(entry.get("ai_credits"), 0),
        "ai_credits_used": _safe_int(entry.get("ai_credits_used"), 0),
    }


def _is_client_active(entry: Dict[str, Any]) -> tuple[bool, str, bool]:
    if not entry:
        return False, "inactive", False
    exp_val = entry.get("expires_at")
    if exp_val:
        try:
            dt = datetime.fromisoformat(str(exp_val).strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if _utc_now() > dt:
                changed = entry.get("plan_active") is True
                entry["plan_active"] = False
                return False, "expired", changed
        except ValueError:
            pass
    if entry.get("plan_active") is not True:
        return False, "inactive", False
    return True, "active", False


def _save_client_record(
    cid: str, entry: Dict[str, Any], password_hash: Optional[str] = None
) -> None:
    clean_cid = cid.upper()
    quota = _get_client_quota(entry)
    embed_key = str(entry.get("client_key") or "").strip() or f"zk_{secrets.token_hex(12)}"

    payload: Dict[str, Any] = {
        "client_id": clean_cid,
        "client_key": embed_key,
        "username": entry.get("username") or entry.get("generated_username") or clean_cid,
        "generated_username": entry.get("generated_username") or entry.get("username") or clean_cid,
        "store_name": entry.get("store_name") or f"{clean_cid} Store",
        "email": (entry.get("email") or "").strip().lower(),
        "plan": entry.get("plan"),
        "plan_active": bool(entry.get("plan_active", False)),
        "expires_at": entry.get("expires_at"),
        "knowledge_base": entry.get("knowledge_base") or "",
        "output_webhook_url": (entry.get("output_webhook_url") or "").strip(),
        "stock_json": entry.get("stock_json") if isinstance(entry.get("stock_json"), dict) else {},
        "conversations_limit": quota["conversations_limit"],
        "conversations_used": quota["conversations_used"],
        "ai_credits": quota["ai_credits"],
        "ai_credits_used": quota["ai_credits_used"],
        "updated_at": _utc_now_iso(),
    }
    if password_hash:
        payload["password_hash"] = password_hash

    local_map = _load_json(CLIENTS_FILE, {})
    if isinstance(local_map, dict):
        local_map[clean_cid] = {**local_map.get(clean_cid, {}), **payload}
        _save_json(CLIENTS_FILE, local_map)

    db = _get_db()
    if db:
        try:
            db.collection("clients").document(clean_cid).set(payload, merge=True)
        except Exception:
            pass


def _build_embed_html(cid: str, store_name: str, client_key: str, base_url: str = "") -> str:
    clean_base = (base_url or os.environ.get("PUBLIC_API_BASE", "http://127.0.0.1:8000")).rstrip("/")
    safe_store = store_name.replace('"', "")
    return (
        f'<script\n'
        f'  src="{clean_base}/../chatbot/app.js"\n'
        f'  data-api-base="{clean_base}"\n'
        f'  data-client-id="{cid}"\n'
        f'  data-client-key="{client_key}"\n'
        f'  data-client-name="{safe_store}"\n'
        f'  defer\n'
        f'></script>'
    )


def _resolve_client(
    db, client_id: str = "", email: str = "", username: str = ""
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    cid = (client_id or "").strip().upper()
    em = (email or "").strip().lower()
    usr = (username or "").strip()

    if cid:
        fs_rec = _fs_get_client(db, cid)
        if fs_rec:
            return cid, fs_rec
        local_map = _load_json(CLIENTS_FILE, {})
        if isinstance(local_map, dict) and cid in local_map:
            rec = dict(local_map[cid])
            rec["client_id"] = cid
            return cid, rec

    if em:
        owner_cid = _fs_find_by_email(db, em)
        if owner_cid:
            fs_rec = _fs_get_client(db, owner_cid)
            if fs_rec:
                return owner_cid, fs_rec
        local_map = _load_json(CLIENTS_FILE, {})
        if isinstance(local_map, dict):
            for k, v in local_map.items():
                if str((v or {}).get("email") or "").strip().lower() == em:
                    rec = dict(v)
                    rec["client_id"] = str(k).upper()
                    return str(k).upper(), rec

    if usr:
        fs_u = _fs_find_by_username(db, usr)
        if fs_u:
            return str(fs_u["client_id"]).upper(), fs_u

    return None, None


def _send_otp_email(to_email: str, otp_code: str, client_id: str) -> None:
    if not BREVO_API_KEY or not SENDER_EMAIL:
        raise RuntimeError("Email OTP service is not configured")
    payload = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": "Zenix AI — Login Verification OTP",
        "htmlContent": (
            f"<div style='font-family:Arial,sans-serif;line-height:1.6'>"
            f"<h3>Zenix AI Verification</h3>"
            f"<p>Client ID: <strong>{client_id}</strong></p>"
            f"<p style='font-size:22px;font-weight:bold;letter-spacing:4px'>{otp_code}</p>"
            f"<p>Expires in 5 minutes.</p></div>"
        ),
    }
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status not in (200, 201, 202):
            raise RuntimeError(f"Brevo HTTP {resp.status}")


def _post_webhook(url: str, payload: Dict[str, Any], timeout: int = 10) -> bool:
    u = (url or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    try:
        req = urllib.request.Request(
            u,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _save_client_order(cid: str, order_obj: Dict[str, Any]) -> Dict[str, Any]:
    clean_cid = cid.upper()
    record = dict(order_obj)
    record["client_id"] = clean_cid
    record.setdefault("order_id", f"ORD-{int(time.time())}-{secrets.randbelow(900) + 100}")
    record.setdefault("created_at", _utc_now_iso())

    orders = _load_json(ORDERS_FILE, [])
    if not isinstance(orders, list):
        orders = []
    oid = str(record["order_id"]).strip()
    orders = [o for o in orders if str(o.get("order_id") or "").strip() != oid]
    orders.insert(0, record)
    _save_json(ORDERS_FILE, orders[:5000])

    db = _get_db()
    if db:
        try:
            db.collection("orders").document(oid).set(record, merge=True)
        except Exception:
            pass

    return record


def _deduct_stock_if_present(entry: Dict[str, Any], cid: str, item_name: str, qty: int) -> None:
    stock_map = entry.get("stock_json")
    if not isinstance(stock_map, dict) or not item_name:
        return
    for k in list(stock_map.keys()):
        if k.strip().lower() == item_name.strip().lower():
            try:
                cur = int(stock_map[k])
                stock_map[k] = max(0, cur - max(1, int(qty or 1)))
                entry["stock_json"] = stock_map
                _save_client_record(cid, entry)
            except (TypeError, ValueError):
                pass
            break


def _call_ai_with_credit_check(
    message: str,
    history: List[Dict[str, Any]],
    entry: Dict[str, Any],
    cid: str,
) -> tuple[str, bool]:
    quota = _get_client_quota(entry)
    remaining_credits = quota["ai_credits"]
    kb_instruction = str(entry.get("knowledge_base") or "").strip()
    store_name = entry.get("store_name") or cid

    if remaining_credits <= 0:
        fallback = (
            f"AI credits අවසන් වී ඇත. "
            + (f"({store_name} Knowledge Base: {kb_instruction})" if kb_instruction else "")
        ).strip()
        return fallback, False

    if not AI_API_KEY or not AI_API_URL or not AI_MODEL:
        reply = (
            f"{store_name}: {kb_instruction}"
            if kb_instruction
            else f"{store_name} AI assistant ready."
        )
        entry["ai_credits"] = max(0, remaining_credits - 1)
        entry["ai_credits_used"] = quota["ai_credits_used"] + 1
        _save_client_record(cid, entry)
        return reply, True

    system_prompt = (
        f"You are the dedicated AI assistant for '{store_name}'. "
        f"Follow ONLY this client's instructions and knowledge base:\n"
        f"{kb_instruction or 'No extra instructions provided.'}"
    )
    msgs: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for h in (history or [])[-6:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        txt = str(h.get("content") or "").strip()
        if txt:
            msgs.append({"role": role, "content": txt})
    msgs.append({"role": "user", "content": message})

    req = urllib.request.Request(
        AI_API_URL,
        data=json.dumps({"model": AI_MODEL, "messages": msgs}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            choices = parsed.get("choices") or []
            if choices:
                content = ((choices[0].get("message") or {}).get("content") or "").strip()
                if content:
                    entry["ai_credits"] = max(0, remaining_credits - 1)
                    entry["ai_credits_used"] = quota["ai_credits_used"] + 1
                    _save_client_record(cid, entry)
                    return content, True
    except Exception:
        pass

    return "AI service temporarily unavailable.", False


# --- Request Models ---


class MaintenanceUpdate(BaseModel):
    maintenance_mode: bool
    maintenance_message: Optional[str] = None


class ClientLimitUpdate(BaseModel):
    client_id: str = Field(..., min_length=1)
    plan_active: Optional[bool] = None
    plan: Optional[str] = None
    expires_at: Optional[str] = None
    conversations_limit: Optional[int] = None
    conversations_used: Optional[int] = None
    ai_credits: Optional[int] = None


class PlanActivateIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    plan: str = Field(..., min_length=1)
    conversations_limit: int = Field(default=100, ge=0)
    ai_credits: int = Field(default=100, ge=0)
    expires_at: Optional[str] = None
    payment_reference: Optional[str] = None


class KnowledgeSave(BaseModel):
    client_id: str = Field(..., min_length=1)
    knowledge_base: Optional[str] = ""
    output_webhook_url: Optional[str] = ""
    stock_json: Any = Field(default_factory=dict)


class ChatIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    session_id: Optional[str] = ""
    message: Optional[str] = ""
    history: List[Dict[str, Any]] = Field(default_factory=list)
    order: Optional[Dict[str, Any]] = None


class OtpSendIn(BaseModel):
    email: Optional[str] = ""
    client_id: Optional[str] = ""
    username: Optional[str] = ""
    password: Optional[str] = ""
    is_new_account: bool = False


class OtpVerifyIn(BaseModel):
    email: Optional[str] = ""
    client_id: Optional[str] = ""
    username: Optional[str] = ""
    otp: str = Field(..., min_length=1)
    store_name: Optional[str] = ""
    password: Optional[str] = ""
    is_new_account: bool = False


# --- App ---

app = FastAPI(title="Zenix AI Client Server", version="4.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    maint = _get_maintenance_state()
    return {
        "ok": True,
        "maintenance_mode": bool(maint.get("maintenance_mode")),
        "timestamp": _utc_now_iso(),
    }


@app.get("/api/maintenance")
def get_maintenance():
    return {"ok": True, **_get_maintenance_state()}


@app.post("/api/admin/maintenance")
def admin_set_maintenance(
    payload: MaintenanceUpdate, x_admin_secret: Optional[str] = Header(default=None)
):
    _require_admin(x_admin_secret)
    st = _get_maintenance_state()
    st["maintenance_mode"] = bool(payload.maintenance_mode)
    if payload.maintenance_message is not None:
        st["maintenance_message"] = payload.maintenance_message.strip()
    _save_maintenance_state(st)
    return {"ok": True, **st}


@app.post("/api/admin/client/update")
def admin_update_client(
    payload: ClientLimitUpdate, x_admin_secret: Optional[str] = Header(default=None)
):
    _require_admin(x_admin_secret)
    cid = payload.client_id.strip().upper()
    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=cid)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Client not found")

    entry = dict(rec)
    if payload.plan_active is not None:
        entry["plan_active"] = bool(payload.plan_active)
    if payload.plan is not None:
        entry["plan"] = payload.plan.strip()
    if payload.expires_at is not None:
        entry["expires_at"] = payload.expires_at
    if payload.conversations_limit is not None:
        entry["conversations_limit"] = max(0, int(payload.conversations_limit))
    if payload.conversations_used is not None:
        entry["conversations_used"] = max(0, int(payload.conversations_used))
    if payload.ai_credits is not None:
        entry["ai_credits"] = max(0, int(payload.ai_credits))

    _save_client_record(resolved_cid, entry)
    return {"ok": True, "client_id": resolved_cid, "client": entry}


@app.post("/api/client/plan/activate")
def activate_client_plan(
    payload: PlanActivateIn,
    authorization: Optional[str] = Header(default=None),
    x_client_token: Optional[str] = Header(default=None),
    x_admin_secret: Optional[str] = Header(default=None),
):
    target_cid = payload.client_id.strip().upper()
    is_admin = bool(ADMIN_SECRET and (x_admin_secret or "").strip() == ADMIN_SECRET)
    authed_cid = _get_authed_client_id(authorization, x_client_token)

    if not is_admin and (not authed_cid or authed_cid != target_cid):
        raise HTTPException(status_code=403, detail="Unauthorized client access")

    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=target_cid)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Client not found")

    entry = dict(rec)
    entry["plan"] = payload.plan.strip()
    entry["plan_active"] = True
    entry["conversations_limit"] = max(0, int(payload.conversations_limit))
    entry["ai_credits"] = max(0, int(payload.ai_credits))
    if payload.expires_at is not None:
        entry["expires_at"] = payload.expires_at
    if payload.payment_reference:
        entry["last_payment_reference"] = payload.payment_reference.strip()

    _save_client_record(resolved_cid, entry)
    store_name = entry.get("store_name") or resolved_cid
    embed_key = str(entry.get("client_key") or "")

    return {
        "ok": True,
        "client_id": resolved_cid,
        "plan_active": True,
        "plan": entry["plan"],
        "embed_html": _build_embed_html(resolved_cid, store_name, embed_key),
    }


@app.get("/api/client/embed")
def get_client_embed(
    client_id: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    x_client_token: Optional[str] = Header(default=None),
):
    authed_cid = _get_authed_client_id(authorization, x_client_token)
    target_cid = (client_id or authed_cid or "").strip().upper()
    if not authed_cid or authed_cid != target_cid:
        raise HTTPException(status_code=403, detail="Unauthorized client access")

    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=target_cid)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Client not found")

    active, reason, _ = _is_client_active(rec)
    if not active:
        return {
            "ok": False,
            "allowed": False,
            "plan_active": False,
            "reason": reason,
            "embed_html": "",
        }

    store_name = rec.get("store_name") or resolved_cid
    embed_key = str(rec.get("client_key") or "")
    return {
        "ok": True,
        "allowed": True,
        "plan_active": True,
        "client_id": resolved_cid,
        "embed_html": _build_embed_html(resolved_cid, store_name, embed_key),
    }


@app.post("/api/otp/send")
def send_otp(payload: OtpSendIn):
    em = (payload.email or "").strip().lower()
    cid = (payload.client_id or "").strip().upper()
    if not em or "@" not in em:
        raise HTTPException(status_code=400, detail="Valid email required")

    now = time.time()
    recent = [t for t in IP_OTP_HISTORY[em] if now - t < 600]
    IP_OTP_HISTORY[em] = recent
    if len(recent) >= 10:
        raise HTTPException(status_code=429, detail="Too many OTP requests")
    recent.append(now)

    otp_code = f"{random.randint(100000, 999999)}"
    key = f"{cid or 'NEW'}:{em}"
    OTP_STORE[key] = {
        "otp": otp_code,
        "email": em,
        "client_id": cid,
        "expires_at": now + OTP_TTL_SECONDS,
    }

    try:
        _send_otp_email(em, otp_code, cid or "NEW-CLIENT")
        return {"ok": True, "message": "OTP sent"}
    except Exception as exc:
        if ALLOW_DEV_CONSOLE_OTP:
            print(f"[DEV OTP] {em}: {otp_code}", flush=True)
            return {"ok": True, "message": "OTP printed in console (dev mode)"}
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/otp/verify")
def verify_otp(payload: OtpVerifyIn):
    em = (payload.email or "").strip().lower()
    cid = (payload.client_id or "").strip().upper()
    key = f"{cid or 'NEW'}:{em}"
    rec = OTP_STORE.get(key)
    if (
        not rec
        or time.time() > float(rec.get("expires_at", 0))
        or str(rec.get("otp")) != payload.otp.strip()
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired OTP")
    OTP_STORE.pop(key, None)

    db = _get_db()
    resolved_cid, existing = _resolve_client(
        db, client_id=cid, email=em, username=payload.username or ""
    )
    if not resolved_cid:
        resolved_cid = f"CLT-{random.randint(1000, 9999)}"

    entry = dict(existing or {})
    entry["client_id"] = resolved_cid
    entry["email"] = em
    if payload.store_name:
        entry["store_name"] = payload.store_name.strip()
    if payload.username:
        entry["username"] = payload.username.strip()
    if payload.is_new_account and not existing:
        entry.setdefault("plan_active", False)
        entry.setdefault("conversations_limit", 0)
        entry.setdefault("conversations_used", 0)
        entry.setdefault("ai_credits", 0)
        entry.setdefault("ai_credits_used", 0)

    pw_hash = _hash_password(payload.password) if payload.password else None
    _save_client_record(resolved_cid, entry, password_hash=pw_hash)

    token = _issue_client_token(resolved_cid, em)
    return {
        "ok": True,
        "verified": True,
        "client_id": resolved_cid,
        "email": em,
        "token": token,
        "plan_active": bool(entry.get("plan_active", False)),
    }


@app.post("/api/client/knowledge")
def save_knowledge(
    payload: KnowledgeSave,
    authorization: Optional[str] = Header(default=None),
    x_client_token: Optional[str] = Header(default=None),
):
    authed_cid = _get_authed_client_id(authorization, x_client_token)
    target_cid = payload.client_id.strip().upper()
    if not authed_cid or authed_cid != target_cid:
        raise HTTPException(status_code=403, detail="Unauthorized client access")

    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=target_cid)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Client not found")

    active, reason, _ = _is_client_active(rec)
    if not active:
        raise HTTPException(status_code=403, detail=f"Plan is {reason}")

    stock_obj = payload.stock_json
    if isinstance(stock_obj, str):
        try:
            stock_obj = json.loads(stock_obj) if stock_obj.strip() else {}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON in stock_json")

    entry = dict(rec)
    entry["knowledge_base"] = (payload.knowledge_base or "").strip()
    entry["output_webhook_url"] = (payload.output_webhook_url or "").strip()
    entry["stock_json"] = stock_obj if isinstance(stock_obj, dict) else {}
    _save_client_record(resolved_cid, entry)

    return {
        "ok": True,
        "client_id": resolved_cid,
        "knowledge_base": entry["knowledge_base"],
        "output_webhook_url": entry["output_webhook_url"],
    }


@app.get("/api/widget/status")
def widget_status(client_id: str = Query(...)):
    maint = _get_maintenance_state()
    if maint.get("maintenance_mode"):
        return {
            "allowed": False,
            "maintenance_mode": True,
            "reason": "maintenance_mode",
            "message": maint.get("maintenance_message"),
        }

    cid = client_id.strip().upper()
    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=cid)
    if not resolved_cid or not rec:
        return {"allowed": False, "maintenance_mode": False, "reason": "not_found"}

    active, reason, changed = _is_client_active(rec)
    if changed:
        _save_client_record(resolved_cid, rec)

    quota = _get_client_quota(rec)
    limit = quota["conversations_limit"]
    used = quota["conversations_used"]

    if active and limit > 0 and used >= limit:
        rec["plan_active"] = False
        _save_client_record(resolved_cid, rec)
        return {
            "allowed": False,
            "maintenance_mode": False,
            "reason": "limit_reached",
            "conversations_used": used,
            "conversations_limit": limit,
            "ai_credits": quota["ai_credits"],
        }

    return {
        "allowed": active,
        "maintenance_mode": False,
        "reason": reason,
        "client_id": resolved_cid,
        "store_name": rec.get("store_name") or resolved_cid,
        "plan_active": bool(active),
        "conversations_used": used,
        "conversations_limit": limit,
        "ai_credits": quota["ai_credits"],
    }


@app.post("/api/chat")
def chat_endpoint(payload: ChatIn):
    maint = _get_maintenance_state()
    if maint.get("maintenance_mode"):
        msg = maint.get("maintenance_message") or (
            "දැනට අපේ server එක තාවකාලිකව නවත්වලා තියෙන්නේ. මෙතන ඉදලා update එකක් දෙනකන් orders එන්නේ නැහැ."
        )
        return {"ok": False, "blocked": True, "maintenance_mode": True, "reply": msg}

    cid = payload.client_id.strip().upper()
    msg_text = (payload.message or "").strip()
    session_id = (payload.session_id or "").strip()

    if not msg_text and not payload.order:
        raise HTTPException(status_code=400, detail="Message or order required")

    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=cid)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Client not found")

    active, reason, changed = _is_client_active(rec)
    if not active:
        if changed:
            _save_client_record(resolved_cid, rec)
        return {"ok": False, "blocked": True, "reason": reason, "reply": f"Service {reason}."}

    entry = dict(rec)
    quota = _get_client_quota(entry)
    limit = quota["conversations_limit"]
    used = quota["conversations_used"]

    if limit > 0 and used >= limit:
        entry["plan_active"] = False
        _save_client_record(resolved_cid, entry)
        return {
            "ok": False,
            "blocked": True,
            "reason": "limit_reached",
            "reply": "Conversation limit reached.",
        }

    dedup_key = f"{resolved_cid}:{session_id}" if session_id else ""
    if not dedup_key or dedup_key not in SEEN_CONV_SESSIONS:
        if dedup_key:
            SEEN_CONV_SESSIONS.add(dedup_key)
        new_used = min(used + 1, limit) if limit > 0 else used + 1
        entry["conversations_used"] = new_used
        if limit > 0 and new_used >= limit:
            entry["plan_active"] = False
        _save_client_record(resolved_cid, entry)

    saved_order = None
    if isinstance(payload.order, dict) and (
        payload.order.get("item") or payload.order.get("customer_name")
    ):
        qty = max(1, int(payload.order.get("quantity", 1) or 1))
        item_name = str(payload.order.get("item") or "").strip()
        saved_order = _save_client_order(
            resolved_cid,
            {
                "customer_name": str(payload.order.get("customer_name") or "").strip(),
                "phone": str(payload.order.get("phone") or "").strip(),
                "address": str(payload.order.get("address") or "").strip(),
                "item": item_name,
                "quantity": qty,
                "payment_method": str(payload.order.get("payment_method") or "COD").strip(),
                "payment_proof": str(payload.order.get("payment_proof") or "").strip(),
                "status": "Pending",
            },
        )
        _deduct_stock_if_present(entry, resolved_cid, item_name, qty)
        out_hook = str(entry.get("output_webhook_url") or "").strip()
        if out_hook:
            _post_webhook(
                out_hook,
                {
                    "client_id": resolved_cid,
                    "order": saved_order,
                    "timestamp": _utc_now_iso(),
                },
            )

    reply_text, credit_used = _call_ai_with_credit_check(
        message=msg_text or f"Order {saved_order['order_id']} captured",
        history=payload.history,
        entry=entry,
        cid=resolved_cid,
    )

    updated_quota = _get_client_quota(entry)
    return {
        "ok": True,
        "client_id": resolved_cid,
        "reply": reply_text,
        "order": saved_order,
        "credit_used": credit_used,
        "conversations_used": updated_quota["conversations_used"],
        "conversations_limit": updated_quota["conversations_limit"],
        "ai_credits": updated_quota["ai_credits"],
        "plan_active": bool(entry.get("plan_active", False)),
    }


@app.get("/api/my/dashboard")
def my_dashboard(
    client_id: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    x_client_token: Optional[str] = Header(default=None),
):
    authed_cid = _get_authed_client_id(authorization, x_client_token)
    target_cid = (client_id or authed_cid or "").strip().upper()
    if not authed_cid or authed_cid != target_cid:
        raise HTTPException(status_code=403, detail="Unauthorized client access")

    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=target_cid)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Client not found")

    active, reason, changed = _is_client_active(rec)
    if changed:
        _save_client_record(resolved_cid, rec)

    quota = _get_client_quota(rec)
    store_name = rec.get("store_name") or resolved_cid
    embed_key = str(rec.get("client_key") or "")

    return {
        "ok": True,
        "client_id": resolved_cid,
        "username": rec.get("username") or resolved_cid,
        "store_name": store_name,
        "email": rec.get("email") or "",
        "plan": rec.get("plan") or "Inactive",
        "plan_active": bool(active),
        "reason": reason,
        "expires_at": rec.get("expires_at"),
        "conversations_used": quota["conversations_used"],
        "conversations_limit": quota["conversations_limit"],
        "ai_credits": quota["ai_credits"],
        "ai_credits_used": quota["ai_credits_used"],
        "knowledge_base": rec.get("knowledge_base") or "",
        "output_webhook_url": rec.get("output_webhook_url") or "",
        "embed_html": _build_embed_html(resolved_cid, store_name, embed_key) if active else "",
        "maintenance": _get_maintenance_state(),
    }


@app.get("/api/my/orders")
def my_orders(
    client_id: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    x_client_token: Optional[str] = Header(default=None),
):
    authed_cid = _get_authed_client_id(authorization, x_client_token)
    target_cid = (client_id or authed_cid or "").strip().upper()
    if not authed_cid or authed_cid != target_cid:
        raise HTTPException(status_code=403, detail="Unauthorized client access")

    all_orders = _load_json(ORDERS_FILE, [])
    mine = [
        o
        for o in (all_orders if isinstance(all_orders, list) else [])
        if str(o.get("client_id") or "").strip().upper() == target_cid
    ]

    q = (search or "").strip().lower()
    if q:
        mine = [
            o
            for o in mine
            if q in str(o.get("order_id") or "").lower()
            or q in str(o.get("customer_name") or "").lower()
            or q in str(o.get("phone") or "").lower()
            or q in str(o.get("item") or "").lower()
        ]

    return {"ok": True, "client_id": target_cid, "orders": mine, "count": len(mine)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        reload=True,
    )