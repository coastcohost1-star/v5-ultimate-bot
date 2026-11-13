import yfinance as yf, pandas as pd, requests, os
from datetime import datetime, timedelta

TOKEN=os.getenv("TELEGRAM_TOKEN")
CHAT_ID=os.getenv("CHAT_ID")
def send(m): requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data={"chat_id":CHAT_ID,"text":m,"parse_mode":"Markdown"})

PAIRS={
    "EURUSD":"EURUSD=X","GBPUSD":"GBPUSD=X","USDJPY":"USDJPY=X","USDCAD":"USDCAD=X",
    "AUDUSD":"AUDUSD=X","GBPJPY":"GBPJPY=X","XAUUSD":"GC=F","BTCUSD":"BTC-USD",
    "USDZAR":"USDZAR=X","NAS100":"NQ=F","US30":"YM=F","SPX500":"ES=F"
}

def detect_pattern(df):
    last=df.iloc[-1]; prev=df.iloc[-2]
    o,c,h,l=float(last['Open']),float(last['Close']),float(last['High']),float(last['Low'])
    po,pc=float(prev['Open']),float(prev['Close'])
    body=abs(c-o); pr=abs(pc-po); rng=h-l
    if rng==0: return "NO PATTERN"
    if c>o and pc<po and c>po and o<pc and body>pr: return "BULLISH ENGULFING"
    if c<o and pc>po and c<po and o>pc and body>pr: return "BEARISH ENGULFING"
    if body < rng*0.1: return "DOJI"
    if c>o and (o-l)>2*body: return "HAMMER"
    if c<o and (h-c)>2*body: return "SHOOTING STAR"
    if max(o,c)<max(po,pc) and min(o,c)>min(po,pc): return "HARAMI"
    return "TRENDLINE BOUNCE"

def trendline_break(df):
    recent_high=float(df['High'].rolling(20).max().iloc[-2])
    recent_low=float(df['Low'].rolling(20).min().iloc[-2])
    curr=float(df['Close'].iloc[-1])
    prev=float(df['Close'].iloc[-2])
    if prev < recent_high and curr > recent_high: return "BREAKOUT_BUY", recent_high
    if prev > recent_low and curr < recent_low: return "BREAKOUT_SELL", recent_low
    if abs(curr-recent_high) < recent_high*0.0015: return "PULLBACK_HIGH", recent_high
    if abs(curr-recent_low) < recent_low*0.0015: return "PULLBACK_LOW", recent_low
    return None, None

def is_nfp():
    now=datetime.utcnow()
    try:
        first_friday=[datetime(now.year,now.month,1)+timedelta(days=i) for i in range(7) if (datetime(now.year,now.month,1)+timedelta(days=i)).weekday()==4][0]
        return now.date()==first_friday.date() and 11 <= now.hour <= 14
    except: return False

signals_found = False
for name,ticker in PAIRS.items():
    try:
        df15=yf.download(ticker,period="5d",interval="15m",progress=False)
        df1h=yf.download(ticker,period="20d",interval="1h",progress=False)
        if len(df15)<50: continue
        pattern=detect_pattern(df15)
        btype, level = trendline_break(df15)
        
        close=df15['Close']
        delta=close.diff(); gain=delta.where(delta>0,0).rolling(14).mean(); loss=-delta.where(delta<0,0).rolling(14).mean()
        rs=100-(100/(1+gain/loss)); rsi=float(rs.iloc[-1]); price=float(close.iloc[-1])
        
        signal=None; setup=""
        if btype=="BREAKOUT_BUY" or (btype=="PULLBACK_LOW" and "BULLISH" in pattern or "HAMMER" in pattern):
            signal="BUY"; setup="Trendline Support Bounce + Bullish Candle"
        elif btype=="BREAKOUT_SELL" or (btype=="PULLBACK_HIGH" and "BEARISH" in pattern or "SHOOTING" in pattern):
            signal="SELL"; setup="Trendline Resistance Bounce + Bearish Candle"
        elif rsi>70: signal="SELL"; setup="RSI Overbought 70+ Trendline Strategy"
        elif rsi<30: signal="BUY"; setup="RSI Oversold 30- Trendline Strategy"
        
        if not signal: continue
        
        # SCALP vs SWING
        is_major = len(df1h)>20 and abs(price-float(df1h['High'].rolling(20).max().iloc[-1])) < price*0.005
        if is_major and "BREAKOUT" in str(btype):
            trade_type="📈 SWING TRADE"; expiry="Hold 4H-2 Days - Major TL Break"
        else:
            trade_type="⚡ SCALP TRADE"; expiry="Hold 15m-2H - Minor TL"
        
        pip = 1 if "NAS" in name or "US30" in name or "SPX" in name else 0.01 if "JPY" in name else 0.1 if "XAU" in name or "GC" in name else 10 if "BTC" in name else 0.0001
        tp_dist=100*pip; sl_dist=30*pip
        
        if signal=="BUY":
            sl=price-sl_dist; tp=price+tp_dist
            candle_instr="🟢 ENTER ON BULLISH CANDLE: Place BUY STOP 2-5 pips above high of "+pattern+" candle. Aggressive: Market at trendline touch."
        else:
            sl=price+sl_dist; tp=price-tp_dist
            candle_instr="🔴 ENTER ON BEARISH CANDLE: Place SELL STOP 2-5 pips below low of "+pattern+" candle. Aggressive: Market at trendline touch."
        
        nfp="\n⚠️ NFP - Avoid USD!" if is_nfp() and "USD" in name else ""
        
        msg=f"""🎯 *{name} {signal}*
{trade_type}
📊 {setup}
🕯️ Pattern: {pattern}
{candle_instr}
⏱️ Expiry: {expiry}
💰 Entry: `{price:.4f}` TL: {level if level else price:.4f}
🛑 SL: {sl:.4f} (30 pips)
✅ TP: {tp:.4f} (100 pips FIXED)
📈 RSI: {rsi:.1f}{nfp}
🕐 {datetime.utcnow().strftime('%H:%M UTC')}
"""
        send(msg)
        signals_found = True
    except Exception as e:
        print(f"{name} {e}")

if not signals_found:
    send("✅ V6 Bot Live - Scanning: EURUSD, GBPUSD, NAS100, XAUUSD etc - No trendline break yet - Next scan in 15m. Features: BUY/SELL, 100 pips TP, SCALP/SWING, Candle pattern")
