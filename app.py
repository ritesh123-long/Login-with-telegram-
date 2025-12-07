import os
import random
import string
import time
from datetime import datetime, timezone

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ==== CONFIG ====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEETDB_URL = os.environ.get("SHEETDB_URL", "https://sheetdb.io/api/v1/17v254fdw500k")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var set karo Render ke dashboard me.")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# OTP store: { chat_id: {"otp": "123456", "expires_at": timestamp} }
otp_store = {}


def generate_otp(length: int = 6) -> str:
    return "".join(random.choice(string.digits) for _ in range(length))


def send_telegram_message(chat_id: int, text: str):
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    resp = requests.post(TELEGRAM_API_URL, json=payload, timeout=10)
    return resp


def current_time_str() -> str:
    # ISO format time (UTC) – SheetDB me "time" column me yahi jayega
    return datetime.now(timezone.utc).isoformat()


# ================== WEB ROUTES (OTP LOGIN) ==================


@app.route("/")
def index():
    return "Telegram OTP service is running."


@app.route("/tg/<int:chat_id>/", methods=["GET"])
def send_otp(chat_id):
    """
    Step 1:
    User /start ya /chat_id se apna chat_id le,
    fir URL open kare:
    https://your-service.onrender.com/tg/<chat_id>/
    """
    otp = generate_otp()
    expires_at = time.time() + 5 * 60  # 5 minutes

    otp_store[chat_id] = {
        "otp": otp,
        "expires_at": expires_at,
    }

    msg = f"Your login OTP is: {otp}\nThis OTP is valid for 5 minutes."
    resp = send_telegram_message(chat_id, msg)

    if not resp.ok:
        return jsonify(
            {
                "login": "failed",
                "error": "Failed to send OTP to Telegram",
                "telegram_response": resp.text,
            }
        ), 400

    return jsonify({"login": "pending", "message": "OTP sent to Telegram"})


@app.route("/tg/<int:chat_id>/<otp>/", methods=["GET"])
def verify_otp(chat_id, otp):
    """
    Step 2:
    User same chat_id ke sath OTP verify kare:
    https://your-service.onrender.com/tg/<chat_id>/<otp>/
    """
    record = otp_store.get(chat_id)

    if not record:
        return jsonify({"login": "failed", "error": "No OTP generated for this chat_id"}), 400

    if time.time() > record["expires_at"]:
        otp_store.pop(chat_id, None)
        return jsonify({"login": "failed", "error": "OTP expired"}), 400

    if otp != record["otp"]:
        return jsonify({"login": "failed", "error": "Invalid OTP"}), 400

    # OTP sahi hai – SheetDB me login entry create karo
    login_time = current_time_str()
    payload = {
        "username": str(chat_id),  # NOTE: direct username + time (data[] nahi)
        "time": login_time,
    }

    sheet_resp = requests.post(SHEETDB_URL, json=payload, timeout=10)

    if not sheet_resp.ok:
        return jsonify(
            {
                "login": "failed",
                "error": "Failed to write to SheetDB",
                "sheetdb_response": sheet_resp.text,
            }
        ), 500

    # OTP ka record hata do
    otp_store.pop(chat_id, None)

    return jsonify(
        {
            "login": "successful",
            "username": str(chat_id),
            "time": login_time,
        }
    )


# ================== TELEGRAM WEBHOOK (commands) ==================


@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """
    Telegram bot webhook endpoint.
    Commands:
      /start           -> chat_id batata hai + instructions
      /chat_id         -> sirf chat_id batata hai
      /login_status    -> SheetDB se check kar ke batata hai login stored hai ya nahi
      /delete_account  -> SheetDB se username row delete karta hai
    """

    update = request.get_json(force=True, silent=True) or {}

    message = update.get("message")
    if not message:
        return "ok"

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "").strip()

    if chat_id is None or not text:
        return "ok"

    # Commands handle karo
    if text.startswith("/start"):
        reply = (
            "Welcome!\n"
            f"Your chat_id is: `{chat_id}`\n\n"
            "Login karne ke liye:\n"
            "1. Yeh URL browser me open karo:\n"
            f"   https://YOUR_RENDER_URL/tg/{chat_id}/\n"
            "2. Telegram me aapko OTP milega.\n"
            "3. Fir yeh URL open karo (OTP ke sath):\n"
            f"   https://YOUR_RENDER_URL/tg/{chat_id}/<OTP>/\n\n"
            "Other commands:\n"
            "/chat_id - apna chat id dekhne ke liye\n"
            "/login_status - check karo login store hua ya nahi\n"
            "/delete_account - account entry delete karne ke liye"
        )
        send_telegram_message(chat_id, reply)
        return "ok"

    if text.startswith("/chat_id"):
        reply = f"Your chat_id is: `{chat_id}`"
        send_telegram_message(chat_id, reply)
        return "ok"

    if text.startswith("/login_status"):
        # SheetDB se search karo: /search?username=<chat_id>
        try:
            r = requests.get(
                f"{SHEETDB_URL}/search",
                params={"username": str(chat_id)},
                timeout=10,
            )
            if not r.ok:
                send_telegram_message(
                    chat_id,
                    "SheetDB se data fetch karne me error aa gaya. Thodi der baad try karo.",
                )
                return "ok"

            rows = r.json()
            if isinstance(rows, list) and len(rows) > 0:
                # Logged in
                row = rows[0]
                time_value = row.get("time", "unknown time")
                send_telegram_message(
                    chat_id,
                    f"Login status: ✅ Logged in\nTime: {time_value}",
                )
            else:
                send_telegram_message(
                    chat_id,
                    "Login status: ❌ Not logged in (SheetDB me entry nahi mili).",
                )
        except Exception as e:
            send_telegram_message(chat_id, f"Error checking login status: {e}")
        return "ok"

    if text.startswith("/delete_account"):
        # SheetDB delete: /username/<chat_id>
        try:
            r = requests.delete(
                f"{SHEETDB_URL}/username/{chat_id}",
                timeout=10,
            )
            if r.ok:
                send_telegram_message(
                    chat_id,
                    "✅ Account delete ho gaya (SheetDB se entry hat gayi).",
                )
            else:
                send_telegram_message(
                    chat_id,
                    f"❌ Account delete nahi ho paya. Response: {r.text}",
                )
        except Exception as e:
            send_telegram_message(chat_id, f"Error deleting account: {e}")

        return "ok"

    # koi unknown text / command
    send_telegram_message(
        chat_id,
        "Unknown command. Use /start, /chat_id, /login_status, or /delete_account.",
    )
    return "ok"


# Render / gunicorn ke liye WSGI entrypoint
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
