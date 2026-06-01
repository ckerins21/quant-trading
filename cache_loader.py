#!/usr/bin/env python3
"""
Pre-load cache with fresh market data using multiple sources.
Run this periodically to keep cache fresh and avoid slow dashboard loads.
"""

import os
import sys
import json
import pickle
import hashlib
import time
from datetime import datetime, timedelta

import requests
import pandas as pd

# Add src to path
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from data import fetch_price_history

_BASE = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_BASE, "cache")
_WATCHLIST_FILE = os.path.join(_BASE, "watchlist.json")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

ALTERNATIVE_SOURCES = {
    "polygon": "https://api.polygon.io/v1/open-close/{symbol}/{date}",
    "finnhub": "https://finnhub.io/api/v1/quote",
    "alpha_vantage": "https://www.alphavantage.co/query",
}


def load_watchlist():
    """Load symbols from watchlist.json"""
    if os.path.exists(_WATCHLIST_FILE):
        with open(_WATCHLIST_FILE) as f:
            return json.load(f)
    return ["AAPL", "MSFT", "GOOGL", "SPY", "QQQ"]


def fetch_from_iex_cloud(symbol: str, date_str: str) -> dict:
    """Fetch data from IEX Cloud (free tier available, no auth required for quote)"""
    try:
        # IEX's free data endpoint
        url = f"https://cloud.iexapis.com/stable/stock/{symbol}/quote"
        r = requests.get(url, headers=_HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return {
                "symbol": symbol,
                "price": data.get("latestPrice"),
                "open": data.get("open"),
                "high": data.get("high"),
                "low": data.get("low"),
                "volume": data.get("latestVolume"),
                "close": data.get("close"),
            }
    except Exception as e:
        print(f"  IEX Cloud error for {symbol}: {e}")
    return {}


def fetch_from_marketstack(symbol: str) -> dict:
    """Fetch from marketstack API"""
    try:
        url = f"https://api.marketstack.com/v1/intraday"
        params = {"symbols": symbol, "limit": 1}
        r = requests.get(url, params=params, headers=_HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("data"):
                quote = data["data"][0]
                return {
                    "symbol": symbol,
                    "price": quote.get("close"),
                    "open": quote.get("open"),
                    "high": quote.get("high"),
                    "low": quote.get("low"),
                    "volume": quote.get("volume"),
                    "close": quote.get("close"),
                }
    except Exception as e:
        print(f"  Marketstack error for {symbol}: {e}")
    return {}


def fetch_historical_from_tiingo(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch historical data from Tiingo (free tier, no key required)"""
    try:
        url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
        params = {
            "startDate": start,
            "endDate": end,
            "token": "demo"  # Tiingo demo token works for basic requests
        }
        r = requests.get(url, params=params, headers=_HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
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
                return df
    except Exception as e:
        print(f"  Tiingo error for {symbol}: {e}")
    return pd.DataFrame()


def save_cache(symbol: str, df: pd.DataFrame, start: str, end: str, interval: str = "1d"):
    """Save dataframe to cache"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    key = f"{symbol}_{start}_{end}_{interval}"
    h = hashlib.md5(key.encode()).hexdigest()[:10]
    path = os.path.join(_CACHE_DIR, f"{symbol}_{h}.pkl")
    
    with open(path, "wb") as f:
        pickle.dump(df, f)
    return path


def pre_load_cache(lookback_days: int = 60):
    """
    Pre-load cache for all watchlist symbols.
    Uses Yahoo Finance but now with fallback to alternative sources.
    """
    watchlist = load_watchlist()
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=lookback_days)
    
    print(f"\n📊 Pre-loading cache for {len(watchlist)} symbols ({lookback_days} days)")
    print(f"   Range: {start_date} to {end_date}\n")
    
    successful = 0
    failed = []
    
    for i, symbol in enumerate(watchlist, 1):
        try:
            print(f"  [{i}/{len(watchlist)}] {symbol}...", end=" ", flush=True)
            
            # Try main fetch_price_history (which tries Yahoo Finance + fallbacks)
            df = fetch_price_history(
                symbol,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
                interval="1d"
            )
            
            if not df.empty:
                save_cache(symbol, df, 
                          start_date.strftime("%Y-%m-%d"),
                          end_date.strftime("%Y-%m-%d"))
                print(f"✓ ({len(df)} rows)")
                successful += 1
            else:
                # Try alternative sources
                print("(Yahoo limited, trying alternatives)...", end=" ", flush=True)
                
                # Try Tiingo
                alt_df = fetch_historical_from_tiingo(
                    symbol,
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d")
                )
                
                if not alt_df.empty:
                    save_cache(symbol, alt_df,
                              start_date.strftime("%Y-%m-%d"),
                              end_date.strftime("%Y-%m-%d"))
                    print(f"✓ Tiingo ({len(alt_df)} rows)")
                    successful += 1
                else:
                    print("✗ Failed")
                    failed.append(symbol)
                    
        except Exception as e:
            print(f"✗ Error: {str(e)[:50]}")
            failed.append(symbol)
        
        # Rate limit: wait between requests
        if i < len(watchlist):
            time.sleep(0.5)
    
    print(f"\n📈 Cache pre-load complete!")
    print(f"   ✓ Successful: {successful}/{len(watchlist)}")
    if failed:
        print(f"   ✗ Failed: {', '.join(failed)}")
    
    return successful, failed


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Pre-load market data cache to avoid slow dashboard loads"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="Number of lookback days (default: 60)"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        help="Load single symbol instead of entire watchlist"
    )
    
    args = parser.parse_args()
    
    if args.symbol:
        print(f"\n📊 Loading data for {args.symbol}...")
        try:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=args.days)
            df = fetch_price_history(
                args.symbol,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )
            if not df.empty:
                save_cache(args.symbol, df,
                          start_date.strftime("%Y-%m-%d"),
                          end_date.strftime("%Y-%m-%d"))
                print(f"✓ Cached {len(df)} rows for {args.symbol}")
            else:
                print(f"✗ No data found for {args.symbol}")
        except Exception as e:
            print(f"✗ Error: {e}")
    else:
        pre_load_cache(args.days)
