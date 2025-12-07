import os
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ===== CONFIG =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Render ENV me set karna
SHEETDB_URL = "https://sheetdb.io/api/v1/17v254fdw500k"
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

OTP_TTL = 300  # seconds
otp_store = {}  # { chat_id: {otp, expires} }


# ===== HELPERS =====

def send_message(chat_id, text):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()


def ist_time():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    return now.strftime("%Y-%m-%d %H:%M:%S")


def save_login(chat_id):
    payload = {
        "username": str(chat_id),
        "time": ist_time()
    }
    r = requests.post(SHEETDB_URL, json=payload, timeout=10)
    r.raise_for_status()


def is_logged_in(chat_id):
    r = requests.get(
        f"{SHEETDB_URL}/search",
        params={"username": str(chat_id)},
        timeout=10
    )
    r.raise_for_status()
    return len(r.json()) > 0


def delete_login(chat_id):
    r = requests.delete(
        f"{SHEETDB_URL}/username/{chat_id}",
        timeout=10
    )
    r.raise_for_status()
    return r.json().get("deleted", 0)


# ===== ROUTES =====

@app.route("/")
def home():
    return "OK"


@app.route("/tg/<int:chat_id>/", methods=["GET"])
def send_otp(chat_id):
    otp = f"{random.randint(0, 999999):06d}"
    otp_store[chat_id] = {
        "otp": otp,
        "expires": time.time() + OTP_TTL
    }

    try:
        send_message(chat_id, f"Your OTP is: {otp}")
    except Exception as e:
        return jsonify({
            "error": "telegram_error",
            "details": str(e)
        }), 400

    return jsonify({"status": "otp_sent"})


@app.route("/tg/<int:chat_id>/<otp>/", methods=["GET"])
def verify_otp(chat_id, otp):
    data = otp_store.get(chat_id)

    if not data:
        return jsonify({"login": "failed", "reason": "no_otp"}), 400

    if time.time() > data["expires"]:
        otp_store.pop(chat_id, None)
        return jsonify({"login": "failed", "reason": "expired"}), 400

    if otp != data["otp"]:
        return jsonify({"login": "failed", "reason": "wrong_otp"}), 400

    otp_store.pop(chat_id, None)
    save_login(chat_id)

    return jsonify({"login": "successful"})


# ===== TELEGRAM WEBHOOK =====

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)

    if not update:
        return "ok"

    message = update.get("message")
    if not message:
        return "ok"

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return "ok"

    try:
        if text.startswith("/start"):
            send_message(chat_id, f"Your chat_id is:\n{chat_id}")

        elif text.startswith("/chat_id"):
            send_message(chat_id, f"Your chat_id is:\n{chat_id}")

        elif text.startswith("/login_status"):
            if is_logged_in(chat_id):
                send_message(chat_id, "Login status: LOGGED IN")
            else:
                send_message(chat_id, "Login status: NOT LOGGED IN")

        elif text.startswith("/delete_account"):
            deleted = delete_login(chat_id)
            if deleted > 0:
                send_message(chat_id, "Account deleted")
            else:
                send_message(chat_id, "No account found")

    except Exception:
        pass

    return "ok"


# ===== ENTRYPOINT =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
