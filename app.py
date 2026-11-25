import base64
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "support@zenixai.online").strip()
SENDER_NAME = os.environ.get("SENDER_NAME", "Zenix AI").strip()
ALLOW_DEV_CONSOLE_OTP = os.environ.get("ALLOW_DEV_CONSOLE_OTP", "0").strip() == "1"

OTP_STORE = {}
OTP_TTL_SECONDS = 300  # 5 minutes

# Same IP OTP rate limit (cost & spam protection)
IP_OTP_HISTORY = defaultdict(list)
IP_OTP_WINDOW_SECONDS = 600  # 10 minutes
IP_OTP_MAX_REQUESTS = 5     # Max 5 OTP sends per IP per 10 mins

# Deduplicate conversation increments per client + bot + session
SEEN_CONVERSATIONS = set()

# Sync clients, usage & orders into data/usage.json (local + GitHub sync)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
USAGE_JSON_PATH = os.environ.get("USAGE_JSON_PATH", "data/usage.json").strip()
LOCAL_USAGE_FILE = os.environ.get("LOCAL_USAGE_FILE", "data/usage.json").strip()


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
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


def send_otp_via_brevo_https(to_email: str, otp_code: str, client_id: str) -> None:
    if not BREVO_API_KEY:
        raise RuntimeError("Missing BREVO_API_KEY.")

    payload = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": "Zenix AI Dashboard — Your Email OTP Code",
        "htmlContent": f"""
            <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111827;">
              <h2 style="margin-bottom: 8px;">Zenix AI Dashboard OTP</h2>
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"Brevo API HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Brevo API error {exc.code}: {body}") from exc


def _default_usage_doc() -> dict:
    return {
        "sync_meta": {
            "schedule": "Daily before 12:00 AM (+05:30)",
            "propagation_note": "Server/CDN updates may take 30–60 minutes to reflect.",
            "last_synced_at": "Before 12:00 AM Daily Sync",
        },
        "plans": {
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
        },
        "clients": {},
    }


def _build_zero_client_entry(client_id: str, store_name: str = "", email: str = "") -> dict:
    return {
        "store_name": store_name or f"{client_id} Store",
        "email": email,
        "plan": None,
        "plan_active": False,
        "expires_at": None,
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


def _github_get_file(path: str) -> tuple[dict | None, str | None]:
    if not (GITHUB_TOKEN and GITHUB_REPO):
        return None, None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}?ref={GITHUB_BRANCH}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ZenixAI-Backend",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            raw_bytes = base64.b64decode(payload["content"])
            return json.loads(raw_bytes.decode("utf-8")), payload.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _default_usage_doc(), None
        raise


def _github_put_file(path: str, doc: dict, sha: str | None, commit_msg: str) -> None:
    if not (GITHUB_TOKEN and GITHUB_REPO):
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    encoded = base64.b64encode(json.dumps(doc, indent=2).encode("utf-8")).decode("utf-8")
    body = {
        "message": commit_msg,
        "content": encoded,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "ZenixAI-Backend",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"GitHub update failed with HTTP {resp.status}")


def _load_usage_doc() -> tuple[dict, str | None]:
    doc = None
    sha = None
    if GITHUB_TOKEN and GITHUB_REPO:
        doc, sha = _github_get_file(USAGE_JSON_PATH)
    if doc is None:
        if os.path.exists(LOCAL_USAGE_FILE):
            with open(LOCAL_USAGE_FILE, "r", encoding="utf-8") as f:
                doc = json.load(f)
        else:
            doc = _default_usage_doc()
    doc.setdefault("clients", {})
    doc.setdefault("plans", _default_usage_doc()["plans"])
    return doc, sha


def _save_usage_doc(doc: dict, sha: str | None, commit_msg: str) -> None:
    local_dir = os.path.dirname(LOCAL_USAGE_FILE)
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)
    with open(LOCAL_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    if GITHUB_TOKEN and GITHUB_REPO:
        _github_put_file(USAGE_JSON_PATH, doc, sha, commit_msg)


def _get_client_conversation_limit(doc: dict, client_entry: dict) -> int:
    plan_name = client_entry.get("plan")
    plans = doc.get("plans", {})
    if plan_name and plan_name in plans:
        return int(plans[plan_name].get("conversations_limit", 0))
    return 0


def _is_client_active_now(client_entry: dict) -> tuple[bool, str]:
    if not client_entry.get("plan_active"):
        return False, "plan_inactive"
    exp_val = client_entry.get("expires_at")
    if exp_val:
        s = str(exp_val).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > dt:
                return False, "plan_expired"
        except ValueError:
            pass
    return True, "active"


def register_client_in_usage_json(client_id: str, store_name: str = "", email: str = "") -> dict:
    cid = str(client_id or "").strip().upper()
    if not cid:
        raise ValueError("client_id is required")

    doc, sha = _load_usage_doc()
    if cid not in doc["clients"]:
        doc["clients"][cid] = _build_zero_client_entry(cid, store_name=store_name, email=email)
        _save_usage_doc(doc, sha, f"Auto-register client {cid} (plan_active=false, expires_at=null)")

    return doc["clients"][cid]


def _increment_usage_in_doc(doc: dict, cid: str, bot_id: str, conv_inc: int = 1, session_id: str = "") -> dict:
    if session_id:
        dedup_key = f"{cid}:{bot_id}:{session_id}"
        if dedup_key in SEEN_CONVERSATIONS:
            return {"ok": True, "deduplicated": True, "changed": False}
        SEEN_CONVERSATIONS.add(dedup_key)

    client_entry = doc["clients"].get(cid)
    if not client_entry:
        raise ValueError(f"Client {cid} not found")

    active, reason = _is_client_active_now(client_entry)
    if not active:
        return {"ok": False, "reason": reason, "changed": False}

    limit = _get_client_conversation_limit(doc, client_entry)
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
    return {
        "ok": True,
        "changed": True,
        "conversations_used": new_used,
        "conversations_limit": limit,
        "limit_reached": bool(limit > 0 and new_used >= limit),
    }


@app.post("/api/clients/register")
def api_register_client():
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id", "")).strip().upper()
    store_name = str(data.get("store_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()

    if not client_id:
        return jsonify({"error": "client_id is required."}), 400

    try:
        entry = register_client_in_usage_json(client_id, store_name=store_name, email=email)
        return jsonify({"ok": True, "client_id": client_id, "client": entry}), 201
    except Exception as exc:
        return jsonify({"error": f"Failed to register client in usage.json: {str(exc)}"}), 500


@app.get("/api/widget/status")
def api_widget_status():
    cid = str(request.args.get("client_id", "")).strip().upper()
    bot_id = str(request.args.get("bot_id", "bot_1")).strip() or "bot_1"
    if not cid:
        return jsonify({"allowed": False, "reason": "missing_client_id"}), 400

    try:
        doc, _ = _load_usage_doc()
        client_entry = doc.get("clients", {}).get(cid)
        if not client_entry:
            return jsonify({"allowed": False, "reason": "client_not_found"}), 404

        active, reason = _is_client_active_now(client_entry)
        limit = _get_client_conversation_limit(doc, client_entry)
        bot_entry = client_entry.get("agents", {}).get(bot_id, {})
        used = int(bot_entry.get("conversations_used", 0))

        if active and limit > 0 and used >= limit:
            return jsonify({
                "allowed": False,
                "reason": "conversation_limit_reached",
                "client_id": cid,
                "store_name": client_entry.get("store_name", cid),
                "used": used,
                "limit": limit,
            }), 200

        return jsonify({
            "allowed": active,
            "reason": reason,
            "client_id": cid,
            "store_name": client_entry.get("store_name", cid),
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
        doc, sha = _load_usage_doc()
        res = _increment_usage_in_doc(doc, cid, bot_id, conv_inc=conv_inc, session_id=session_id)
        if not res.get("ok"):
            return jsonify(res), 403
        if res.get("changed"):
            _save_usage_doc(doc, sha, f"Track usage for {cid} ({bot_id}): {res.get('conversations_used')}")
        return jsonify(res), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
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
        doc, sha = _load_usage_doc()
        client_entry = doc.get("clients", {}).get(cid)
        if not client_entry:
            return jsonify({"error": f"Client {cid} not found."}), 404

        active, reason = _is_client_active_now(client_entry)
        if not active:
            return jsonify({"ok": False, "blocked": True, "reason": reason}), 403

        order_record = {
            "order_id": str(data.get("order_id") or f"ORD-{int(time.time())}").strip(),
            "customer_name": str(data.get("customer_name", "")).strip(),
            "phone": str(data.get("phone", "")).strip(),
            "item": str(data.get("item", "")).strip(),
            "quantity": int(data.get("quantity", 1) or 1),
            "payment_method": str(data.get("payment_method", "")).strip(),
            "payment_proof": str(data.get("payment_proof", "")).strip(),
            "status": str(data.get("status", "Pending")).strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        orders = client_entry.setdefault("orders_today", [])
        orders.insert(0, order_record)

        usage_res = _increment_usage_in_doc(
            doc, cid, bot_id, conv_inc=1, session_id=str(data.get("session_id", "")).strip()
        )
        _save_usage_doc(doc, sha, f"Add order {order_record['order_id']} for {cid}")

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
        doc, sha = _load_usage_doc()
        client_entry = doc.get("clients", {}).get(cid)
        if not client_entry:
            return jsonify({
                "blocked": True,
                "message": f"Client {cid} is not registered or inactive.",
            }), 403

        active, reason = _is_client_active_now(client_entry)
        if not active:
            msg = "Plan expired. Please renew." if reason == "plan_expired" else "Chatbot is currently deactivated."
            return jsonify({"blocked": True, "reason": reason, "message": msg}), 403

        usage_res = _increment_usage_in_doc(doc, cid, bot_id, conv_inc=1, session_id=session_id)
        if not usage_res.get("ok"):
            return jsonify({
                "blocked": True,
                "reason": usage_res.get("reason"),
                "message": "Conversation limit reached for this plan.",
            }), 403

        # If structured order payload is attached by bot/widget, save it under this client
        order_payload = data.get("order")
        saved_order = None
        if isinstance(order_payload, dict) and (order_payload.get("item") or order_payload.get("customer_name")):
            saved_order = {
                "order_id": str(order_payload.get("order_id") or f"ORD-{int(time.time())}").strip(),
                "customer_name": str(order_payload.get("customer_name", "")).strip(),
                "phone": str(order_payload.get("phone", "")).strip(),
                "item": str(order_payload.get("item", "")).strip(),
                "quantity": int(order_payload.get("quantity", 1) or 1),
                "payment_method": str(order_payload.get("payment_method", "")).strip(),
                "payment_proof": str(order_payload.get("payment_proof", "")).strip(),
                "status": str(order_payload.get("status", "Pending")).strip(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            client_entry.setdefault("orders_today", []).insert(0, saved_order)

        if usage_res.get("changed") or saved_order:
            _save_usage_doc(doc, sha, f"Chat update for {cid} ({bot_id})")

        reply_text = (
            f"Order {saved_order['order_id']} recorded for {saved_order['customer_name'] or 'Customer'}."
            if saved_order
            else f"Received: \"{message}\" — Assistant is active for {client_entry.get('store_name', cid)}."
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
    client_id = str(data.get("client_id", "CLIENT")).strip().upper()

    if "@" not in to_email or "." not in to_email.split("@")[-1]:
        return jsonify({"error": "Valid email address is required."}), 400

    otp_code = f"{random.randint(100000, 999999)}"
    store_key = f"{client_id}:{to_email.lower()}"
    OTP_STORE[store_key] = {
        "otp": otp_code,
        "expires_at": time.time() + OTP_TTL_SECONDS,
    }

    try:
        send_otp_via_brevo_https(to_email, otp_code, client_id)
        return jsonify({"ok": True, "message": "OTP sent to email."}), 200
    except Exception as exc:
        if ALLOW_DEV_CONSOLE_OTP:
            print(f"[DEV OTP FALLBACK] To: {to_email} | Client: {client_id} | OTP: {otp_code}", flush=True)
            return jsonify({"ok": True, "message": "Fallback OTP printed in terminal."}), 200
        return jsonify({"error": f"Failed to send email OTP: {str(exc)}"}), 500


@app.post("/api/otp/verify")
def api_verify_otp():
    data = request.get_json(silent=True) or {}
    to_email = str(data.get("email", "")).strip().lower()
    client_id = str(data.get("client_id", "CLIENT")).strip().upper()
    entered_otp = str(data.get("otp", "")).strip()

    store_key = f"{client_id}:{to_email}"
    record = OTP_STORE.get(store_key)

    if not record or time.time() > record["expires_at"]:
        OTP_STORE.pop(store_key, None)
        return jsonify({"error": "OTP expired or not found."}), 400

    if entered_otp != record["otp"]:
        return jsonify({"error": "Invalid OTP code."}), 401

    OTP_STORE.pop(store_key, None)
    return jsonify({"ok": True, "verified": True}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))