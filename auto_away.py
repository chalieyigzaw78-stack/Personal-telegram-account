"""
Telegram Personal-Account Auto-Away Responder + Math Calculator
-----------------------------------------------
Logs in as YOUR Telegram account (not a bot) using Telethon, and auto-replies
to incoming private messages while you're in "away mode".

TOGGLE AWAY MODE:
  From ANY device, open your own "Saved Messages" chat and send:
    .away    -> turns away mode ON
    .back    -> turns away mode OFF

MATH CALCULATOR:
  When your specific chat partner sends a list of calculations like:
    Sg iver = 200*85
    wound spray = 5*730
  Your account automatically replies with results in format:
    Sg iver = 200 * 85 = 17,000
    wound spray = 5 * 730 = 3,650
    Total = 20,650

SETUP:
  1. Get API credentials (free) at https://my.telegram.org
  2. pip install telethon python-dotenv
  3. Create a .env file next to this script
  4. Run: python auto_away.py
"""

import os
import re
import asyncio
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

load_dotenv()

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ.get("TG_SESSION_STRING", "")

AWAY_MESSAGE = os.environ.get(
    "AWAY_MESSAGE",
    "Hi! Chalie is currently unavailable.\nሰላም! @username ቻሌ አሁን አይገኝም። በቅርቡ ይመልስልዎታል።"
)

# --- Username of the chat partner who gets math replies ---
# Change this to the username you want (without @)
MATH_CHAT_USERNAME = os.environ.get("MATH_CHAT_USERNAME", "")

# In-memory state
is_away = False
already_replied_to = set()
math_chat_id = None  # resolved at startup

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


# --- Math calculator ---

def parse_and_calculate(text: str):
    """
    Parses lines like:
      Sg iver = 200*85
      wound spray=5*730
      plain: 200*85
    Returns formatted result string or None if no math found.
    """
    lines = text.strip().splitlines()
    results = []
    grand_total = 0
    found_any = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Match: optional label = number * number
        # Supports: label=200*85 or label = 200*85 or just 200*85
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
            # Try plain addition, subtraction, division too
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
                # Not a math line — include as-is so context is preserved
                results.append(line)

    if not found_any:
        return None

    results.append("")
    results.append(f"Total = {grand_total:,}")
    return "\n".join(results)


# --- Resolve math chat ID at startup ---

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


# --- Handlers ---

@client.on(events.NewMessage(outgoing=True, chats="me"))
async def toggle_handler(event):
    """Listen for commands sent to Saved Messages."""
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

    await resolve_math_chat()

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
