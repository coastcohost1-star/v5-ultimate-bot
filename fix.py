from pathlib import Path

BOT_SOURCE = r'''"""V5 technical-signal scanner using Yahoo Finance market data.

This produces rule-based research signals, not investment advice or guaranteed
predictions. Yahoo Finance data can be delayed, incomplete, or unavailable.
"""

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

PAIRS = {
    "USDCAD": "USDCAD=X",
    "GBPJPY": "GBPJPY=X",
    "EURUSD": "EURUSD=X",
    "NAS100": "NQ=F",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F",
}
DXY_TICKER = "DX-Y.NYB"
MIN_CONFIDENCE = 70


def download_hourly(ticker):
    """Fetch 60 days of hourly candles and normalize yfinance's column layout."""
    data = yf.download(
        ticker, period="60d", interval="60m", auto_adjust=True,
        progress=False, threads=False,
    )
    if data is None or data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close"]
    if any(column not in data.columns for column in required):
        return pd.DataFrame()
    data = data[required].apply(pd.to_numeric, errors="coerce").dropna()
    if data.index.tz is None:
        data.index = data.index.tz_localize("UTC")
    else:
        data.index = data.index.tz_convert("UTC")
    return data


def resample_ohlc(data, interval):
    rule = "4h" if interval == "4h" else "1D"
    result = data.resample(rule).agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
    }).dropna()
    return result


def add_indicators(data):
    data = data.copy()
    close = data["Close"]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    data["RSI"] = 100 - (100 / (1 + relative_strength))
    previous_close = close.shift(1)
    true_range = pd.concat([
        data["High"] - data["Low"],
        (data["High"] - previous_close).abs(),
        (data["Low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    data["ATR"] = true_range.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    return data.dropna()


def local_extrema(series, kind, radius=2):
    values = series.to_numpy(dtype=float)
    indexes = []
    for i in range(radius, len(values) - radius):
        window = values[i - radius:i + radius + 1]
        if kind == "low" and values[i] == np.min(window):
            indexes.append(i)
        elif kind == "high" and values[i] == np.max(window):
            indexes.append(i)
    return indexes


def double_touch(data, side):
    """Return a recent pair of similarly priced swing lows/highs, if present."""
    start = max(0, len(data) - 80)
    column, kind = ("Low", "low") if side == "BUY" else ("High", "high")
    candidates = [i for i in local_extrema(data[column], kind) if i >= start]
    if len(candidates) < 2:
        return None
    a, b = candidates[-2], candidates[-1]
    if b - a < 3:
        return None
    price_a, price_b = float(data[column].iloc[a]), float(data[column].iloc[b])
    tolerance = max(float(data["ATR"].iloc[-1]) * 0.35, abs(price_b) * 0.0015)
    if abs(price_a - price_b) > tolerance:
        return None
    return {"level": (price_a + price_b) / 2, "first": a, "second": b}


def fibonacci_match(data, side):
    """Check whether the latest price is near the 61.8% swing retracement."""
    recent = data.tail(80)
    if len(recent) < 20:
        return None
    high_pos = int(np.argmax(recent["High"].to_numpy()))
    low_pos = int(np.argmin(recent["Low"].to_numpy()))
    high = float(recent["High"].iloc[high_pos])
    low = float(recent["Low"].iloc[low_pos])
    span = high - low
    if span <= 0:
        return None
    # For a bullish retracement, price pulls down from the swing high; vice versa for bearish.
    level = high - 0.618 * span if side == "BUY" else low + 0.618 * span
    close = float(data["Close"].iloc[-1])
    tolerance = max(float(data["ATR"].iloc[-1]) * 0.5, abs(close) * 0.002)
    return level if abs(close - level) <= tolerance else None


def rsi_divergence(data, side, extrema):
    if not extrema or len(extrema) < 2:
        return False
    a, b = extrema["first"], extrema["second"]
    price_a = float(data["Low" if side == "BUY" else "High"].iloc[a])
    price_b = float(data["Low" if side == "BUY" else "High"].iloc[b])
    rsi_a, rsi_b = float(data["RSI"].iloc[a]), float(data["RSI"].iloc[b])
    if side == "BUY":
        return price_b <= price_a * 1.002 and rsi_b >= rsi_a + 2
    return price_b >= price_a * 0.998 and rsi_b <= rsi_a - 2


def head_shoulders_match(data, side):
    """Basic three-pivot head-and-shoulders / inverse pattern check."""
    if side == "SELL":
        points = local_extrema(data["High"].tail(100), "high")
        values = data["High"].tail(100).to_numpy(dtype=float)
        if len(points) < 3:
            return False
        left, head, right = [float(values[i]) for i in points[-3:]]
        return head > left * 1.003 and head > right * 1.003 and abs(left - right) / max(left, right) < 0.03
    points = local_extrema(data["Low"].tail(100), "low")
    values = data["Low"].tail(100).to_numpy(dtype=float)
    if len(points) < 3:
        return False
    left, head, right = [float(values[i]) for i in points[-3:]]
    return head < left * 0.997 and head < right * 0.997 and abs(left - right) / max(left, right) < 0.03


def dxy_aligned(pair, side, dxy_change):
    """Check that recent DXY direction agrees with the pair's USD exposure."""
    if dxy_change is None:
        return False
    usd_is_base = pair.startswith("USD")
    usd_is_quote = pair.endswith("USD") or pair == "USDCAD"
    if not (usd_is_base or usd_is_quote):
        return True
    usd_direction = 1 if (side == "BUY") == usd_is_base else -1
    return dxy_change * usd_direction > 0


def analyze(pair, ticker, interval, dxy_change):
    data = download_hourly(ticker)
    if data.empty:
        print(f"{pair} ({interval}): no market data")
        return None
    data = add_indicators(resample_ohlc(data, interval))
    if len(data) < 30:
        print(f"{pair} ({interval}): not enough candles")
        return None

    candidates = []
    for side in ("BUY", "SELL"):
        touch = double_touch(data, side)
        fib_level = fibonacci_match(data, side)
        divergence = rsi_divergence(data, side, touch)
        dxy_ok = dxy_aligned(pair, side, dxy_change)
        hs = head_shoulders_match(data, side)
        # Required setup filters: double touch, 61.8% retracement, RSI divergence,
        # and DXY alignment for USD-denominated pairs.
        needs_dxy = pair.startswith("USD") or pair.endswith("USD") or pair == "USDCAD"
        if not (touch and fib_level is not None and divergence and (dxy_ok or not needs_dxy)):
            continue
        confidence = 20 + 20 + 20 + (15 if hs else 0) + (15 if dxy_ok else 0)
        if confidence < MIN_CONFIDENCE:
            continue

        entry = float(data["Close"].iloc[-1])
        atr = float(data["ATR"].iloc[-1])
        structure = float(touch["level"])
        stop = min(structure, entry - 1.5 * atr) if side == "BUY" else max(structure, entry + 1.5 * atr)
        risk = abs(entry - stop)
        if not np.isfinite(risk) or risk <= 0:
            continue
        sign = 1 if side == "BUY" else -1
        candidates.append({
            "pair": pair, "ticker": ticker, "interval": interval, "side": side,
            "entry": entry, "stop_loss": stop,
            "take_profit_1": entry + sign * 2.2 * risk,
            "take_profit_2": entry + sign * 4.2 * risk,
            "confidence": confidence,
            "reason": "double touch + Fib 61.8% + RSI divergence"
                      + (" + H&S" if hs else "")
                      + (" + DXY aligned" if dxy_ok else ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return max(candidates, key=lambda item: item["confidence"]) if candidates else None


def format_signal(signal):
    return (
        f"{signal['pair']} | {signal['side']} | Entry: {signal['entry']:.5f} | "
        f"SL: {signal['stop_loss']:.5f} | TP1: {signal['take_profit_1']:.5f} | "
        f"TP2: {signal['take_profit_2']:.5f} | Confidence: {signal['confidence']}% | "
        f"Reason: {signal['reason']} ({signal['interval']})"
    )


def send_telegram(messages):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token:
        return
    if not chat_id:
        print("TELEGRAM_TOKEN is set, but TELEGRAM_CHAT_ID is missing; skipping Telegram.")
        return
    for message in messages:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message}, timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Telegram send failed: {exc}")


def main():
    signals = []
    try:
        dxy_data = download_hourly(DXY_TICKER)
        dxy_change = None
        if len(dxy_data) >= 25:
            dxy_close = dxy_data["Close"].dropna()
            dxy_change = float(dxy_close.iloc[-1] / dxy_close.iloc[-24] - 1)
    except Exception as exc:
        print(f"DXY data unavailable: {exc}")
        dxy_change = None

    for pair, ticker in PAIRS.items():
        try:
            for interval in ("4h", "1d"):
                signal = analyze(pair, ticker, interval, dxy_change)
                if signal:
                    signals.append(signal)
        except Exception as exc:
            print(f"{pair}: analysis failed: {exc}")

    # Keep one strongest signal per pair, preferring confidence and then the 4h setup.
    selected = {}
    for signal in signals:
        old = selected.get(signal["pair"])
        if old is None or (signal["confidence"], signal["interval"] == "4h") > (
            old["confidence"], old["interval"] == "4h"
        ):
            selected[signal["pair"]] = signal
    signals = list(selected.values())

    if not signals:
        print("No high-confidence V5 setup - monitoring")
    else:
        messages = [format_signal(signal) for signal in signals]
        for message in messages:
            print(message)
        try:
            with open("signals.json", "w", encoding="utf-8") as file:
                json.dump(signals, file, indent=2)
        except OSError as exc:
            print(f"Could not save signals.json: {exc}")
        send_telegram(messages)


if __name__ == "__main__":
    main()
'''

Path("v5_ultimate_bot.py").write_text(BOT_SOURCE, encoding="utf-8")
print("Created v5_ultimate_bot.py")
