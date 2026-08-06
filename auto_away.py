"""
Run this ONCE to log in and generate a reusable session string.
Best run in Google Colab (colab.research.google.com) from your phone browser
-- no computer needed.

STEPS (in Colab):
  1. New notebook -> new code cell -> paste:
       !pip install telethon
  2. Run that cell.
  3. New cell -> paste the ENTIRE contents of this file.
  4. Run it. It will ask for:
       - your api_id / api_hash (from https://my.telegram.org)
       - your phone number
       - the login code Telegram sends you
       - your 2FA password, if you have one set
  5. It prints a long string. COPY THAT STRING.
  6. Paste it into Render as an environment variable named TG_SESSION_STRING.

You only need to do this once. Do NOT share the printed string with anyone --
it's equivalent to your Telegram login.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("api_id: "))
api_hash = input("api_hash: ")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session_string = client.session.save()
    print("\n\n=== COPY EVERYTHING BELOW THIS LINE ===\n")
    print(session_string)
    print("\n=== COPY EVERYTHING ABOVE THIS LINE ===")
