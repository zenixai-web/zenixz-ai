import base64
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections import defaultdict
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

# Auto-register new clients into data/usage.json (local + GitHub sync)
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


def register_client_in_usage_json(client_id: str, store_name: str = "", email: str = "") -> dict:
    cid = str(client_id or "").strip().upper()
    if not cid:
        raise ValueError("client_id is required")

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
    if cid not in doc["clients"]:
        doc["clients"][cid] = _build_zero_client_entry(cid, store_name=store_name, email=email)

    local_dir = os.path.dirname(LOCAL_USAGE_FILE)
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)
    with open(LOCAL_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    if GITHUB_TOKEN and GITHUB_REPO:
        _github_put_file(
            USAGE_JSON_PATH,
            doc,
            sha,
            f"Auto-register client {cid} (plan_active=false, expires_at=null)",
        )

    return doc["clients"][cid]


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