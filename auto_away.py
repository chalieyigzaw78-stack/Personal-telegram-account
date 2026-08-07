"""
Telegram Personal-Account Assistant
------------------------------------
Logs in as YOUR Telegram account (not a bot) using Telethon.

FEATURES:
  1. Auto-away responder   — toggle with .away / .back in Saved Messages
  2. Math calculator        — for one designated chat partner
  3. Auto-forward / mirror  — forward matching messages to another chat
  4. Media auto-organizer   — save incoming photos/docs to a local folder
  5. Daily digest           — end-of-day summary of who messaged you, sent to Saved Messages

TOGGLE AWAY MODE (send from Saved Messages, any device):
    .away    -> turns away mode ON
    .back    -> turns away mode OFF

MATH CALCULATOR:
  When MATH_CHAT_USERNAME sends lines like:
    Sg iver = 200*85
    wound spray = 5*730
  You get back:
    Sg iver = 200 * 85 = 17,000
    wound spray = 5 * 730 = 3,650
    Total = 20,650

AUTO-FORWARD:
  Any incoming private message that either:
    - contains one of FORWARD_KEYWORDS (comma-separated, case-insensitive), or
    - includes media and FORWARD_ON_MEDIA=true
  gets forwarded as-is to FORWARD_TARGET (username, phone-linked id, or numeric chat id).

MEDIA ORGANIZER:
  Any incoming photo/document/video is downloaded to MEDIA_SAVE_DIR,
  named "<sender>_<timestamp>.<ext>".

DAILY DIGEST:
  At DIGEST_TIME (HH:MM, local server time) each day, a summary of the day's
  incoming private messages (sender + count) is sent to your Saved Messages,
  then counters reset.

SETUP:
  1. Get API credentials (free) at https://my.telegram.org
  2. pip install telethon python-dotenv
  3. Create a .env file next to this script (see ENV VARS below)
  4. Run: python auto_away.py

ENV VARS (.env):
  TG_API_ID=...
  TG_API_HASH=...
  TG_SESSION_STRING=...              (leave blank first run, script will print one)
  AWAY_MESSAGE=...                   (optional, has a default)
  MATH_CHAT_USERNAME=someuser        (optional, without @)
  FORWARD_TARGET=someuser_or_id      (optional — enables forwarding)
  FORWARD_KEYWORDS=urgent,invoice    (optional, comma-separated)
  FORWARD_ON_MEDIA=true              (optional, default false)
  MEDIA_SAVE_DIR=./saved_media       (optional, default ./saved_media)
  DIGEST_ENABLED=true                (optional, default false)
  DIGEST_TIME=20:00                  (optional, default 20:00, 24h HH:MM)
"""

import os
import re
import asyncio
import threading
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User

load_dotenv()

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ.get("TG_SESSION_STRING", "")

AWAY_MESSAGE = os.environ.get(
    "AWAY_MESSAGE",
    "Hi! Chalie is currently unavailable.\nሰላም! @username ቻሌ አሁን አይገኝም። በቅርቡ ይመልስልዎታል።"
)

# --- Math calculator target ---
MATH_CHAT_USERNAME = os.environ.get("MATH_CHAT_USERNAME", "")

# --- Auto-forward config ---
FORWARD_TARGET = os.environ.get("FORWARD_TARGET", "")
FORWARD_KEYWORDS = [
    k.strip().lower() for k in os.environ.get("FORWARD_KEYWORDS", "").split(",") if k.strip()
]
FORWARD_ON_MEDIA = os.environ.get("FORWARD_ON_MEDIA", "false").lower() == "true"

# --- Media organizer config ---
MEDIA_SAVE_DIR = os.environ.get("MEDIA_SAVE_DIR", "./saved_media")

# --- Daily digest config ---
DIGEST_ENABLED = os.environ.get("DIGEST_ENABLED", "false").lower() == "true"
DIGEST_TIME = os.environ.get("DIGEST_TIME", "20:00")  # "HH:MM"

# In-memory state
is_away = False
already_replied_to = set()
math_chat_id = None
forward_target_entity = None

# sender_id -> {"name": str, "count": int}
daily_stats = {}
last_digest_date = None

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


# --- Math calculator ---

def parse_and_calculate(text: str):
    lines = text.strip().splitlines()
    results = []
    grand_total = 0
    found_any = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        match = re.match(
            r'^(.*?)=?\s*(\d[\d,]*)\s*[\*x×]\s*(\d[\d,]*)\s*$',
            line,
            re.IGNORECASE
        )

        if match:
            label = match.group(1).strip().rstrip("=").strip()
            num1 = int(match.group(2).replace(",", ""))
            num2 = int(match.group(3).replace(",", ""))
            result = num1 * num2
            grand_total += result
            found_any = True

            if label:
                results.append(f"{label} = {num1:,} * {num2:,} = {result:,}")
            else:
                results.append(f"{num1:,} * {num2:,} = {result:,}")
        else:
            plain_match = re.match(
                r'^(.*?)=?\s*(\d[\d,]*)\s*([+\-÷/])\s*(\d[\d,]*)\s*$',
                line
            )
            if plain_match:
                label = plain_match.group(1).strip().rstrip("=").strip()
                num1 = int(plain_match.group(2).replace(",", ""))
                op = plain_match.group(3)
                num2 = int(plain_match.group(4).replace(",", ""))

                if op == "+":
                    result = num1 + num2
                    op_display = "+"
                elif op == "-":
                    result = num1 - num2
                    op_display = "-"
                elif op in ["÷", "/"]:
                    result = round(num1 / num2, 2) if num2 != 0 else 0
                    op_display = "÷"
                else:
                    continue

                grand_total += result
                found_any = True

                if label:
                    results.append(f"{label} = {num1:,} {op_display} {num2:,} = {result:,}")
                else:
                    results.append(f"{num1:,} {op_display} {num2:,} = {result:,}")
            else:
                results.append(line)

    if not found_any:
        return None

    results.append("")
    results.append(f"Total = {grand_total:,}")
    return "\n".join(results)


# --- Helpers ---

def sender_display_name(sender) -> str:
    if isinstance(sender, User):
        name = " ".join(filter(None, [sender.first_name, sender.last_name])).strip()
        if not name:
            name = sender.username or f"id{sender.id}"
        return name
    return getattr(sender, "title", None) or f"id{getattr(sender, 'id', 'unknown')}"


def safe_filename_part(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', "_", name)[:40]


async def resolve_math_chat():
    global math_chat_id
    if not MATH_CHAT_USERNAME:
        print("[math] No MATH_CHAT_USERNAME set — math feature disabled.")
        return
    try:
        entity = await client.get_entity(MATH_CHAT_USERNAME)
        math_chat_id = entity.id
        print(f"[math] Math chat resolved: {MATH_CHAT_USERNAME} → ID {math_chat_id}")
    except Exception as e:
        print(f"[math] Could not resolve username {MATH_CHAT_USERNAME}: {e}")


async def resolve_forward_target():
    global forward_target_entity
    if not FORWARD_TARGET:
        print("[forward] No FORWARD_TARGET set — auto-forward disabled.")
        return
    try:
        forward_target_entity = await client.get_entity(FORWARD_TARGET)
        print(f"[forward] Forward target resolved: {FORWARD_TARGET}")
    except Exception as e:
        print(f"[forward] Could not resolve FORWARD_TARGET {FORWARD_TARGET}: {e}")


def ensure_media_dir():
    if not os.path.isdir(MEDIA_SAVE_DIR):
        os.makedirs(MEDIA_SAVE_DIR, exist_ok=True)
        print(f"[media] Created media save directory: {MEDIA_SAVE_DIR}")


# --- Handlers ---

@client.on(events.NewMessage(outgoing=True, chats="me"))
async def toggle_handler(event):
    global is_away, already_replied_to
    text = (event.raw_text or "").strip().lower()

    if text == ".away":
        is_away = True
        already_replied_to.clear()
        await event.reply(f"✅ Away mode ON ({datetime.now().strftime('%H:%M')})")
        print("[state] Away mode enabled")

    elif text == ".back":
        is_away = False
        already_replied_to.clear()
        await event.reply(f"✅ Away mode OFF ({datetime.now().strftime('%H:%M')})")
        print("[state] Away mode disabled")


@client.on(events.NewMessage(incoming=True))
async def message_handler(event):
    """Handle all incoming private messages."""
    if not event.is_private:
        return

    sender = await event.get_sender()
    if sender is None or getattr(sender, "bot", False):
        return

    sender_id = sender.id
    message_text = event.raw_text or ""

    # --- Daily stats tracking ---
    name = sender_display_name(sender)
    entry = daily_stats.setdefault(sender_id, {"name": name, "count": 0})
    entry["count"] += 1
    entry["name"] = name  # keep name fresh

    # --- Media organizer ---
    if event.message.media:
        try:
            ensure_media_dir()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefix = f"{safe_filename_part(name)}_{ts}"
            saved_path = await client.download_media(
                event.message, file=os.path.join(MEDIA_SAVE_DIR, prefix)
            )
            if saved_path:
                print(f"[media] Saved media from {name} -> {saved_path}")
        except Exception as e:
            print(f"[media] Failed to save media from {name}: {e}")

    # --- Auto-forward ---
    should_forward = False
    if forward_target_entity is not None:
        if event.message.media and FORWARD_ON_MEDIA:
            should_forward = True
        elif FORWARD_KEYWORDS and message_text:
            lowered = message_text.lower()
            if any(kw in lowered for kw in FORWARD_KEYWORDS):
                should_forward = True

    if should_forward:
        try:
            await client.forward_messages(forward_target_entity, event.message)
            print(f"[forward] Forwarded message from {name} to {FORWARD_TARGET}")
        except Exception as e:
            print(f"[forward] Failed to forward message from {name}: {e}")

    # --- Math feature: only for specific chat partner ---
    if math_chat_id and sender_id == math_chat_id:
        calc_result = parse_and_calculate(message_text)
        if calc_result:
            await event.reply(calc_result)
            print(f"[math] Replied with calculation to {MATH_CHAT_USERNAME}")
            return  # don't also send away message

    # --- Auto-away reply ---
    if not is_away:
        return

    if sender_id in already_replied_to:
        return

    await event.reply(AWAY_MESSAGE)
    already_replied_to.add(sender_id)
    print(f"[auto-reply] Sent away message to {sender_id}")


# --- Daily digest loop ---

async def digest_loop():
    global last_digest_date, daily_stats

    if not DIGEST_ENABLED:
        print("[digest] Daily digest disabled (DIGEST_ENABLED=false).")
        return

    try:
        target_hour, target_minute = (int(p) for p in DIGEST_TIME.split(":"))
    except Exception:
        print(f"[digest] Invalid DIGEST_TIME '{DIGEST_TIME}', expected HH:MM. Digest disabled.")
        return

    print(f"[digest] Daily digest enabled, will send around {DIGEST_TIME} each day.")

    while True:
        now = datetime.now()
        today = date.today()

        if (
            now.hour == target_hour
            and now.minute == target_minute
            and last_digest_date != today
        ):
            await send_digest()
            last_digest_date = today

        await asyncio.sleep(30)  # check twice a minute


async def send_digest():
    if not daily_stats:
        summary = "📊 Daily digest: no incoming private messages today."
    else:
        lines = ["📊 Daily digest — incoming private messages:"]
        total = 0
        for stats in sorted(daily_stats.values(), key=lambda s: -s["count"]):
            lines.append(f"  • {stats['name']}: {stats['count']}")
            total += stats["count"]
        lines.append("")
        lines.append(f"Total messages: {total}")
        lines.append(f"Unique senders: {len(daily_stats)}")
        summary = "\n".join(lines)

    try:
        await client.send_message("me", summary)
        print("[digest] Sent daily digest to Saved Messages")
    except Exception as e:
        print(f"[digest] Failed to send digest: {e}")

    daily_stats.clear()


# --- HTTP keep-alive server ---

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        status = "AWAY" if is_away else "ACTIVE"
        self.wfile.write(f"OK - bot running - status: {status}".encode())

    def log_message(self, format, *args):
        pass


def start_ping_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    print(f"[keep-alive] HTTP ping server listening on port {port}")
    server.serve_forever()


# --- Main ---

async def main():
    threading.Thread(target=start_ping_server, daemon=True).start()

    print("Logging in...")
    await client.start()
    print("Logged in. Listening for messages.")
    print("Send '.away' or '.back' to your Saved Messages to toggle.")

    if not SESSION_STRING:
        print("\n[!] No TG_SESSION_STRING was set. Save this string to your .env "
              "so you don't have to log in again:\n")
        print(client.session.save())
        print()

    await resolve_math_chat()
    await resolve_forward_target()

    if DIGEST_ENABLED:
        asyncio.create_task(digest_loop())

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
