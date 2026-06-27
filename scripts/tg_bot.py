"""
Telegram Bot Poller  v1.2
Fixes: deletes webhook before polling (webhook blocks getUpdates)
"""
import os, sys, time, subprocess, requests

TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API     = f"https://api.telegram.org/bot{TOKEN}"
MAX_AGE = 600


def send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                      timeout=10)
    except Exception as e:
        print(f"[bot] sendMessage error: {e}")


def ensure_polling_mode():
    """Delete any webhook so getUpdates works. Safe to call even if no webhook set."""
    try:
        wh = requests.get(f"{API}/getWebhookInfo", timeout=10).json()
        wh_url = wh.get("result", {}).get("url", "")
        if wh_url:
            print(f"[bot] Webhook active ({wh_url}), deleting it...")
            requests.post(f"{API}/deleteWebhook", json={"drop_pending_updates": False}, timeout=10)
            print("[bot] Webhook deleted. Polling mode active.")
        else:
            print("[bot] No webhook set. Polling mode OK.")
    except Exception as e:
        print(f"[bot] Webhook check error: {e}")


def get_updates():
    try:
        r = requests.get(f"{API}/getUpdates",
                         params={"limit": 20, "timeout": 0,
                                 "allowed_updates": ["message", "channel_post"]},
                         timeout=15)
        data = r.json()
        print(f"[bot] getUpdates raw: ok={data.get('ok')} count={len(data.get('result',[]))}")
        return data.get("result", [])
    except Exception as e:
        print(f"[bot] getUpdates error: {e}")
        return []


def ack(last_id):
    try:
        requests.get(f"{API}/getUpdates",
                     params={"offset": last_id + 1, "limit": 1}, timeout=10)
    except Exception:
        pass


def run_analysis(chat_id):
    send(chat_id,
         "⏳ <b>Fetching live market data...</b>\n"
         "Full analysis + WHY section in ~60 sec ⚡")
    engine = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nightly_engine.py")
    # Override chat so analysis always replies to whoever sent the command
    env = os.environ.copy()
    env["TELEGRAM_CHAT_ID"] = str(chat_id)
    result = subprocess.run([sys.executable, engine], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        print("[bot] Engine stderr:", result.stderr[-800:])
        send(chat_id, "❌ Analysis failed — check GitHub Actions logs.")
    else:
        print("[bot] Analysis sent.")
        print(result.stdout[-300:])


def main():
    ensure_polling_mode()
    now = time.time()
    updates = get_updates()

    if not updates:
        print("[bot] No Telegram updates. Idle.")
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
        age      = int(now - msg_time)

        print(f"[bot] Update uid={last_uid} chat={chat_id} age={age}s text={text[:40]!r}")

        if now - msg_time > MAX_AGE:
            print(f"[bot]   -> too old ({age}s), skipping")
            continue
        if CHAT_ID and chat_id != CHAT_ID:
            print(f"[bot]   -> wrong chat, skipping")
            continue
        if text.startswith("/"):
            print(f"[bot]   -> COMMAND! Will run analysis.")
            command_chat = chat_id

    if last_uid is not None:
        ack(last_uid)

    if command_chat:
        run_analysis(command_chat)
    else:
        print("[bot] No recent /commands found.")


if __name__ == "__main__":
    main()