"""
Run this once to build your portfolio automatically.
It fetches live prices and adds all 15 positions.

Usage:  .venv\Scripts\python.exe setup_portfolio.py
"""
import sys, time, json, requests
import yfinance as yf

API = "http://localhost:8000"
TOTAL_BUDGET = 50_000  # change this if you want a different total

# How much of the budget to put in each stock (must add to 100)
WEIGHTS = {
    # Core index ETFs — biggest slice, safest base
    "SPY":  10,
    "QQQ":  10,
    # Big stable tech
    "AAPL":  7,
    "MSFT":  7,
    "NVDA":  7,
    "AMD":   5,
    # Cybersecurity / cloud
    "CRWD":  5,
    "NET":   4,
    # Gold miners — hedge against market crashes
    "NEM":   5,
    "GOLD":  5,
    "AEM":   5,
    # Speculative (small positions — high risk, high upside)
    "SMCI":  5,
    "IONQ":  5,
    "OKLO":  5,
    "ACHR":  5,
}

print(f"\nBuilding ${TOTAL_BUDGET:,} paper portfolio...\n")

# Step 1: Set cash
r = requests.put(f"{API}/api/portfolio/cash",
                 json={"cash": TOTAL_BUDGET},
                 headers={"Content-Type": "application/json"})
if r.ok:
    print(f"Cash set to ${TOTAL_BUDGET:,}")
else:
    print("Could not set cash — is the dashboard running?")
    sys.exit(1)

# Step 2: Fetch prices and add each position
failed = []
for symbol, weight_pct in WEIGHTS.items():
    allocation = TOTAL_BUDGET * weight_pct / 100

    # Try fast_info first, then history fallback
    price = None
    for attempt in range(3):
        try:
            price = float(yf.Ticker(symbol).fast_info.last_price)
            break
        except Exception:
            pass
        try:
            r2 = requests.get(f"{API}/api/quote/{symbol}")
            if r2.ok:
                price = r2.json()["price"]
                break
        except Exception:
            pass
        if attempt < 2:
            time.sleep(3)

    if not price:
        print(f"  {symbol}: could not get price — skipping")
        failed.append(symbol)
        continue

    shares = round(allocation / price, 4)
    shares = max(shares, 0.0001)

    r3 = requests.post(f"{API}/api/portfolio/add",
                       json={"symbol": symbol, "shares": shares, "avg_price": round(price, 2)},
                       headers={"Content-Type": "application/json"})
    if r3.ok:
        print(f"  {symbol:5s}  ${price:>8.2f}  x {shares:>8.4f} shares  (${allocation:,.0f} allocation)")
    else:
        print(f"  {symbol}: API error — {r3.text}")
        failed.append(symbol)

    time.sleep(1)  # small gap between requests

print(f"\nDone! Check http://localhost:8000 → Portfolio tab.")
if failed:
    print(f"\nThese failed and need to be added manually: {', '.join(failed)}")
