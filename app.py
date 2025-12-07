import os
import random
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

from flask import Flask, request, jsonify

app = Flask(__name__)

# ==== CONFIG ====
BOT_TOKEN = os.environ.get(
    "BOT_TOKEN",
    "8137191289:AAHDXMoIf9TqEIpQ85z8VpUsnjnSuSZOLnk"  # yahan naya token daal dena
)

# Apne Render wale URL se isko change karna
BASE_URL = os.environ.get("BASE_URL", "https://example.onrender.com")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
SHEETDB_URL = "https://sheetdb.io/api/v1/17v254fdw500k"

OTP_TTL_MINUTES = 5  # OTP validity
pending_otps = {}    # { chat_id: {otp, expires} }


# ==== HELPERS ====


def send_telegram_message(chat_id, text):
    """Telegram pe simple text message bhejne ka helper."""
    resp = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )
    resp.raise_for_status()
    return resp.json()


def get_chat_info(chat_id):
    """Telegram se chat ke details (username, first_name, ...) nikalne ke liye."""
    resp = requests.get(
        f"{TELEGRAM_API}/getChat",
        params={"chat_id": chat_id}
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", {})


def get_login_key_from_chat(chat: dict) -> str:
    """
    SheetDB mein username column me kya save kare:
    - agar Telegram username hai -> '@username'
    - warna chat_id string
    """
    username = chat.get("username")
    if username:
        return f"@{username}"
    return str(chat.get("id"))


def get_ist_time_string():
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist)
    return now_ist.strftime("%Y-%m-%d %H:%M:%S")


def sheetdb_create_login_row(username_key: str):
    """SheetDB me direct username + time POST karo (without data{})."""
    payload = {
        "username": username_key,
        "time": get_ist_time_string(),
    }
    r = requests.post(SHEETDB_URL, json=payload)
    r.raise_for_status()
    return r.json()


def sheetdb_is_logged_in(username_key: str) -> bool:
    """SheetDB se check kare ki ye user login hai ya nahi."""
    r = requests.get(
        f"{SHEETDB_URL}/search",
        params={"username": username_key}
    )
    r.raise_for_status()
    data = r.json()
    return len(data) > 0


def sheetdb_delete_user(username_key: str) -> int:
    """
    SheetDB se user delete kare:
    DELETE /api/v1/{API_ID}/username/{VALUE}
    """
    url = f"{SHEETDB_URL}/username/{quote_plus(username_key)}"
    r = requests.delete(url)
    r.raise_for_status()
    data = r.json()
    # response example: { "deleted": 1 }
    return int(data.get("deleted", 0))


# ==== FLASK ROUTES ====


@app.route("/", methods=["GET"])
def index():
    return "Telegram OTP login backend is running."


@app.route("/tg/<chat_id>", methods=["GET"])
def send_otp(chat_id):
    """Step 1: URL hit -> user ke chat_id pe OTP send karo."""
    otp = f"{random.randint(0, 999999):06d}"
    expires = datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)

    pending_otps[chat_id] = {"otp": otp, "expires": expires}

    try:
        send_telegram_message(
            chat_id,
            f"🔐 Login OTP: {otp}\n"
            f"Valid for {OTP_TTL_MINUTES} minutes."
        )
    except Exception as e:
        return jsonify({
            "error": "Failed to send OTP to Telegram",
            "details": str(e),
        }), 400

    return jsonify({"otp_sent": True, "chat_id": chat_id})


@app.route("/tg/<chat_id>/<otp>", methods=["GET"])
def verify_otp(chat_id, otp):
    """Step 2: OTP verify karo, success pe SheetDB me row create karo."""
    otp_entry = pending_otps.get(chat_id)

    if not otp_entry:
        return jsonify({"login": "failed", "reason": "no_otp_or_expired"}), 400

    if datetime.utcnow() > otp_entry["expires"]:
        pending_otps.pop(chat_id, None)
        return jsonify({"login": "failed", "reason": "otp_expired"}), 400

    if otp_entry["otp"] != otp:
        return jsonify({"login": "failed", "reason": "wrong_otp"}), 400

    try:
        # Telegram se chat info le aao -> username ya chat_id
        chat = get_chat_info(chat_id)
        username_key = get_login_key_from_chat(chat)

        # SheetDB me username + time store karo
        sheetdb_create_login_row(username_key)

        pending_otps.pop(chat_id, None)
        return jsonify({"login": "successful", "username": username_key})
    except Exception as e:
        return jsonify({
            "login": "failed",
            "error": str(e),
        }), 500


# ==== TELEGRAM WEBHOOK (commands) ====


@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """
    Telegram webhook:
      - /start
      - /chat_id
      - /login_status
      - /delete_account
    """
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")

    if not message:
        return "ok"

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return "ok"

    # SheetDB key decide karo (username ya chat_id)
    username_key = get_login_key_from_chat(chat)

    try:
        if text.startswith("/start"):
            login_url = f"{BASE_URL}/tg/{chat_id}"
            send_telegram_message(
                chat_id,
                "👋 Welcome!\n\n"
                f"Your chat ID: `{chat_id}`\n\n"
                "Login steps:\n"
                f"1️⃣ Browser me open kare: {login_url}\n"
                "2️⃣ Bot tumhe OTP send karega\n"
                "3️⃣ OTP verify URL open karo (website se call hoga)\n"
                "4️⃣ Login ho jaoge ✅"
            )

        elif text.startswith("/chat_id"):
            login_url = f"{BASE_URL}/tg/{chat_id}"
            send_telegram_message(
                chat_id,
                f"🆔 Your chat ID: `{chat_id}`\n\n"
                f"Login URL: {login_url}"
            )

        elif text.startswith("/login_status"):
            logged_in = sheetdb_is_logged_in(username_key)
            if logged_in:
                send_telegram_message(
                    chat_id,
                    f"✅ Login status: **LOGGED IN**\nUsername key: {username_key}"
                )
            else:
                send_telegram_message(
                    chat_id,
                    "❌ Login status: NOT LOGGED IN\n"
                    "Pehele login karne ke liye /start se URL lo aur browser me open karo."
                )

        elif text.startswith("/delete_account"):
            deleted = sheetdb_delete_user(username_key)
            if deleted > 0:
                send_telegram_message(
                    chat_id,
                    f"🗑️ Account deleted from SheetDB.\nUsername key: {username_key}"
                )
            else:
                send_telegram_message(
                    chat_id,
                    "⚠️ SheetDB me tumhara koi record nahi mila."
                )

        else:
            send_telegram_message(
                chat_id,
                "Available commands:\n"
                "/start - login info aur URL\n"
                "/chat_id - apna chat ID aur login URL\n"
                "/login_status - SheetDB se login status check\n"
                "/delete_account - SheetDB se apna record delete"
            )

    except Exception as e:
        # Agar kuch bhi error aaye toh bhi webhook 200 return kare
        try:
            send_telegram_message(
                chat_id,
                f"⚠️ Internal error: {e}"
            )
        except Exception:
            pass

    return "ok"


# ==== ENTRYPOINT ====


if __name__ == "__main__":
    # Render ke liye PORT env se lete hai
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
