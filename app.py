import os
import time
import random
import string
from datetime import datetime, timedelta
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ====== SETTINGS (env se lo, code me mat likho) ======
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SHEETDB_URL = os.environ.get("SHEETDB_URL")

if not BOT_TOKEN or not SHEETDB_URL:
    raise RuntimeError("Please set TELEGRAM_BOT_TOKEN and SHEETDB_URL environment variables")

# username -> {'otp': '123456', 'expires': 12345678}
otp_store = {}
OTP_EXPIRY_SECONDS = 300  # 5 minute

def generate_otp(length=6):
    return ''.join(random.choice("0123456789") for _ in range(length))


def send_telegram_otp(username, otp):
    """
    NOTE:
    Yaha 'username' ko maine directly chat_id maana hai.
    Agar aap @username use karoge, to pehle uska numeric chat_id nikalna padega
    (bot se /start kara ke, getUpdates ya webhook se store karke).
    Filhaal simple example ke liye direct chat_id use kar rahe hain.
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": username,           # yaha username = chat_id assume kiya hai
        "text": f"Your login OTP is: {otp}"
    }

    resp = requests.post(url, json=payload, timeout=10)
    try:
        data = resp.json()
    except Exception:
        data = {}

    if not resp.ok or not data.get("ok", False):
        # Debug ke liye error return kar dete hain
        return False, data
    return True, data


def save_login_to_sheetdb(username):
    # Time ko India (IST) me store kar rahe hain
    ist_time = datetime.utcnow() + timedelta(hours=5, minutes=30)
    login_time_str = ist_time.strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "data": [
            {
                "username": username,
                "time": login_time_str
            }
        ]
    }

    resp = requests.post(SHEETDB_URL, json=payload, timeout=10)

    try:
        data = resp.json()
    except Exception:
        data = {}

    if not resp.ok:
        return False, data

    return True, data


@app.route("/")
def index():
    return jsonify({"status": "ok", "message": "Telegram OTP login backend"})


# Step 1: /tg/<username>/  -> OTP send kare
@app.route("/tg/<username>/", methods=["GET"])
def send_otp_route(username):
    # OTP generate
    otp = generate_otp()

    # memory me store karo (5 min ke liye)
    otp_store[username] = {
        "otp": otp,
        "expires": time.time() + OTP_EXPIRY_SECONDS
    }

    ok, info = send_telegram_otp(username, otp)
    if not ok:
        return jsonify({
            "status": "error",
            "message": "Failed to send OTP to Telegram",
            "details": info
        }), 500

    return jsonify({
        "status": "otp_sent",
        "username": username
    })


# Step 2: /tg/<username>/<otp>/  -> OTP verify + SheetDB me save
@app.route("/tg/<username>/<otp>/", methods=["GET"])
def verify_otp_route(username, otp):
    record = otp_store.get(username)

    if not record:
        return jsonify({
            "login": "failed",
            "reason": "no_otp_for_user"
        }), 400

    # expiry check
    if time.time() > record["expires"]:
        otp_store.pop(username, None)
        return jsonify({
            "login": "failed",
            "reason": "otp_expired"
        }), 400

    if otp != record["otp"]:
        return jsonify({
            "login": "failed",
            "reason": "invalid_otp"
        }), 400

    # OTP sahi hai, ek baar use hone ke baad hata do
    otp_store.pop(username, None)

    # SheetDB me username + time save karo
    ok, info = save_login_to_sheetdb(username)
    if not ok:
        return jsonify({
            "login": "failed",
            "reason": "sheetdb_error",
            "details": info
        }), 500

    return jsonify({
        "login": "successful",
        "username": username
    })


if __name__ == "__main__":
    # Local test ke liye
    app.run(host="0.0.0.0", port=5000)
