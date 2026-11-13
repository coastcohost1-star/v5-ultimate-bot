from v5_ultimate_bot import get_signal
import time
PAIRS=["USDCAD","GBPJPY","EURUSD","NAS100","USDJPY","XAUUSD"]
while True:
 for p in PAIRS:
  print(get_signal(p))
  time.sleep(2)