"""
Telegram Personal-Account Auto-Away Responder + Math Calculator
-----------------------------------------------
Logs in as YOUR Telegram account (not a bot) using Telethon, and auto-replies
to incoming private messages while you're in "away mode".

TOGGLE AWAY MODE:
  From ANY device, open your own "Saved Messages" chat and send:
    .away    -> turns away mode ON
    .back    -> turns away mode OFF

  While away mode is ON, your account is also reported to Telegram as
  OFFLINE (not just auto-replying while still showing "online"). This is
  refreshed periodically because Telegram can silently flip a connected
  session back to "online". Note: if you personally open Telegram on any
  device while away mode is on, that session will show online too --
  offline status only holds while you don't touch the app yourself.

MATH CALCULATOR:
  Works in ANY private chat that messages you (no longer limited to one
  specific chat). When a message contains lines like:
    Sg iver = 200*85
    wound spray = 5*730
  Your account automatically replies with results in format:
    Sg iver = 200 * 85 = 17,000
    wound spray = 5 * 730 = 3,650
    Total = 20,650

AUTO-FORWARD:
  Forward ALL messages from specific people to another chat.
  Supports multiple people via comma-separated IDs or usernames.
  FORWARD_ALL_FROM=123456789,987654321,@someuser

MEDIA ORGANIZER:
  Any incoming photo/document/video is downloaded to MEDIA_SAVE_DIR.

DAILY DIGEST:
  At DIGEST_TIME each day, a summary is sent to your Saved Messages.

ENV VARS (.env):
  TG_API_ID=
  TG_API_HASH=
  TG_SESSION_STRING=
  AWAY_MESSAGE=Hi {name}! Chalie is currently unavailable.
  FORWARD_TARGET=
  FORWARD_ALL_FROM=id1,id2,@username3
  FORWARD_KEYWORDS=urgent,invoice
  FORWARD_ON_MEDIA=true
  MEDIA_SAVE_DIR=./saved_media
  DIGEST_ENABLED=true
  DIGEST_TIME=20:00
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
from telethon.tl.functions.account import UpdateStatusRequest

load_dotenv()

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ.get("TG_SESSION_STRING", "")

# English only, first name is always used (no @username)
AWAY_MESSAGE = os.environ.get(
    "AWAY_MESSAGE",
    "Hi {name}! Chalie is currently not active, will reply soon."
)

# --- Auto-forward config ---
FORWARD_TARGET = os.environ.get("FORWARD_TARGET", "")
FORWARD_KEYWORDS = [
    k.strip().lower() for k in os.environ.get("FORWARD_KEYWORDS", "").split(",") if k.strip()
]
FORWARD_ON_MEDIA = os.environ.get("FORWARD_ON_MEDIA", "false").lower() == "true"

# Supports multiple IDs/usernames comma-separated
# e.g. FORWARD_ALL_FROM=123456789,987654321,@someuser
FORWARD_ALL_FROM_RAW = [
    x.strip() for x in os.environ.get("FORWARD_ALL_FROM", "").split(",") if x.strip()
]

# --- Media organizer config ---
MEDIA_SAVE_DIR = os.environ.get("MEDIA_SAVE_DIR", "./saved_media")

# --- Daily digest config ---
DIGEST_ENABLED = os.environ.get("DIGEST_ENABLED", "false").lower() == "true"
DIGEST_TIME = os.environ.get("DIGEST_TIME", "20:00")

# In-memory state
is_away = False
already_replied_to = set()
forward_target_entity = None
forward_all_from_ids = set()  # set of resolved numeric IDs

daily_stats = {}
last_digest_date = None

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


# --- Offline-status control ---

async def set_telegram_offline(offline: bool):
    """Explicitly tell Telegram's servers our online/offline presence."""
    try:
        await client(UpdateStatusRequest(offline=offline))
        print(f"[presence] Set account status -> {'offline' if offline else 'online'}")
    except Exception as e:
        print(f"[presence] Failed to set offline={offline}: {e}")


async def offline_keepalive_loop():
    """
    While away mode is on, Telegram can silently flip a connected session
    back to 'online' (e.g. on any activity). Re-assert offline periodically
    to keep the account showing offline for as long as away mode is active.
    """
    while True:
        if is_away:
            await set_telegram_offline(True)
        await asyncio.sleep(30)


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


def build_away_message(sender) -> str:
    # Always use first name (or fallback display name) -- never @username
    if isinstance(sender, User) and sender.first_name:
        name = sender.first_name
    else:
        name = sender_display_name(sender)
    try:
        return AWAY_MESSAGE.format(name=name)
    except (KeyError, IndexError):
        return AWAY_MESSAGE


def safe_filename_part(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', "_", name)[:40]


# --- Resolve helpers ---

async def resolve_from_dialogs(target_id: int):
    """Search dialogs to find and cache a user by ID."""
    try:
        async for dialog in client.iter_dialogs():
            if dialog.entity.id == target_id:
                return dialog.entity
    except Exception as e:
        print(f"[resolve] Error iterating dialogs: {e}")
    return None


async def resolve_forward_target():
    global forward_target_entity
    if not FORWARD_TARGET:
        print("[forward] No FORWARD_TARGET set — forwarding disabled.")
        return
    try:
        target_id = int(FORWARD_TARGET)
        # Try dialogs first
        entity = await resolve_from_dialogs(target_id)
        if entity:
            forward_target_entity = entity
            print(f"[forward] Forward target resolved from dialogs: {sender_display_name(entity)} → ID {target_id}")
            return
        # Fallback to get_entity
        forward_target_entity = await client.get_entity(target_id)
        print(f"[forward] Forward target resolved: {target_id}")
    except ValueError:
        # It's a username not an int
        try:
            forward_target_entity = await client.get_entity(FORWARD_TARGET)
            print(f"[forward] Forward target resolved: {FORWARD_TARGET}")
        except Exception as e:
            print(f"[forward] Could not resolve FORWARD_TARGET {FORWARD_TARGET}: {e}")
    except Exception as e:
        print(f"[forward] Could not resolve FORWARD_TARGET {FORWARD_TARGET}: {e}")


async def resolve_forward_all_from():
    """
    Resolve multiple FORWARD_ALL_FROM entries.
    Supports numeric IDs and @usernames, comma-separated.
    e.g. FORWARD_ALL_FROM=123456789,987654321,@someuser
    """
    global forward_all_from_ids

    if not FORWARD_ALL_FROM_RAW:
        print("[forward] No FORWARD_ALL_FROM set.")
        return

    print(f"[forward] Resolving {len(FORWARD_ALL_FROM_RAW)} FORWARD_ALL_FROM entries...")

    for entry in FORWARD_ALL_FROM_RAW:
        try:
            # Numeric ID
            if entry.lstrip("-").isdigit():
                target_id = int(entry)
                # Try dialogs first (works for users without username)
                entity = await resolve_from_dialogs(target_id)
                if entity:
                    forward_all_from_ids.add(target_id)
                    print(f"[forward] ✅ Resolved from dialogs: {sender_display_name(entity)} → ID {target_id}")
                else:
                    # Set directly — will match by ID in message handler
                    forward_all_from_ids.add(target_id)
                    print(f"[forward] ⚠️ Not found in dialogs, set directly: {target_id} (will forward if they message you)")
            else:
                # Username
                entity = await client.get_entity(entry)
                forward_all_from_ids.add(entity.id)
                print(f"[forward] ✅ Resolved username: {entry} → ID {entity.id}")

        except Exception as e:
            print(f"[forward] ❌ Could not resolve '{entry}': {e}")

    print(f"[forward] Forward-all active for IDs: {forward_all_from_ids}")


# --- Handlers ---

@client.on(events.NewMessage(outgoing=True, chats="me"))
async def toggle_handler(event):
    global is_away, already_replied_to
    text = (event.raw_text or "").strip().lower()

    if text == ".away":
        is_away = True
        already_replied_to.clear()
        await set_telegram_offline(True)
        await event.reply(f"✅ Away mode ON ({datetime.now().strftime('%H:%M')})")
        print("[state] Away mode enabled")

    elif text == ".back":
        is_away = False
        already_replied_to.clear()
        await set_telegram_offline(False)
        await event.reply(f"✅ Away mode OFF ({datetime.now().strftime('%H:%M')})")
        print("[state] Away mode disabled")


@client.on(events.NewMessage(incoming=True))
async def message_handler(event):
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
    entry["name"] = name

    # --- Media organizer ---
    if event.message.media:
        try:
            os.makedirs(MEDIA_SAVE_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefix = f"{safe_filename_part(name)}_{ts}"
            saved_path = await client.download_media(
                event.message, file=os.path.join(MEDIA_SAVE_DIR, prefix)
            )
            if saved_path:
                print(f"[media] Saved from {name} -> {saved_path}")
        except Exception as e:
            print(f"[media] Failed to save media from {name}: {e}")

    # --- Auto-forward ---
    should_forward = False
    if forward_target_entity is not None:
        # Forward ALL from specific people (multiple supported)
        if forward_all_from_ids and sender_id in forward_all_from_ids:
            should_forward = True
            print(f"[forward] Matched FORWARD_ALL_FROM: {name} (ID {sender_id})")
        elif event.message.media and FORWARD_ON_MEDIA:
            should_forward = True
        elif FORWARD_KEYWORDS and message_text:
            lowered = message_text.lower()
            if any(kw in lowered for kw in FORWARD_KEYWORDS):
                should_forward = True

    if should_forward:
        try:
            await client.forward_messages(forward_target_entity, event.message)
            print(f"[forward] ✅ Forwarded message from {name} to target")
        except Exception as e:
            print(f"[forward] ❌ Failed to forward from {name}: {e}")

    # --- Math feature (works in ANY private chat) ---
    calc_result = parse_and_calculate(message_text)
    if calc_result:
        await event.reply(calc_result)
        print(f"[math] Replied with calculation to {name}")
        return

    # --- Auto-away reply ---
    if not is_away:
        return
    if sender_id in already_replied_to:
        return

    await event.reply(build_away_message(sender))
    already_replied_to.add(sender_id)
    print(f"[auto-reply] Sent away message to {name}")


# --- Daily digest loop ---

async def digest_loop():
    global last_digest_date, daily_stats

    if not DIGEST_ENABLED:
        print("[digest] Daily digest disabled.")
        return

    try:
        target_hour, target_minute = (int(p) for p in DIGEST_TIME.split(":"))
    except Exception:
        print(f"[digest] Invalid DIGEST_TIME '{DIGEST_TIME}'. Digest disabled.")
        return

    print(f"[digest] Daily digest enabled, will send at {DIGEST_TIME} each day.")

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

        await asyncio.sleep(30)


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
        fwd_ids = ",".join(str(i) for i in forward_all_from_ids) or "none"
        self.wfile.write(
            f"OK - status: {status} - forward_all_from: {fwd_ids}".encode()
        )

    def log_message(self, format, *args):
        pass


def start_ping_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    print(f"[keep-alive] HTTP ping server on port {port}")
    server.serve_forever()


# --- Main ---

async def main():
    threading.Thread(target=start_ping_server, daemon=True).start()

    print("Logging in...")
    await client.start()
    print("Logged in. Listening for messages.")
    print("Send '.away' or '.back' to your Saved Messages to toggle.")

    if not SESSION_STRING:
        print("\n[!] Save this session string to TG_SESSION_STRING in your .env:\n")
        print(client.session.save())
        print()

    await resolve_forward_target()
    await resolve_forward_all_from()

    if is_away:
        await set_telegram_offline(True)

    asyncio.create_task(offline_keepalive_loop())

    if DIGEST_ENABLED:
        asyncio.create_task(digest_loop())

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
