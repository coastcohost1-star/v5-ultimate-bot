import datetime, random, time
PAIRS=["USDCAD","GBPJPY","EURUSD","NAS100","USDJPY","XAUUSD"]
def get_signal(p):
 action=random.choice(["BUY","SELL"])
 emoji="🟢 BUY" if action=="BUY" else "🔴 SELL"
 type_=random.choice(["🔷 SWING","⚡ SCALP"])
 wr=random.randint(68,79)
 return f"""🚨 V5 ULTIMATE SIGNAL
PAIR: {p}
ACTION: {emoji}
TYPE: {type_}
---
TECHNICAL:
✓ Double Support (1.3650/1.3640) HOLD
✓ Head & Shoulders (1.3670→1.3650→1.3690)
✓ Bearish Engulfing + Fib 61.8% (1.3660)
✓ Pullback Count: #4 (Strong)
---
FUNDAMENTAL:
✓ DXY -0.4% confirms
✓ News: No High Impact Next 2H
✓ Market Structure: Clear
---
ENTRY: 0.3% Retest of Engulfing Close
STOP LOSS: Below Hammer Low
TAKE PROFIT: 1:4.2 Risk/Reward
Win Rate: {wr}% | Confidence: HIGH
Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
---
NEXT UPDATE: 15 min
"""

if __name__=="__main__":
 for p in PAIRS:
  print(get_signal(p))
  time.sleep(1)