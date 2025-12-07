import os
import random
import string
from datetime import datetime

import pytz
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ---- CONFIG ----

# Bot token ko environment variable me rakhna better hai
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# SheetDB API
SHEETDB_URL = "https://sheetdb.io/api/v1/17v254fdw500k"

# Memory me OTP store (simple use ke liye)
otp_store = {}  # { "username_without_at": "123456" }


def generate_otp(length=6):
    # Sirf digits ka OTP
    return "".join(random.choices(string.digits, k=length))


def get_ist_time_string():
    # India time (Asia/Kolkata)
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def clean_username(raw_username: str) -> str:
    """
    URL se aane wale username ko normalize kare:
    - agar '@' laga ho to hata de
    """
    if raw_username.startswith("@"):
        return raw_username[1:]
    return raw_username


def send_otp_to_telegram(username: str, otp: str):
    """
    Telegram pe OTP send kare.
    Yaha `chat_id` me `@username` use kiya gaya hai.
    """
    chat_id = f"@{username}"  # yaha @username ka use ho raha hai
    text = f"Your login OTP is: {otp}"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def save_login_to_sheetdb(username: str):
    """
    ✅ IMPORTANT:
    Yaha payload direct `{"username": "...", "time": "..."}` hai
    `data` key BILKUL NAHI hai.
    """
    login_time = get_ist_time_string()
    payload = {
        "username": username,
        "time": login_time
    }
    resp = requests.post(SHEETDB_URL, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ----------- ROUTES -----------

@app.route("/tg/<path:raw_username>/", methods=["GET"])
def send_otp_route(raw_username):
    """
    URL: /tg/@username/
    Kaam:
      - username clean kare
      - OTP generate kare
      - Telegram pe bheje
      - JSON response de
    """
    username = clean_username(raw_username)

    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return jsonify({
            "error": "Telegram bot token not set on server"
        }), 500

    # OTP generate & store
    otp = generate_otp()
    otp_store[username] = otp

    try:
        telegram_res = send_otp_to_telegram(username, otp)
    except Exception as e:
        return jsonify({
            "error": "Failed to send OTP to Telegram",
            "details": str(e)
        }), 500

    return jsonify({
        "status": "otp_sent",
        "username": f"@{username}",
        "message": "OTP sent to Telegram username",
        "telegram_response_ok": telegram_res.get("ok", False)
    })


@app.route("/tg/<path:raw_username>/<otp>", methods=["GET"])
def verify_otp_route(raw_username, otp):
    """
    URL: /tg/@username/otp
    Kaam:
      - username clean kare
      - otp compare kare
      - sahi hua to SheetDB me username + time save kare
      - JSON me login: successful return kare
    """
    username = clean_username(raw_username)

    # Check OTP
    stored_otp = otp_store.get(username)
    if stored_otp is None:
        return jsonify({
            "login": "failed",
            "reason": "no_otp_for_this_username"
        }), 400

    if otp != stored_otp:
        return jsonify({
            "login": "failed",
            "reason": "invalid_otp"
        }), 400

    # OTP correct hai, use hata do (optional but better)
    del otp_store[username]

    # SheetDB me save karo
    try:
        sheetdb_res = save_login_to_sheetdb(username)
    except Exception as e:
        return jsonify({
            "login": "failed",
            "reason": "sheetdb_error",
            "details": str(e)
        }), 500

    return jsonify({
        "login": "successful",
        "username": f"@{username}",
        "sheetdb_response": sheetdb_res
    })


# Render ke liye entrypoint
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
