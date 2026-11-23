import json
import os
import random
import time
import urllib.error
import urllib.request
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


@app.post("/api/otp/send")
def api_send_otp():
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