import os
from datetime import datetime, timedelta
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# === HIGH QUALITY CONFIG ===
HIGH_QUALITY_ONLY = True
RSI_LOW = 25      # Only oversold below 25
RSI_HIGH = 75     # Only overbought above 75
REQUIRE_PATTERN = True

def send(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN MISSING")
        return
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=15
        )
        print(f"TELEGRAM response: {response.status_code}")
    except Exception as ex:
        print(f"TELEGRAM exception: {ex}")

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def detect_pattern(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last['Close'] - last['Open'])
    upper_wick = last['High'] - max(last['Close'], last['Open'])
    lower_wick = min(last['Close'], last['Open']) - last['Low']
    if last['Close'] > last['Open'] and prev['Close'] < prev['Open']:
        if last['Close'] > prev['Open'] and last['Open'] < prev['Close']:
            return "BULLISH ENGULFING"
    if last['Close'] < last['Open'] and prev['Close'] > prev['Open']:
        if last['Close'] < prev['Open'] and last['Open'] > prev['Close']:
            return "BEARISH ENGULFING"
    if lower_wick > body * 2 and upper_wick < body * 0.5:
        return "HAMMER / PIN BAR"
    if upper_wick > body * 2 and lower_wick < body * 0.5:
        return "SHOOTING STAR"
    return "NO PATTERN"

def is_high_quality(rsi_val, pattern):
    if rsi_val > RSI_LOW and rsi_val < RSI_HIGH:
        print(f"SKIP RSI {rsi_val:.1f}")
        return False
    if REQUIRE_PATTERN and pattern == "NO PATTERN":
        print(f"SKIP NO PATTERN")
        return False
    return True

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "BTC-USD": "BTC-USD",
    "XAUUSD": "GC=F",
    "USDCHF": "USDCHF=X",
}

print(f"Starting HIGH QUALITY scan")

for name, ticker in PAIRS.items():
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False)
        if df.empty or len(df) < 20:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df['RSI'] = calculate_rsi(df['Close'])
        rsi_val = float(df['RSI'].iloc[-1])
        pattern = detect_pattern(df)
        if not is_high_quality(rsi_val, pattern):
            continue
        close_price = float(df['Close'].iloc[-1])
        if rsi_val < RSI_LOW:
            direction = "BUY"
            reason = "RSI Oversold"
        elif rsi_val > RSI_HIGH:
            direction = "SELL"
            reason = "RSI Overbought"
        else:
            continue
        msg = f"""🎯 *{name} {direction}* - HIGH QUALITY
⏰ {datetime.utcnow().strftime('%H:%M UTC')}
📊 {reason}: RSI {rsi_val:.1f}
🕯️ Pattern: {pattern}
💰 Price: {close_price}
🎯 TP: 100 pips FIXED
🛑 SL: 50 pips FIXED
⏳ Expiry: 15M-1H
🔥 Confidence: EXTREME RSI + {pattern}
"""
        print(f"SENDING HQ: {name} {direction} RSI {rsi_val:.1f} {pattern}")
        send(msg)
    except Exception as e:
        print(f"Error {name}: {e}")
        continue
print("HQ Scan done")
