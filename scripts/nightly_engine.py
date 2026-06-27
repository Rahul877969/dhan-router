"""
DHAN STRATEGY ROUTER -- Nightly Cloud Engine v2.1
Runs via GitHub Actions at 9:30 PM IST (16:00 UTC) every weekday.

Scoring engine is kept 1:1 in sync with strategy_dashboard.py v2.
Bugs fixed in v2.1 vs v2.0:
  FIX A -- VIX level threshold: zen if vix < 16 (was < 14)
  FIX B -- VIX direction: uses 5-day Yahoo history (was today's NSE % change)
  FIX C -- WR threshold: only awards point if best strategy >= 60% WR

Env vars (set in GitHub Secrets):
  SUPABASE_URL          your project URL
  SUPABASE_SERVICE_KEY  service_role key (not anon)
  TELEGRAM_BOT_TOKEN    from BotFather
  TELEGRAM_CHAT_ID      your personal chat ID
"""

import os, json, time, datetime, requests
from supabase import create_client

# -- Clients --------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TG_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===============================================================
#  DATA FETCHERS
# ===============================================================

def fetch_india(market):
    """NSE: VIX, Nifty spot."""
    try:
        sess = requests.Session()
        sess.get("https://www.nseindia.com/",
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        r = sess.get("https://www.nseindia.com/api/allIndices",
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                              "Referer": "https://www.nseindia.com/"}, timeout=10)
        for row in r.json().get("data", []):
            if row.get("index") == "INDIA VIX":
                market["vix"]         = float(row["last"])
                market["vix_chg_pct"] = float(row["percentChange"])   # today only (not used for scoring)
            elif row.get("index") == "NIFTY 50":
                market["nifty"]         = float(row["last"])
                market["nifty_chg_pct"] = float(row["percentChange"])
        print(f"  NSE: Nifty={market.get('nifty')} VIX={market.get('vix')}")
    except Exception as e:
        print(f"  NSE error: {e}")

    # PCR
    try:
        sess2 = requests.Session()
        sess2.get("https://www.nseindia.com/", headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        rp = sess2.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                     "Referer": "https://www.nseindia.com/option-chain"}, timeout=12)
        oc    = rp.json()["filtered"]["data"]
        puts  = sum(x["PE"]["openInterest"] for x in oc if "PE" in x)
        calls = sum(x["CE"]["openInterest"] for x in oc if "CE" in x)
        market["pcr"] = round(puts / calls, 3) if calls else 1.0
        print(f"  PCR={market['pcr']}")
    except Exception as e:
        market.setdefault("pcr", 1.0)
        print(f"  PCR error: {e}")


def fetch_nifty_history(market):
    """Yahoo Finance: 55-day closes for 50 DMA, 20d return, TSR."""
    try:
        end_ts = int(time.time())
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
            f"?interval=1d&period1={end_ts - 80*86400}&period2={end_ts}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        raw    = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in raw if c][-55:]
        nifty  = market.get("nifty", closes[-1])

        # 20d return -- primary regime input
        ret20 = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0

        # 50 DMA
        dma50 = sum(closes[-50:]) / min(50, len(closes))

        # TSR -- position within 20-day range
        closes20 = closes[-20:]
        h20, l20 = max(closes20), min(closes20)
        tsr = round((nifty - l20) / (h20 - l20) * 100, 1) if h20 != l20 else 50.0

        # 5-day trend
        trend5 = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0

        market.update({
            "dma_50":     round(dma50, 2),
            "ret_20d":    round(ret20, 2),
            "above_dma50": nifty > dma50,
            "tsr":        tsr,
            "trend_5d":   trend5,
        })
        print(f"  50DMA={dma50:.0f} ret20d={ret20:+.2f}% TSR={tsr:.0f}% above={'Y' if nifty > dma50 else 'N'}")
    except Exception as e:
        market.setdefault("ret_20d", 0)
        market.setdefault("above_dma50", True)
        market.setdefault("dma_50", market.get("nifty", 24000))
        market.setdefault("tsr", 50.0)
        market.setdefault("trend_5d", 0.0)
        print(f"  Yahoo Nifty error: {e}")


def fetch_vix_history(market):
    """
    FIX B: Yahoo Finance VIX 5-day history.
    strategy_dashboard.py uses % change over last 5 days (not today's % from NSE).
    This ensures both files score VIX direction identically.
    """
    try:
        end_ts = int(time.time())
        rv = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EINDIAVIX"
            f"?interval=1d&period1={end_ts - 15*86400}&period2={end_ts}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        vix_closes = [c for c in
                      rv.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                      if c is not None][-6:]
        if len(vix_closes) >= 5:
            market["vix_direction"] = round(
                (vix_closes[-1] - vix_closes[-5]) / vix_closes[-5] * 100, 1)
        else:
            market["vix_direction"] = 0.0
        print(f"  VIX 5d direction={market['vix_direction']:+.1f}%")
    except Exception as e:
        market.setdefault("vix_direction", 0.0)
        print(f"  VIX history error: {e}")


def fetch_global(market):
    """Yahoo Finance: S&P500, Nasdaq, DXY, Crude, Gold, US VIX."""
    symbols = {
        "^GSPC":    ("sp500",    "sp500_chg_pct"),
        "^IXIC":    ("nasdaq",   "nasdaq_chg_pct"),
        "DX-Y.NYB": ("dxy",      None),
        "CL=F":     ("crude_oil", None),
        "GC=F":     ("gold",     None),
        "^VIX":     ("us_vix",   None),
    }
    for sym, (price_key, chg_key) in symbols.items():
        try:
            end_ts = int(time.time())
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                f"?interval=1d&period1={end_ts - 5*86400}&period2={end_ts}",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            res    = r.json()["chart"]["result"][0]
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c]
            if closes:
                market[price_key] = round(closes[-1], 2)
                if chg_key and len(closes) >= 2:
                    market[chg_key] = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
            print(f"  {sym}: {market.get(price_key)}")
        except Exception as e:
            print(f"  {sym} error: {e}")

    # CNN Fear & Greed
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cnn.com/"}, timeout=8)
        fg = r.json()["fear_and_greed"]["score"]
        market["fear_greed"] = int(float(fg))
        print(f"  Fear&Greed={market['fear_greed']}")
    except Exception as e:
        print(f"  Fear&Greed error: {e}")


def classify_regime(market):
    vix   = market.get("vix", 15)
    ret20 = market.get("ret_20d", 0)
    above = market.get("above_dma50", True)
    if vix > 22:               return "EXTREME"
    if ret20 > 4 and above:    return "BULL"
    if ret20 < -4 or not above: return "BEAR"
    return "SIDEWAYS"


# ===============================================================
#  v2.1 SCORING ENGINE -- 1:1 sync with strategy_dashboard.py
#
#  6 metrics, 10 pts max:
#    1. Market Regime      4 pts
#    2. VIX Level          1 pt   FIX A: zen if vix < 16 (was < 14)
#    3. VIX Direction      1 pt   FIX B: uses 5-day Yahoo history
#    4. PCR Sentiment      1 pt
#    5. Rolling 5-trade WR 1 pt   FIX C: only award if >= 60% WR
#    6. Streak/Momentum    2 pts  cap non-regime winner at +1
# ===============================================================

def compute_scores(market, momentum):
    """Returns (zen, curv, damp, score_breakdown_dict)."""
    zen = curv = damp = 0
    breakdown = {"zen": [], "curv": [], "damp": []}

    regime      = market.get("regime", "SIDEWAYS")
    vix         = market.get("vix", 15.0)
    vix_dir     = market.get("vix_direction", 0.0)   # FIX B: 5-day %
    pcr         = market.get("pcr", 1.0)

    regime_winner = {"SIDEWAYS": "zen", "BEAR": "curv", "BULL": "damp"}.get(regime, "zen")

    # -- 1. MARKET REGIME (4 pts) --
    if regime == "SIDEWAYS":
        zen += 4; curv += 2; damp += 1
        breakdown["zen"].append("SIDEWAYS -> Zen +4")
        breakdown["curv"].append("SIDEWAYS -> Curv +2")
        breakdown["damp"].append("SIDEWAYS -> Damp +1")
    elif regime == "BULL":
        damp += 4; curv += 2; zen += 1
        breakdown["damp"].append("BULL -> Damp +4")
        breakdown["curv"].append("BULL -> Curv +2")
        breakdown["zen"].append("BULL -> Zen +1")
    elif regime == "BEAR":
        curv += 4; zen += 2; damp += 1
        breakdown["curv"].append("BEAR -> Curv +4")
        breakdown["zen"].append("BEAR -> Zen +2")
        breakdown["damp"].append("BEAR -> Damp +1")
    else:  # EXTREME
        breakdown["zen"].append("VIX>22 EXTREME -- all paused")

    # -- 2. VIX LEVEL (1 pt) -- FIX A: threshold 16, not 14 --
    if vix < 16:
        zen += 1
        breakdown["zen"].append(f"VIX {vix:.1f} (<16 low) -> Zen +1")
    else:
        curv += 1
        breakdown["curv"].append(f"VIX {vix:.1f} (>=16 elevated) -> Curv +1")

    # -- 3. VIX DIRECTION (1 pt) -- FIX B: 5-day % from Yahoo --
    if vix_dir <= -15:
        zen += 1
        breakdown["zen"].append(f"VIX 5d {vix_dir:+.1f}% sharp fall -> Zen +1")
    elif vix_dir >= 15:
        curv += 1
        breakdown["curv"].append(f"VIX 5d {vix_dir:+.1f}% spike -> Curv +1")
    elif vix_dir <= -5:
        zen += 1
        breakdown["zen"].append(f"VIX 5d {vix_dir:+.1f}% easing -> Zen +1")
    elif vix_dir >= 5:
        curv += 1
        breakdown["curv"].append(f"VIX 5d {vix_dir:+.1f}% rising -> Curv +1")
    elif regime == "SIDEWAYS" and vix_dir >= 2:
        curv += 1
        breakdown["curv"].append(f"VIX 5d {vix_dir:+.1f}% in SIDEWAYS -> Curv +1")
    elif regime == "SIDEWAYS" and vix_dir <= -2:
        zen += 1
        breakdown["zen"].append(f"VIX 5d {vix_dir:+.1f}% in SIDEWAYS -> Zen +1")
    else:
        if regime == "SIDEWAYS":   zen  += 1; breakdown["zen"].append(f"VIX stable ({vix_dir:+.1f}%) -> Zen +1")
        elif regime == "BEAR":     curv += 1; breakdown["curv"].append(f"VIX stable ({vix_dir:+.1f}%) -> Curv +1")
        else:                      damp += 1; breakdown["damp"].append(f"VIX stable ({vix_dir:+.1f}%) -> Damp +1")

    # -- 4. PCR (1 pt) --
    if pcr > 1.25:
        damp += 1
        breakdown["damp"].append(f"PCR {pcr:.2f} (>1.25 bullish) -> Damp +1")
    elif pcr < 0.80:
        curv += 1
        breakdown["curv"].append(f"PCR {pcr:.2f} (<0.80 bearish) -> Curv +1")
    else:
        zen += 1
        breakdown["zen"].append(f"PCR {pcr:.2f} (neutral) -> Zen +1")

    # -- 5. ROLLING 5-TRADE WR (1 pt) -- FIX C: need >= 60% --
    recent_wrs = {}
    for key in ["zen", "curv", "damp"]:
        l5wins = momentum.get(key, {}).get("last5_wins", 0)
        l5count = momentum.get(key, {}).get("last5_count", 5)
        recent_wrs[key] = l5wins / l5count if l5count else 0.5

    best = max(recent_wrs, key=recent_wrs.get)
    if recent_wrs[best] >= 0.6:
        if best == "zen":   zen  += 1
        elif best == "curv": curv += 1
        else:               damp += 1
        breakdown[best].append(f"Best recent WR {recent_wrs[best]*100:.0f}% -> {best.title()} +1")
    else:
        breakdown["zen"].append("All strategies WR <60% -- no +1 awarded")

    # -- 6. STREAK (2 pts max, cap non-regime winner at +1) --
    for key in ["zen", "curv", "damp"]:
        sk   = momentum.get(key, {}).get("streak", 0)
        is_w = (key == regime_winner)
        if is_w:
            pts = 2 if sk >= 4 else 1 if sk >= 2 else 0 if sk >= 0 else -1 if sk == -1 else -2
        else:
            pts = 1 if sk >= 2 else 0 if sk >= 0 else -1 if sk == -1 else -2
        cap = " (capped)" if not is_w and sk >= 4 else ""
        if key == "zen":   zen  += pts
        elif key == "curv": curv += pts
        else:               damp += pts
        breakdown[key].append(f"Streak {sk:+d} -> {pts:+d} pts{cap}")

    return max(0, zen), max(0, curv), max(0, damp), breakdown


def decide(zen, curv, damp, regime, vix):
    if regime == "EXTREME" or vix > 22:
        return "PAUSE ALL", "PAUSE", "VIX>22 -- extreme fear. Skip tomorrow.", 0
    rd_map  = {"SIDEWAYS": "ZEN", "BEAR": "CURVATURE", "BULL": "DAMPER"}
    scores  = {"ZEN": zen, "CURVATURE": curv, "DAMPER": damp}
    winner  = max(scores, key=scores.get)
    top2    = sorted(scores.values(), reverse=True)
    gap     = top2[0] - top2[1]
    if gap >= 3:
        return f"ACTIVATE {winner} CS", winner, f"{winner} leads by {gap} pts -- strong signal.", gap
    elif gap >= 2:
        return f"LEAN {winner} CS", winner, f"Mild edge {winner} +{gap} pts.", gap
    else:
        rd = rd_map.get(regime, "ZEN")
        return f"REGIME DEFAULT {rd} CS", rd, f"Gap={gap} too thin -- defaulting to {regime} winner.", gap


# ==============================================================
#  TELEGRAM NOTIFICATION  v2.2
#  All non-ASCII stored as Python unicode escapes so the .py
#  source is 100% ASCII -- survives Windows clipboard paste.
# ==============================================================

# Section divider: 5x U+2501 BOX DRAWINGS HEAVY HORIZONTAL
_D5 = "\u2501" * 5  # renders: =====


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("  Telegram not configured -- skipping")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10)
        if r.status_code == 200:
            print("  Telegram sent ok")
        else:
            print(f"  Telegram error: {r.text[:200]}")
    except Exception as e:
        print(f"  Telegram exception: {e}")


# -- Analysis helpers -------------------------------------------

def _regime_analysis(market):
    """5-line block explaining HOW the regime was determined."""
    regime  = market.get("regime", "SIDEWAYS")
    ret20   = market.get("ret_20d", 0.0)
    vix     = market.get("vix", 15.0)
    vix_dir = market.get("vix_direction", 0.0)
    tsr     = market.get("tsr", 50.0)
    above   = market.get("above_dma50", True)
    dma50   = market.get("dma_50", 0.0)
    pcr     = market.get("pcr", 1.0)
    trend5  = market.get("trend_5d", 0.0)

    dma_word  = "ABOVE" if above else "BELOW"
    dma_note  = "bullish structure" if above else "BEARISH structure -- warning"
    vix_word  = "calm" if vix < 16 else "elevated" if vix < 22 else "EXTREME"
    vix_trend = (f"falling {abs(vix_dir):.1f}% (5d) -- fear easing"  if vix_dir <= -5
                 else f"RISING {vix_dir:.1f}% (5d) -- fear building" if vix_dir >= 5
                 else f"stable {vix_dir:+.1f}% (5d)")
    pcr_word  = "bullish" if pcr > 1.25 else "bearish" if pcr < 0.80 else "neutral"

    rule = {
        "SIDEWAYS": f"20d return {ret20:+.1f}% is in the flat band (-4% to +4%)",
        "BULL":     f"20d return {ret20:+.1f}% > +4% AND Nifty above DMA50",
        "BEAR":     f"20d return {ret20:+.1f}% < -4% OR Nifty below DMA50",
        "EXTREME":  f"VIX {vix:.1f} > 22 -- panic zone, all strategies paused",
    }.get(regime, "")

    # bullet = U+2022
    b = "\u2022"
    lines = [
        f"{b} Nifty 20d return: <b>{ret20:+.1f}%</b>  |  5d drift: {trend5:+.2f}%  |  TSR: {tsr:.0f}%",
        f"{b} DMA50 ({dma50:,.0f}): Nifty is <b>{dma_word}</b> -- {dma_note}",
        f"{b} VIX {vix:.2f}: <b>{vix_word}</b>, {vix_trend}",
        f"{b} PCR {pcr:.2f}: {pcr_word} options flow",
        f"\u27A1 <b>RESULT: {regime}</b> -- {rule}",
    ]
    return "\n".join(lines)


def _strategy_analysis(market, winner, z, c, d, gap, breakdown):
    """Explain WHY the winning strategy fits today's market.
    Returns (text_block, top_breakdown_lines)."""
    regime = market.get("regime", "SIDEWAYS")
    vix    = market.get("vix", 15.0)

    # Strategy one-line profiles
    profile = {
        "ZEN":       ("Theta / premium seller",
                      "Earns on time decay in calm, range-bound, low-VIX conditions",
                      "Exposed to: large gap moves, VIX spikes above 16+"),
        "CURVATURE": ("Directional hedge / downside catcher",
                      "Earns when Nifty falls or fear spikes (structured payoff curve)",
                      "Exposed to: strong uninterrupted bull runs"),
        "DAMPER":    ("Momentum / trend follower",
                      "Earns in trending bull market with high bullish options flow",
                      "Exposed to: choppy reversals, sudden sentiment shifts"),
        "PAUSE":     ("No trade",
                      "VIX extreme or scores too close -- skip tomorrow",
                      "N/A"),
    }
    pf = profile.get(winner, ("?", "?", "?"))

    # Why this strategy fits THIS regime
    fit = {
        ("SIDEWAYS", "ZEN"):       "Flat market = ZEN goldmine; small daily moves = pure theta profit, minimal delta risk",
        ("SIDEWAYS", "CURVATURE"): "Mild bear pockets within sideways range; CURV hedge earns on dips",
        ("SIDEWAYS", "DAMPER"):    "Mild upside drift despite flat regime; DAMP earns small momentum edge",
        ("BULL",     "DAMPER"):    "Trending rally = DAMP follows momentum and captures sustained directional move",
        ("BULL",     "CURVATURE"): "Bullish with hedging need; CURV captures upside while protecting tail risk",
        ("BULL",     "ZEN"):       "Calm bull + low VIX; ZEN sells premium in muted volatility environment",
        ("BEAR",     "CURVATURE"): "Falling market = CURV structure profits directly from downside price action",
        ("BEAR",     "ZEN"):       "Moderate bear with VIX still calm; ZEN earns near support with limited moves",
        ("BEAR",     "DAMPER"):    "Counter-trend regime default; DAMP earns if reversal comes",
        ("EXTREME",  "PAUSE"):     "VIX > 22 = any strategy could blow up overnight; preserving capital",
    }.get((regime, winner),
          f"{winner} scored highest ({max(z, c, d)} pts) across all 6 scoring metrics")

    # Signal strength label
    sig = ("Strong signal -- high confidence" if gap >= 3
           else "Mild edge -- proceed with awareness" if gap >= 2
           else "Thin gap -- regime default, low conviction")

    # Top breakdown reasons for winner
    wk = {"ZEN": "zen", "CURVATURE": "curv", "DAMPER": "damp"}.get(winner, "zen")
    top_bd = breakdown.get(wk, [])[:5]

    b = "\u2022"
    lines = [
        f"{b} Type: {pf[0]}",
        f"{b} Edge: {pf[1]}",
        f"{b} Risk: {pf[2]}",
        f"{b} Why it fits today: {fit}",
        f"{b} Signal: {sig}  (gap = {gap} pts)",
        f"{b} Score card: Zen <b>{z}</b>  |  Curv <b>{c}</b>  |  Damp <b>{d}</b>",
    ]
    return "\n".join(lines), top_bd


def _flip_conditions(market, winner, gap):
    """Dynamic conditions that would change tomorrow's verdict."""
    regime = market.get("regime", "SIDEWAYS")
    vix    = market.get("vix", 15.0)
    ret20  = market.get("ret_20d", 0.0)

    flips = []
    if regime == "SIDEWAYS":
        flips.append("Nifty 20d return drops below -4% OR below DMA50  ->  BEAR  ->  CURV CS takes over")
        flips.append("Nifty 20d return rises above +4% AND above DMA50  ->  BULL  ->  DAMP CS takes over")
    elif regime == "BULL":
        flips.append("Nifty 20d return reverses below -4% or drops under DMA50  ->  BEAR  ->  CURV CS")
        flips.append("VIX spikes above 22  ->  EXTREME  ->  PAUSE ALL strategies")
    elif regime == "BEAR":
        flips.append("Nifty 20d return recovers above +4% AND above DMA50  ->  BULL  ->  DAMP CS")
        flips.append("VIX spikes above 22  ->  EXTREME  ->  PAUSE ALL strategies")
    elif regime == "EXTREME":
        flips.append("VIX drops back below 22  ->  regime re-evaluates (likely BEAR at first)")

    if vix < 16:
        flips.append(f"VIX rises above 16  ->  Curv gains VIX-level pt; Zen loses it  (gap narrows by 2)")
    elif vix < 22:
        flips.append(f"VIX drops below 16  ->  Zen gains VIX-level pt; Curv loses it  (gap widens for Zen)")

    if gap <= 2:
        flips.append(f"Thin gap ({gap} pts): one streak or WR change can flip the verdict tomorrow")

    b = "\u2022"
    return "\n".join(f"{b} {f}" for f in flips)


# -- Main message builder ---------------------------------------

def build_telegram_message(verdict_text, winner, reason, market,
                           z, c, d, breakdown, mom_raw, today):
    regime    = market.get("regime", "?")
    vix       = market.get("vix", 0)
    vix_chg   = market.get("vix_chg_pct", 0)
    vix_dir   = market.get("vix_direction", 0)
    nifty     = market.get("nifty", 0)
    nifty_chg = market.get("nifty_chg_pct", 0)
    pcr       = market.get("pcr", 0)
    ret20     = market.get("ret_20d", 0)
    tsr       = market.get("tsr", 50)
    trend5    = market.get("trend_5d", 0)
    dma50     = market.get("dma_50", 0)
    above_dma = market.get("above_dma50", True)
    sp500_chg = market.get("sp500_chg_pct", 0)
    dxy       = market.get("dxy", 0)
    crude     = market.get("crude_oil", 0)
    fg        = market.get("fear_greed", 0)
    us_vix    = market.get("us_vix", 0)

    # -- Emoji constants (unicode escapes -- source stays ASCII) --
    e_bot    = "\U0001F916"  # robot
    e_check  = "\u2705"      # check mark
    e_cross  = "\u274C"      # cross mark
    e_green  = "\U0001F7E2"  # green circle
    e_red    = "\U0001F534"  # red circle
    e_yellow = "\U0001F7E1"  # yellow circle
    e_blue   = "\U0001F535"  # blue circle
    e_purple = "\U0001F7E3"  # purple circle
    e_sos    = "\U0001F198"  # SOS
    e_stop   = "\U0001F6D1"  # stop sign
    e_circle = "\u26AA"      # white circle
    e_pin    = "\U0001F4CD"  # location pin
    e_india  = "\U0001F1EE\U0001F1F3"  # IN flag
    e_zap    = "\u26A1"      # lightning
    e_earth  = "\U0001F30D"  # globe
    e_finger = "\U0001F449"  # point right
    e_alarm  = "\u23F0"      # alarm clock
    e_pause  = "\u23F8"      # pause button
    e_play   = "\u25B6"      # play button
    e_up     = "\U0001F4C8"  # chart up
    e_down   = "\U0001F4C9"  # chart down
    e_chart  = "\U0001F4CA"  # bar chart
    e_mag    = "\U0001F50D"  # magnifying glass
    e_flip   = "\U0001F504"  # arrows circular
    e_info   = "\u2139"      # info

    # -- Derived display values ------------------------------------
    regime_emoji = {
        "BULL": e_green, "BEAR": e_red,
        "SIDEWAYS": e_yellow, "EXTREME": e_sos,
    }.get(regime, e_circle)

    action_emoji = (e_check  if "ACTIVATE" in verdict_text
                    else e_blue   if "LEAN"     in verdict_text
                    else e_circle if "DEFAULT"  in verdict_text
                    else e_stop)

    step_map = {
        "ZEN":       f"{e_pause} Pause Curv + Damp  {e_play} Keep ZEN CS active",
        "CURVATURE": f"{e_pause} Pause Zen + Damp   {e_play} Keep CURVATURE CS active",
        "DAMPER":    f"{e_pause} Pause Zen + Curv   {e_play} Keep DAMPER CS active",
        "PAUSE":     f"{e_stop} PAUSE ALL 3 strategies -- do not trade tomorrow",
    }

    zen_sk  = mom_raw.get("zen",  {}).get("streak", 0)
    curv_sk = mom_raw.get("curv", {}).get("streak", 0)
    damp_sk = mom_raw.get("damp", {}).get("streak", 0)

    def wr_str(key):
        w = mom_raw.get(key, {}).get("last5_wins", 0)
        n = mom_raw.get(key, {}).get("last5_count", 5)
        return f"{int(w / n * 100) if n else 0}%"

    dma_lbl = (f"{e_check if above_dma else e_cross} "
               f"{'Above' if above_dma else 'Below'} 50 DMA ({dma50:,.0f})")
    pcr_lbl = (f"bullish {e_up}" if pcr > 1.25
               else f"bearish {e_down}" if pcr < 0.80
               else "neutral \u2194")  # left-right arrow
    vix_dir_arrow = "\u2191" if vix_dir >= 0 else "\u2193"  # up/down arrow
    vix_dir_lbl   = f"{vix_dir_arrow}{abs(vix_dir):.1f}% (5d)"

    # Score gap
    scores_map = {"ZEN": z, "CURVATURE": c, "DAMPER": d}
    top2 = sorted(scores_map.values(), reverse=True)
    gap  = top2[0] - top2[1]

    # Analysis blocks
    regime_why             = _regime_analysis(market)
    strategy_why, top_bd   = _strategy_analysis(market, winner, z, c, d, gap, breakdown)
    flip_conds             = _flip_conditions(market, winner, gap)
    bd_lines = "\n".join(f"  {l}" for l in top_bd) if top_bd else "  (no detail)"

    msg = (
        f"<b>{e_bot} Dhan Strategy Router -- {today}</b>\n\n"

        f"{action_emoji} <b>{verdict_text}</b>\n"
        f"<i>{reason}</i>\n\n"

        f"<b>{_D5} SCORES (max 10) {_D5}</b>\n"
        f"{e_green} Zen CS        <b>{z}/10</b>  streak {zen_sk:+d}  WR {wr_str('zen')}\n"
        f"{e_blue}  Curvature CS <b>{c}/10</b>  streak {curv_sk:+d}  WR {wr_str('curv')}\n"
        f"{e_purple} Damper CS   <b>{d}/10</b>  streak {damp_sk:+d}  WR {wr_str('damp')}\n\n"

        f"<b>{_D5} MARKET {_D5}</b>\n"
        f"{regime_emoji} <b>Regime:</b> {regime}  |  20d ret {ret20:+.1f}%  |  TSR {tsr:.0f}%\n"
        f"{e_pin} {dma_lbl}\n"
        f"{e_india} <b>Nifty:</b> {nifty:,.0f} ({nifty_chg:+.1f}%)  5d {trend5:+.2f}%\n"
        f"{e_zap} <b>VIX:</b> {vix:.2f} ({vix_chg:+.1f}% today  {vix_dir_lbl})"
        f"  PCR {pcr:.2f} {pcr_lbl}\n\n"

        f"<b>{_D5} GLOBAL {_D5}</b>\n"
        f"{e_earth} S&P {sp500_chg:+.1f}%  |  DXY {dxy:.1f}  |  "
        f"Crude {crude:.1f}  |  US VIX {us_vix:.1f}  |  F&G {fg}\n\n"

        f"<b>{_D5} WHY THIS VERDICT? {_D5}</b>\n\n"

        f"{e_chart} <b>REGIME: {regime}</b>\n"
        f"{regime_why}\n\n"

        f"{e_mag} <b>WHY {winner} CS WINS:</b>\n"
        f"{strategy_why}\n\n"

        f"{e_info} <b>SCORING FACTORS (top):</b>\n"
        f"{bd_lines}\n\n"

        f"{e_flip} <b>WHAT WOULD FLIP THIS:</b>\n"
        f"{flip_conds}\n\n"

        f"<b>{_D5} ACTION {_D5}</b>\n"
        f"{e_finger} {step_map.get(winner, '--')}\n"
        f"{e_alarm} Set orders before sleeping -- they fire at 4:45 AM!"
    )
    return msg


# ===============================================================
#  MAIN
# ===============================================================

def main():
    today = datetime.date.today().isoformat()
    print(f"\n{'='*60}")
    print(f"  Dhan Nightly Engine v2.1  --  {today}")
    print(f"{'='*60}\n")

    # 1. Fetch market data
    market = {}
    print("[1/5] India data (NSE)...")
    fetch_india(market)

    print("\n[2/5] Nifty 55-day history (Yahoo Finance)...")
    fetch_nifty_history(market)

    print("\n[3/5] VIX 5-day history (Yahoo Finance)...")   # FIX B
    fetch_vix_history(market)

    print("\n[4/5] Global markets...")
    fetch_global(market)

    # Classify regime
    market["regime"] = classify_regime(market)
    print(f"\n  REGIME: {market['regime']}")

    # 5. Load momentum from Supabase
    print("\n[5/5] Loading momentum from Supabase...")
    mom_raw = {"zen": {}, "curv": {}, "damp": {}}
    try:
        res = sb.table("strategy_momentum").select("*").order("updated_date", desc=True).limit(1).execute()
        if res.data:
            row = res.data[0]
            mom_raw = {
                "zen":  {"streak": row["zen_streak"],  "last5_wins": row["zen_last5_wins"],  "last5_count": 5},
                "curv": {"streak": row["curv_streak"], "last5_wins": row["curv_last5_wins"], "last5_count": 5},
                "damp": {"streak": row["damp_streak"], "last5_wins": row["damp_last5_wins"], "last5_count": 5},
            }
            print(f"  Momentum: Zen{row['zen_streak']:+d} Curv{row['curv_streak']:+d} Damp{row['damp_streak']:+d}")
    except Exception as e:
        print(f"  Momentum load error: {e}")

    # 6. Score + decide
    vix = market.get("vix", 15)
    z, c, d, breakdown = compute_scores(market, mom_raw)
    verdict_text, winner, reason, gap = decide(z, c, d, market["regime"], vix)

    print(f"\n  SCORES: Zen={z} Curv={c} Damp={d}")
    print(f"  VERDICT: {verdict_text}")
    print(f"  REASON: {reason}")

    # 7. Save market snapshot to Supabase
    snap = {
        "snapshot_date":  today,
        "vix":            market.get("vix"),
        "vix_chg_pct":    market.get("vix_chg_pct"),
        "vix_direction":  market.get("vix_direction"),
        "nifty":          market.get("nifty"),
        "nifty_chg_pct":  market.get("nifty_chg_pct"),
        "pcr":            market.get("pcr"),
        "ret_20d":        market.get("ret_20d"),
        "dma_50":         market.get("dma_50"),
        "above_dma50":    market.get("above_dma50"),
        "tsr":            market.get("tsr"),
        "trend_5d":       market.get("trend_5d"),
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
        print("  Snapshot saved to Supabase ok")
    except Exception as e:
        print(f"  Snapshot save error: {e}")

    # 8. Save verdict to Supabase
    zen_sk  = mom_raw.get("zen",  {}).get("streak", 0)
    curv_sk = mom_raw.get("curv", {}).get("streak", 0)
    damp_sk = mom_raw.get("damp", {}).get("streak", 0)

    verdict_row = {
        "verdict_date":    today,
        "zen_score":       z,
        "curv_score":      c,
        "damp_score":      d,
        "winner":          winner,
        "verdict_text":    verdict_text,
        "reason":          reason,
        "signal_strength": ("ACTIVATE" if "ACTIVATE" in verdict_text
                            else "LEAN" if "LEAN" in verdict_text
                            else "REGIME DEFAULT" if "DEFAULT" in verdict_text
                            else "PAUSE"),
        "gap":             gap,
        "regime":          market.get("regime"),
        "zen_streak":      zen_sk,
        "curv_streak":     curv_sk,
        "damp_streak":     damp_sk,
    }
    try:
        sb.table("strategy_verdicts").upsert(verdict_row, on_conflict="verdict_date").execute()
        print("  Verdict saved to Supabase ok")
    except Exception as e:
        print(f"  Verdict save error: {e}")

    # 9. Send Telegram
    print("\n  Sending Telegram...")
    tg_msg = build_telegram_message(
        verdict_text, winner, reason, market,
        z, c, d, breakdown, mom_raw, today)
    send_telegram(tg_msg)

    print(f"\n{'='*60}")
    print(f"  Done. Tonight's call: {verdict_text}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
