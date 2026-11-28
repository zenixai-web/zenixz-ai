import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import mimetypes
import os
import random
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import firebase_admin
from firebase_admin import credentials, firestore

BASE_DIR = Path(__file__).resolve().parent
CHATBOT_DIR = BASE_DIR / "chatbot"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CLIENTS_FILE = DATA_DIR / "clients.json"
ORDERS_FILE = DATA_DIR / "orders.json"
SYSTEM_STATE_FILE = DATA_DIR / "system_state.json"

DEFAULT_WIDGET_OPTIONS: Dict[str, bool] = {
    "show_language_switcher": True,
    "show_theme_toggle": True,
    "show_choice_cards": True,
    "strict_off_topic": True,
}

AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_API_URL = os.environ.get("AI_API_URL", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "gemini-4-hydrogen").strip()

# Token pricing per 1,000 tokens in USD (includes slight profit margin over model cost)
INPUT_USD_PER_1K_TOKENS = float(os.environ.get("INPUT_USD_PER_1K_TOKENS", "0.00035"))
OUTPUT_USD_PER_1K_TOKENS = float(os.environ.get("OUTPUT_USD_PER_1K_TOKENS", "0.00140"))
MIN_CHARGE_USD = float(os.environ.get("MIN_CHARGE_USD", "0.0002"))

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_NAME = os.environ.get("SENDER_NAME", "Zenix AI").strip()
SESSION_TOKEN_SECRET = (
    os.environ.get("SESSION_TOKEN_SECRET", "").strip() or secrets.token_hex(32)
)
ADMIN_SECRET = os.environ.get("ZENIX_ADMIN_SECRET", "").strip()

OTP_STORE: Dict[str, Dict[str, Any]] = {}
OTP_TTL_SECONDS = 300
IP_OTP_HISTORY: Dict[str, List[float]] = defaultdict(list)
SEEN_CONV_SESSIONS = set()
BOARD_WINDOW_HOURS = 24
ORDER_RETENTION_DAYS = 30

_FIRESTORE_LAST_ERROR = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _normalize_dashboard_lang(raw: Any) -> str:
    val = str(raw or "si").strip().lower()
    return val if val in ("en", "si") else "si"


def _normalize_widget_options(raw: Any) -> Dict[str, bool]:
    opts = dict(DEFAULT_WIDGET_OPTIONS)
    if isinstance(raw, dict):
        for k in DEFAULT_WIDGET_OPTIONS:
            if k in raw:
                opts[k] = bool(raw[k])
    return opts


def _extract_client_ip(request: Optional[Request]) -> str:
    if not request:
        return "Unknown"
    for h_name in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        raw = (request.headers.get(h_name) or "").strip()
        if raw:
            first_ip = raw.split(",")[0].strip()
            if first_ip:
                return first_ip
    if request.client and request.client.host:
        return str(request.client.host).strip()
    return "Unknown"


def _parse_iso_dt(val: Any) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


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
                cred_dict["private_key"] = str(cred_dict["private_key"]).replace(
                    "\\n", "\n"
                )
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


def _verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt, digest = stored_hash.split("$", 1)
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000
        ).hex()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


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
    return (
        st
        if isinstance(st, dict)
        else {"maintenance_mode": False, "maintenance_message": ""}
    )


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
    clean_cid = cid.strip().upper()
    try:
        snap = db.collection("clients").document(clean_cid).get()
        if snap.exists:
            data = snap.to_dict() or {}
            data["client_id"] = str(data.get("client_id") or snap.id or clean_cid).upper()
            return data
        for d in db.collection("clients").stream():
            data = d.to_dict() or {}
            doc_cid = str(data.get("client_id") or d.id or "").strip().upper()
            if doc_cid == clean_cid:
                data["client_id"] = doc_cid
                return data
    except Exception as exc:
        globals()["_FIRESTORE_LAST_ERROR"] = str(exc)
    return None


def _fs_find_by_email(db, email_lower: str) -> Optional[str]:
    if not db or not email_lower:
        return None
    em_clean = email_lower.strip().lower()
    try:
        for field_name in ("email", "owner_email", "client_email"):
            docs = (
                db.collection("clients").where(field_name, "==", em_clean).limit(1).stream()
            )
            for d in docs:
                data = d.to_dict() or {}
                return str(data.get("client_id") or d.id).upper()
        for d in db.collection("clients").stream():
            data = d.to_dict() or {}
            for field_name in ("email", "owner_email", "client_email"):
                if str(data.get(field_name) or "").strip().lower() == em_clean:
                    return str(data.get("client_id") or d.id).upper()
    except Exception as exc:
        globals()["_FIRESTORE_LAST_ERROR"] = str(exc)
    return None


def _fs_find_by_username(db, username_val: str) -> Optional[Dict[str, Any]]:
    if not db or not username_val:
        return None
    u_clean = username_val.strip()
    u_lower = u_clean.lower()
    try:
        for field_name in ("username", "generated_username", "client_id"):
            for candidate in (u_clean, u_lower, u_clean.upper()):
                docs = (
                    db.collection("clients").where(field_name, "==", candidate).limit(1).stream()
                )
                for d in docs:
                    data = d.to_dict() or {}
                    data["client_id"] = str(data.get("client_id") or d.id).upper()
                    return data
        for d in db.collection("clients").stream():
            data = d.to_dict() or {}
            u_match = str(data.get("username") or "").strip().lower()
            g_match = str(data.get("generated_username") or "").strip().lower()
            c_match = str(data.get("client_id") or d.id or "").strip().lower()
            if u_lower in (u_match, g_match, c_match):
                data["client_id"] = str(data.get("client_id") or d.id).upper()
                return data
    except Exception as exc:
        globals()["_FIRESTORE_LAST_ERROR"] = str(exc)
    return None


def _get_client_quota(entry: Dict[str, Any]) -> Dict[str, Any]:
    def _safe_int(val: Any, default: int = 0) -> int:
        try:
            return max(0, int(float(val)))
        except (TypeError, ValueError):
            return default

    def _safe_usd(val: Any, default: float = 0.0) -> float:
        try:
            return max(0.0, round(float(val), 6))
        except (TypeError, ValueError):
            return default

    raw_usd = entry.get("ai_credits_usd")
    if raw_usd is None:
        raw_usd = entry.get("ai_credits", 0.0)
    usd_balance = _safe_usd(raw_usd, 0.0)

    raw_used_usd = entry.get("ai_credits_used_usd")
    if raw_used_usd is None:
        raw_used_usd = entry.get("ai_credits_used", 0.0)
    usd_used = _safe_usd(raw_used_usd, 0.0)

    return {
        "conversations_limit": _safe_int(entry.get("conversations_limit"), 0),
        "conversations_used": _safe_int(entry.get("conversations_used"), 0),
        "ai_credits": usd_balance,
        "ai_credits_usd": usd_balance,
        "ai_credits_used": usd_used,
        "ai_credits_used_usd": usd_used,
        "total_tokens_used": _safe_int(entry.get("total_tokens_used"), 0),
        "file_storage_mb": _safe_int(entry.get("file_storage_mb"), 0),
        "file_storage_limit_mb": _safe_int(entry.get("file_storage_limit_mb"), 0),
        "vector_db_mb": _safe_int(entry.get("vector_db_mb"), 0),
        "vector_db_limit_mb": _safe_int(entry.get("vector_db_limit_mb"), 0),
        "table_rows_used": _safe_int(entry.get("table_rows_used"), 0),
        "table_rows_limit": _safe_int(entry.get("table_rows_limit"), 0),
    }


def _calculate_token_cost_usd(
    prompt_tokens: int, completion_tokens: int, total_tokens: int
) -> float:
    p_tok = max(0, int(prompt_tokens or 0))
    c_tok = max(0, int(completion_tokens or 0))
    if p_tok == 0 and c_tok == 0 and total_tokens > 0:
        p_tok = int(total_tokens * 0.7)
        c_tok = max(0, int(total_tokens) - p_tok)
    raw_cost = (p_tok / 1000.0) * INPUT_USD_PER_1K_TOKENS + (
        c_tok / 1000.0
    ) * OUTPUT_USD_PER_1K_TOKENS
    charged = max(MIN_CHARGE_USD, raw_cost) if (p_tok + c_tok) > 0 else MIN_CHARGE_USD
    return round(charged, 6)


def _is_client_active(entry: Dict[str, Any]) -> tuple[bool, str, bool]:
    if not entry:
        return False, "inactive", False
    exp_val = entry.get("expires_at")
    if exp_val:
        dt = _parse_iso_dt(exp_val)
        if dt and _utc_now() > dt:
            changed = entry.get("plan_active") is True
            entry["plan_active"] = False
            return False, "expired", changed
    if entry.get("plan_active") is not True:
        return False, "inactive", False
    return True, "active", False


def _save_client_record(
    cid: str, entry: Dict[str, Any], password_hash: Optional[str] = None
) -> Dict[str, Any]:
    global _FIRESTORE_LAST_ERROR
    clean_cid = cid.upper()
    quota = _get_client_quota(entry)
    widget_opts = _normalize_widget_options(entry.get("widget_options"))
    dash_lang = _normalize_dashboard_lang(entry.get("dashboard_lang"))
    embed_key = (
        str(entry.get("client_key") or "").strip() or f"zk_{secrets.token_hex(12)}"
    )
    sheet_secret = (
        str(entry.get("sheet_secret_key") or "").strip()
        or f"sec_{secrets.token_hex(10)}"
    )

    payload: Dict[str, Any] = {
        "client_id": clean_cid,
        "client_key": embed_key,
        "sheet_secret_key": sheet_secret,
        "username": entry.get("username")
        or entry.get("generated_username")
        or clean_cid,
        "generated_username": entry.get("generated_username")
        or entry.get("username")
        or clean_cid,
        "store_name": entry.get("store_name") or f"{clean_cid} Store",
        "email": (entry.get("email") or entry.get("owner_email") or "").strip().lower(),
        "business_reg_no": (entry.get("business_reg_no") or "").strip().upper(),
        "plan": (entry.get("plan") or "normal").strip().lower(),
        "plan_active": bool(entry.get("plan_active", False)),
        "expires_at": entry.get("expires_at"),
        "last_login_ip": str(entry.get("last_login_ip") or "").strip(),
        "previous_login_ip": str(entry.get("previous_login_ip") or "").strip(),
        "last_login_at": entry.get("last_login_at"),
        "dashboard_lang": dash_lang,
        "knowledge_base": entry.get("knowledge_base") or "",
        "widget_options": widget_opts,
        "output_webhook_url": (entry.get("output_webhook_url") or "").strip(),
        "stock_csv_url": (entry.get("stock_csv_url") or "").strip(),
        "stock_webapp_url": (entry.get("stock_webapp_url") or "").strip(),
        "stock_json": (
            entry.get("stock_json") if isinstance(entry.get("stock_json"), dict) else {}
        ),
        "conversations_limit": quota["conversations_limit"],
        "conversations_used": quota["conversations_used"],
        "ai_credits": quota["ai_credits_usd"],
        "ai_credits_usd": quota["ai_credits_usd"],
        "ai_credits_used": quota["ai_credits_used_usd"],
        "ai_credits_used_usd": quota["ai_credits_used_usd"],
        "total_tokens_used": quota["total_tokens_used"],
        "file_storage_mb": quota["file_storage_mb"],
        "file_storage_limit_mb": quota["file_storage_limit_mb"],
        "vector_db_mb": quota["vector_db_mb"],
        "vector_db_limit_mb": quota["vector_db_limit_mb"],
        "table_rows_used": quota["table_rows_used"],
        "table_rows_limit": quota["table_rows_limit"],
        "updated_at": _utc_now_iso(),
    }
    if password_hash:
        payload["password_hash"] = password_hash
    elif entry.get("password_hash"):
        payload["password_hash"] = entry["password_hash"]

    local_map = _load_json(CLIENTS_FILE, {})
    merged_local = (
        {**local_map.get(clean_cid, {}), **payload}
        if isinstance(local_map, dict)
        else payload
    )
    if isinstance(local_map, dict):
        local_map[clean_cid] = merged_local
        _save_json(CLIENTS_FILE, local_map)

    db = _get_db()
    if db:
        try:
            db.collection("clients").document(clean_cid).set(payload, merge=True)
            _FIRESTORE_LAST_ERROR = ""
        except Exception as exc:
            _FIRESTORE_LAST_ERROR = str(exc)

    return merged_local


def _record_login_ip(cid: str, entry: Dict[str, Any], ip_addr: str) -> Dict[str, Any]:
    clean_ip = (ip_addr or "Unknown").strip()
    prev_ip = str(entry.get("last_login_ip") or "").strip()
    updated = dict(entry)
    if prev_ip:
        updated["previous_login_ip"] = prev_ip
    updated["last_login_ip"] = clean_ip
    updated["last_login_at"] = _utc_now_iso()
    return _save_client_record(cid, updated)


def _build_embed_html(
    cid: str, store_name: str, client_key: str, base_url: str = ""
) -> str:
    clean_base = (
        base_url
        or os.environ.get("PUBLIC_API_BASE", "https://zenixz-ai-1.onrender.com")
    ).rstrip("/")
    safe_store = store_name.replace('"', "")
    return (
        f'<script\n'
        f'  src="{clean_base}/chatbot/app.js"\n'
        f'  data-api-base="{clean_base}"\n'
        f'  data-client-id="{cid}"\n'
        f'  data-client-key="{client_key}"\n'
        f'  data-client-name="{safe_store}"\n'
        f'  defer\n'
        f'></script>'
    )


def _build_premium_apps_script(cid: str, secret_key: str) -> str:
    return (
        f"// Zenix AI Premium Stock Auto-Deduct Script for {cid}\n"
        f"// 1. Open your Google Sheet -> Extensions -> Apps Script\n"
        f"// 2. Paste this code -> Deploy -> New deployment -> Web app (Execute as: Me, Access: Anyone)\n"
        f"// 3. Copy the Web App URL and paste it in Zenix AI Dashboard (Premium Stock Web App URL)\n\n"
        f'const ZENIX_CLIENT_ID = "{cid}";\n'
        f'const ZENIX_SECRET_KEY = "{secret_key}";\n'
        f'const STOCK_SHEET_NAME = "Stock"; // Columns: Item | Quantity\n\n'
        f"function doPost(e) {{\n"
        f"  try {{\n"
        f'    const data = JSON.parse((e && e.postData && e.postData.contents) || "{{}}");\n'
        f"    if (data.client_id && String(data.client_id).toUpperCase() !== ZENIX_CLIENT_ID) {{\n"
        f'      return ContentService.createTextOutput(JSON.stringify({{ ok: false, error: "Client mismatch" }})).setMimeType(ContentService.MimeType.JSON);\n'
        f"    }}\n"
        f'    const itemName = String(data.item || "").trim().toLowerCase();\n'
        f"    const qty = Math.max(1, parseInt(data.quantity || 1, 10));\n"
        f"    const ss = SpreadsheetApp.getActiveSpreadsheet();\n"
        f"    const sheet = ss.getSheetByName(STOCK_SHEET_NAME) || ss.getSheets()[0];\n"
        f"    const values = sheet.getDataRange().getValues();\n"
        f"    for (let i = 1; i < values.length; i++) {{\n"
        f'      const rowItem = String(values[i][0] || "").trim().toLowerCase();\n'
        f"      if (rowItem && (rowItem === itemName || rowItem.indexOf(itemName) !== -1)) {{\n"
        f"        const curQty = parseInt(values[i][1] || 0, 10) || 0;\n"
        f"        const nextQty = Math.max(0, curQty - qty);\n"
        f"        sheet.getRange(i + 1, 2).setValue(nextQty);\n"
        f'        return ContentService.createTextOutput(JSON.stringify({{ ok: true, item: values[i][0], remaining: nextQty }})).setMimeType(ContentService.MimeType.JSON);\n'
        f"      }}\n"
        f"    }}\n"
        f'    return ContentService.createTextOutput(JSON.stringify({{ ok: false, error: "Item not found" }})).setMimeType(ContentService.MimeType.JSON);\n'
        f"  }} catch (err) {{\n"
        f'    return ContentService.createTextOutput(JSON.stringify({{ ok: false, error: String(err) }})).setMimeType(ContentService.MimeType.JSON);\n'
        f"  }}\n"
        f"}}\n"
    )


def _match_local_client(
    local_map: Dict[str, Any],
    cid_upper: str = "",
    em_lower: str = "",
    usr_raw: str = "",
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    if not isinstance(local_map, dict):
        return None, None

    if cid_upper:
        if cid_upper in local_map and isinstance(local_map[cid_upper], dict):
            rec = dict(local_map[cid_upper])
            rec["client_id"] = cid_upper
            return cid_upper, rec
        for k, v in local_map.items():
            if isinstance(v, dict):
                v_cid = str(v.get("client_id") or k or "").strip().upper()
                if v_cid == cid_upper:
                    rec = dict(v)
                    rec["client_id"] = v_cid
                    return v_cid, rec

    if em_lower:
        for k, v in local_map.items():
            if isinstance(v, dict):
                for e_field in ("email", "owner_email", "client_email"):
                    if str(v.get(e_field) or "").strip().lower() == em_lower:
                        v_cid = str(v.get("client_id") or k or "").strip().upper()
                        rec = dict(v)
                        rec["client_id"] = v_cid
                        return v_cid, rec

    if usr_raw:
        u_low = usr_raw.strip().lower()
        for k, v in local_map.items():
            if isinstance(v, dict):
                u_match = str(v.get("username") or "").strip().lower()
                g_match = str(v.get("generated_username") or "").strip().lower()
                c_match = str(v.get("client_id") or k or "").strip().lower()
                if u_low in (u_match, g_match, c_match):
                    v_cid = str(v.get("client_id") or k or "").strip().upper()
                    rec = dict(v)
                    rec["client_id"] = v_cid
                    return v_cid, rec

    return None, None


def _resolve_client(
    db, client_id: str = "", email: str = "", username: str = ""
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    cid = (client_id or "").strip().upper()
    em = (email or "").strip().lower()
    usr = (username or "").strip()

    # Check if `username` actually contains an email or client_id format
    if not em and usr and "@" in usr:
        em = usr.lower()
    if not cid and usr and usr.upper().startswith(("CLT-", "USR-")):
        cid = usr.upper()

    if cid:
        fs_rec = _fs_get_client(db, cid)
        if fs_rec:
            return str(fs_rec.get("client_id") or cid).upper(), fs_rec
        local_map = _load_json(CLIENTS_FILE, {})
        l_cid, l_rec = _match_local_client(local_map, cid_upper=cid)
        if l_cid and l_rec:
            return l_cid, l_rec

    if em:
        owner_cid = _fs_find_by_email(db, em)
        if owner_cid:
            fs_rec = _fs_get_client(db, owner_cid)
            if fs_rec:
                return str(fs_rec.get("client_id") or owner_cid).upper(), fs_rec
        local_map = _load_json(CLIENTS_FILE, {})
        l_cid, l_rec = _match_local_client(local_map, em_lower=em)
        if l_cid and l_rec:
            return l_cid, l_rec

    if usr:
        fs_u = _fs_find_by_username(db, usr)
        if fs_u:
            return str(fs_u["client_id"]).upper(), fs_u
        local_map = _load_json(CLIENTS_FILE, {})
        l_cid, l_rec = _match_local_client(local_map, usr_raw=usr)
        if l_cid and l_rec:
            return l_cid, l_rec

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


def _cleanup_expired_orders(cid_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    cutoff_30d = _utc_now() - timedelta(days=ORDER_RETENTION_DAYS)
    raw_orders = _load_json(ORDERS_FILE, [])
    if not isinstance(raw_orders, list):
        raw_orders = []

    kept: List[Dict[str, Any]] = []
    expired_ids: List[str] = []

    for o in raw_orders:
        if not isinstance(o, dict):
            continue
        dt = _parse_iso_dt(o.get("created_at"))
        oid = str(o.get("order_id") or "").strip()
        if dt and dt < cutoff_30d:
            if oid:
                expired_ids.append(oid)
            continue
        kept.append(o)

    if len(kept) != len(raw_orders):
        _save_json(ORDERS_FILE, kept[:5000])

    db = _get_db()
    if db and expired_ids:
        for oid in expired_ids[:100]:
            try:
                db.collection("orders").document(oid).delete()
            except Exception:
                pass

    if cid_filter:
        c_up = cid_filter.strip().upper()
        return [
            o for o in kept if str(o.get("client_id") or "").strip().upper() == c_up
        ]
    return kept


def _filter_orders_24h_or_30d_search(
    orders: List[Dict[str, Any]], search_query: Optional[str] = None
) -> List[Dict[str, Any]]:
    q = (search_query or "").strip().lower()
    q_digits = "".join(ch for ch in q if ch.isdigit())
    cutoff_24h = _utc_now() - timedelta(hours=BOARD_WINDOW_HOURS)
    cutoff_30d = _utc_now() - timedelta(days=ORDER_RETENTION_DAYS)

    within_30d: List[Dict[str, Any]] = []
    for o in orders:
        dt = _parse_iso_dt(o.get("created_at"))
        if dt and dt < cutoff_30d:
            continue
        within_30d.append(o)

    if q:
        matched: List[Dict[str, Any]] = []
        for o in within_30d:
            oid = str(o.get("order_id") or "").lower()
            cname = str(o.get("customer_name") or "").lower()
            phone = str(o.get("phone") or "").lower()
            phone_digits = "".join(ch for ch in phone if ch.isdigit())
            item = str(o.get("item") or "").lower()
            if (
                q in oid
                or q in cname
                or q in phone
                or q in item
                or (len(q_digits) >= 3 and q_digits in phone_digits)
            ):
                matched.append(o)
        return matched

    recent_24h: List[Dict[str, Any]] = []
    for o in within_30d:
        dt = _parse_iso_dt(o.get("created_at"))
        if dt is None or dt >= cutoff_24h:
            recent_24h.append(o)
    return recent_24h


def _save_client_order(cid: str, order_obj: Dict[str, Any]) -> Dict[str, Any]:
    clean_cid = cid.upper()
    record = dict(order_obj)
    record["client_id"] = clean_cid
    record.setdefault(
        "order_id", f"ORD-{int(time.time())}-{secrets.randbelow(900) + 100}"
    )
    record.setdefault("created_at", _utc_now_iso())

    orders = _cleanup_expired_orders()
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


def _update_order_status(
    cid: str, order_id: str, new_status: str
) -> Optional[Dict[str, Any]]:
    clean_cid = cid.strip().upper()
    clean_oid = order_id.strip()
    orders = _cleanup_expired_orders()
    updated_order = None

    for o in orders:
        if (
            str(o.get("client_id") or "").strip().upper() == clean_cid
            and str(o.get("order_id") or "").strip() == clean_oid
        ):
            o["status"] = new_status
            o["updated_at"] = _utc_now_iso()
            updated_order = dict(o)
            break

    if not updated_order:
        return None

    _save_json(ORDERS_FILE, orders[:5000])
    db = _get_db()
    if db:
        try:
            db.collection("orders").document(clean_oid).set(updated_order, merge=True)
        except Exception:
            pass

    return updated_order


def _deduct_stock_if_present(
    entry: Dict[str, Any], cid: str, item_name: str, qty: int
) -> None:
    plan_name = str(entry.get("plan") or "normal").strip().lower()
    if "premium" not in plan_name:
        return

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


def _apply_token_credit_deduction(
    entry: Dict[str, Any],
    cid: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    fallback_text: str = "",
) -> float:
    quota = _get_client_quota(entry)
    p_tok = max(0, int(prompt_tokens or 0))
    c_tok = max(0, int(completion_tokens or 0))
    t_tok = max(0, int(total_tokens or 0))

    if t_tok == 0 and fallback_text:
        t_tok = max(1, len(fallback_text) // 4)
        p_tok = int(t_tok * 0.4)
        c_tok = max(1, t_tok - p_tok)

    cost_usd = _calculate_token_cost_usd(p_tok, c_tok, t_tok)
    remaining_usd = quota["ai_credits_usd"]
    charged_usd = min(remaining_usd, cost_usd)
    new_bal_usd = round(max(0.0, remaining_usd - charged_usd), 6)
    new_used_usd = round(quota["ai_credits_used_usd"] + charged_usd, 6)

    entry["ai_credits"] = new_bal_usd
    entry["ai_credits_usd"] = new_bal_usd
    entry["ai_credits_used"] = new_used_usd
    entry["ai_credits_used_usd"] = new_used_usd
    entry["total_tokens_used"] = quota["total_tokens_used"] + t_tok
    _save_client_record(cid, entry)
    return charged_usd


def _call_ai_with_credit_check(
    message: str,
    history: List[Dict[str, Any]],
    entry: Dict[str, Any],
    cid: str,
) -> tuple[str, bool, str]:
    quota = _get_client_quota(entry)
    remaining_usd = quota["ai_credits_usd"]
    kb_instruction = str(entry.get("knowledge_base") or "").strip()
    store_name = entry.get("store_name") or cid

    if remaining_usd <= 0:
        fallback = (
            f"AI credits balance අවසන් වී ඇත. "
            + (f"({store_name} Knowledge Base: {kb_instruction})" if kb_instruction else "")
        ).strip()
        return fallback, False, "no_ai_credits"

    if not AI_API_KEY:
        reply = (
            f"{store_name}: {kb_instruction}"
            if kb_instruction
            else f"{store_name} AI assistant ready."
        )
        _apply_token_credit_deduction(
            entry,
            cid,
            prompt_tokens=max(10, len(message) // 4),
            completion_tokens=max(10, len(reply) // 4),
            total_tokens=max(20, (len(message) + len(reply)) // 4),
            fallback_text=reply,
        )
        return reply, True, ""

    system_prompt = (
        f"You are the dedicated AI assistant for '{store_name}'. "
        f"Greet politely, explain products clearly using ONLY the client's knowledge base "
        f"(honoring any specially highlighted items/instructions), and help customers place orders.\n"
        f"Client Knowledge Base & Instructions:\n"
        f"{kb_instruction or 'No custom knowledge base provided yet; use general polite store assistant behavior.'}"
    )

    model_id = os.environ.get("AI_MODEL", AI_MODEL).strip() or "gemini-4-hydrogen"
    api_url_env = os.environ.get("AI_API_URL", AI_API_URL).strip()
    api_key_env = os.environ.get("AI_API_KEY", AI_API_KEY).strip()

    if not api_url_env or "generativelanguage.googleapis.com" in api_url_env:
        gem_url = api_url_env
        if not gem_url:
            q_key = urllib.parse.quote(api_key_env)
            gem_url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{urllib.parse.quote(model_id)}:generateContent?key={q_key}"
            )
        contents: List[Dict[str, Any]] = []
        for h in (history or [])[-6:]:
            role = "model" if h.get("role") == "assistant" else "user"
            txt = str(h.get("content") or "").strip()
            if txt:
                contents.append({"role": role, "parts": [{"text": txt}]})
        contents.append({"role": "user", "parts": [{"text": message}]})

        gem_payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
        }
        req = urllib.request.Request(
            gem_url,
            data=json.dumps(gem_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
                usage = parsed.get("usageMetadata") or {}
                p_tok = int(usage.get("promptTokenCount") or 0)
                c_tok = int(usage.get("candidatesTokenCount") or 0)
                t_tok = int(usage.get("totalTokenCount") or (p_tok + c_tok))
                cands = parsed.get("candidates") or []
                if cands:
                    parts = ((cands[0].get("content") or {}).get("parts")) or []
                    content = "".join(str(p.get("text") or "") for p in parts).strip()
                    if content:
                        _apply_token_credit_deduction(
                            entry,
                            cid,
                            prompt_tokens=p_tok,
                            completion_tokens=c_tok,
                            total_tokens=t_tok,
                            fallback_text=content,
                        )
                        return content, True, ""
                return "AI service temporarily unavailable.", False, "empty_candidates"
        except urllib.error.HTTPError as exc:
            body_txt = ""
            try:
                body_txt = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            return (
                "AI service temporarily unavailable.",
                False,
                f"HTTP {exc.code} ({model_id}): {body_txt}",
            )
        except Exception as exc:
            return "AI service temporarily unavailable.", False, f"AI error: {exc}"

    msgs: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for h in (history or [])[-6:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        txt = str(h.get("content") or "").strip()
        if txt:
            msgs.append({"role": role, "content": txt})
    msgs.append({"role": "user", "content": message})

    req = urllib.request.Request(
        api_url_env,
        data=json.dumps({"model": model_id, "messages": msgs}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key_env}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            usage = parsed.get("usage") or {}
            p_tok = int(usage.get("prompt_tokens") or 0)
            c_tok = int(usage.get("completion_tokens") or 0)
            t_tok = int(usage.get("total_tokens") or (p_tok + c_tok))
            choices = parsed.get("choices") or []
            if choices:
                content = (
                    (choices[0].get("message") or {}).get("content") or ""
                ).strip()
                if content:
                    _apply_token_credit_deduction(
                        entry,
                        cid,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        total_tokens=t_tok,
                        fallback_text=content,
                    )
                    return content, True, ""
            return "AI service temporarily unavailable.", False, "empty_choices"
    except urllib.error.HTTPError as exc:
        body_txt = ""
        try:
            body_txt = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return (
            "AI service temporarily unavailable.",
            False,
            f"HTTP {exc.code} ({model_id}): {body_txt}",
        )
    except Exception as exc:
        return "AI service temporarily unavailable.", False, f"AI error: {exc}"


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
    ai_credits: Optional[float] = None
    ai_credits_usd: Optional[float] = None
    file_storage_mb: Optional[int] = None
    file_storage_limit_mb: Optional[int] = None
    vector_db_mb: Optional[int] = None
    vector_db_limit_mb: Optional[int] = None
    table_rows_used: Optional[int] = None
    table_rows_limit: Optional[int] = None


class ClientQuotasUpdate(BaseModel):
    client_id: str = Field(..., min_length=1)
    file_storage_limit_mb: Optional[int] = Field(default=None, ge=0)
    file_storage_mb: Optional[int] = Field(default=None, ge=0)
    vector_db_limit_mb: Optional[int] = Field(default=None, ge=0)
    vector_db_mb: Optional[int] = Field(default=None, ge=0)
    table_rows_limit: Optional[int] = Field(default=None, ge=0)
    table_rows_used: Optional[int] = Field(default=None, ge=0)


class PlanActivateIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    plan: str = Field(..., min_length=1)
    conversations_limit: int = Field(default=100, ge=0)
    ai_credits: float = Field(default=10.0, ge=0)
    file_storage_limit_mb: int = Field(default=100, ge=0)
    vector_db_limit_mb: int = Field(default=100, ge=0)
    table_rows_limit: int = Field(default=1000, ge=0)
    expires_at: Optional[str] = None
    payment_reference: Optional[str] = None


class ClientSettingsUpdate(BaseModel):
    client_id: str = Field(..., min_length=1)
    store_name: Optional[str] = Field(default=None, max_length=120)
    dashboard_lang: Optional[str] = Field(default=None, max_length=10)


class KnowledgeSave(BaseModel):
    client_id: str = Field(..., min_length=1)
    knowledge_base: Optional[str] = ""
    widget_options: Optional[Dict[str, bool]] = None
    output_webhook_url: Optional[str] = ""
    stock_csv_url: Optional[str] = ""
    stock_webapp_url: Optional[str] = ""
    stock_json: Any = Field(default_factory=dict)


class OrderStatusUpdateIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)


class ChatIn(BaseModel):
    client_id: str = Field(..., min_length=1)
    session_id: Optional[str] = ""
    message: Optional[str] = ""
    history: List[Dict[str, Any]] = Field(default_factory=list)
    order: Optional[Dict[str, Any]] = None


class ClientLookupIn(BaseModel):
    client_id: Optional[str] = ""
    email: Optional[str] = ""
    username: Optional[str] = ""
    identifier: Optional[str] = ""


class ForgotIdIn(BaseModel):
    email: str = Field(..., min_length=3)


class OtpSendIn(BaseModel):
    email: Optional[str] = ""
    client_id: Optional[str] = ""
    username: Optional[str] = ""
    identifier: Optional[str] = ""
    store_name: Optional[str] = ""
    business_reg_no: Optional[str] = ""
    password: Optional[str] = ""
    new_password: Optional[str] = ""
    is_new_account: bool = False


class OtpVerifyIn(BaseModel):
    email: Optional[str] = ""
    owner_email: Optional[str] = ""
    client_id: Optional[str] = ""
    username: Optional[str] = ""
    identifier: Optional[str] = ""
    otp: str = Field(..., min_length=1)
    store_name: Optional[str] = ""
    business_reg_no: Optional[str] = ""
    password: Optional[str] = ""
    new_password: Optional[str] = ""
    is_new_account: bool = False


# --- App ---

app = FastAPI(title="Zenix AI Client Server", version="4.6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_chatbot_asset(rel_path: str) -> Optional[Path]:
    clean_rel = rel_path.lstrip("/").replace("\\", "/")
    candidate_dirs = [CHATBOT_DIR, BASE_DIR]
    for base in candidate_dirs:
        base_resolved = base.resolve()
        target = (base_resolved / clean_rel).resolve()
        if target == base_resolved or base_resolved in target.parents:
            if target.exists() and target.is_file():
                return target
    return None


@app.get("/chatbot/app.js")
@app.get("/app.js")
def serve_chatbot_js():
    js_path = _resolve_chatbot_asset("app.js")
    if not js_path:
        raise HTTPException(status_code=404, detail="app.js not found")
    return FileResponse(js_path, media_type="application/javascript")


@app.get("/chatbot/{file_path:path}")
def serve_chatbot_files(file_path: str):
    asset_path = _resolve_chatbot_asset(file_path)
    if not asset_path:
        raise HTTPException(status_code=404, detail="Asset not found")
    guessed_type, _ = mimetypes.guess_type(str(asset_path))
    return FileResponse(
        asset_path, media_type=guessed_type or "application/octet-stream"
    )


@app.get("/")
def root():
    return {"ok": True, "service": "Zenix AI Client Server", "status": "live"}


@app.get("/health")
def health():
    maint = _get_maintenance_state()
    db = _get_db()
    return {
        "ok": True,
        "maintenance_mode": bool(maint.get("maintenance_mode")),
        "firestore_connected": bool(db),
        "firestore_error": _FIRESTORE_LAST_ERROR,
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
        entry["plan"] = payload.plan.strip().lower()
    if payload.expires_at is not None:
        entry["expires_at"] = payload.expires_at
    if payload.conversations_limit is not None:
        entry["conversations_limit"] = max(0, int(payload.conversations_limit))
    if payload.conversations_used is not None:
        entry["conversations_used"] = max(0, int(payload.conversations_used))
    if payload.ai_credits_usd is not None:
        val_usd = max(0.0, round(float(payload.ai_credits_usd), 6))
        entry["ai_credits_usd"] = val_usd
        entry["ai_credits"] = val_usd
    elif payload.ai_credits is not None:
        val_usd = max(0.0, round(float(payload.ai_credits), 6))
        entry["ai_credits_usd"] = val_usd
        entry["ai_credits"] = val_usd
    if payload.file_storage_mb is not None:
        entry["file_storage_mb"] = max(0, int(payload.file_storage_mb))
    if payload.file_storage_limit_mb is not None:
        entry["file_storage_limit_mb"] = max(0, int(payload.file_storage_limit_mb))
    if payload.vector_db_mb is not None:
        entry["vector_db_mb"] = max(0, int(payload.vector_db_mb))
    if payload.vector_db_limit_mb is not None:
        entry["vector_db_limit_mb"] = max(0, int(payload.vector_db_limit_mb))
    if payload.table_rows_used is not None:
        entry["table_rows_used"] = max(0, int(payload.table_rows_used))
    if payload.table_rows_limit is not None:
        entry["table_rows_limit"] = max(0, int(payload.table_rows_limit))

    saved_entry = _save_client_record(resolved_cid, entry)
    return {"ok": True, "client_id": resolved_cid, "client": saved_entry}


@app.post("/api/clients/lookup")
@app.post("/api/client/lookup")
def lookup_client(payload: ClientLookupIn):
    raw_ident = (payload.identifier or "").strip()
    cid_in = (payload.client_id or "").strip().upper()
    em_in = (payload.email or "").strip().lower()
    usr_in = (payload.username or "").strip()

    if raw_ident and not cid_in and not em_in and not usr_in:
        if "@" in raw_ident:
            em_in = raw_ident.lower()
        elif raw_ident.upper().startswith(("CLT-", "USR-")):
            cid_in = raw_ident.upper()
            usr_in = raw_ident
        else:
            usr_in = raw_ident

    if not cid_in and not em_in and not usr_in:
        raise HTTPException(status_code=400, detail="Identifier required")

    db = _get_db()
    resolved_cid, rec = _resolve_client(
        db, client_id=cid_in, email=em_in, username=usr_in
    )
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Account not found")

    active, reason, changed = _is_client_active(rec)
    if changed:
        _save_client_record(resolved_cid, rec)

    return {
        "ok": True,
        "client_id": resolved_cid,
        "username": rec.get("username") or rec.get("generated_username") or resolved_cid,
        "generated_username": rec.get("generated_username")
        or rec.get("username")
        or resolved_cid,
        "store_name": rec.get("store_name") or f"{resolved_cid} Store",
        "email": (rec.get("email") or "").strip().lower(),
        "owner_email": (rec.get("email") or "").strip().lower(),
        "business_reg_no": (rec.get("business_reg_no") or "").strip().upper(),
        "plan": (rec.get("plan") or "normal").strip().lower(),
        "plan_active": bool(active),
        "reason": reason,
    }


@app.post("/api/client/forgot-id")
@app.post("/api/client/resend-id")
def forgot_client_id(payload: ForgotIdIn):
    em_in = (payload.email or "").strip().lower()
    if not em_in or "@" not in em_in:
        raise HTTPException(status_code=400, detail="Valid email required")

    db = _get_db()
    resolved_cid, rec = _resolve_client(db, email=em_in)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="No account found for this email")

    return {
        "ok": True,
        "client_id": resolved_cid,
        "username": rec.get("username") or resolved_cid,
        "email": em_in,
    }


@app.post("/api/client/quotas")
def update_client_quotas(
    payload: ClientQuotasUpdate,
    authorization: Optional[str] = Header(default=None),
    x_client_token: Optional[str] = Header(default=None),
    x_admin_secret: Optional[str] = Header(default=None),
):
    is_admin = bool(ADMIN_SECRET and (x_admin_secret or "").strip() == ADMIN_SECRET)
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Usage limits are locked and managed only via Admin / Firebase.",
        )

    authed_cid = _get_authed_client_id(authorization, x_client_token)
    target_cid = payload.client_id.strip().upper()
    if not is_admin and (not authed_cid or authed_cid != target_cid):
        raise HTTPException(status_code=403, detail="Unauthorized client access")

    db = _get_db()
    resolved_cid, rec = _resolve_client(db, client_id=target_cid)
    if not resolved_cid or not rec:
        raise HTTPException(status_code=404, detail="Client not found")

    entry = dict(rec)
    if payload.file_storage_limit_mb is not None:
        entry["file_storage_limit_mb"] = max(0, int(payload.file_storage_limit_mb))
    if payload.file_storage_mb is not None:
        entry["file_storage_mb"] = max(0, int(payload.file_storage_mb))
    if payload.vector_db_limit_mb is not None:
        entry["vector_db_limit_mb"] = max(0, int(payload.vector_db_limit_mb))
    if payload.vector_db_mb is not None:
        entry["vector_db_mb"] = max(0, int(payload.vector_db_mb))
    if payload.table_rows_limit is not None:
        entry["table_rows_limit"] = max(0, int(payload.table_rows_limit))
    if payload.table_rows_used is not None:
        entry["table_rows_used"] = max(0, int(payload.table_rows_used))

    _save_client_record(resolved_cid, entry)
    q = _get_client_quota(entry)
    return {"ok": True, "client_id": resolved_cid, **q}


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
    entry["plan"] = payload.plan.strip().lower()
    entry["plan_active"] = True
    entry["conversations_limit"] = max(0, int(payload.conversations_limit))
    usd_val = max(0.0, round(float(payload.ai_credits), 6))
    entry["ai_credits"] = usd_val
    entry["ai_credits_usd"] = usd_val
    entry["file_storage_limit_mb"] = max(0, int(payload.file_storage_limit_mb))
    entry["vector_db_limit_mb"] = max(0, int(payload.vector_db_limit_mb))
    entry["table_rows_limit"] = max(0, int(payload.table_rows_limit))
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


@app.post("/api/client/settings")
def update_client_settings(
    payload: ClientSettingsUpdate,
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

    entry = dict(rec)
    if payload.store_name is not None:
        clean_name = payload.store_name.strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="Store name cannot be empty")
        entry["store_name"] = clean_name

    if payload.dashboard_lang is not None:
        lang_val = payload.dashboard_lang.strip().lower()
        if lang_val not in ("en", "si"):
            raise HTTPException(
                status_code=400, detail="dashboard_lang must be 'en' or 'si'"
            )
        entry["dashboard_lang"] = lang_val

    saved = _save_client_record(resolved_cid, entry)

    return {
        "ok": True,
        "client_id": resolved_cid,
        "store_name": saved.get("store_name") or resolved_cid,
        "dashboard_lang": _normalize_dashboard_lang(saved.get("dashboard_lang")),
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
@app.post("/api/password/reset/send-otp")
def send_otp(payload: OtpSendIn):
    raw_ident = (payload.identifier or "").strip()
    cid_in = (payload.client_id or "").strip().upper()
    usr_in = (payload.username or "").strip()
    em_in = (payload.email or "").strip().lower()

    if raw_ident and not cid_in and not em_in and not usr_in:
        if "@" in raw_ident:
            em_in = raw_ident.lower()
        elif raw_ident.upper().startswith(("CLT-", "USR-")):
            cid_in = raw_ident.upper()
            usr_in = raw_ident
        else:
            usr_in = raw_ident

    db = _get_db()
    resolved_cid, existing = _resolve_client(
        db, client_id=cid_in, email=em_in, username=usr_in
    )

    if not payload.is_new_account and not existing:
        raise HTTPException(
            status_code=404,
            detail="Account not found for the provided Client ID / Email / Username",
        )

    if existing and payload.password and existing.get("password_hash"):
        if not _verify_password(payload.password, str(existing["password_hash"])):
            raise HTTPException(status_code=401, detail="Password incorrect.")

    target_email = em_in or str((existing or {}).get("email") or "").strip().lower()
    if not target_email or "@" not in target_email:
        raise HTTPException(status_code=400, detail="Valid email required")

    target_cid = (resolved_cid or cid_in or "").upper()
    if not target_cid and payload.is_new_account:
        target_cid = f"CLT-{random.randint(1000, 9999)}"
    if not target_cid:
        target_cid = "NEW-CLIENT"

    now = time.time()
    recent = [t for t in IP_OTP_HISTORY[target_email] if now - t < 600]
    IP_OTP_HISTORY[target_email] = recent
    if len(recent) >= 10:
        raise HTTPException(status_code=429, detail="Too many OTP requests")
    recent.append(now)

    otp_code = f"{random.randint(100000, 999999)}"
    OTP_STORE[target_email] = {
        "otp": otp_code,
        "email": target_email,
        "client_id": target_cid,
        "username": usr_in,
        "store_name": (payload.store_name or "").strip(),
        "business_reg_no": (payload.business_reg_no or "").strip().upper(),
        "password": payload.new_password or payload.password or "",
        "is_new_account": bool(payload.is_new_account),
        "expires_at": now + OTP_TTL_SECONDS,
    }

    try:
        _send_otp_email(target_email, otp_code, target_cid)
        return {
            "ok": True,
            "message": "OTP sent",
            "email": target_email,
            "client_id": target_cid,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/otp/verify")
@app.post("/api/password/reset/confirm")
def verify_otp(payload: OtpVerifyIn, request: Request):
    raw_ident = (payload.identifier or "").strip()
    cid_in = (payload.client_id or "").strip().upper()
    usr_in = (payload.username or "").strip()
    em_in = (payload.email or payload.owner_email or "").strip().lower()

    if raw_ident and not cid_in and not em_in and not usr_in:
        if "@" in raw_ident:
            em_in = raw_ident.lower()
        elif raw_ident.upper().startswith(("CLT-", "USR-")):
            cid_in = raw_ident.upper()
            usr_in = raw_ident
        else:
            usr_in = raw_ident

    db = _get_db()
    resolved_cid, existing = _resolve_client(
        db, client_id=cid_in, email=em_in, username=usr_in
    )
    target_email = em_in or str((existing or {}).get("email") or "").strip().lower()
    if not target_email:
        raise HTTPException(status_code=400, detail="Email or registered account required")

    rec = OTP_STORE.get(target_email)
    if (
        not rec
        or time.time() > float(rec.get("expires_at", 0))
        or str(rec.get("otp")) != payload.otp.strip()
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired OTP")

    stored_cid = str(rec.get("client_id") or "").strip().upper()
    stored_pw = str(rec.get("password") or "")
    stored_store = str(rec.get("store_name") or "")
    stored_br = str(rec.get("business_reg_no") or "")
    stored_new_acc = bool(rec.get("is_new_account"))
    OTP_STORE.pop(target_email, None)

    final_cid = (resolved_cid or cid_in or stored_cid or "").upper()
    if not final_cid or final_cid == "NEW-CLIENT":
        final_cid = f"CLT-{random.randint(1000, 9999)}"

    digits_match = "".join(ch for ch in final_cid if ch.isdigit())
    default_user = f"USR-{digits_match}" if digits_match else final_cid

    entry = dict(existing or {})
    entry["client_id"] = final_cid
    entry["email"] = target_email

    store_clean = (payload.store_name or stored_store or "").strip()
    if store_clean:
        entry["store_name"] = store_clean
    elif not existing:
        entry["store_name"] = f"{final_cid} Store"

    br_clean = (payload.business_reg_no or stored_br or "").strip().upper()
    if br_clean:
        entry["business_reg_no"] = br_clean

    if usr_in:
        entry["username"] = usr_in
    elif not entry.get("username"):
        entry["username"] = default_user
    entry.setdefault("generated_username", entry["username"])
    entry.setdefault("dashboard_lang", "si")

    if payload.is_new_account or stored_new_acc:
        entry["plan"] = "normal"
        entry["plan_active"] = False
        entry.setdefault("widget_options", dict(DEFAULT_WIDGET_OPTIONS))
        entry.setdefault("conversations_limit", 0)
        entry.setdefault("conversations_used", 0)
        entry.setdefault("ai_credits", 0.0)
        entry.setdefault("ai_credits_usd", 0.0)
        entry.setdefault("ai_credits_used", 0.0)
        entry.setdefault("ai_credits_used_usd", 0.0)
        entry.setdefault("total_tokens_used", 0)
        entry.setdefault("file_storage_mb", 0)
        entry.setdefault("file_storage_limit_mb", 0)
        entry.setdefault("vector_db_mb", 0)
        entry.setdefault("vector_db_limit_mb", 0)
        entry.setdefault("table_rows_used", 0)
        entry.setdefault("table_rows_limit", 0)

    login_ip = _extract_client_ip(request)
    prev_ip = str(entry.get("last_login_ip") or "").strip()
    if prev_ip:
        entry["previous_login_ip"] = prev_ip
    entry["last_login_ip"] = login_ip
    entry["last_login_at"] = _utc_now_iso()

    raw_pw = payload.new_password or payload.password or stored_pw or ""
    pw_hash = _hash_password(raw_pw) if raw_pw else None
    saved_entry = _save_client_record(final_cid, entry, password_hash=pw_hash)

    token = _issue_client_token(final_cid, target_email)
    return {
        "ok": True,
        "verified": True,
        "client_id": final_cid,
        "username": saved_entry.get("username") or default_user,
        "generated_username": saved_entry.get("generated_username")
        or saved_entry.get("username")
        or default_user,
        "store_name": saved_entry.get("store_name") or final_cid,
        "email": target_email,
        "dashboard_lang": _normalize_dashboard_lang(saved_entry.get("dashboard_lang")),
        "last_login_ip": saved_entry.get("last_login_ip") or login_ip,
        "previous_login_ip": saved_entry.get("previous_login_ip") or "",
        "last_login_at": saved_entry.get("last_login_at") or "",
        "token": token,
        "plan": saved_entry.get("plan") or "normal",
        "plan_active": bool(saved_entry.get("plan_active", False)),
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

    merged_widget_opts = _normalize_widget_options(rec.get("widget_options"))
    if payload.widget_options is not None:
        merged_widget_opts = _normalize_widget_options(
            {**merged_widget_opts, **payload.widget_options}
        )

    entry = dict(rec)
    entry["knowledge_base"] = (payload.knowledge_base or "").strip()
    entry["widget_options"] = merged_widget_opts
    entry["output_webhook_url"] = (payload.output_webhook_url or "").strip()
    if payload.stock_csv_url is not None:
        entry["stock_csv_url"] = payload.stock_csv_url.strip()
    if payload.stock_webapp_url is not None:
        entry["stock_webapp_url"] = payload.stock_webapp_url.strip()
    entry["stock_json"] = stock_obj if isinstance(stock_obj, dict) else {}
    _save_client_record(resolved_cid, entry)

    return {
        "ok": True,
        "client_id": resolved_cid,
        "knowledge_base": entry["knowledge_base"],
        "widget_options": merged_widget_opts,
        "output_webhook_url": entry["output_webhook_url"],
        "stock_csv_url": entry["stock_csv_url"],
        "stock_webapp_url": entry["stock_webapp_url"],
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
    widget_opts = _normalize_widget_options(rec.get("widget_options"))

    if active and limit > 0 and used >= limit:
        rec["plan_active"] = False
        _save_client_record(resolved_cid, rec)
        return {
            "allowed": False,
            "maintenance_mode": False,
            "reason": "limit_reached",
            "widget_options": widget_opts,
            "conversations_used": used,
            "conversations_limit": limit,
            "ai_credits": quota["ai_credits_usd"],
            "ai_credits_usd": quota["ai_credits_usd"],
        }

    return {
        "allowed": active,
        "maintenance_mode": False,
        "reason": reason,
        "client_id": resolved_cid,
        "store_name": rec.get("store_name") or resolved_cid,
        "plan": (rec.get("plan") or "normal").strip().lower(),
        "plan_active": bool(active),
        "widget_options": widget_opts,
        "stock_csv_url": (rec.get("stock_csv_url") or "").strip(),
        "stock_webapp_url": (rec.get("stock_webapp_url") or "").strip(),
        "conversations_used": used,
        "conversations_limit": limit,
        "ai_credits": quota["ai_credits_usd"],
        "ai_credits_usd": quota["ai_credits_usd"],
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
        pay_method = str(payload.order.get("payment_method") or "COD").strip()
        default_status = (
            "Confirmed"
            if "cod" in pay_method.lower() or "cash" in pay_method.lower()
            else "Pending"
        )
        req_status = str(payload.order.get("status") or default_status).strip()
        if req_status not in ("Pending", "Confirmed", "Close"):
            req_status = default_status

        saved_order = _save_client_order(
            resolved_cid,
            {
                "order_id": str(payload.order.get("order_id") or "").strip()
                or f"ORD-{int(time.time())}-{secrets.randbelow(900) + 100}",
                "customer_name": str(payload.order.get("customer_name") or "").strip(),
                "phone": str(payload.order.get("phone") or "").strip(),
                "address": str(payload.order.get("address") or "").strip(),
                "item": item_name,
                "quantity": qty,
                "payment_method": pay_method,
                "payment_proof": str(payload.order.get("payment_proof") or "").strip(),
                "payment_proof_name": str(
                    payload.order.get("payment_proof_name") or ""
                ).strip(),
                "status": req_status,
            },
        )
        _deduct_stock_if_present(entry, resolved_cid, item_name, qty)
        out_hook = str(entry.get("output_webhook_url") or "").strip()
        if out_hook:
            _post_webhook(
                out_hook,
                {
                    "client_id": resolved_cid,
                    "order_id": saved_order.get("order_id"),
                    "customer_name": saved_order.get("customer_name"),
                    "phone": saved_order.get("phone"),
                    "address": saved_order.get("address"),
                    "item": saved_order.get("item"),
                    "quantity": saved_order.get("quantity"),
                    "payment_method": saved_order.get("payment_method"),
                    "status": saved_order.get("status"),
                    "created_at": saved_order.get("created_at"),
                    "order": saved_order,
                },
            )

    reply_text, credit_used, ai_err = _call_ai_with_credit_check(
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
        "ai_error": ai_err,
        "order": saved_order,
        "credit_used": credit_used,
        "conversations_used": updated_quota["conversations_used"],
        "conversations_limit": updated_quota["conversations_limit"],
        "ai_credits": updated_quota["ai_credits_usd"],
        "ai_credits_usd": updated_quota["ai_credits_usd"],
        "plan_active": bool(entry.get("plan_active", False)),
    }


@app.get("/api/my/dashboard")
def my_dashboard(
    request: Request,
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

    req_ip = _extract_client_ip(request)
    if not rec.get("last_login_ip") and req_ip and req_ip != "Unknown":
        rec = _record_login_ip(resolved_cid, rec, req_ip)

    active, reason, changed = _is_client_active(rec)
    if changed:
        _save_client_record(resolved_cid, rec)

    quota = _get_client_quota(rec)
    widget_opts = _normalize_widget_options(rec.get("widget_options"))
    dash_lang = _normalize_dashboard_lang(rec.get("dashboard_lang"))
    store_name = rec.get("store_name") or resolved_cid
    embed_key = str(rec.get("client_key") or "")
    secret_key = str(rec.get("sheet_secret_key") or "")
    plan_name = (rec.get("plan") or "normal").strip().lower()
    is_premium = bool(active and "premium" in plan_name)

    return {
        "ok": True,
        "client_id": resolved_cid,
        "client_key": embed_key,
        "username": rec.get("username") or resolved_cid,
        "store_name": store_name,
        "email": rec.get("email") or "",
        "dashboard_lang": dash_lang,
        "last_login_ip": rec.get("last_login_ip") or req_ip,
        "previous_login_ip": rec.get("previous_login_ip") or "",
        "last_login_at": rec.get("last_login_at") or "",
        "plan": plan_name,
        "plan_active": bool(active),
        "is_premium": is_premium,
        "reason": reason,
        "expires_at": rec.get("expires_at"),
        "conversations_used": quota["conversations_used"],
        "conversations_limit": quota["conversations_limit"],
        "ai_credits": quota["ai_credits_usd"],
        "ai_credits_usd": quota["ai_credits_usd"],
        "ai_credits_used": quota["ai_credits_used_usd"],
        "ai_credits_used_usd": quota["ai_credits_used_usd"],
        "total_tokens_used": quota["total_tokens_used"],
        "file_storage_mb": quota["file_storage_mb"],
        "file_storage_limit_mb": quota["file_storage_limit_mb"],
        "vector_db_mb": quota["vector_db_mb"],
        "vector_db_limit_mb": quota["vector_db_limit_mb"],
        "table_rows_used": quota["table_rows_used"],
        "table_rows_limit": quota["table_rows_limit"],
        "knowledge_base": rec.get("knowledge_base") or "",
        "widget_options": widget_opts,
        "output_webhook_url": rec.get("output_webhook_url") or "",
        "stock_csv_url": rec.get("stock_csv_url") or "",
        "stock_webapp_url": rec.get("stock_webapp_url") or "",
        "premium_apps_script": (
            _build_premium_apps_script(resolved_cid, secret_key) if is_premium else ""
        ),
        "embed_html": (
            _build_embed_html(resolved_cid, store_name, embed_key) if active else ""
        ),
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

    mine = _cleanup_expired_orders(cid_filter=target_cid)
    filtered = _filter_orders_24h_or_30d_search(mine, search_query=search)

    return {
        "ok": True,
        "client_id": target_cid,
        "orders": filtered,
        "count": len(filtered),
    }


@app.post("/api/my/orders/status")
def my_order_status(
    payload: OrderStatusUpdateIn,
    authorization: Optional[str] = Header(default=None),
    x_client_token: Optional[str] = Header(default=None),
):
    authed_cid = _get_authed_client_id(authorization, x_client_token)
    target_cid = payload.client_id.strip().upper()
    if not authed_cid or authed_cid != target_cid:
        raise HTTPException(status_code=403, detail="Unauthorized client access")

    st = payload.status.strip()
    if st not in ("Pending", "Confirmed", "Close"):
        raise HTTPException(
            status_code=400, detail="Status must be Pending, Confirmed, or Close"
        )

    updated = _update_order_status(target_cid, payload.order_id, st)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")

    return {"ok": True, "client_id": target_cid, "order": updated}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        reload=True,
    )
