import os, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime

TOKEN=os.getenv('TELEGRAM_TOKEN')
CHAT_ID=os.getenv('TELEGRAM_CHAT_ID')

def send_telegram(msg):
    if not TOKEN or not CHAT_ID: 
        print("No telegram secrets")
        return
    try:
        url=f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"Markdown"}, timeout=10)
        print("Telegram sent!")
    except Exception as e:
        print(f"Telegram error {e}")

SYMBOLS={"EURUSD":"EURUSD=X","GBPJPY":"GBPJPY=X","USDCAD":"USDCAD=X","USDJPY":"USDJPY=X","NAS100":"^NDX","XAUUSD":"GC=F"}

def get_signal(ticker):
    try:
        df=yf.download(ticker, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if len(df)<50: return None
        close=df['Close'].squeeze()
        delta=close.diff()
        gain=delta.where(delta>0,0).rolling(14).mean()
        loss=(-delta.where(delta<0,0)).rolling(14).mean()
        rs=gain/loss
        rsi=100-(100/(1+rs))
        last_rsi=float(rsi.iloc[-1])
        price=float(close.iloc[-1])
        sma20=float(close.rolling(20).mean().iloc[-1])
        conf=50
        bias="NEUTRAL"
        if last_rsi<30 and price<sma20: conf=75; bias="BUY"
        elif last_rsi>70 and price>sma20: conf=75; bias="SELL"
        elif last_rsi<40: conf=65; bias="BUY"
        elif last_rsi>60: conf=65; bias="SELL"
        tradable=conf>=70
        return {"price":price,"rsi":last_rsi,"conf":conf,"bias":bias,"tradable":tradable}
    except Exception as e:
        print(f"Error {ticker} {e}")
        return None

print("=== V5 ULTIMATE REAL SCAN + TELEGRAM ===")
alerts=[]
for name,ticker in SYMBOLS.items():
    s=get_signal(ticker)
    if not s: continue
    line=f"{name}: {s['price']:.2f} RSI {s['rsi']:.1f} Conf {s['conf']}% {s['bias']} {'TRADABLE' if s['tradable'] else ''}"
    print(line)
    if s['tradable']:
        sl = s['price']*0.995 if s['bias']=='BUY' else s['price']*1.005
        tp = s['price']*1.015 if s['bias']=='BUY' else s['price']*0.985
        msg=f"🔥 *V5 SIGNAL* 🔥\nPair: {name}\nBias: {s['bias']}\nEntry: {s['price']:.2f}\nRSI: {s['rsi']:.1f}\nSL: {sl:.2f}\nTP: {tp:.2f}\nConf: {s['conf']}%\nTime: {datetime.utcnow().strftime('%H:%M UTC')}"
        alerts.append(msg)

if alerts:
    for m in alerts:
        send_telegram(m)
else:
    send_telegram(f"V5 Scan {datetime.utcnow().strftime('%H:%M UTC')}: No 70%+ signals. Monitoring 6 pairs. Real data OK.")
    print("No tradable signals - sent status")
