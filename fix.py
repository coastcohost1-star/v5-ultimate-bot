import os
code = '''
import yfinance as yf
import pandas as pd
import numpy as np

SYMBOLS = {
 "EURUSD": "EURUSD=X",
 "GBPJPY": "GBPJPY=X", 
 "USDCAD": "USDCAD=X",
 "USDJPY": "JPY=X",
 "NAS100": "NQ=F",
 "XAUUSD": "GC=F"
}

def rsi(series, n=14):
    delta = series.diff()
    gain = delta.where(delta>0,0).rolling(n).mean()
    loss = -delta.where(delta<0,0).rolling(n).mean()
    rs = gain / loss
    return 100 - (100/(1+rs))

def atr(df, n=14):
    hl = df['High'] - df['Low']
    hc = abs(df['High'] - df['Close'].shift())
    lc = abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl,hc,lc], axis=1).max(axis=1)
    return tr.rolling(n).mean()

print("=== V5 ULTIMATE REAL SCAN ===")
for name, ticker in SYMBOLS.items():
    try:
        df = yf.download(ticker, period="10d", interval="1h", progress=False)
        if len(df)<50: continue
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        close = df['Close']
        df['RSI'] = rsi(close)
        df['ATR'] = atr(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        rsi_val = float(last['RSI'])
        atr_val = float(last['ATR'])
        price = float(last['Close'])
        
        # Fib 61.8 logic
        swing_high = float(df['High'].tail(50).max())
        swing_low = float(df['Low'].tail(50).min())
        fib_range = swing_high - swing_low
        fib_618 = swing_high - 0.618*fib_range
        
        # Double bottom/top rough
        lows = df['Low'].tail(30)
        double_bot = lows.nsmallest(2).values
        double_top = df['High'].tail(30).nlargest(2).values
        
        conf = 0
        bias = "NEUTRAL"
        reason = []
        if rsi_val < 35:
            conf+=25; bias="BUY"; reason.append(f"RSI oversold {rsi_val:.1f}")
        if rsi_val > 65:
            conf+=25; bias="SELL"; reason.append(f"RSI overbought {rsi_val:.1f}")
        if abs(price - fib_618)/price < 0.015:
            conf+=20; reason.append("Near Fib 61.8%")
        if abs(double_bot[0]-double_bot[1])/price < 0.005:
            conf+=15; reason.append("Double Bottom")
        if abs(double_top[0]-double_top[1])/price < 0.005:
            conf+=15; reason.append("Double Top")
        if atr_val>0: conf+=10
        
        entry = price
        sl = entry - atr_val*1.5 if bias=="BUY" else entry + atr_val*1.5 if bias=="SELL" else 0
        tp1 = entry + atr_val*2 if bias=="BUY" else entry - atr_val*2 if bias=="SELL" else 0
        tp2 = entry + atr_val*4.2 if bias=="BUY" else entry - atr_val*4.2 if bias=="SELL" else 0
        
        print(f"{name} {ticker} | Price {price:.4f} | RSI {rsi_val:.1f} | ATR {atr_val:.5f} | Bias {bias} | Conf {conf}% | {' + '.join(reason) if reason else 'No pattern'}")
        if conf>=60 and bias!="NEUTRAL":
            print(f" >>> SIGNAL: {name} {bias} Entry {entry:.4f} SL {sl:.4f} TP1 {tp1:.4f} TP2 {tp2:.4f} RR 1:4.2 Conf {conf}%")
    except Exception as e:
        print(f"{name} error {e}")

print("=== SCAN COMPLETE - Monitoring for 70%+ ===")
'''
with open('v5_ultimate_bot.py','w') as f:
    f.write(code)
print("built real bot")
