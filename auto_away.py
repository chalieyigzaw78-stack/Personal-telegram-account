"""
Telegram Personal-Account Auto-Away Responder
-----------------------------------------------
Logs in as YOUR Telegram account (not a bot) using Telethon, and auto-replies
to incoming private messages while you're in "away mode".

TOGGLE AWAY MODE:
  From ANY device, open your own "Saved Messages" chat and send:
    .away    -> turns away mode ON
    .back    -> turns away mode OFF

BEHAVIOR:
  - While away, each contact gets ONE auto-reply per "away session"
    (won't spam them if they send multiple messages).
  - Sending any normal message yourself elsewhere does NOT auto-disable away
    mode (only ".back" does) -- keeps behavior predictable.
  - Group chats and channels are ignored; only 1:1 private messages trigger
    the auto-reply.

SETUP:
  1. Get API credentials (free) at https://my.telegram.org -> "API development tools"
  2. pip install telethon python-dotenv
  3. Create a .env file next to this script (see .env.example)
  4. Run: python auto_away.py
     First run will ask for your phone number + login code (sent via Telegram) 
     to create a local session file. After that it logs in automatically.

RENDER FREE WEB SERVICE + UPTIMEROBOT:
  This script also runs a tiny HTTP server on $PORT that just replies "OK".
  Render's free Web Service tier sleeps after 15 minutes with no HTTP
  traffic -- point UptimeRobot (or any uptime pinger) at your Render URL
  every 5-10 minutes to keep it awake 24/7, same pattern as your other
  projects.

NOTE ON TERMS OF SERVICE:
  Automating a personal account sits outside Telegram's official Bot API.
  Keep the behavior light (a single polite auto-reply) rather than bulk
  messaging, to stay well clear of spam-detection systems.
"""

import os
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

# Portable session (works well for cloud hosting like Render, no local file needed).
# Generate this once using generate_session.py (e.g. in Google Colab), then paste
# it into Render's environment variables as TG_SESSION_STRING.
SESSION_STRING = os.environ.get("TG_SESSION_STRING", "")

AWAY_MESSAGE = os.environ.get(
    "AWAY_MESSAGE",
    "Hey, I'm away right now and will get back to you as soon as I can!"
)

# In-memory state
is_away = False
already_replied_to = set()  # user_ids replied to during current away session

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


@client.on(events.NewMessage(outgoing=True, chats="me"))
async def toggle_handler(event):
    """Listen for commands sent to Saved Messages (chat with yourself)."""
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
async def auto_reply_handler(event):
    """Auto-reply to private incoming messages while away."""
    if not is_away:
        return

    # Only handle 1:1 private chats, never groups/channels
    if not event.is_private:
        return

    sender = await event.get_sender()
    if sender is None or getattr(sender, "bot", False):
        return  # skip bots

    sender_id = sender.id

    if sender_id in already_replied_to:
        return  # already told this person once this away-session

    await event.reply(AWAY_MESSAGE)
    already_replied_to.add(sender_id)
    print(f"[auto-reply] Sent away message to {sender_id}")


class PingHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler so Render sees a live web service and
    UptimeRobot has something to ping."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        status = "AWAY" if is_away else "ACTIVE"
        self.wfile.write(f"OK - bot running - status: {status}".encode())

    def log_message(self, format, *args):
        pass  # silence default request logging, keep console clean


def start_ping_server():
    port = int(os.environ.get("PORT", 10000))  # Render sets $PORT automatically
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    print(f"[keep-alive] HTTP ping server listening on port {port}")
    server.serve_forever()


async def main():
    # Run the tiny HTTP server in a background thread so it doesn't block
    # the Telegram event loop.
    threading.Thread(target=start_ping_server, daemon=True).start()

    print("Logging in...")
    await client.start()
    print("Logged in. Listening for messages.")
    print("Send '.away' or '.back' to your Saved Messages to toggle.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
