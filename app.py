import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import random
import secrets
import time
import urllib.error
import urllib.request
from collections import defaultdict
from flask import Flask, jsonify, request
from flask_cors import CORS

import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "support@zenixai.online").strip()
SENDER_NAME = os.environ.get("SENDER_NAME", "Zenix AI").strip()
ALLOW_DEV_CONSOLE_OTP = os.environ.get("ALLOW_DEV_CONSOLE_OTP", "0").strip() == "1"
SESSION_TOKEN_SECRET = os.environ.get("SESSION_TOKEN_SECRET", "zenix-session-secret-change-me").strip()
ADMIN_SECRET = os.environ.get("ZENIX_ADMIN_SECRET", "zenix-admin-secret-change-me").strip()

OTP_STORE = {}
RESET_OTP_STORE = {}
OTP_TTL_SECONDS = 300  # 5 minutes

IP_OTP_HISTORY = defaultdict(list)
IP_OTP_WINDOW_SECONDS = 600
IP_OTP_MAX_REQUESTS = 8

SEEN_CONVERSATIONS = set()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
USAGE_JSON_PATH = os.environ.get("USAGE_JSON_PATH", "data/usage.json").strip()
LOCAL_USAGE_FILE = os.environ.get("LOCAL_USAGE_FILE", "data/usage.json").strip()
ENABLE_USAGE_FILE_SYNC = os.environ.get("ENABLE_USAGE_FILE_SYNC", "0").strip() == "1"

_FIRESTORE_LAST_ERROR = ""


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
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
    return f"{salt}${digest}"


def _verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash or "$" not in stored_hash:
        return False
    salt, expected = stored_hash.split("$", 1)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
    return hmac.compare_digest(actual, expected)


def _issue_client_token(client_id: str, email: str) -> str:
    exp_ts = int(time.time()) + 86400 * 7
    payload_obj = {
        "client_id": client_id.upper(),
        "email": (email or "").strip().lower(),
        "exp": exp_ts,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    sig = hmac.new(
        SESSION_TOKEN_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_client_token(token: str) -> dict | None:
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
        data = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        if not data.get("client_id"):
            return None
        return data
    except Exception:
        return None


def _get_authenticated_client_id() -> str | None:
    auth_header = (request.headers.get("Authorization") or "").strip()
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = (request.headers.get("X-Client-Token") or "").strip()
    verified = _verify_client_token(token)
    if verified:
        return str(verified.get("client_id") or "").strip().upper()
    return None


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    return (request.remote_addr or "unknown").strip()


def check_and_record_ip_rate_limit(ip_addr: str) -> tuple[bool, int]:
    now = time.time()
    recent = [ts for ts in IP_OTP_HISTORY[ip_addr] if now - ts < IP_OTP_WINDOW_SECONDS]
    IP_OTP_HISTORY[ip_addr] = recent
    if len(recent) >= IP_OTP_MAX_REQUESTS:
        retry_after = int(IP_OTP_WINDOW_SECONDS - (now - recent[0])) + 1
        return True, max(1, retry_after)
    recent.append(now)
    return False, 0


def send_otp_via_brevo_https(to_email: str, otp_code: str, client_id: str, purpose: str = "Login") -> None:
    if not BREVO_API_KEY:
        raise RuntimeError("Missing BREVO_API_KEY.")

    payload = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": f"Zenix AI Dashboard — Your {purpose} OTP Code",
        "htmlContent": f"""
            <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111827;">
              <h2 style="margin-bottom: 8px;">Zenix AI Dashboard — {purpose} OTP</h2>
              <p><strong>Client ID:</strong> {client_id}</p>
              <p>Your 6-digit verification code is:</p>
              <div style="font-size: 24px; font-weight: bold; letter-spacing: 6px; padding: 10px 16px; background: #f3f4f6; display: inline-block; border-radius: 8px;">
                {otp_code}
              </div>
              <p style="margin-top: 12px; font-size: 13px; color: #4b5563;">
                Valid for 5 minutes. Do not share it with anyone.
              </p>
            </div>
        """,
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

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"Brevo API HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Brevo API error {exc.code}: {body}") from exc


def send_client_id_email_via_brevo(to_email: str, client_id: str, username: str, store_name: str) -> None:
    if not BREVO_API_KEY:
        raise RuntimeError("Missing BREVO_API_KEY.")

    payload = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": "Zenix AI Dashboard — Your Client ID & Username",
        "htmlContent": f"""
            <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111827;">
              <h2 style="margin-bottom: 8px;">Zenix AI Dashboard — Account Details</h2>
              <p><strong>Store:</strong> {store_name}</p>
              <p><strong>Client ID:</strong> <code>{client_id}</code></p>
              <p><strong>Username:</strong> <code>{username}</code></p>
              <p style="margin-top: 12px; font-size: 13px; color: #4b5563;">
                Use your Email, Client ID, or Username with your Password to log in from any device.
              </p>
            </div>
        """,
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

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"Brevo API HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Brevo API error {exc.code}: {body}") from exc


DEFAULT_PLANS = {
    "credits_50": {
        "conversations_limit": 50,
        "file_storage_limit_mb": 50,
        "vector_db_limit_mb": 50,
        "table_rows_limit": 500,
    },
    "credits_100": {
        "conversations_limit": 100,
        "file_storage_limit_mb": 100,
        "vector_db_limit_mb": 100,
        "table_rows_limit": 1000,
    },
    "credits_10000": {
        "conversations_limit": 10000,
        "file_storage_limit_mb": 10000,
        "vector_db_limit_mb": 10000,
        "table_rows_limit": 10000,
    },
}


def _default_usage_doc() -> dict:
    return {
        "sync_meta": {
            "schedule": "Live Firebase Firestore Sync",
            "propagation_note": "Controlled via Firebase Firestore.",
            "last_synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
        "plans": dict(DEFAULT_PLANS),
        "clients": {},
    }


def _build_zero_client_entry(
    client_id: str,
    store_name: str = "",
    email: str = "",
    username: str = "",
    last_ip: str = "",
) -> dict:
    clean_cid = str(client_id or "").strip().upper()
    num_part = clean_cid.replace("CLT-", "").strip() or "1000"
    clean_user = (username or f"USR-{num_part}").strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "client_id": clean_cid,
        "username": clean_user,
        "generated_username": clean_user,
        "store_name": store_name or f"{clean_cid} Store",
        "email": (email or "").strip().lower(),
        "owner_email": (email or "").strip().lower(),
        "plan": None,
        "plan_active": False,
        "expires_at": None,
        "conversations_limit": 0,
        "file_storage_limit_mb": 0,
        "vector_db_limit_mb": 0,
        "table_rows_limit": 0,
        "last_login_ip": last_ip or "",
        "last_login_at": now_iso if last_ip else None,
        "agents": {
            "bot_1": {
                "label": "Main Order Agent",
                "conversations_used": 0,
                "file_storage_mb": 0,
                "vector_db_mb": 0,
                "table_rows_used": 0,
            }
        },
        "orders_today": [],
    }


def _load_usage_doc() -> tuple[dict, str | None]:
    doc = _default_usage_doc()
    sha = None
    if ENABLE_USAGE_FILE_SYNC and os.path.exists(LOCAL_USAGE_FILE):
        try:
            with open(LOCAL_USAGE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    doc.update(loaded)
        except Exception:
            pass
    doc.setdefault("clients", {})
    doc.setdefault("plans", dict(DEFAULT_PLANS))
    return doc, sha


def _save_usage_doc(doc: dict, sha: str | None, commit_msg: str, sync_github: bool = False) -> None:
    if not ENABLE_USAGE_FILE_SYNC:
        return
    doc.setdefault("sync_meta", {})["last_synced_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    local_dir = os.path.dirname(LOCAL_USAGE_FILE)
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)
    with open(LOCAL_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def _get_client_limits(client_entry: dict) -> dict:
    plan_name = str((client_entry or {}).get("plan") or "").strip()
    preset = DEFAULT_PLANS.get(plan_name, {})

    def _pick_limit(field_name: str) -> int:
        if client_entry and client_entry.get(field_name) is not None:
            try:
                return max(0, int(client_entry.get(field_name)))
            except (TypeError, ValueError):
                pass
        return int(preset.get(field_name, 0))

    return {
        "conversations_limit": _pick_limit("conversations_limit"),
        "file_storage_limit_mb": _pick_limit("file_storage_limit_mb"),
        "vector_db_limit_mb": _pick_limit("vector_db_limit_mb"),
        "table_rows_limit": _pick_limit("table_rows_limit"),
    }


def _get_client_conversation_limit(client_entry: dict) -> int:
    return _get_client_limits(client_entry)["conversations_limit"]


def _check_and_enforce_plan_state(client_entry: dict) -> tuple[bool, str, bool]:
    if not client_entry:
        return False, "plan_inactive", False

    exp_val = client_entry.get("expires_at")
    if exp_val:
        s = str(exp_val).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > dt:
                was_active = client_entry.get("plan_active") is True
                client_entry["plan_active"] = False
                return False, "plan_expired", was_active
        except ValueError:
            pass

    if client_entry.get("plan_active") is not True:
        return False, "plan_inactive", False

    return True, "active", False


def _fs_find_email_owner(db, email_lower: str) -> str | None:
    if not db or not email_lower:
        return None
    try:
        docs = db.collection("clients").where("email", "==", email_lower).limit(1).stream()
        for d in docs:
            return str(d.id).upper()
    except Exception:
        return None
    return None


def _fs_find_by_username(db, username_val: str) -> dict | None:
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
        return None
    return None


def _fs_get_client(db, cid: str) -> dict | None:
    if not db or not cid:
        return None
    try:
        snap = db.collection("clients").document(cid.upper()).get()
        if snap.exists:
            data = snap.to_dict() or {}
            data["client_id"] = cid.upper()
            return data
    except Exception:
        return None
    return None


def _fs_sync_client(
    cid: str,
    client_entry: dict,
    password_hash: str | None = None,
    last_login_ip: str | None = None,
) -> None:
    db = _get_db()
    if not db or not cid:
        return
    clean_cid = cid.upper()
    now_iso = datetime.now(timezone.utc).isoformat()
    user_val = (
        client_entry.get("username")
        or client_entry.get("generated_username")
        or f"USR-{clean_cid.replace('CLT-', '')}"
    )
    email_val = (client_entry.get("email") or client_entry.get("owner_email") or "").strip().lower()
    limits = _get_client_limits(client_entry)
    doc_payload = {
        "client_id": clean_cid,
        "username": user_val,
        "generated_username": user_val,
        "store_name": client_entry.get("store_name") or f"{clean_cid} Store",
        "email": email_val,
        "owner_email": email_val,
        "plan": client_entry.get("plan"),
        "plan_active": bool(client_entry.get("plan_active", False)),
        "expires_at": client_entry.get("expires_at"),
        "conversations_limit": limits["conversations_limit"],
        "file_storage_limit_mb": limits["file_storage_limit_mb"],
        "vector_db_limit_mb": limits["vector_db_limit_mb"],
        "table_rows_limit": limits["table_rows_limit"],
        "agents": client_entry.get("agents") or {},
        "updated_at": now_iso,
    }
    if last_login_ip:
        doc_payload["last_login_ip"] = last_login_ip
        doc_payload["last_login_at"] = now_iso
    if password_hash:
        doc_payload["password_hash"] = password_hash
    db.collection("clients").document(clean_cid).set(doc_payload, merge=True)


def _fs_save_order(cid: str, order_record: dict) -> None:
    db = _get_db()
    if not db or not cid:
        return
    clean_cid = cid.upper()
    oid = str(order_record.get("order_id") or "").strip()
    if not oid:
        return
    payload = dict(order_record)
    payload["client_id"] = clean_cid
    payload.setdefault("tracking_events", [
        {
            "stage": "Order Placed",
            "status": payload.get("status", "Pending"),
            "timestamp": payload.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }
    ])
    db.collection("orders").document(oid).set(payload, merge=True)


def _fs_save_otp(store_key: str, email_lower: str, cid: str, otp_code: str, expires_at: float) -> None:
    db = _get_db()
    if not db:
        return
    try:
        db.collection("otp_codes").document(store_key).set({
            "client_id": cid.upper(),
            "email": email_lower,
            "otp": str(otp_code).strip(),
            "expires_at": float(expires_at),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass


def _fs_get_and_consume_otp(store_key: str, entered_otp: str, consume: bool = True) -> dict | None:
    db = _get_db()
    if not db:
        return None
    try:
        doc_ref = db.collection("otp_codes").document(store_key)
        snap = doc_ref.get()
        if not snap.exists:
            return None
        rec = snap.to_dict() or {}
        if time.time() > float(rec.get("expires_at", 0)):
            doc_ref.delete()
            return None
        if str(rec.get("otp", "")).strip() != str(entered_otp or "").strip():
            return None
        if consume:
            doc_ref.delete()
        return rec
    except Exception:
        return None


def _generate_unique_client_id(doc: dict, db) -> str:
    existing_ids = {str(k).strip().upper() for k in (doc.get("clients") or {}).keys()}
    for _ in range(50):
        num = random.randint(1000, 9999)
        candidate = f"CLT-{num}"
        if candidate in existing_ids:
            continue
        if db and _fs_get_client(db, candidate):
            continue
        return candidate
    return f"CLT-{int(time.time()) % 100000}"


def _resolve_account_record(
    db, doc: dict, client_id: str = "", email: str = "", username: str = ""
) -> tuple[str | None, dict | None]:
    cid_clean = str(client_id or "").strip().upper()
    email_clean = str(email or "").strip().lower()
    user_clean = str(username or "").strip()
    user_upper = user_clean.upper()

    if cid_clean:
        if db:
            fs_rec = _fs_get_client(db, cid_clean)
            if fs_rec:
                return cid_clean, fs_rec
        clients_map = (doc or {}).get("clients") or {}
        if cid_clean in clients_map:
            entry = dict(clients_map[cid_clean])
            entry["client_id"] = cid_clean
            return cid_clean, entry

    if email_clean:
        if db:
            owner_cid = _fs_find_email_owner(db, email_clean)
            if owner_cid:
                fs_rec = _fs_get_client(db, owner_cid)
                if fs_rec:
                    return owner_cid, fs_rec
        clients_map = (doc or {}).get("clients") or {}
        for k, v in clients_map.items():
            if str((v or {}).get("email") or "").strip().lower() == email_clean:
                cid_found = str(k).strip().upper()
                entry = dict(v)
                entry["client_id"] = cid_found
                return cid_found, entry

    if user_clean:
        if db:
            fs_by_user = _fs_find_by_username(db, user_clean)
            if fs_by_user:
                return str(fs_by_user["client_id"]).upper(), fs_by_user
        clients_map = (doc or {}).get("clients") or {}
        for k, v in clients_map.items():
            u_entry = str((v or {}).get("username") or (v or {}).get("generated_username") or "").strip()
            if u_entry.upper() == user_upper or str(k).strip().upper() == user_upper:
                cid_found = str(k).strip().upper()
                entry = dict(v)
                entry["client_id"] = cid_found
                return cid_found, entry

    return None, None


def register_client_record(
    client_id: str,
    store_name: str = "",
    email: str = "",
    username: str = "",
    password: str = "",
    last_ip: str = "",
    is_new_account: bool = False,
) -> tuple[str, dict]:
    cid = str(client_id or "").strip().upper()
    email_clean = str(email or "").strip().lower()
    user_clean = str(username or "").strip()
    db = _get_db()
    doc, sha = _load_usage_doc()

    if not cid and db and email_clean:
        existing_owner = _fs_find_email_owner(db, email_clean)
        if existing_owner:
            cid = existing_owner

    if not cid:
        for k, v in (doc.get("clients") or {}).items():
            if email_clean and str((v or {}).get("email") or "").strip().lower() == email_clean:
                cid = str(k).strip().upper()
                break

    if not cid:
        cid = _generate_unique_client_id(doc, db)

    if db and email_clean:
        existing_owner = _fs_find_email_owner(db, email_clean)
        if existing_owner and existing_owner != cid:
            raise ValueError("මෙම Email එකෙන් දැනටමත් Account එකක් සාදා ඇත. වෙනත් Email එකක් භාවිතා කරන්න.")

    existing_fs = _fs_get_client(db, cid) if db else None
    entry = dict(existing_fs or (doc.get("clients") or {}).get(cid) or _build_zero_client_entry(
        cid, store_name=store_name, email=email_clean, username=user_clean, last_ip=last_ip
    ))
    entry["client_id"] = cid
    if store_name:
        entry["store_name"] = store_name
    if email_clean and not entry.get("email"):
        entry["email"] = email_clean
        entry["owner_email"] = email_clean
    if user_clean:
        entry["username"] = user_clean
        entry["generated_username"] = user_clean
    if last_ip:
        entry["last_login_ip"] = last_ip
        entry["last_login_at"] = datetime.now(timezone.utc).isoformat()
    if not entry.get("agents"):
        entry["agents"] = {
            "bot_1": {
                "label": "Main Order Agent",
                "conversations_used": 0,
                "file_storage_mb": 0,
                "vector_db_mb": 0,
                "table_rows_used": 0,
            }
        }

    if is_new_account:
        entry["plan"] = None
        entry["plan_active"] = False
        entry["expires_at"] = None

    _, _, expired_changed = _check_and_enforce_plan_state(entry)

    pw_hash = _hash_password(password) if password else None
    _fs_sync_client(cid, entry, password_hash=pw_hash, last_login_ip=last_ip or None)

    if ENABLE_USAGE_FILE_SYNC and (is_new_account or expired_changed):
        doc.setdefault("clients", {})[cid] = entry
        _save_usage_doc(doc, sha, f"Sync client {cid}", sync_github=True)

    return cid, entry


def _increment_usage_in_firestore(
    db, cid: str, client_entry: dict, bot_id: str, conv_inc: int = 1, session_id: str = ""
) -> dict:
    if session_id:
        dedup_key = f"{cid}:{bot_id}:{session_id}"
        if dedup_key in SEEN_CONVERSATIONS:
            return {"ok": True, "deduplicated": True, "changed": False}
        SEEN_CONVERSATIONS.add(dedup_key)

    active, reason, expired_changed = _check_and_enforce_plan_state(client_entry)
    if not active:
        if expired_changed:
            try:
                _fs_sync_client(cid, client_entry)
            except Exception:
                pass
        return {"ok": False, "reason": reason, "changed": expired_changed}

    limit = _get_client_conversation_limit(client_entry)
    agents = client_entry.setdefault("agents", {})
    bot_entry = agents.setdefault(bot_id, {
        "label": "Main Order Agent",
        "conversations_used": 0,
        "file_storage_mb": 0,
        "vector_db_mb": 0,
        "table_rows_used": 0,
    })

    used = int(bot_entry.get("conversations_used", 0))
    if limit > 0 and used >= limit:
        return {
            "ok": False,
            "reason": "conversation_limit_reached",
            "conversations_used": used,
            "conversations_limit": limit,
            "changed": False,
        }

    new_used = min(used + int(conv_inc), limit) if limit > 0 else used + int(conv_inc)
    bot_entry["conversations_used"] = new_used
    _fs_sync_client(cid, client_entry)
    return {
        "ok": True,
        "changed": True,
        "conversations_used": new_used,
        "conversations_limit": limit,
        "limit_reached": bool(limit > 0 and new_used >= limit),
    }


@app.get("/api/health/firestore")
def api_health_firestore():
    db = _get_db()
    return jsonify({
        "ok": bool(db is not None),
        "firestore_connected": bool(db is not None),
        "error": _FIRESTORE_LAST_ERROR or None,
    }), (200 if db else 500)


@app.post("/api/clients/lookup")
def api_lookup_client():
    data = request.get_json(silent=True) or {}
    cid = str(data.get("client_id", "")).strip().upper()
    email = str(data.get("email", "")).strip().lower()
    username = str(data.get("username", "")).strip()

    db = _get_db()
    doc, sha = _load_usage_doc()
    resolved_cid, rec = _resolve_account_record(db, doc, client_id=cid, email=email, username=username)

    if not resolved_cid or not rec:
        return jsonify({"ok": False, "error": "Account not found."}), 404

    active, _, expired_changed = _check_and_enforce_plan_state(rec)
    if expired_changed:
        try:
            _fs_sync_client(resolved_cid, rec)
        except Exception:
            pass
        if ENABLE_USAGE_FILE_SYNC and resolved_cid in doc.get("clients", {}):
            doc["clients"][resolved_cid]["plan_active"] = False
            _save_usage_doc(doc, sha, f"Auto-expire plan for {resolved_cid}", sync_github=True)

    limits = _get_client_limits(rec)
    return jsonify({
        "ok": True,
        "client_id": resolved_cid,
        "username": rec.get("username") or rec.get("generated_username") or resolved_cid,
        "generated_username": rec.get("generated_username") or rec.get("username") or resolved_cid,
        "store_name": rec.get("store_name") or f"{resolved_cid} Store",
        "email": rec.get("email") or rec.get("owner_email") or "",
        "owner_email": rec.get("owner_email") or rec.get("email") or "",
        "plan": rec.get("plan"),
        "plan_active": bool(active),
        "expires_at": rec.get("expires_at"),
        **limits,
    }), 200


@app.post("/api/clients/register")
def api_register_client():
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id", "")).strip().upper()
    store_name = str(data.get("store_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    client_ip = get_client_ip()

    try:
        resolved_cid, entry = register_client_record(
            client_id,
            store_name=store_name,
            email=email,
            username=username,
            password=password,
            last_ip=client_ip,
            is_new_account=True,
        )
        return jsonify({"ok": True, "client_id": resolved_cid, "client": entry, "ip": client_ip}), 201
    except ValueError as exc:
        msg = str(exc)
        status_code = 409 if "Email" in msg else 400
        return jsonify({"error": msg, "code": "EMAIL_ALREADY_EXISTS" if status_code == 409 else "INVALID_INPUT"}), status_code
    except Exception as exc:
        return jsonify({"error": f"Failed to register client: {str(exc)}"}), 500


@app.post("/api/client/activate-30d")
def api_activate_30d_plan():
    data = request.get_json(silent=True) or {}
    cid = str(data.get("client_id", "")).strip().upper()
    plan_name = str(data.get("plan", "credits_50")).strip() or "credits_50"
    if not cid:
        return jsonify({"error": "client_id is required."}), 400

    doc, _ = _load_usage_doc()
    db = _get_db()
    resolved_cid, rec = _resolve_account_record(db, doc, client_id=cid)
    if not resolved_cid or not rec:
        return jsonify({"error": "Client not found."}), 404

    now_dt = datetime.now(timezone.utc)
    exp_iso = (now_dt + timedelta(days=30)).isoformat()
    preset = DEFAULT_PLANS.get(plan_name, {})
    entry = dict(rec)
    entry["plan"] = plan_name
    entry["plan_active"] = True
    entry["expires_at"] = exp_iso
    for lim_key in ("conversations_limit", "file_storage_limit_mb", "vector_db_limit_mb", "table_rows_limit"):
        if lim_key in data and data[lim_key] is not None:
            entry[lim_key] = int(data[lim_key])
        elif lim_key in preset:
            entry[lim_key] = int(preset[lim_key])
    entry.setdefault("agents", {
        "bot_1": {
            "label": "Main Order Agent",
            "conversations_used": 0,
            "file_storage_mb": 0,
            "vector_db_mb": 0,
            "table_rows_used": 0,
        }
    })
    _fs_sync_client(resolved_cid, entry)

    return jsonify({
        "ok": True,
        "client_id": resolved_cid,
        "plan": plan_name,
        "plan_active": True,
        "expires_at": exp_iso,
        **_get_client_limits(entry),
    }), 200


@app.post("/api/admin/client/manage")
def api_admin_manage_client():
    admin_sec = (request.headers.get("X-Admin-Secret") or "").strip()
    if ADMIN_SECRET and admin_sec and admin_sec != ADMIN_SECRET:
        return jsonify({"error": "Invalid admin secret"}), 403

    data = request.get_json(silent=True) or {}
    cid = str(data.get("client_id", "")).strip().upper()
    if not cid:
        return jsonify({"error": "client_id is required."}), 400

    db = _get_db()
    doc, _ = _load_usage_doc()
    resolved_cid, rec = _resolve_account_record(db, doc, client_id=cid)
    if not resolved_cid or not rec:
        return jsonify({"error": "Client not found."}), 404

    entry = dict(rec)
    if "plan_active" in data and isinstance(data["plan_active"], bool):
        entry["plan_active"] = data["plan_active"]
    if "expires_at" in data:
        entry["expires_at"] = data["expires_at"]
    if "conversations_limit" in data and data["conversations_limit"] is not None:
        entry["conversations_limit"] = max(0, int(data["conversations_limit"]))
    if "file_storage_limit_mb" in data and data["file_storage_limit_mb"] is not None:
        entry["file_storage_limit_mb"] = max(0, int(data["file_storage_limit_mb"]))
    if "vector_db_limit_mb" in data and data["vector_db_limit_mb"] is not None:
        entry["vector_db_limit_mb"] = max(0, int(data["vector_db_limit_mb"]))
    if "table_rows_limit" in data and data["table_rows_limit"] is not None:
        entry["table_rows_limit"] = max(0, int(data["table_rows_limit"]))

    _fs_sync_client(resolved_cid, entry)
    return jsonify({
        "ok": True,
        "client_id": resolved_cid,
        "plan_active": bool(entry.get("plan_active")),
        "expires_at": entry.get("expires_at"),
        **_get_client_limits(entry),
    }), 200


@app.post("/api/client/forgot-id")
def api_forgot_client_id():
    data = request.get_json(silent=True) or {}
    email_lower = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if "@" not in email_lower or "." not in email_lower.split("@")[-1]:
        return jsonify({"error": "Valid email address is required."}), 400

    db = _get_db()
    doc, _ = _load_usage_doc()
    owner_cid, client_doc = _resolve_account_record(db, doc, email=email_lower)

    if not owner_cid or not client_doc:
        return jsonify({"error": "මෙම Email එකට අදාළ Account එකක් හමු නොවීය."}), 404

    stored_hash = str((client_doc or {}).get("password_hash") or "").strip()
    if stored_hash and password and not _verify_password(password, stored_hash):
        return jsonify({"error": "Email එක හෝ Password එක නොගැළපේ."}), 401

    store_name = (client_doc or {}).get("store_name") or f"{owner_cid} Store"
    username_val = (client_doc or {}).get("username") or (client_doc or {}).get("generated_username") or owner_cid

    try:
        if BREVO_API_KEY:
            send_client_id_email_via_brevo(email_lower, owner_cid, username_val, store_name)
        elif ALLOW_DEV_CONSOLE_OTP:
            print(f"[DEV FORGOT CLIENT ID] Email: {email_lower} | Client ID: {owner_cid} | User: {username_val}", flush=True)
        return jsonify({
            "ok": True,
            "message": f"Client ID ({owner_cid}) ඔබගේ Email එකට යවන ලදී.",
            "client_id": owner_cid,
            "username": username_val,
        }), 200
    except Exception as exc:
        if ALLOW_DEV_CONSOLE_OTP:
            print(f"[DEV FORGOT CLIENT ID] Email: {email_lower} | Client ID: {owner_cid}", flush=True)
            return jsonify({
                "ok": True,
                "message": f"Client ID ({owner_cid}) sent (dev mode).",
                "client_id": owner_cid,
                "username": username_val,
            }), 200
        return jsonify({"error": f"Failed to send Client ID: {str(exc)}"}), 500


@app.get("/api/widget/status")
def api_widget_status():
    cid = str(request.args.get("client_id", "")).strip().upper()
    bot_id = str(request.args.get("bot_id", "bot_1")).strip() or "bot_1"
    if not cid:
        return jsonify({"allowed": False, "reason": "missing_client_id"}), 400

    try:
        doc, _ = _load_usage_doc()
        db = _get_db()
        _, client_entry = _resolve_account_record(db, doc, client_id=cid)
        if not client_entry:
            return jsonify({"allowed": False, "reason": "client_not_found"}), 404

        active, reason, expired_changed = _check_and_enforce_plan_state(client_entry)
        if expired_changed:
            try:
                _fs_sync_client(cid, client_entry)
            except Exception:
                pass

        limit = _get_client_conversation_limit(client_entry)
        bot_entry = client_entry.get("agents", {}).get(bot_id, {})
        used = int(bot_entry.get("conversations_used", 0))

        if active and limit > 0 and used >= limit:
            return jsonify({
                "allowed": False,
                "reason": "conversation_limit_reached",
                "client_id": cid,
                "store_name": client_entry.get("store_name", cid),
                "plan_active": False,
                "used": used,
                "limit": limit,
            }), 200

        return jsonify({
            "allowed": active,
            "reason": reason,
            "client_id": cid,
            "store_name": client_entry.get("store_name", cid),
            "plan_active": bool(active),
            "used": used,
            "limit": limit,
        }), 200
    except Exception as exc:
        return jsonify({"allowed": False, "reason": "error", "detail": str(exc)}), 500


@app.post("/api/usage/track")
def api_track_usage():
    data = request.get_json(silent=True) or {}
    cid = str(data.get("client_id", "")).strip().upper()
    bot_id = str(data.get("bot_id", "bot_1")).strip() or "bot_1"
    session_id = str(data.get("session_id", "")).strip()
    conv_inc = int(data.get("conversations_inc", 1))

    if not cid:
        return jsonify({"error": "client_id is required."}), 400

    try:
        db = _get_db()
        doc, _ = _load_usage_doc()
        resolved_cid, client_entry = _resolve_account_record(db, doc, client_id=cid)
        if not resolved_cid or not client_entry:
            return jsonify({"error": f"Client {cid} not found"}), 404

        res = _increment_usage_in_firestore(db, resolved_cid, client_entry, bot_id, conv_inc=conv_inc, session_id=session_id)
        if not res.get("ok"):
            return jsonify(res), 403
        return jsonify(res), 200
    except Exception as exc:
        return jsonify({"error": f"Failed to track usage: {str(exc)}"}), 500


@app.post("/api/orders/create")
def api_create_order():
    data = request.get_json(silent=True) or {}
    cid = str(data.get("client_id", "")).strip().upper()
    bot_id = str(data.get("bot_id", "bot_1")).strip() or "bot_1"

    if not cid:
        return jsonify({"error": "client_id is required."}), 400

    try:
        db = _get_db()
        doc, _ = _load_usage_doc()
        resolved_cid, client_entry = _resolve_account_record(db, doc, client_id=cid)
        if not resolved_cid or not client_entry:
            return jsonify({"error": f"Client {cid} not found."}), 404

        active, reason, expired_changed = _check_and_enforce_plan_state(client_entry)
        if not active:
            if expired_changed:
                try:
                    _fs_sync_client(resolved_cid, client_entry)
                except Exception:
                    pass
            return jsonify({"ok": False, "blocked": True, "reason": reason}), 403

        now_iso = datetime.now(timezone.utc).isoformat()
        order_record = {
            "order_id": str(data.get("order_id") or f"ORD-{int(time.time())}").strip(),
            "client_id": resolved_cid,
            "bot_id": bot_id,
            "customer_name": str(data.get("customer_name", "")).strip(),
            "phone": str(data.get("phone", "")).strip(),
            "address": str(data.get("address", "")).strip(),
            "item": str(data.get("item", "")).strip(),
            "quantity": int(data.get("quantity", 1) or 1),
            "payment_method": str(data.get("payment_method", "")).strip(),
            "payment_proof": str(data.get("payment_proof", "")).strip(),
            "status": str(data.get("status", "Pending")).strip(),
            "created_at": now_iso,
            "tracking_events": [
                {
                    "stage": "Order Placed",
                    "status": str(data.get("status", "Pending")).strip(),
                    "timestamp": now_iso,
                }
            ],
        }

        _fs_save_order(resolved_cid, order_record)
        usage_res = _increment_usage_in_firestore(
            db, resolved_cid, client_entry, bot_id, conv_inc=1, session_id=str(data.get("session_id", "")).strip()
        )

        return jsonify({
            "ok": True,
            "order": order_record,
            "usage": usage_res,
        }), 201
    except Exception as exc:
        return jsonify({"error": f"Failed to create order: {str(exc)}"}), 500


@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    cid = str(data.get("client_id", "DEFAULT")).strip().upper()
    bot_id = str(data.get("bot_id", "bot_1")).strip() or "bot_1"
    session_id = str(data.get("session_id", "")).strip()
    message = str(data.get("message", "")).strip()

    if not message:
        return jsonify({"error": "Message is required."}), 400

    try:
        db = _get_db()
        doc, _ = _load_usage_doc()
        resolved_cid, client_entry = _resolve_account_record(db, doc, client_id=cid)
        if not resolved_cid or not client_entry:
            return jsonify({
                "blocked": True,
                "message": f"Client {cid} is not registered or inactive.",
            }), 403

        active, reason, expired_changed = _check_and_enforce_plan_state(client_entry)
        if not active:
            if expired_changed:
                try:
                    _fs_sync_client(resolved_cid, client_entry)
                except Exception:
                    pass
            msg = "Plan expired. Please renew." if reason == "plan_expired" else "Chatbot is currently deactivated."
            return jsonify({"blocked": True, "reason": reason, "message": msg}), 403

        usage_res = _increment_usage_in_firestore(
            db, resolved_cid, client_entry, bot_id, conv_inc=1, session_id=session_id
        )
        if not usage_res.get("ok"):
            return jsonify({
                "blocked": True,
                "reason": usage_res.get("reason"),
                "message": "Conversation limit reached for this plan.",
            }), 403

        order_payload = data.get("order")
        saved_order = None
        if isinstance(order_payload, dict) and (order_payload.get("item") or order_payload.get("customer_name")):
            now_iso = datetime.now(timezone.utc).isoformat()
            saved_order = {
                "order_id": str(order_payload.get("order_id") or f"ORD-{int(time.time())}").strip(),
                "client_id": resolved_cid,
                "bot_id": bot_id,
                "customer_name": str(order_payload.get("customer_name", "")).strip(),
                "phone": str(order_payload.get("phone", "")).strip(),
                "address": str(order_payload.get("address", "")).strip(),
                "item": str(order_payload.get("item", "")).strip(),
                "quantity": int(order_payload.get("quantity", 1) or 1),
                "payment_method": str(order_payload.get("payment_method", "")).strip(),
                "payment_proof": str(order_payload.get("payment_proof", "")).strip(),
                "status": str(order_payload.get("status", "Pending")).strip(),
                "created_at": now_iso,
                "tracking_events": [
                    {
                        "stage": "Order Placed",
                        "status": str(order_payload.get("status", "Pending")).strip(),
                        "timestamp": now_iso,
                    }
                ],
            }
            _fs_save_order(resolved_cid, saved_order)

        reply_text = (
            f"Order {saved_order['order_id']} recorded for {saved_order['customer_name'] or 'Customer'}."
            if saved_order
            else f"Received: \"{message}\" — Assistant is active for {client_entry.get('store_name', resolved_cid)}."
        )

        return jsonify({
            "ok": True,
            "reply": reply_text,
            "order": saved_order,
            "limit_reached": bool(usage_res.get("limit_reached")),
        }), 200
    except Exception as exc:
        return jsonify({"error": f"Chat processing error: {str(exc)}"}), 500


@app.post("/api/otp/send")
def api_send_otp():
    ip_addr = get_client_ip()
    blocked, retry_after = check_and_record_ip_rate_limit(ip_addr)
    if blocked:
        return jsonify({
            "error": "Too many OTP requests from this IP. Please try again later.",
            "retry_after_seconds": retry_after,
        }), 429

    data = request.get_json(silent=True) or {}
    to_email = str(data.get("email", "")).strip()
    to_email_lower = to_email.lower()
    client_id = str(data.get("client_id", "")).strip().upper()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    is_new_account = bool(data.get("is_new_account", False))

    db = _get_db()
    doc, _ = _load_usage_doc()

    resolved_cid, existing_client = _resolve_account_record(
        db, doc, client_id=client_id, email=to_email_lower, username=username
    )
    if resolved_cid and not client_id:
        client_id = resolved_cid
    if not to_email_lower and existing_client:
        to_email_lower = str(existing_client.get("email") or existing_client.get("owner_email") or "").strip().lower()
        to_email = to_email_lower

    if not to_email or "@" not in to_email or "." not in to_email.split("@")[-1]:
        return jsonify({"error": "Valid email address is required."}), 400

    if not client_id:
        client_id = "NEW-CLIENT"

    if is_new_account and resolved_cid and existing_client:
        return jsonify({
            "error": "මෙම Email එකෙන් දැනටමත් Account එකක් සාදා ඇත. වෙනත් Email එකක් භාවිතා කරන්න.",
            "code": "EMAIL_ALREADY_EXISTS",
        }), 409

    if not is_new_account and existing_client:
        stored_email = str(existing_client.get("email") or existing_client.get("owner_email") or "").strip().lower()
        if stored_email and to_email_lower and stored_email != to_email_lower:
            return jsonify({"error": "Account එකට අදාළ Email එක මෙය නොවේ."}), 403
        stored_user = str(existing_client.get("username") or existing_client.get("generated_username") or "").strip()
        if username and stored_user and stored_user.upper() != username.upper() and resolved_cid != username.upper():
            return jsonify({"error": "Username එක නොගැළපේ."}), 403
        stored_hash = str(existing_client.get("password_hash") or "").strip()
        if stored_hash and password and not _verify_password(password, stored_hash):
            return jsonify({"error": "Password එක වැරදියි."}), 401

    otp_code = f"{random.randint(100000, 999999)}"
    expires_at = time.time() + OTP_TTL_SECONDS
    otp_record = {
        "otp": otp_code,
        "email": to_email_lower,
        "client_id": client_id,
        "username": username,
        "password": password,
        "expires_at": expires_at,
    }

    keys = {f"{client_id}:{to_email_lower}", f"email:{to_email_lower}"}
    for k in keys:
        OTP_STORE[k] = dict(otp_record)
        _fs_save_otp(k, to_email_lower, client_id, otp_code, expires_at)

    try:
        send_otp_via_brevo_https(to_email, otp_code, client_id, purpose="Login")
        return jsonify({
            "ok": True,
            "message": "OTP sent to email.",
            "client_id": client_id,
            "email": to_email_lower,
        }), 200
    except Exception as exc:
        if ALLOW_DEV_CONSOLE_OTP:
            print(f"[DEV OTP FALLBACK] To: {to_email} | Client: {client_id} | OTP: {otp_code}", flush=True)
            return jsonify({
                "ok": True,
                "message": "Fallback OTP printed in terminal.",
                "client_id": client_id,
                "email": to_email_lower,
            }), 200
        return jsonify({"error": f"Failed to send email OTP: {str(exc)}"}), 500


@app.post("/api/otp/verify")
def api_verify_otp():
    data = request.get_json(silent=True) or {}
    to_email = str(data.get("email", "")).strip().lower()
    requested_cid = str(data.get("client_id", "")).strip().upper()
    username = str(data.get("username", "")).strip()
    entered_otp = str(data.get("otp", "")).strip()
    store_name = str(data.get("store_name", "")).strip()
    password = str(data.get("password", "")).strip()
    is_new_account = bool(data.get("is_new_account", False))
    client_ip = get_client_ip()

    if not entered_otp:
        return jsonify({"error": "OTP code is required."}), 400

    candidate_keys = []
    if requested_cid:
        candidate_keys.append(f"{requested_cid}:{to_email}")
    candidate_keys.append(f"NEW-CLIENT:{to_email}")
    candidate_keys.append(f"email:{to_email}")

    matched = False
    stored_pw_from_otp = ""
    stored_user_from_otp = ""
    for k in candidate_keys:
        rec = OTP_STORE.get(k)
        if rec and time.time() <= float(rec.get("expires_at", 0)) and str(rec.get("otp", "")).strip() == entered_otp:
            matched = True
            stored_pw_from_otp = str(rec.get("password") or "").strip()
            stored_user_from_otp = str(rec.get("username") or "").strip()
            break
        fs_rec = _fs_get_and_consume_otp(k, entered_otp, consume=True)
        if fs_rec:
            matched = True
            if not to_email and fs_rec.get("email"):
                to_email = str(fs_rec.get("email")).strip().lower()
            break

    if not matched:
        return jsonify({"error": "OTP expired or invalid."}), 401

    for k in candidate_keys:
        OTP_STORE.pop(k, None)

    final_password = password or stored_pw_from_otp
    final_username = username or stored_user_from_otp

    db = _get_db()
    usage_doc, _ = _load_usage_doc()
    resolved_existing_cid, existing_client = _resolve_account_record(
        db,
        usage_doc,
        client_id="" if requested_cid == "NEW-CLIENT" else requested_cid,
        email=to_email,
        username=final_username,
    )

    final_cid = resolved_existing_cid or (requested_cid if requested_cid and requested_cid != "NEW-CLIENT" else "")

    if is_new_account and existing_client:
        return jsonify({
            "error": "මෙම Email එකෙන් දැනටමත් Account එකක් සාදා ඇත. වෙනත් Email එකක් භාවිතා කරන්න.",
            "code": "EMAIL_ALREADY_EXISTS",
        }), 409

    if existing_client:
        stored_email = str(existing_client.get("email") or existing_client.get("owner_email") or "").strip().lower()
        if stored_email and to_email and stored_email != to_email:
            return jsonify({"error": "Email does not match this account."}), 403
        stored_hash = str(existing_client.get("password_hash") or "").strip()
        if stored_hash and final_password and not _verify_password(final_password, stored_hash):
            return jsonify({"error": "Password එක වැරදියි."}), 401

    try:
        resolved_cid, client_entry = register_client_record(
            final_cid,
            store_name=store_name or (existing_client or {}).get("store_name", ""),
            email=to_email,
            username=final_username or (existing_client or {}).get("username", ""),
            password=final_password if (is_new_account or not (existing_client or {}).get("password_hash")) else "",
            last_ip=client_ip,
            is_new_account=is_new_account,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "EMAIL_ALREADY_EXISTS"}), 409

    token = _issue_client_token(resolved_cid, to_email)
    plan_active_val = bool(client_entry.get("plan_active", False))
    user_out = client_entry.get("username") or client_entry.get("generated_username") or resolved_cid

    return jsonify({
        "ok": True,
        "verified": True,
        "client_id": resolved_cid,
        "username": user_out,
        "generated_username": user_out,
        "email": to_email,
        "store_name": client_entry.get("store_name", f"{resolved_cid} Store"),
        "plan_active": plan_active_val,
        "expires_at": client_entry.get("expires_at"),
        "last_login_ip": client_ip,
        "token": token,
    }), 200


@app.post("/api/password/reset/send-otp")
def api_password_reset_send_otp():
    ip_addr = get_client_ip()
    blocked, retry_after = check_and_record_ip_rate_limit(ip_addr)
    if blocked:
        return jsonify({
            "error": "Too many OTP requests from this IP. Please try again later.",
            "retry_after_seconds": retry_after,
        }), 429

    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id", "")).strip().upper()
    username = str(data.get("username", "")).strip()
    to_email = str(data.get("email", "")).strip().lower()
    new_password = str(data.get("new_password", "")).strip()

    if len(new_password) < 4:
        return jsonify({"error": "New password අවම වශයෙන් අක්ෂර 4ක් වත් විය යුතුය."}), 400

    db = _get_db()
    usage_doc, _ = _load_usage_doc()
    resolved_cid, client_doc = _resolve_account_record(
        db, usage_doc, client_id=client_id, email=to_email, username=username
    )

    if not resolved_cid or not client_doc:
        return jsonify({"error": "Account එක database එකේ හමු නොවීය."}), 404

    stored_email = str(client_doc.get("email") or client_doc.get("owner_email") or "").strip().lower()
    if not to_email:
        to_email = stored_email
    if stored_email and to_email and stored_email != to_email:
        return jsonify({"error": "Account එකට අදාළ Email එක නොගැළපේ."}), 403
    if not to_email or "@" not in to_email:
        return jsonify({"error": "Valid email address is required."}), 400

    otp_code = f"{random.randint(100000, 999999)}"
    expires_at = time.time() + OTP_TTL_SECONDS
    reset_key = f"reset:{resolved_cid}:{to_email}"
    RESET_OTP_STORE[reset_key] = {
        "otp": otp_code,
        "client_id": resolved_cid,
        "email": to_email,
        "new_password": new_password,
        "expires_at": expires_at,
    }
    _fs_save_otp(reset_key, to_email, resolved_cid, otp_code, expires_at)

    try:
        send_otp_via_brevo_https(to_email, otp_code, resolved_cid, purpose="Password Reset")
        return jsonify({
            "ok": True,
            "message": "Password reset OTP code sent to email.",
            "client_id": resolved_cid,
            "email": to_email,
        }), 200
    except Exception as exc:
        if ALLOW_DEV_CONSOLE_OTP:
            print(f"[DEV RESET OTP] To: {to_email} | Client: {resolved_cid} | OTP: {otp_code}", flush=True)
            return jsonify({
                "ok": True,
                "message": "Fallback OTP printed in terminal.",
                "client_id": resolved_cid,
                "email": to_email,
            }), 200
        return jsonify({"error": f"Failed to send reset OTP: {str(exc)}"}), 500


@app.post("/api/password/reset/confirm")
def api_password_reset_confirm():
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id", "")).strip().upper()
    to_email = str(data.get("email", "")).strip().lower()
    entered_otp = str(data.get("otp", "")).strip()
    new_password = str(data.get("new_password", "")).strip()
    client_ip = get_client_ip()

    if not entered_otp:
        return jsonify({"error": "OTP අවශ්‍ය වේ."}), 400

    db = _get_db()
    usage_doc, _ = _load_usage_doc()
    resolved_cid, _ = _resolve_account_record(db, usage_doc, client_id=client_id, email=to_email)
    cid_lookup = resolved_cid or client_id

    reset_key = f"reset:{cid_lookup}:{to_email}"
    rec = RESET_OTP_STORE.get(reset_key)
    valid = bool(rec and time.time() <= float(rec.get("expires_at", 0)) and str(rec.get("otp", "")).strip() == entered_otp)
    if not valid:
        fs_rec = _fs_get_and_consume_otp(reset_key, entered_otp, consume=True)
        if not fs_rec:
            return jsonify({"error": "Invalid or expired reset OTP."}), 401

    final_pw = new_password or str((rec or {}).get("new_password") or "").strip()
    if len(final_pw) < 4:
        return jsonify({"error": "New password අවම වශයෙන් අක්ෂර 4ක් වත් විය යුතුය."}), 400

    RESET_OTP_STORE.pop(reset_key, None)

    final_cid, client_entry = register_client_record(cid_lookup, email=to_email, last_ip=client_ip)
    pw_hash = _hash_password(final_pw)
    _fs_sync_client(final_cid, client_entry, password_hash=pw_hash, last_login_ip=client_ip)

    token = _issue_client_token(final_cid, to_email)
    return jsonify({
        "ok": True,
        "message": "Password reset successful.",
        "client_id": final_cid,
        "email": to_email,
        "last_login_ip": client_ip,
        "token": token,
    }), 200


@app.get("/api/my/dashboard")
def api_my_dashboard():
    authed_cid = _get_authenticated_client_id()
    requested_cid = str(request.args.get("client_id", "")).strip().upper()
    if not authed_cid:
        return jsonify({"error": "Authentication required."}), 401
    if requested_cid and requested_cid != authed_cid:
        return jsonify({"error": "Access denied: you can only view your own dashboard."}), 403

    client_ip = get_client_ip()
    db = _get_db()
    fs_client = _fs_get_client(db, authed_cid) if db else None

    usage_doc, _ = _load_usage_doc()
    usage_client = (usage_doc.get("clients") or {}).get(authed_cid)

    if not fs_client and not usage_client:
        return jsonify({"error": "Client account not found."}), 404

    merged = dict(usage_client or {})
    if fs_client:
        for k, v in fs_client.items():
            if v is not None and k != "password_hash":
                merged[k] = v

    active, _, expired_changed = _check_and_enforce_plan_state(merged)
    merged["plan_active"] = bool(active)

    if expired_changed:
        try:
            _fs_sync_client(authed_cid, merged)
        except Exception:
            pass

    try:
        _fs_sync_client(authed_cid, merged, last_login_ip=client_ip)
    except Exception:
        pass

    limits = _get_client_limits(merged)
    merged.update(limits)

    agents = merged.get("agents") or {}
    total_conversations_used = sum(
        int((bot_data or {}).get("conversations_used", 0))
        for bot_data in agents.values()
        if isinstance(bot_data, dict)
    )

    return jsonify({
        "ok": True,
        "client_id": authed_cid,
        "username": merged.get("username") or merged.get("generated_username") or authed_cid,
        "store_name": merged.get("store_name") or f"{authed_cid} Store",
        "email": merged.get("email") or "",
        "plan": merged.get("plan") or "No Active Plan",
        "plan_active": bool(active),
        "expires_at": merged.get("expires_at"),
        "last_login_ip": client_ip,
        "conversations_used": total_conversations_used,
        "conversations_limit": limits["conversations_limit"],
        "file_storage_limit_mb": limits["file_storage_limit_mb"],
        "vector_db_limit_mb": limits["vector_db_limit_mb"],
        "table_rows_limit": limits["table_rows_limit"],
        "agents": agents,
        "client": merged,
    }), 200


@app.get("/api/my/orders")
def api_my_orders():
    authed_cid = _get_authenticated_client_id()
    requested_cid = str(request.args.get("client_id", "")).strip().upper()
    if not authed_cid:
        return jsonify({"error": "Authentication required."}), 401
    if requested_cid and requested_cid != authed_cid:
        return jsonify({"error": "Access denied: you can only view your own orders."}), 403

    orders_by_id = {}
    db = _get_db()
    if db:
        try:
            docs = db.collection("orders").where("client_id", "==", authed_cid).stream()
            for d in docs:
                item = d.to_dict() or {}
                oid = str(item.get("order_id") or d.id).strip()
                item["order_id"] = oid
                item["client_id"] = authed_cid
                orders_by_id[oid] = item
        except Exception:
            pass

    my_orders = list(orders_by_id.values())
    my_orders.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return jsonify({
        "ok": True,
        "client_id": authed_cid,
        "orders": my_orders,
        "count": len(my_orders),
    }), 200


@app.get("/api/my/tracking/<order_id>")
def api_my_order_tracking(order_id: str):
    authed_cid = _get_authenticated_client_id()
    if not authed_cid:
        return jsonify({"error": "Authentication required."}), 401

    db = _get_db()
    if not db:
        return jsonify({"error": "Firebase Firestore is not configured."}), 500

    clean_oid = str(order_id or "").strip()
    snap = db.collection("orders").document(clean_oid).get()
    if not snap.exists:
        return jsonify({"error": "Order not found."}), 404

    order_data = snap.to_dict() or {}
    owner_cid = str(order_data.get("client_id") or "").strip().upper()
    if owner_cid != authed_cid:
        return jsonify({"error": "Access denied: this order belongs to another client."}), 403

    order_data.setdefault("order_id", clean_oid)
    order_data.setdefault("tracking_events", [
        {
            "stage": "Order Placed",
            "status": order_data.get("status", "Pending"),
            "timestamp": order_data.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }
    ])

    return jsonify({
        "ok": True,
        "client_id": authed_cid,
        "tracking": order_data,
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))