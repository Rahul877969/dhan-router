"""
DHAN STRATEGY ROUTER â Nightly Cloud Engine v2
Runs via GitHub Actions at 9:30 PM IST (16:00 UTC) every weekday.

Env vars (set in GitHub Secrets):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service_role key (not anon)
  TELEGRAM_BOT_TOKEN    from BotFather
  TELEGRAM_CHAT_ID      your personal chat ID
"""

import os, json, time, datetime, requests
from supabase import create_client

# ââ Clients ââââââââââââââââââââââââââââââââââââââââââââââââââ
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TG_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
#  DATA FETCHERS
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def fetch_india(market):
    """NSE: VIX, Nifty spot, PCR."""
    try:
        sess = requests.Session()
        sess.get("https://www.nseindia.com/",
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        r = sess.get("https://www.nseindia.com/api/allIndices",
                     headers={"User-Agent":"Mozilla/5.0","Accept":"application/json",
                              "Referer":"https://www.nseindia.com/"}, timeout=10)
        for row in r.json().get("data", []):
            if row.get("index") == "INDIA VIX":
                market["vix"]         = float(row["last"])
                market["vix_chg_pct"] = float(row["percentChange"])
            elif row.get("index") == "NIFTY 50":
                market["nifty"]         = float(row["last"])
                market["nifty_chg_pct"] = float(row["percentChange"])
        print(f"  NSE: Nifty={market.get('nifty')} VIX={market.get('vix')}")
    except Exception as e:
        print(f"  NSE error: {e}")

    # PCR
    try:
        sess2 = requests.Session()
        sess2.get("https://www.nseindia.com/", headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        rp = sess2.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers={"User-Agent":"Mozilla/5.0","Accept":"application/json",
                     "Referer":"https://www.nseindia.com/option-chain"}, timeout=12)
        oc    = rp.json()["filtered"]["data"]
        puts  = sum(x["PE"]["openInterest"] for x in oc if "PE" in x)
        calls = sum(x["CE"]["openInterest"] for x in oc if "CE" in x)
        market["pcr"] = round(puts / calls, 3) if calls else 1.0
        print(f"  PCR={market['pcr']}")
    except Exception as e:
        market.setdefault("pcr", 1.0)
        print(f"  PCR error: {e}")


def fetch_nifty_history(market):
    """Yahoo Finance: 55-day closes for 50 DMA + 20d return."""
    try:
        end_ts = int(time.time())
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
            f"?interval=1d&period1={end_ts - 80*86400}&period2={end_ts}",
            headers={"User-Agent":"Mozilla/5.0"}, timeout=12)
        raw    = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in raw if c][-55:]
        nifty  = market.get("nifty", closes[-1])
        dma50  = sum(closes[-50:]) / min(50, len(closes))
        ret20  = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
        market.update({"dma_50": round(dma50, 2), "ret_20d": round(ret20, 2),
                       "above_dma50": nifty > dma50})
        print(f"  50DMA={dma50:.0f} ret20d={ret20:+.2f}% above={'Y' if nifty>dma50 else 'N'}")
    except Exception as e:
        market.setdefault("ret_20d", 0)
        market.setdefault("above_dma50", True)
        market.setdefault("dma_50", market.get("nifty", 24000))
        print(f"  Yahoo Nifty error: {e}")


def fetch_global(market):
    """Yahoo Finance: S&P500, Nasdaq, DXY, Crude, Gold, US VIX."""
    symbols = {
        "^GSPC":  ("sp500",         "sp500_chg_pct"),
        "^IXIC":  ("nasdaq",        "nasdaq_chg_pct"),
        "DX-Y.NYB":("dxy",          None),
        "CL=F":   ("crude_oil",     None),
        "GC=F":   ("gold",          None),
        "^VIX":   ("us_vix",        None),
    }
    for sym, (price_key, chg_key) in symbols.items():
        try:
            end_ts = int(time.time())
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                f"?interval=1d&period1={end_ts - 5*86400}&period2={end_ts}",
                headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
            res    = r.json()["chart"]["result"][0]
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c]
            if closes:
                market[price_key] = round(closes[-1], 2)
                if chg_key and len(closes) >= 2:
                    market[chg_key] = round((closes[-1]-closes[-2])/closes[-2]*100, 2)
            print(f"  {sym}: {market.get(price_key)}")
        except Exception as e:
            print(f"  {sym} error: {e}")

    # CNN Fear & Greed (best-effort)
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.cnn.com/"}, timeout=8)
        fg = r.json()["fear_and_greed"]["score"]
        market["fear_greed"] = int(float(fg))
        print(f"  Fear&Greed={market['fear_greed']}")
    except Exception as e:
        print(f"  Fear&Greed error: {e}")


def classify_regime(market):
    vix    = market.get("vix", 15)
    ret20  = market.get("ret_20d", 0)
    above  = market.get("above_dma50", True)
    if vix > 22:              return "EXTREME"
    if ret20 > 4 and above:   return "BULL"
    if ret20 < -4 or not above: return "BEAR"
    return "SIDEWAYS"


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
#  v2 SCORING ENGINE (identical to strategy_dashboard.py)
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def compute_scores(market, momentum):
    zen = curv = damp = 0
    regime  = market.get("regime", "SIDEWAYS")
    vix     = market.get("vix", 15)
    vix_dir = market.get("vix_chg_pct", 0)
    pcr     = market.get("pcr", 1.0)
    regime_winner = {"SIDEWAYS":"zen","BEAR":"curv","BULL":"damp"}.get(regime,"zen")

    # 1. Regime (4 pts)
    if regime == "SIDEWAYS":  zen+=4; curv+=2; damp+=1
    elif regime == "BULL":    damp+=4; curv+=2; zen+=1
    elif regime == "BEAR":    curv+=4; zen+=2; damp+=1

    # 2. VIX level (1 pt)
    if vix < 14:    zen+=1
    elif vix < 18:  curv+=1
    else:           curv+=1

    # 3. VIX direction â FIX 1: 2% threshold in SIDEWAYS
    if vix_dir <= -15:   zen+=1
    elif vix_dir >= 15:  curv+=1
    elif vix_dir <= -5:  zen+=1
    elif vix_dir >= 5:   curv+=1
    elif regime == "SIDEWAYS" and vix_dir >= 2:   curv+=1
    elif regime == "SIDEWAYS" and vix_dir <= -2:  zen+=1
    else:
        if regime=="SIDEWAYS": zen+=1
        elif regime=="BEAR":   curv+=1
        else:                  damp+=1

    # 4. PCR (1 pt)
    if pcr > 1.25:   damp+=1
    elif pcr < 0.80: curv+=1
    else:            zen+=1

    # 5. Rolling 5-trade WR (1 pt)
    wr = {k: momentum.get(k,{}).get("last5_wins",3)
          for k in ["zen","curv","damp"]}
    bw = max(wr, key=wr.get)
    if bw=="zen": zen+=1
    elif bw=="curv": curv+=1
    else: damp+=1

    # 6. Streak â FIX 2: cap non-regime winner at +1
    streaks = {k: momentum.get(k,{}).get("streak",0)
               for k in ["zen","curv","damp"]}
    for key, sk in streaks.items():
        is_w = (key == regime_winner)
        if is_w:
            pts = 2 if sk>=4 else 1 if sk>=2 else 0 if sk>=0 else -1 if sk==-1 else -2
        else:
            pts = 1 if sk>=2 else 0 if sk>=0 else -1 if sk==-1 else -2
        if key=="zen": zen+=pts
        elif key=="curv": curv+=pts
        else: damp+=pts

    return max(0,zen), max(0,curv), max(0,damp)


def decide(zen, curv, damp, regime, vix):
    if regime == "EXTREME" or vix > 22:
        return "PAUSE ALL", "PAUSE", "VIX>22 â extreme fear.", 0
    rd_map   = {"SIDEWAYS":"ZEN","BEAR":"CURVATURE","BULL":"DAMPER"}
    scores   = {"ZEN":zen,"CURVATURE":curv,"DAMPER":damp}
    winner   = max(scores, key=scores.get)
    top2     = sorted(scores.values(), reverse=True)
    gap      = top2[0] - top2[1]
    if gap >= 3:
        return f"ACTIVATE {winner} CS", winner, f"{winner} leads by {gap} pts.", gap
    elif gap >= 2:
        return f"LEAN â {winner} CS", winner, f"Mild edge {winner} +{gap} pts.", gap
    else:
        rd = rd_map.get(regime, "ZEN")
        return f"REGIME DEFAULT â {rd} CS", rd, f"Gap={gap}, defaulting to {regime} winner.", gap


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
#  TELEGRAM NOTIFICATION
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("  Telegram not configured â skipping")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10)
        if r.status_code == 200:
            print("  Telegram sent â")
        else:
            print(f"  Telegram error: {r.text[:100]}")
    except Exception as e:
        print(f"  Telegram exception: {e}")


def build_telegram_message(verdict_text, winner, reason, market, z, c, d,
                           zen_sk, curv_sk, damp_sk, today):
    regime    = market.get("regime","?")
    vix       = market.get("vix", 0)
    vix_chg   = market.get("vix_chg_pct", 0)
    nifty     = market.get("nifty", 0)
    nifty_chg = market.get("nifty_chg_pct", 0)
    pcr       = market.get("pcr", 0)
    ret20     = market.get("ret_20d", 0)
    sp500_chg = market.get("sp500_chg_pct", 0)
    dxy       = market.get("dxy", 0)
    crude     = market.get("crude_oil", 0)
    fg        = market.get("fear_greed", 0)

    regime_emoji = {"BULL":"ð¢","BEAR":"ð´","SIDEWAYS":"ð¡","EXTREME":"ð"}.get(regime,"âª")
    action_emoji = "â" if "ACTIVATE" in verdict_text else "ðµ" if "LEAN" in verdict_text else "âª" if "DEFAULT" in verdict_text else "ð"

    step_map = {
        "ZEN":       "â¸ PAUSE Curv + Damp  â  â¶ Keep ZEN CS active",
        "CURVATURE": "â¸ PAUSE Zen + Damp   â  â¶ Keep CURVATURE CS active",
        "DAMPER":    "â¸ PAUSE Zen + Curv   â  â¶ Keep DAMPER CS active",
        "PAUSE":     "â¸ PAUSE ALL 3 strategies",
    }

    msg = (
        f"<b>ð¤ Dhan Strategy Router â {today}</b>\n\n"
        f"{action_emoji} <b>{verdict_text}</b>\n"
        f"<i>{reason}</i>\n\n"
        f"ð <b>Scores:</b>  Zen {z}  |  Curv {c}  |  Damp {d}\n"
        f"ð <b>Streaks:</b>  Zen {zen_sk:+d}  |  Curv {curv_sk:+d}  |  Damp {damp_sk:+d}\n\n"
        f"{regime_emoji} <b>Regime:</b> {regime}  (20d {ret20:+.1f}%)\n"
        f"ð®ð³ <b>India:</b>  VIX {vix:.1f} ({vix_chg:+.1f}%)  |  Nifty {nifty:,.0f} ({nifty_chg:+.1f}%)  |  PCR {pcr:.2f}\n"
        f"ð <b>Global:</b>  S&P500 {sp500_chg:+.1f}%  |  DXY {dxy:.1f}  |  Crude {crude:.1f}  |  F&G {fg}\n\n"
        f"ð <b>Action:</b>  {step_map.get(winner, 'â')}\n"
        f"â° Orders fire 4:45 AM â do this before sleeping!"
    )
    return msg


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
#  MAIN
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def main():
    today = datetime.date.today().isoformat()
    print(f"\n{'='*60}")
    print(f"  Dhan Nightly Engine v2  â  {today}")
    print(f"{'='*60}\n")

    # 1. Fetch all market data
    market = {}
    print("[1/4] India data (NSE)...")
    fetch_india(market)

    print("\n[2/4] Nifty history (Yahoo Finance)...")
    fetch_nifty_history(market)

    print("\n[3/4] Global markets...")
    fetch_global(market)

    # 4. Classify regime
    market["regime"] = classify_regime(market)
    print(f"\n  REGIME: {market['regime']}")

    # 5. Load momentum from Supabase
    print("\n[4/4] Loading momentum from Supabase...")
    mom_raw = {"zen":{},"curv":{},"damp":{}}
    try:
        res = sb.table("strategy_momentum").select("*").order("updated_date", desc=True).limit(1).execute()
        if res.data:
            row = res.data[0]
            mom_raw = {
                "zen":  {"streak": row["zen_streak"],  "last5_wins": row["zen_last5_wins"]},
                "curv": {"streak": row["curv_streak"], "last5_wins": row["curv_last5_wins"]},
                "damp": {"streak": row["damp_streak"], "last5_wins": row["damp_last5_wins"]},
            }
            print(f"  Momentum: Zen{row['zen_streak']:+d} Curv{row['curv_streak']:+d} Damp{row['damp_streak']:+d}")
    except Exception as e:
        print(f"  Momentum load error: {e}")

    # 6. Score + decide
    vix = market.get("vix", 15)
    z, c, d = compute_scores(market, mom_raw)
    verdict_text, winner, reason, gap = decide(z, c, d, market["regime"], vix)
    print(f"\n  SCORES: Zen={z} Curv={c} Damp={d}")
    print(f"  VERDICT: {verdict_text}")
    print(f"  REASON: {reason}")

    # 7. Write market snapshot to Supabase
    snap = {
        "snapshot_date":  today,
        "vix":            market.get("vix"),
        "vix_chg_pct":    market.get("vix_chg_pct"),
        "nifty":          market.get("nifty"),
        "nifty_chg_pct":  market.get("nifty_chg_pct"),
        "pcr":            market.get("pcr"),
        "ret_20d":        market.get("ret_20d"),
        "dma_50":         market.get("dma_50"),
        "above_dma50":    market.get("above_dma50"),
        "regime":         market.get("regime"),
        "sp500":          market.get("sp500"),
        "sp500_chg_pct":  market.get("sp500_chg_pct"),
        "nasdaq_chg_pct": market.get("nasdaq_chg_pct"),
        "dxy":            market.get("dxy"),
        "crude_oil":      market.get("crude_oil"),
        "gold":           market.get("gold"),
        "us_vix":         market.get("us_vix"),
        "fear_greed":     market.get("fear_greed"),
    }
    try:
        sb.table("market_snapshots").upsert(snap, on_conflict="snapshot_date").execute()
        print("  Snapshot saved to Supabase â")
    except Exception as e:
        print(f"  Snapshot save error: {e}")

    # 8. Write verdict to Supabase
    zen_sk  = mom_raw.get("zen",  {}).get("streak", 0)
    curv_sk = mom_raw.get("curv", {}).get("streak", 0)
    damp_sk = mom_raw.get("damp", {}).get("streak", 0)

    verdict_row = {
        "verdict_date":   today,
        "zen_score":      z,
        "curv_score":     c,
        "damp_score":     d,
        "winner":         winner,
        "verdict_text":   verdict_text,
        "reason":         reason,
        "signal_strength": ("ACTIVATE" if "ACTIVATE" in verdict_text
                            else "LEAN" if "LEAN" in verdict_text
                            else "REGIME DEFAULT" if "DEFAULT" in verdict_text
                            else "PAUSE"),
        "gap":            gap,
        "regime":         market.get("regime"),
        "zen_streak":     zen_sk,
        "curv_streak":    curv_sk,
        "damp_streak":    damp_sk,
    }
    try:
        sb.table("strategy_verdicts").upsert(verdict_row, on_conflict="verdict_date").execute()
        print("  Verdict saved to Supabase â")
    except Exception as e:
        print(f"  Verdict save error: {e}")

    # 9. Telegram notification
    print("\n  Sending Telegram...")
    tg_msg = build_telegram_message(
        verdict_text, winner, reason, market,
        z, c, d, zen_sk, curv_sk, damp_sk, today)
    send_telegram(tg_msg)

    print(f"\n{'='*60}")
    print(f"  Done. Tonight's call: {verdict_text}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
