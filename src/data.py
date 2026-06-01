import os
import time
import pickle
import hashlib
from datetime import datetime

import requests
import pandas as pd
import yfinance as yf

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_CACHE_TTL = 86400  # 24 hours

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}


def _cache_file(symbol: str, start: str, end: str, interval: str) -> str:
    key = f"{symbol}_{start}_{end}_{interval}"
    h = hashlib.md5(key.encode()).hexdigest()[:10]
    return os.path.join(_CACHE_DIR, f"{symbol}_{h}.pkl")


def _fetch_via_chart_api(symbol: str, start: str, end: str, interval: str) -> pd.DataFrame:
    """Fetch OHLCV history using Yahoo Finance chart API (same endpoint as live quotes — not rate-limited the same way)."""
    p1 = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
    p2 = int(datetime.strptime(end,   "%Y-%m-%d").timestamp())
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&period1={p1}&period2={p2}&includePrePost=false"
    )
    r = requests.get(url, headers=_HEADERS, timeout=15)
    result = r.json()["chart"]["result"][0]

    timestamps = result["timestamp"]
    q = result["indicators"]["quote"][0]

    # adjclose is in a separate key if present
    adj = result["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])

    df = pd.DataFrame({
        "Open":   q["open"],
        "High":   q["high"],
        "Low":    q["low"],
        "Close":  adj if adj else q["close"],
        "Volume": q["volume"],
    }, index=pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None).normalize())

    df.index.name = "Date"
    return df.dropna(subset=["Close"])


def _fetch_via_tiingo(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV history from Tiingo (free tier available)."""
    url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
    params = {
        "startDate": start,
        "endDate": end,
        "token": "demo"  # Tiingo demo token for basic free access
    }
    r = requests.get(url, params=params, headers=_HEADERS, timeout=15)
    data = r.json()
    
    if not isinstance(data, list) or len(data) == 0:
        return pd.DataFrame()
    
    df = pd.DataFrame(data)
    df["Date"] = pd.to_datetime(df["date"])
    df = df.set_index("Date")
    df = df[["open", "high", "low", "close", "volume"]].rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume"
    })
    
    return df.dropna(subset=["Close"])


def fetch_price_history(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_file(symbol, start, end, interval)

    # Serve from cache if exists (even if stale, use it as fallback)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < _CACHE_TTL:
            try:
                with open(path, "rb") as f:
                    cached = pickle.load(f)
                    if not cached.empty:
                        return cached
            except Exception:
                pass  # Corrupted cache, fetch fresh

    df = pd.DataFrame()
    last_error = None

    # Method A: Yahoo chart API (works even when yfinance is rate-limited)
    for attempt in range(2):
        try:
            df = _fetch_via_chart_api(symbol, start, end, interval)
            if not df.empty:
                break
        except Exception as e:
            last_error = str(e)
            if attempt == 0:
                time.sleep(2)

    # Method B: yfinance Ticker.history() fallback
    if df.empty:
        for attempt in range(2):
            try:
                raw = yf.Ticker(symbol).history(
                    start=start, end=end,
                    interval=interval,
                    auto_adjust=True, repair=False,
                )
                if not raw.empty:
                    df = raw
                    break
            except Exception as e:
                last_error = str(e)
                if attempt == 0:
                    time.sleep(3)
    
    # Method C: Tiingo as last resort (free demo token available)
    if df.empty:
        try:
            df = _fetch_via_tiingo(symbol, start, end)
        except Exception as e:
            last_error = str(e)

    if df.empty:
        # Try to use stale cache rather than fail completely
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    stale = pickle.load(f)
                    if not stale.empty:
                        return stale
            except Exception:
                pass
        
        raise ValueError(
            f"Could not fetch data for {symbol} ({start} to {end}). "
            "All sources (Yahoo Finance, yfinance, Tiingo) failed. "
            "Try running: python cache_loader.py --symbol {symbol} "
            f"Last error: {last_error}"
        )

    # Normalise columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    df.columns = [c.title() for c in df.columns]

    expected = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}. Got: {list(df.columns)}")

    df = df[expected].dropna()
    if df.empty:
        raise ValueError(f"All rows were empty after cleanup for {symbol}.")

    # Save to cache
    try:
        with open(path, "wb") as f:
            pickle.dump(df, f)
    except Exception as e:
        print(f"Warning: Could not save cache for {symbol}: {e}")

    return df
