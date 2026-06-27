"""
Telegram Bot Poller  v1.1
=========================
Runs every 5 min via GitHub Actions.
Send /analysis (or any /command) -> within 5 min you get full WHY analysis.
"""

import os, sys, time, subprocess, requests

TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")   # matches nightly.yml secret name
API     = f"https://api.telegram.org/bot{TOKEN}"
MAX_AGE = 600   # 10 min window to catch commands even with poll delays


def send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                      timeout=10)
    except Exception as e:
        print(f"[bot] sendMessage error: {e}")


def get_updates():
    try:
        r = requests.get(f"{API}/getUpdates", params={"limit": 20, "timeout": 0}, timeout=15)
        return r.json().get("result", [])
    except Exception as e:
        print(f"[bot] getUpdates error: {e}")
        return []


def ack(last_id):
    try:
        requests.get(f"{API}/getUpdates", params={"offset": last_id + 1, "limit": 1}, timeout=10)
    except Exception:
        pass


def run_analysis(chat_id):
    send(chat_id,
         "⏳ <b>Fetching live market data...</b>\n"
         "Full analysis + WHY section arriving in ~60 sec ⚡")
    engine = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nightly_engine.py")
    result = subprocess.run([sys.executable, engine], capture_output=True, text=True)
    if result.returncode != 0:
        print("[bot] Engine error:", result.stderr[-800:])
        send(chat_id, "❌ Analysis failed — check GitHub Actions logs.")
    else:
        print("[bot] Analysis sent successfully.")


def main():
    now = time.time()
    updates = get_updates()
    if not updates:
        print("[bot] No Telegram updates.")
        return

    command_chat = None
    last_uid = None
    for u in updates:
        last_uid = u["update_id"]
        msg = u.get("message") or u.get("channel_post")
        if not msg:
            continue
        chat_id  = str(msg["chat"]["id"])
        text     = msg.get("text", "")
        msg_time = msg.get("date", 0)
        if now - msg_time > MAX_AGE:
            continue
        if CHAT_ID and chat_id != CHAT_ID:
            print(f"[bot] Ignored msg from chat {chat_id}")
            continue
        if text.startswith("/"):
            print(f"[bot] Command '{text}' from chat {chat_id} (age {int(now-msg_time)}s)")
            command_chat = chat_id

    if last_uid is not None:
        ack(last_uid)

    if command_chat:
        run_analysis(command_chat)
    else:
        print("[bot] No recent /commands. Idle.")


if __name__ == "__main__":
    main()