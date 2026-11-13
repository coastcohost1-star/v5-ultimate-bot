import os
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def send(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN MISSING")
        return
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=15,
        )
        print(f"TELEGRAM response status: {response.status_code}")
        print(f"TELEGRAM response: {response.text}")
    except requests.RequestException as exc:
        print(f"TELEGRAM request failed: {exc}")


def safe_float(x):
    try:
        value = float(x)
        if pd.isna(value):
            return None
        return value
    except (TypeError, ValueError, OverflowError):
        return None


PAIRS = {
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X", "USDCAD": "USDCAD=X",
    "AUDUSD": "AUDUSD=X", "GBPJPY": "GBPJPY=X", "XAUUSD": "GC=F", "BTCUSD": "BTC-USD",
    "USDZAR": "USDZAR=X", "NAS100": "NQ=F", "US30": "YM=F", "SPX500": "ES=F",
}


def normalize_data(data):
    if data is None or data.empty:
        return None
    if isinstance(data.columns, pd.MultiIndex):
        data = data.copy()
        data.columns = data.columns.get_level_values(0)
    required = ("Open", "High", "Low", "Close")
    if any(column not in data.columns for column in required):
        return None
    return data


def detect_pattern(data):
    if data is None or data.empty or len(data) < 2:
        return None
    last = data.iloc[-1]
    prev = data.iloc[-2]
    o, c, h, low = (safe_float(last[key]) for key in ("Open", "Close", "High", "Low"))
    po, pc = (safe_float(prev[key]) for key in ("Open", "Close"))
    if any(value is None for value in (o, c, h, low, po, pc)):
        return None
    body = abs(c - o)
    previous_body = abs(pc - po)
    candle_range = h - low
    if candle_range == 0:
        return "NO PATTERN"
    if c > o and pc < po and c > po and o < pc and body > previous_body:
        return "BULLISH ENGULFING"
    if c < o and pc > po and c < po and o > pc and body > previous_body:
        return "BEARISH ENGULFING"
    if body < candle_range * 0.1:
        return "DOJI"
    if c > o and (o - low) > 2 * body:
        return "HAMMER"
    if c < o and (h - c) > 2 * body:
        return "SHOOTING STAR"
    if max(o, c) < max(po, pc) and min(o, c) > min(po, pc):
        return "HARAMI"
    return "TRENDLINE BOUNCE"


def trendline_break(data):
    if data is None or data.empty or len(data) < 21:
        return None, None
    recent_high = safe_float(data["High"].rolling(20).max().iloc[-2])
    recent_low = safe_float(data["Low"].rolling(20).min().iloc[-2])
    current = safe_float(data["Close"].iloc[-1])
    previous = safe_float(data["Close"].iloc[-2])
    if any(value is None for value in (recent_high, recent_low, current, previous)):
        return None, None
    if previous < recent_high and current > recent_high:
        return "BREAKOUT_BUY", recent_high
    if previous > recent_low and current < recent_low:
        return "BREAKOUT_SELL", recent_low
    if abs(current - recent_high) < recent_high * 0.0015:
        return "PULLBACK_HIGH", recent_high
    if abs(current - recent_low) < recent_low * 0.0015:
        return "PULLBACK_LOW", recent_low
    return None, None


def is_nfp():
    now = datetime.utcnow()
    try:
        first_friday = [
            datetime(now.year, now.month, 1) + timedelta(days=day)
            for day in range(7)
            if (datetime(now.year, now.month, 1) + timedelta(days=day)).weekday() == 4
        ][0]
        return now.date() == first_friday.date() and 11 <= now.hour <= 14
    except (IndexError, ValueError):
        return False


signals_found = False
for name, ticker in PAIRS.items():
    try:
        raw_15m = yf.download(ticker, period="5d", interval="15m", progress=False)
        raw_1h = yf.download(ticker, period="20d", interval="1h", progress=False)
        df15 = normalize_data(raw_15m)
        df1h = normalize_data(raw_1h)
        if df15 is None or df1h is None or len(df15) < 50:
            print(f"{name}: empty or insufficient market data; skipping")
            continue

        pattern = detect_pattern(df15)
        btype, level = trendline_break(df15)
        if pattern is None:
            print(f"{name}: invalid candle values; skipping")
            continue

        close = df15["Close"]
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        relative_strength = gain / loss
        rsi_series = 100 - (100 / (1 + relative_strength))
        rsi = safe_float(rsi_series.iloc[-1])
        price = safe_float(close.iloc[-1])
        if rsi is None or price is None:
            print(f"{name}: unable to parse RSI or price; skipping")
            continue

        signal = None
        setup = ""
        if btype == "BREAKOUT_BUY" or (
            btype == "PULLBACK_LOW" and ("BULLISH" in pattern or "HAMMER" in pattern)
        ):
            signal = "BUY"
            setup = "Trendline Support Bounce + Bullish Candle"
        elif btype == "BREAKOUT_SELL" or (
            btype == "PULLBACK_HIGH" and ("BEARISH" in pattern or "SHOOTING" in pattern)
        ):
            signal = "SELL"
            setup = "Trendline Resistance Bounce + Bearish Candle"
        elif rsi > 70:
            signal = "SELL"
            setup = "RSI Overbought 70+ Trendline Strategy"
        elif rsi < 30:
            signal = "BUY"
            setup = "RSI Oversold 30- Trendline Strategy"

        if not signal:
            continue

        hourly_high = safe_float(df1h["High"].rolling(20).max().iloc[-1]) if len(df1h) >= 20 else None
        is_major = hourly_high is not None and abs(price - hourly_high) < price * 0.005
        if is_major and "BREAKOUT" in str(btype):
            trade_type = "📈 SWING TRADE"
            expiry = "Hold 4H-2 Days - Major TL Break"
        else:
            trade_type = "⚡ SCALP TRADE"
            expiry = "Hold 15m-2H - Minor TL"

        pip = 1 if "NAS" in name or "US30" in name or "SPX" in name else 0.01 if "JPY" in name else 0.1 if "XAU" in name else 10 if "BTC" in name else 0.0001
        take_profit_distance = 100 * pip
        stop_distance = 30 * pip

        if signal == "BUY":
            stop_loss = price - stop_distance
            take_profit = price + take_profit_distance
            candle_instruction = (
                "🟢 ENTER ON BULLISH CANDLE: Place BUY STOP 2-5 pips above high of "
                + pattern + " candle. Aggressive: Market at trendline touch."
            )
        else:
            stop_loss = price + stop_distance
            take_profit = price - take_profit_distance
            candle_instruction = (
                "🔴 ENTER ON BEARISH CANDLE: Place SELL STOP 2-5 pips below low of "
                + pattern + " candle. Aggressive: Market at trendline touch."
            )

        nfp_warning = "\n⚠️ NFP - Avoid USD!" if is_nfp() and "USD" in name else ""
        trendline_level = level if level is not None else price
        message = f"""🎯 *{name} {signal}*
{trade_type}
📊 {setup}
🕯️ Pattern: {pattern}
{candle_instruction}
⏱️ Expiry: {expiry}
💰 Entry: `{price:.4f}` TL: {trendline_level:.4f}
🛑 SL: {stop_loss:.4f} (30 pips)
✅ TP: {take_profit:.4f} (100 pips FIXED)
📈 RSI: {rsi:.1f}{nfp_warning}
🕐 {datetime.utcnow().strftime('%H:%M UTC')}
"""
        send(message)
        signals_found = True
    except Exception as exc:
        print(f"{name} {exc}")

if not signals_found:
    send("✅ V6 Bot Live - Scanning: EURUSD, GBPUSD, NAS100, XAUUSD etc - No trendline break yet - Next scan in 15m. Features: BUY/SELL, 100 pips TP, SCALP/SWING, Candle pattern")
