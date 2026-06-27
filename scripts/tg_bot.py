"""
Telegram Bot Poller  v1.0
=========================
Runs every 5 min via GitHub Actions.
When you send ANY /command in your Telegram chat, within 5 min this script:
  1. Sends  "Fetching live market data..."  back to you
  2. Runs   nightly_engine.py  (live NSE fetch -> score -> WHY analysis)
  3. nightly_engine.py sends the full analysis to Telegram automatically

Supported commands (any / prefix works):
  /analysis   /verdict   /why   /report   /check   /now
"""

import os
import sys
import time
import subprocess
import requests

TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("TG_CHAT_ID", "")   # restrict to your chat only
API     = f"https://api.telegram.org/bot{TOKEN}"

# How old a command message can be and still be acted on (seconds)
# Set to 480 (8 min) so any 5-min poll window is covered with overlap
MAX_AGE = 480


def send(chat_id: str, text: str) -> None:
    try:
        requests.post(
            f"{API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[bot] sendMessage error: {exc}")


def get_updates() -> list:
    try:
        r = requests.get(
            f"{API}/getUpdates",
            params={"limit": 20, "timeout": 0},
            timeout=15,
        )
        return r.json().get("result", [])
    except Exception as exc:
        print(f"[bot] getUpdates error: {exc}")
        return []


def ack_updates(last_id: int) -> None:
    """Mark all updates up to last_id as processed."""
    try:
        requests.get(
            f"{API}/getUpdates",
            params={"offset": last_id + 1, "limit": 1},
            timeout=10,
        )
    except Exception:
        pass


def run_analysis(chat_id: str) -> None:
    """Fire nightly_engine.py as a subprocess; it sends Telegram msg itself."""
    send(chat_id,
         "⏳ <b>Fetching live market data...</b>\n"
         "Full analysis + WHY section coming in ~60 sec ⚡")

    engine = os.path.join(os.path.dirname(__file__), "nightly_engine.py")
    result = subprocess.run(
        [sys.executable, engine],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("[bot] Engine stderr:", result.stderr[-800:])
        send(chat_id,
             "❌ Analysis failed — check GitHub Actions logs.")
    else:
        print("[bot] Analysis completed and sent to Telegram.")
        if result.stdout:
            print(result.stdout[-400:])


def main() -> None:
    now = time.time()
    updates = get_updates()

    if not updates:
        print("[bot] No updates from Telegram.")
        return

    command_chat  = None
    last_update_id = None

    for u in updates:
        uid = u["update_id"]
        last_update_id = uid

        msg = u.get("message") or u.get("channel_post")
        if not msg:
            continue

        chat_id  = str(msg["chat"]["id"])
        text     = msg.get("text", "")
        msg_time = msg.get("date", 0)

        # Skip stale messages
        if now - msg_time > MAX_AGE:
            continue

        # Skip unauthorised chats (if TG_CHAT_ID env var is set)
        if CHAT_ID and chat_id != CHAT_ID:
            print(f"[bot] Ignoring msg from unknown chat {chat_id}")
            continue

        if text.startswith("/"):
            print(f"[bot] Command '{text}' received from chat {chat_id}")
            command_chat = chat_id
            # Use the most recent command's chat (last write wins)

    # Acknowledge all updates so they don't repeat next poll
    if last_update_id is not None:
        ack_updates(last_update_id)

    if command_chat:
        run_analysis(command_chat)
    else:
        print("[bot] No recent /commands found. Idle.")


if __name__ == "__main__":
    main()
