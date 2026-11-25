"""
V6 - multi-setup S/R signal bot (signals only, does NOT place trades)
Needs v5_ultimate_bot.py in the same folder (it reuses its data, S/R, candle and news code).

Run:  python v6_bot.py            (scan every 30 min)
      python v6_bot.py --once

What is new vs V5 (retest only):
  SETUPS (each can be switched on/off in SETUPS below)
    RETEST          break level -> pullback -> confirmation + Fib      (V5 logic)
    REJECTION       price wicks into a 2+ touch level and is rejected (pin bar / engulfing)
    TREND_PULLBACK  pullback to EMA21 inside a confirmed HTF trend, higher-low / lower-high structure
    BREAKOUT        strong momentum close through a level (OFF by default - test it first)
  FILTERS
    HTF trend (EMA21/EMA55 + slope) and ADX regime
    Counter-trend trades are penalised, and blocked when ADX >= 30
    Scalp session filter (07:00-20:00 UTC, Mon-Fri)
    Stop size must be 0.6-3.0 x ATR (no tiny stops that spread eats, no huge ones)
    Room-to-run: next opposing S/R must be far enough away (else no trade)
    TP = your RR, or just before the next opposing level if that is closer (but still >= min room)
    News block (same as V5)
  MANAGEMENT
    SL to breakeven at +1R, SWING trails behind confirmed swings, SCALP expires after 4h

IMPORTANT: more filters does not automatically mean more accurate. Use backtest6.py to see
which setups have positive expectancy on real data and switch the losers off.
"""
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

import v5_ultimate_bot as v5

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
SETUPS = {"RETEST": True, "REJECTION": True, "TREND_PULLBACK": True, "BREAKOUT": False}
BASE_SCORE = {"RETEST": 3, "REJECTION": 2, "TREND_PULLBACK": 3, "BREAKOUT": 2}
MIN_SCORE = {"SCALP": 4, "SWING": 4}            # signal must reach this score
MIN_ROOM_R = {"SCALP": 1.2, "SWING": 2.0}       # min distance to next opposing level, in R
MIN_RISK_ATR, MAX_RISK_ATR = 0.6, 3.0
ADX_TREND_MIN = 20                              # trend-following setups need ADX >= this
ADX_STRONG = 30                                 # counter-trend trades blocked above this
SCALP_SESSION_UTC = (7, 20)
EMA_FAST, EMA_SLOW = 21, 55


# ----------------------------------------------------------------------------
# INDICATORS
# ----------------------------------------------------------------------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def adx(df, n=14):
    up, dn = df.high.diff(), -df.low.diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    a = 1.0 / n
    atr_ = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=a, adjust=False).mean() / atr_
    mdi = 100 * minus.ewm(alpha=a, adjust=False).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean()


def trend_state(htf):
    """('UP'|'DOWN'|'RANGE', adx) from closed higher-timeframe bars."""
    c = htf.close
    if len(c) < EMA_SLOW + 10:
        return "RANGE", 0.0
    f, s = ema(c, EMA_FAST), ema(c, EMA_SLOW)
    ax = float(adx(htf).iloc[-1])
    if not np.isfinite(ax):
        ax = 0.0
    if c.iloc[-1] > s.iloc[-1] and f.iloc[-1] > s.iloc[-1] and s.iloc[-1] > s.iloc[-6]:
        return "UP", ax
    if c.iloc[-1] < s.iloc[-1] and f.iloc[-1] < s.iloc[-1] and s.iloc[-1] < s.iloc[-6]:
        return "DOWN", ax
    return "RANGE", ax
# ----------------------------------------------------------------------------
# SETUPS  (each looks at the LAST closed entry candle only)
# ----------------------------------------------------------------------------
def _mk(setup, side, lv, conf, entry, sl, htf):
    return dict(setup=setup, side=side, level=lv["price"], kind=lv["kind"], touches=lv["touches"],
                double=lv["double"], conf=conf, entry=float(entry), sl=float(sl), fib=[],
                patterns=v5.detect_patterns(htf, side, lv["price"]))


def setup_rejection(ltf, levels, htf, a):
    out, n = [], len(ltf)
    r = ltf.iloc[-1]
    prior = ltf.close.iloc[-7:-1]
    buf = v5.SL_BUFFER_ATR * a
    for lv in levels:
        p = lv["price"]
        z = p * v5.ZONE_TOL
        if r.low <= p + z and r.low >= p - a and r.close > p and prior.min() > p - z:
            conf = ("Bullish Engulfing" if v5.bullish_engulfing(ltf, n - 1)
                    else "Hammer" if v5.hammer(ltf, n - 1) else None)
            if conf:
                out.append(_mk("REJECTION", "BUY", lv, conf, r.close, r.low - buf, htf))
        if r.high >= p - z and r.high <= p + a and r.close < p and prior.max() < p + z:
            conf = ("Bearish Engulfing" if v5.bearish_engulfing(ltf, n - 1)
                    else "Shooting Star" if v5.shooting_star(ltf, n - 1) else None)
            if conf:
                out.append(_mk("REJECTION", "SELL", lv, conf, r.close, r.high + buf, htf))
    return out


def setup_pullback(ltf, a, trend):
    if trend == "RANGE":
        return []
    n = len(ltf)
    r = ltf.iloc[-1]
    e = ema(ltf.close, EMA_FAST)
    ev = float(e.iloc[-1])
    highs, lows = v5.find_swings(ltf.tail(120).reset_index(drop=True))
    buf = v5.SL_BUFFER_ATR * a
    lv = dict(price=ev, kind="S" if trend == "UP" else "R", touches=0, double=False)
    out = []
    if trend == "UP":
        intact = (ltf.close.iloc[-9:-2] > e.iloc[-9:-2]).mean() >= 0.6
        hl = len(lows) >= 2 and lows[-1][1] > lows[-2][1]
        conf = ("Bullish Engulfing" if v5.bullish_engulfing(ltf, n - 1)
                else "Hammer" if v5.hammer(ltf, n - 1) else None)
        if r.low <= ev + 0.2 * a and r.close > ev and intact and hl and conf:
            c = dict(setup="TREND_PULLBACK", side="BUY", conf=conf, entry=float(r.close),
                     sl=float(ltf.low.iloc[-3:].min() - buf), fib=[], patterns=[], **lv)
            out.append(c)
    else:
        intact = (ltf.close.iloc[-9:-2] < e.iloc[-9:-2]).mean() >= 0.6
        lh = len(highs) >= 2 and highs[-1][1] < highs[-2][1]
        conf = ("Bearish Engulfing" if v5.bearish_engulfing(ltf, n - 1)
                else "Shooting Star" if v5.shooting_star(ltf, n - 1) else None)
        if r.high >= ev - 0.2 * a and r.close < ev and intact and lh and conf:
            c = dict(setup="TREND_PULLBACK", side="SELL", conf=conf, entry=float(r.close),
                     sl=float(ltf.high.iloc[-3:].max() + buf), fib=[], patterns=[], **lv)
            out.append(c)
    return out


def setup_breakout(ltf, levels, htf, a):
    out = []
    r = ltf.iloc[-1]
    prev = float(ltf.close.iloc[-2])
    rng = r.high - r.low
    body = abs(r.close - r.open)
    if rng <= 0 or body < 0.9 * a:
        return out
    buf = v5.SL_BUFFER_ATR * a
    for lv in levels:
        p = lv["price"]
        if prev <= p < r.close and (r.high - r.close) <= 0.3 * rng:
            out.append(_mk("BREAKOUT", "BUY", lv, "Momentum close above", r.close, r.low - buf, htf))
        if prev >= p > r.close and (r.close - r.low) <= 0.3 * rng:
            out.append(_mk("BREAKOUT", "SELL", lv, "Momentum close below", r.close, r.high + buf, htf))
    return out


# ----------------------------------------------------------------------------
# FINALISE: risk size, room to run, TP, trend scoring
# ----------------------------------------------------------------------------
def finalize(c, a, levels, trend, ax, mode, cfg):
    buy = c["side"] == "BUY"
    risk = abs(c["entry"] - c["sl"])
    if not (MIN_RISK_ATR * a <= risk <= MAX_RISK_ATR * a):
        return None

    counter = (trend == "UP" and not buy) or (trend == "DOWN" and buy)
    aligned = (trend == "UP" and buy) or (trend == "DOWN" and not buy)
    if counter and (c["setup"] in ("TREND_PULLBACK", "BREAKOUT") or ax >= ADX_STRONG):
        return None
    if c["setup"] in ("TREND_PULLBACK", "BREAKOUT") and ax < ADX_TREND_MIN:
        return None

    ahead = [l["price"] for l in levels
             if (l["price"] > c["entry"] + 0.1 * a if buy else l["price"] < c["entry"] - 0.1 * a)]
    tp = c["entry"] + cfg["rr"] * risk if buy else c["entry"] - cfg["rr"] * risk
    if ahead:
        nxt = min(ahead) if buy else max(ahead)
        room = abs(nxt - c["entry"]) / risk
        if room < MIN_ROOM_R[mode]:
            return None
        if room < cfg["rr"]:
            tp = nxt - 0.1 * a if buy else nxt + 0.1 * a

    score = BASE_SCORE[c["setup"]] + (1 if aligned else -1 if counter else 0)
    score += int(c["double"]) + len(c["patterns"])
    if c["setup"] == "REJECTION" and trend == "RANGE":
        score += 1
    c.update(tp=float(tp), rr_eff=round(abs(tp - c["entry"]) / risk, 2), score=score,
             trend=trend, adx=round(ax, 1))
    return c


# ----------------------------------------------------------------------------
# MAIN SIGNAL FUNCTION (used by the live bot AND the backtest)
# ----------------------------------------------------------------------------
def find_signals(ltf, htf, mode, levels=None):
    cfg = v5.MODES[mode]
    if ltf is None or htf is None or len(ltf) < 60 or len(htf) < 40:
        return []
    t = ltf.index[-1]
    if mode == "SCALP" and not (SCALP_SESSION_UTC[0] <= t.hour < SCALP_SESSION_UTC[1] and t.weekday() < 5):
        return []
    a = v5.atr(ltf).iloc[-1]
    if not np.isfinite(a) or a <= 0:
        return []
    if levels is None:
        levels = v5.find_sr_levels(htf)
    trend, ax = trend_state(htf)
    stamp = (t + pd.Timedelta(minutes=v5.INTERVAL_MIN[cfg["ltf_interval"]])).isoformat()

    raw = []
    if SETUPS["RETEST"]:
        for c in v5.check_retest(ltf, levels, htf, cfg):
            c["setup"] = "RETEST"
            raw.append(c)
    if SETUPS["REJECTION"]:
        raw += setup_rejection(ltf, levels, htf, a)
    if SETUPS["TREND_PULLBACK"]:
        raw += setup_pullback(ltf, a, trend)
    if SETUPS["BREAKOUT"]:
        raw += setup_breakout(ltf, levels, htf, a)

    out = []
    for c in raw:
        c.setdefault("time", stamp)
        f = finalize(c, a, levels, trend, ax, mode, cfg)
        if f and f["score"] >= MIN_SCORE[mode]:
            out.append(f)
    return sorted(out, key=lambda c: -c["score"])


# ----------------------------------------------------------------------------
# OUTPUT
# ----------------------------------------------------------------------------
def format_signal(s):
    f = lambda x: v5.fmt_price(s["pair"], x)
    why = [s["setup"], s["conf"], f"HTF trend {s['trend']} (ADX {s['adx']})"]
    if s.get("double"):
        why.append("Double S/R")
    if s.get("patterns"):
        why.append(", ".join(s["patterns"]))
    if s.get("fib"):
        why.append("Fib " + "/".join(str(x) for x in s["fib"]))
    if s.get("addon"):
        why.append("ADD-ON")
    why.append(f"score {s['score']}")
    dist = abs(s["entry"] - s["sl"])
    risk_amt = v5.ACCOUNT_BALANCE * v5.RISK_PCT
    return (f"PAIR: {s['pair']} | {s['side']} | {s['mode']} | {s['setup']}\n"
            f"  ENTRY: {f(s['entry'])}  SL: {f(s['sl'])}  TP: {f(s['tp'])}  (RR {s['rr_eff']})\n"
            f"  Risk {v5.RISK_PCT * 100:.0f}% = {risk_amt:.2f}; SL distance {f(dist)} "
            f"-> size ~ {risk_amt / dist:.2f} units (only valid if quote ccy = account ccy)\n"
            f"  Reason: {'; '.join(why)}")
# ----------------------------------------------------------------------------
# OPEN-SIGNAL MANAGEMENT (alerts only)
# ----------------------------------------------------------------------------
def manage_open(state, cache):
    keep = []
    now = pd.Timestamp.now(tz="UTC")
    for s in state["open"]:
        cfg = v5.MODES[s["mode"]]
        df = v5.get(cache, v5.SYMBOLS[s["pair"]], cfg["ltf_interval"], cfg["ltf_period"])
        if df is None:
            keep.append(s)
            continue
        f = lambda x: v5.fmt_price(s["pair"], x)
        buy = s["side"] == "BUY"
        t0 = pd.Timestamp(s["time"])
        epos = int(df.index.searchsorted(t0)) - 1
        sl, be, closed = s["sl0"], False, False
        for pos in range(epos + 1, len(df)):
            r = df.iloc[pos]
            if buy:
                hit_sl = r.low <= sl
                hit_tp = r.high >= s["tp"]
                hit_be = r.high >= s["entry"] + s["risk"]
            else:
                hit_sl = r.high >= sl
                hit_tp = r.low <= s["tp"]
                hit_be = r.low <= s["entry"] - s["risk"]
            if hit_sl:
                print(f"[CLOSED] {s['pair']} {s['mode']} {s['side']}: "
                      f"{'breakeven stop' if be else 'STOP LOSS'} hit at {f(sl)}")
                closed = True
                break
            if hit_tp:
                print(f"[CLOSED] {s['pair']} {s['mode']} {s['side']}: TAKE PROFIT hit at {f(s['tp'])}")
                closed = True
                break
            if not be and hit_be:
                be, sl = True, s["entry"]
        if closed:
            continue
        if cfg["expiry_hours"] and now - t0 >= pd.Timedelta(hours=cfg["expiry_hours"]):
            print(f"[EXPIRED] {s['pair']} {s['mode']} {s['side']}: {cfg['expiry_hours']}h passed - close/cancel")
            continue
        if s["mode"] == "SWING":
            highs, lows = v5.find_swings(df)
            a = v5.atr(df).iloc[-1]
            buf = v5.SL_BUFFER_ATR * (a if np.isfinite(a) else 0)
            pts = [p for i, p in (lows if buy else highs) if i > epos]
            if pts:
                new = pts[-1] - buf if buy else pts[-1] + buf
                last = df.close.iloc[-1]
                if (buy and new > sl and new < last) or (not buy and new < sl and new > last):
                    sl = float(new)
        better = max(sl, s["sl"]) if buy else min(sl, s["sl"])
        if better != s["sl"]:
            print(f"[MOVE SL] {s['pair']} {s['mode']} {s['side']}: new SL {f(better)}")
            s["sl"] = float(better)
        keep.append(s)
    state["open"] = keep

# ----------------------------------------------------------------------------
# SCAN LOOP
# ----------------------------------------------------------------------------
def scan_once(state):
    cache = {}
    print(f"\n=== V6 SCAN {datetime.now(v5.SAST):%Y-%m-%d %H:%M} SAST ===")
    manage_open(state, cache)
    blocked, why = v5.check_news_block()
    if blocked:
        print(f"NEWS BLOCK: no new signals ({why})")
        v5.save_state(state)
        return
    found = 0
    for pair in v5.SYMBOLS:
        for mode, cfg in v5.MODES.items():
            try:
                htf = v5.get_htf(cache, pair, cfg)
                ltf = v5.get(cache, v5.SYMBOLS[pair], cfg["ltf_interval"], cfg["ltf_period"])
                if htf is None or ltf is None:
                    continue
                age = (pd.Timestamp.now(tz="UTC") - (ltf.index[-1] + pd.Timedelta(
                    minutes=v5.INTERVAL_MIN[cfg["ltf_interval"]]))).total_seconds() / 60
                if age > cfg["max_age_min"]:
                    continue
                cur = float(ltf.close.iloc[-1])
                for s in find_signals(ltf, htf, mode):
                    s.update(pair=pair, mode=mode)
                    key = f"{pair}|{mode}|{s['side']}|{s['setup']}|{s['time']}"
                    if key in state["seen"]:
                        continue
                    state["seen"].append(key)
                    risk = abs(s["entry"] - s["sl"])
                    if abs(cur - s["entry"]) > 0.5 * risk:
                        continue
                    same = [o for o in state["open"]
                            if o["pair"] == pair and o["mode"] == mode and o["side"] == s["side"]]
                    if same:
                        if mode == "SWING" and all(abs(o["level"] - s["level"]) / s["level"] > v5.LEVEL_TOL
                                                   for o in same):
                            s["addon"] = True
                        else:
                            continue
                    print(format_signal(s))
                    found += 1
                    state["open"].append(dict(pair=pair, mode=mode, side=s["side"], setup=s["setup"],
                                              entry=s["entry"], sl=s["sl"], sl0=s["sl"], tp=s["tp"],
                                              risk=risk, level=s["level"], time=s["time"]))
                    break
            except Exception as e:
                print(f"  [error] {pair} {mode}: {e}")
    if not found:
        print("No new signals.")
    v5.save_state(state)

def main():
    state = v5.load_state()
    once = "--once" in sys.argv
    while True:
        try:
            scan_once(state)
        except Exception as e:
            print(f"[scan error] {e}")
        if once:
            break
        now = datetime.now()
        wait = (v5.SCAN_EVERY_MIN - now.minute % v5.SCAN_EVERY_MIN) * 60 - now.second + 15
        print(f"Next scan in {wait // 60} min...")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            print("Stopped.")
            break

if __name__ == "__main__":
    main()
