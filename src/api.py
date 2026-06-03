import sys
import os
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

_SRC = os.path.dirname(os.path.abspath(__file__))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import yfinance as yf
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel

from data import fetch_price_history
from strategy import moving_average_crossover_signals
from performance import (
    compute_daily_returns,
    compute_cumulative_returns,
    annualized_return,
    annualized_volatility,
    sharpe_ratio,
    max_drawdown,
)
from indicators import (
    relative_strength_index,
    bollinger_bands,
    simple_moving_average,
    exponential_moving_average,
)

app = FastAPI(title="Quant Trading Dashboard")

_BASE = os.path.dirname(_SRC)
_HTML = os.path.join(_BASE, "dashboard", "templates", "index.html")
_WATCHLIST_FILE = os.path.join(_BASE, "watchlist.json")
_PORTFOLIO_FILE = os.path.join(_BASE, "portfolio.json")
_HISTORY_FILE   = os.path.join(_BASE, "portfolio_history.json")


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_watchlist():
    if os.path.exists(_WATCHLIST_FILE):
        with open(_WATCHLIST_FILE) as f:
            return json.load(f)
    return ["AAPL", "MSFT", "GOOGL", "SPY"]


def _save_watchlist(symbols):
    with open(_WATCHLIST_FILE, "w") as f:
        json.dump(symbols, f)


def _load_portfolio():
    if os.path.exists(_PORTFOLIO_FILE):
        with open(_PORTFOLIO_FILE) as f:
            return json.load(f)
    return {"positions": [], "cash": 10000.0}


def _save_portfolio(data):
    with open(_PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)


import requests as _requests

_PRICE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

def _batch_prices(symbols: list[str]) -> dict[str, float]:
    """Fetch live prices for multiple symbols in one HTTP request."""
    prices = {}
    try:
        joined = ",".join(symbols)
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{joined}?interval=1d&range=2d"
        # For multiple symbols use the spark endpoint
        url2 = f"https://query2.finance.yahoo.com/v7/finance/spark?symbols={joined}&range=1d&interval=1d"
        r = _requests.get(url2, headers=_PRICE_HEADERS, timeout=8)
        data = r.json().get("spark", {}).get("result", []) or []
        for item in data:
            sym = item.get("symbol")
            try:
                price = item["response"][0]["meta"]["regularMarketPrice"]
                prices[sym] = float(price)
            except Exception:
                pass
    except Exception:
        pass

    # Fallback: fetch any missing symbols individually via chart API
    for sym in symbols:
        if sym not in prices:
            try:
                url = f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d"
                r = _requests.get(url, headers=_PRICE_HEADERS, timeout=6)
                price = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
                prices[sym] = float(price)
            except Exception:
                pass
    return prices


def _chart_quote(symbol: str) -> dict:
    """Single-symbol price + daily % change via chart API."""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
    r = _requests.get(url, headers=_PRICE_HEADERS, timeout=6)
    meta = r.json()["chart"]["result"][0]["meta"]
    price = float(meta["regularMarketPrice"])
    prev  = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)
    chg   = (price - prev) / prev * 100 if prev else 0
    return {"price": round(price, 2), "change_pct": round(chg, 2), "prev": round(prev, 2)}


def _current_price(symbol: str) -> float:
    prices = _batch_prices([symbol])
    if symbol in prices:
        return prices[symbol]
    try:
        return float(yf.Ticker(symbol).fast_info.last_price)
    except Exception:
        end = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=8)).strftime("%Y-%m-%d")
        df = fetch_price_history(symbol, start, end)
        return float(df.iloc[-1]["Close"]) if not df.empty else 0.0


def _grade(sharpe, mdd, vs_bh):
    score = 0
    if sharpe > 1.5: score += 3
    elif sharpe > 1.0: score += 2
    elif sharpe > 0.5: score += 1
    if mdd > -10: score += 2
    elif mdd > -20: score += 1
    if vs_bh > 5: score += 2
    elif vs_bh > 0: score += 1
    if score >= 6: return "A"
    if score >= 4: return "B"
    if score >= 2: return "C"
    return "D"


# ── Stock classification ──────────────────────────────────────────────────────

_SECTOR_DATA: dict = {
    "Healthcare":             ("Defensive",         5),
    "Consumer Defensive":     ("Defensive",         5),
    "Utilities":              ("Defensive",         5),
    "Communication Services": ("Defensive",         4),
    "Technology":             ("Cyclical",          2),
    "Consumer Cyclical":      ("Cyclical",          1),
    "Financial Services":     ("Cyclical",          2),
    "Financial":              ("Cyclical",          2),
    "Industrials":            ("Cyclical",          2),
    "Basic Materials":        ("Cyclical",          2),
    "Energy":                 ("Cyclical",          3),
    "Real Estate":            ("Cyclical",          2),
}

_SYMBOL_OVERRIDES: dict = {
    "GLD":    ("Counter-Cyclical", 5), "IAU":    ("Counter-Cyclical", 5),
    "NEM":    ("Counter-Cyclical", 5), "GOLD":   ("Counter-Cyclical", 5),
    "AEM":    ("Counter-Cyclical", 5), "WPM":    ("Counter-Cyclical", 5),
    "FNV":    ("Counter-Cyclical", 5), "RGLD":   ("Counter-Cyclical", 5),
    "SPY":    ("Broad Market ETF", 3), "IVV":    ("Broad Market ETF", 3),
    "VTI":    ("Broad Market ETF", 3), "VWRP.L": ("Broad Market ETF", 3),
    "FWRG.L": ("Broad Market ETF", 3), "ACWI.L": ("Broad Market ETF", 3),
    "ESGV":   ("ESG ETF",          3),
    "QQQ":    ("Growth ETF",       2),
    "SMH":    ("Sector ETF",       1), "XLK":   ("Sector ETF", 2), "XLE": ("Sector ETF", 3),
    "VFEG.L": ("Emerging Market ETF", 2),
    "IONQ":   ("Speculative", 1), "OKLO":  ("Speculative", 1),
    "ACHR":   ("Speculative", 1), "RKLB":  ("Speculative", 1),
    "BBAI":   ("Speculative", 1), "COIN":  ("Speculative", 1),
    "MSTR":   ("Speculative", 1),
}

_TYPE_DESCS: dict = {
    "Counter-Cyclical":    "Tends to RISE when markets fall. Gold and precious metals are classic safe havens — investors rush to them in a crisis.",
    "Defensive":           "Holds its value in downturns. Demand for healthcare, food and utilities barely changes regardless of the economy.",
    "Broad Market ETF":    "Tracks the whole market — falls in recessions but has historically always recovered to new highs. Good for long-term holding.",
    "ESG ETF":             "Broad market ETF with ESG screening — similar recession profile to the total market.",
    "Growth ETF":          "Heavy technology weighting. Falls harder than the broad market in recessions and rate-hike cycles.",
    "Sector ETF":          "Concentrated in one sector. Recession risk depends on how cyclical that sector is — semiconductors are very cyclical.",
    "Cyclical":            "Follows the economic cycle — rises in booms, falls in recessions as company revenues decline.",
    "Speculative":         "Pre-profit or very early-stage company. High potential upside but extreme downside risk in any downturn.",
    "Emerging Market ETF": "Developing economies — higher growth potential but more volatile, especially when investors flee to safety.",
    "Unknown":             "Sector data not available — classification uncertain.",
}

_RECESSION_LABELS: dict = {
    5: "Very High — historically holds value or gains during recessions",
    4: "High — typically outperforms the market in downturns",
    3: "Moderate — falls with the market, usually less than average",
    2: "Low — expected to fall significantly in a recession",
    1: "Very Low — could lose 50-80%+ in a severe recession",
}

_BEST_TIME: dict = {
    "Counter-Cyclical":    "Buy during bull markets when investors ignore them, or just before economic slowdowns begin.",
    "Defensive":           "Good at almost any time. Best during late bull markets when they get cheap relative to growth stocks.",
    "Broad Market ETF":    "Good anytime for long-term investors. Buy more during significant corrections (−15%+). Dollar-cost averaging works excellently.",
    "ESG ETF":             "Same as broad market ETFs — ideal for monthly regular investing.",
    "Growth ETF":          "Best in low-interest-rate, growth-friendly environments. Buy on significant tech sector pullbacks.",
    "Sector ETF":          "Cyclical sectors: buy at cycle bottoms when the news is worst. Time entry to economic cycle.",
    "Cyclical":            "Best bought at economic cycle bottoms — when pessimism is highest and the news feels terrible.",
    "Speculative":         "Only buy on major dips in bull market environments. Keep positions tiny (1-3% max). Never chase after big runs.",
    "Emerging Market ETF": "Best when the USD is weakening and global growth is accelerating.",
    "Unknown":             "Research the fundamentals first. Look for consistent revenue growth and a clear path to profitability.",
}


def _cap_tier(market_cap) -> tuple:
    if not market_cap:
        return "Unknown", "Market cap data not available."
    if market_cap >= 200e9:
        return "Mega Cap", ("Over $200B — among the largest companies in the world. Deep institutional ownership and high liquidity. "
                            "Very unlikely to disappear, but also limited chance of spectacular gains.")
    if market_cap >= 10e9:
        return "Large Cap", ("$10B–$200B — established, mature businesses widely covered by analysts. "
                             "More stable than smaller companies with a good balance of stability and growth potential.")
    if market_cap >= 2e9:
        return "Mid Cap", ("$2B–$10B — growing companies with a meaningful market presence. "
                           "More volatile than large caps but offer better long-term growth potential.")
    if market_cap >= 300e6:
        return "Small Cap", ("$300M–$2B — smaller businesses with high growth upside. "
                             "More volatile and less liquid than larger stocks. Higher risk of permanent loss.")
    return "Micro Cap", ("Under $300M — very small and thinly traded. "
                         "Enormous potential gains but equally large risk of total loss. Keep any positions very small.")


def _classify_stock(symbol: str, sector: str, beta, market_cap) -> dict:
    override = _SYMBOL_OVERRIDES.get(symbol.upper())
    if override:
        stock_type, rec_score = override
    else:
        sec = _SECTOR_DATA.get(sector or "")
        stock_type, rec_score = sec if sec else ("Unknown", 3)
    cap_tier, cap_desc = _cap_tier(market_cap)
    return {
        "stock_type":      stock_type,
        "recession_score": rec_score,
        "type_desc":       _TYPE_DESCS.get(stock_type, ""),
        "recession_label": _RECESSION_LABELS.get(rec_score, ""),
        "cap_tier":        cap_tier,
        "cap_desc":        cap_desc,
        "best_time":       _BEST_TIME.get(stock_type, ""),
    }


# ── Scenario impact ranges ────────────────────────────────────────────────────
# Each entry: (low_pct, high_pct, rating, note)
# rating: "safe" | "moderate" | "risk" | "high_risk"

_SCENARIO_IMPACTS: dict = {
    "recession": {
        "Counter-Cyclical":    ( 10,  35, "safe",      "Gold & precious metals typically rise in recessions as investors seek safety."),
        "Defensive":           ( -5,   5, "safe",      "Healthcare, staples and utilities — demand is stable regardless of the economy."),
        "Broad Market ETF":    (-40, -25, "moderate",  "Broad ETFs fall with the overall market in recessions."),
        "ESG ETF":             (-35, -20, "moderate",  "ESG broad ETFs track the market — similar recession exposure."),
        "Growth ETF":          (-50, -35, "risk",      "Tech-heavy ETFs fall harder as growth expectations are slashed and rates stay high."),
        "Sector ETF":          (-55, -30, "risk",      "Semiconductor and cyclical sector ETFs can drop sharply."),
        "Cyclical":            (-50, -30, "risk",      "Cyclical stocks underperform as company revenues fall and budgets are cut."),
        "Speculative":         (-80, -55, "high_risk", "Pre-profit speculative stocks can fall 60-80%+ as investors flee risk entirely."),
        "Emerging Market ETF": (-50, -35, "risk",      "Emerging markets fall harder in global recessions as capital flows to safety."),
        "Unknown":             (-40, -20, "moderate",  "Impact uncertain — no sector classification available."),
    },
    "tech_crash": {
        "Counter-Cyclical":    ( 10,  25, "safe",      "Gold rises as tech-heavy portfolios rotate to safety."),
        "Defensive":           ( -5,   5, "safe",      "Non-tech companies are relatively insulated from a tech crash."),
        "Broad Market ETF":    (-25, -15, "moderate",  "The S&P 500 is 30%+ tech — a tech crash hits broad ETFs meaningfully."),
        "ESG ETF":             (-25, -15, "moderate",  "ESG broad ETFs also have significant tech exposure."),
        "Growth ETF":          (-55, -40, "high_risk", "Nasdaq/tech ETFs are the epicentre of a tech crash."),
        "Sector ETF":          (-60, -35, "high_risk", "Semiconductor ETFs can fall 40-60% in severe tech downturns."),
        "Cyclical":            (-45, -25, "risk",      "Tech cyclicals and semiconductor stocks fall sharply."),
        "Speculative":         (-75, -50, "high_risk", "Speculative tech stocks are hit hardest — investors cut risk completely."),
        "Emerging Market ETF": (-35, -20, "risk",      "EM with tech exposure also suffers in a tech bear market."),
        "Unknown":             (-40, -20, "moderate",  "Impact uncertain."),
    },
    "inflation": {
        "Counter-Cyclical":    ( 15,  30, "safe",      "Gold is one of the best inflation hedges historically — preserves purchasing power."),
        "Defensive":           ( -5,  10, "safe",      "Consumer staples can raise prices to protect margins — relatively resilient."),
        "Broad Market ETF":    (-20, -10, "moderate",  "Inflation erodes real returns; the Fed raises rates, pressuring all valuations."),
        "ESG ETF":             (-20, -10, "moderate",  "Similar to broad market exposure."),
        "Growth ETF":          (-35, -20, "risk",      "Higher rates compress growth stock valuations — Nasdaq is most exposed."),
        "Sector ETF":          (-30, -15, "risk",      "Tech/semiconductor ETFs suffer when the cost of capital rises."),
        "Cyclical":            (-30, -15, "risk",      "Growth tech stocks de-rate sharply when rates rise to fight inflation."),
        "Speculative":         (-55, -30, "high_risk", "Rate hikes devastate pre-profit companies — their future earnings are worth far less."),
        "Emerging Market ETF": (-35, -20, "risk",      "Strong dollar and high rates hurt emerging market assets."),
        "Unknown":             (-25, -10, "moderate",  "Impact uncertain."),
    },
    "recovery": {
        "Counter-Cyclical":    (-10,   5, "moderate", "Gold tends to underperform in bull markets as risk appetite returns."),
        "Defensive":           (  0,  15, "moderate", "Defensive stocks lag in recoveries — investors chase higher-return cyclicals."),
        "Broad Market ETF":    ( 20,  40, "safe",     "Broad market ETFs fully participate in economic recoveries."),
        "ESG ETF":             ( 20,  35, "safe",     "ESG ETFs track the broad market recovery."),
        "Growth ETF":          ( 30,  60, "safe",     "Tech and growth stocks typically lead market recoveries."),
        "Sector ETF":          ( 25,  60, "safe",     "Cyclical sector ETFs can rally strongly from depressed levels."),
        "Cyclical":            ( 30,  70, "safe",     "Cyclicals lead recoveries as economic activity ramps back up."),
        "Speculative":         ( 50, 200, "safe",     "Speculative stocks can multiply 2-4x in strong risk-on recoveries."),
        "Emerging Market ETF": ( 25,  50, "safe",     "EM tends to outperform in global recoveries."),
        "Unknown":             ( 15,  35, "moderate", "Recovery upside uncertain."),
    },
    "geopolitical": {
        "Counter-Cyclical":    ( 20,  40, "safe",      "Gold surges in geopolitical crises — the ultimate safe haven asset."),
        "Defensive":           ( -5,  10, "safe",      "Defensive companies are relatively insulated from geopolitical shocks."),
        "Broad Market ETF":    (-25, -15, "moderate",  "Geopolitical shocks cause sharp drops — usually followed by recovery."),
        "ESG ETF":             (-25, -15, "moderate",  "Similar to broad market — sharp drop but tends to recover."),
        "Growth ETF":          (-35, -20, "risk",      "Tech sells off in risk-off events but often recovers once tensions ease."),
        "Sector ETF":          (-30, -10, "risk",      "Depends on sector — defence benefits, consumer cyclical suffers."),
        "Cyclical":            (-30, -15, "risk",      "Cyclicals sell off in risk-off environments."),
        "Speculative":         (-60, -35, "high_risk", "Speculative stocks are dumped first in any risk-off event."),
        "Emerging Market ETF": (-40, -25, "risk",      "EM often suffers more in geopolitical crises."),
        "Unknown":             (-30, -15, "moderate",  "Impact uncertain."),
    },
}

_SCENARIO_META: dict = {
    "recession":    ("🔴", "Global Recession",          "How your portfolio holds up in a 2008-style recession over 6 months."),
    "tech_crash":   ("💻", "Tech Crash (−40% on tech)", "Your exposure if technology stocks fall 35-50%, similar to 2022 or the dot-com bust."),
    "inflation":    ("📈", "Inflation / Rate Hikes",    "How your portfolio holds up if inflation stays high and central banks raise rates aggressively."),
    "recovery":     ("🟢", "Market Recovery / Bull Run","What your portfolio could do if the economy recovers strongly over 6-12 months."),
    "geopolitical": ("⚡", "Geopolitical Shock",        "How your portfolio would react to a major geopolitical event over 1-3 months."),
}


# ── pydantic bodies ───────────────────────────────────────────────────────────

class WatchlistBody(BaseModel):
    symbols: list[str]


class AddPositionBody(BaseModel):
    symbol: str
    shares: float
    avg_price: float


class CashBody(BaseModel):
    cash: float

class EditPositionBody(BaseModel):
    shares: float


# ── pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return FileResponse(_HTML, media_type="text/html")


# ── watchlist ─────────────────────────────────────────────────────────────────

@app.get("/api/watchlist")
async def get_watchlist():
    return {"symbols": _load_watchlist()}


@app.post("/api/watchlist")
async def update_watchlist(body: WatchlistBody):
    symbols = [s.upper().strip() for s in body.symbols if s.strip()]
    _save_watchlist(symbols)
    return {"symbols": symbols}


# ── quotes ────────────────────────────────────────────────────────────────────

@app.get("/api/quote/{symbol}")
async def get_quote(symbol: str):
    symbol = symbol.upper()
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
        r = _requests.get(url, headers=_PRICE_HEADERS, timeout=6)
        meta = r.json()["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev  = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)
        change = price - prev
        return {
            "symbol": symbol,
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change / prev * 100, 2) if prev else 0.0,
            "high":   round(float(meta.get("regularMarketDayHigh") or price), 2),
            "low":    round(float(meta.get("regularMarketDayLow")  or price), 2),
            "volume": int(meta.get("regularMarketVolume") or 0),
        }
    except Exception:
        try:
            end   = datetime.today().strftime("%Y-%m-%d")
            start = (datetime.today() - timedelta(days=8)).strftime("%Y-%m-%d")
            df    = fetch_price_history(symbol, start, end)
            if len(df) < 2:
                raise HTTPException(404, f"No data for {symbol}")
            last, prev_row = df.iloc[-1], df.iloc[-2]
            change = float(last["Close"]) - float(prev_row["Close"])
            return {
                "symbol": symbol,
                "price": round(float(last["Close"]), 2),
                "change": round(change, 2),
                "change_pct": round(change / float(prev_row["Close"]) * 100, 2),
                "high":   round(float(last["High"]), 2),
                "low":    round(float(last["Low"]), 2),
                "volume": int(last["Volume"]),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))


# ── backtest ──────────────────────────────────────────────────────────────────

@app.get("/api/backtest")
async def run_backtest(
    symbol: str = Query("AAPL"),
    start:  str = Query("2020-01-01"),
    end:    str = Query(None),
    short_window:    int   = Query(50),
    long_window:     int   = Query(200),
    initial_capital: float = Query(10000),
):
    if end is None:
        end = datetime.today().strftime("%Y-%m-%d")
    symbol = symbol.upper()

    try:
        df = fetch_price_history(symbol, start, end)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if len(df) < long_window + 5:
        raise HTTPException(400, f"Need at least {long_window + 5} trading days — try an earlier start date.")

    df = moving_average_crossover_signals(df, short_window=short_window, long_window=long_window)
    df["returns"]          = compute_daily_returns(df["Close"])
    df["strategy_returns"] = df["position"] * df["returns"]
    df["cumulative_returns"] = compute_cumulative_returns(df["strategy_returns"])
    df["buyhold_returns"]  = compute_cumulative_returns(df["returns"])
    df["portfolio_value"]  = initial_capital * (1 + df["cumulative_returns"])
    df["rsi"]              = relative_strength_index(df["Close"])
    bb = bollinger_bands(df["Close"])
    df = pd.concat([df, bb], axis=1)

    total_return = float(df["cumulative_returns"].iloc[-1])
    ann_ret  = float(annualized_return(df["strategy_returns"]))
    ann_vol  = float(annualized_volatility(df["strategy_returns"]))
    s_ratio  = float(sharpe_ratio(df["strategy_returns"]))
    mdd      = float(max_drawdown(df["cumulative_returns"]))
    bh_total = float(df["buyhold_returns"].iloc[-1])
    final_value = float(df["portfolio_value"].iloc[-1])

    sig_diff  = df["signal"].diff().fillna(0)
    buy_pts   = df[sig_diff > 0]
    sell_pts  = df[sig_diff < 0]
    trades    = len(buy_pts) + len(sell_pts)

    trade_list = []
    for idx, row in buy_pts.iterrows():
        trade_list.append({"date": idx.strftime("%Y-%m-%d"), "action": "BUY",  "price": round(float(row["Close"]), 2)})
    for idx, row in sell_pts.iterrows():
        trade_list.append({"date": idx.strftime("%Y-%m-%d"), "action": "SELL", "price": round(float(row["Close"]), 2)})
    trade_list.sort(key=lambda x: x["date"], reverse=True)

    dates = df.index.strftime("%Y-%m-%d").tolist()

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.55, 0.25, 0.20],
        subplot_titles=[
            f"{symbol} — Price, SMAs & Bollinger Bands",
            "Cumulative Returns — Strategy vs Buy & Hold",
            "RSI (14)",
        ],
        vertical_spacing=0.06,
    )

    fig.add_trace(go.Candlestick(
        x=dates,
        open=df["Open"].round(2).tolist(), high=df["High"].round(2).tolist(),
        low=df["Low"].round(2).tolist(),   close=df["Close"].round(2).tolist(),
        name="OHLC",
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
        increasing_fillcolor="#3fb950",  decreasing_fillcolor="#f85149",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(x=dates, y=df["bb_upper"].round(2).tolist(),
        name="BB Upper", line=dict(color="rgba(88,166,255,0.35)", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=df["bb_lower"].round(2).tolist(),
        name="BB Lower", line=dict(color="rgba(88,166,255,0.35)", width=1),
        fill="tonexty", fillcolor="rgba(88,166,255,0.04)"), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=df["sma_short"].round(2).tolist(),
        name=f"SMA {short_window}", line=dict(color="#e3b341", width=1.8)), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=df["sma_long"].round(2).tolist(),
        name=f"SMA {long_window}", line=dict(color="#58a6ff", width=1.8)), row=1, col=1)

    if len(buy_pts):
        fig.add_trace(go.Scatter(
            x=buy_pts.index.strftime("%Y-%m-%d").tolist(),
            y=(buy_pts["Low"] * 0.988).round(2).tolist(),
            mode="markers", name="Buy Signal",
            marker=dict(symbol="triangle-up", size=9, color="#3fb950"),
            hovertemplate="Buy<br>%{x}<br>$%{y:.2f}<extra></extra>",
        ), row=1, col=1)
    if len(sell_pts):
        fig.add_trace(go.Scatter(
            x=sell_pts.index.strftime("%Y-%m-%d").tolist(),
            y=(sell_pts["High"] * 1.012).round(2).tolist(),
            mode="markers", name="Sell Signal",
            marker=dict(symbol="triangle-down", size=9, color="#f85149"),
            hovertemplate="Sell<br>%{x}<br>$%{y:.2f}<extra></extra>",
        ), row=1, col=1)

    fig.add_trace(go.Scatter(x=dates, y=(df["cumulative_returns"]*100).round(2).tolist(),
        name="Strategy", line=dict(color="#a371f7", width=2),
        hovertemplate="Strategy: %{y:.2f}%<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=dates, y=(df["buyhold_returns"]*100).round(2).tolist(),
        name="Buy & Hold", line=dict(color="#8b949e", width=2, dash="dot"),
        hovertemplate="Buy & Hold: %{y:.2f}%<extra></extra>"), row=2, col=1)

    fig.add_trace(go.Scatter(x=dates, y=df["rsi"].round(1).tolist(),
        name="RSI", line=dict(color="#e3b341", width=1.5),
        hovertemplate="RSI: %{y:.1f}<extra></extra>"), row=3, col=1)
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(248,81,73,0.06)",  layer="below", row=3, col=1)
    fig.add_hrect(y0=0,  y1=30,  fillcolor="rgba(63,185,80,0.06)",  layer="below", row=3, col=1)
    fig.add_hline(y=70, line_color="rgba(248,81,73,0.45)", line_dash="dash", row=3, col=1)
    fig.add_hline(y=30, line_color="rgba(63,185,80,0.45)", line_dash="dash", row=3, col=1)

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        autosize=True, showlegend=True,
        legend=dict(bgcolor="rgba(13,17,23,0.85)", bordercolor="#30363d",
                    borderwidth=1, font=dict(size=11), x=0.01, y=0.99),
        margin=dict(l=10, r=10, t=45, b=10),
        xaxis_rangeslider_visible=False,
        font=dict(color="#c9d1d9"),
        hovermode="x unified",
    )
    for row in (1, 2, 3):
        fig.update_yaxes(gridcolor="#21262d", zerolinecolor="#30363d", row=row, col=1)
        fig.update_xaxes(gridcolor="#21262d", zerolinecolor="#30363d", row=row, col=1)
    fig.update_yaxes(ticksuffix="%", row=2, col=1)
    fig.update_yaxes(range=[0, 100], row=3, col=1)

    vs_bh = round((total_return - bh_total) * 100, 2)
    grade = _grade(s_ratio, mdd * 100, vs_bh)

    return {
        "chart": json.loads(fig.to_json()),
        "metrics": {
            "total_return":    round(total_return * 100, 2),
            "ann_return":      round(ann_ret * 100, 2),
            "ann_volatility":  round(ann_vol * 100, 2),
            "sharpe_ratio":    round(s_ratio, 3),
            "max_drawdown":    round(mdd * 100, 2),
            "buyhold_return":  round(bh_total * 100, 2),
            "vs_buyhold":      vs_bh,
            "trades":          trades,
            "final_value":     round(final_value, 2),
            "initial_capital": initial_capital,
            "grade":           grade,
        },
        "trades": trade_list[:30],
    }


# ── portfolio ─────────────────────────────────────────────────────────────────

@app.get("/api/portfolio")
async def get_portfolio():
    return _load_portfolio()


@app.post("/api/portfolio/add")
async def add_position(body: AddPositionBody):
    portfolio = _load_portfolio()
    sym = body.symbol.upper()
    cost = round(body.shares * body.avg_price, 2)

    # Deduct purchase cost from cash
    portfolio["cash"] = round(portfolio.get("cash", 0) - cost, 2)

    for pos in portfolio["positions"]:
        if pos["symbol"] == sym:
            total_shares = pos["shares"] + body.shares
            total_cost   = pos["shares"] * pos["avg_price"] + body.shares * body.avg_price
            pos["shares"]    = round(total_shares, 6)
            pos["avg_price"] = round(total_cost / total_shares, 4)
            _save_portfolio(portfolio)
            return portfolio

    portfolio["positions"].append({
        "symbol":     sym,
        "shares":     round(body.shares, 6),
        "avg_price":  round(body.avg_price, 4),
        "date_added": datetime.today().strftime("%Y-%m-%d"),
    })
    _save_portfolio(portfolio)
    return portfolio


@app.put("/api/portfolio/{symbol}")
async def edit_position(symbol: str, body: EditPositionBody):
    portfolio = _load_portfolio()
    sym = symbol.upper()
    for pos in portfolio["positions"]:
        if pos["symbol"] == sym:
            old_shares = pos["shares"]
            new_shares = round(body.shares, 6)
            diff = new_shares - old_shares
            # Positive diff = buying more (deduct cash), negative = selling some (add cash)
            portfolio["cash"] = round(portfolio.get("cash", 0) - diff * pos["avg_price"], 2)
            if new_shares <= 0:
                # Remove position entirely and refund remaining
                portfolio["cash"] = round(portfolio.get("cash", 0) + new_shares * pos["avg_price"], 2)
                portfolio["positions"] = [p for p in portfolio["positions"] if p["symbol"] != sym]
            else:
                pos["shares"] = new_shares
            _save_portfolio(portfolio)
            return portfolio
    raise HTTPException(404, f"{sym} not in portfolio")


@app.delete("/api/portfolio/{symbol}")
async def remove_position(symbol: str):
    portfolio = _load_portfolio()
    sym = symbol.upper()
    # Refund the original cost basis back to cash when removing
    for pos in portfolio["positions"]:
        if pos["symbol"] == sym:
            refund = round(pos["shares"] * pos["avg_price"], 2)
            portfolio["cash"] = round(portfolio.get("cash", 0) + refund, 2)
            break
    portfolio["positions"] = [p for p in portfolio["positions"] if p["symbol"] != sym]
    _save_portfolio(portfolio)
    return portfolio


@app.put("/api/portfolio/cash")
async def set_cash(body: CashBody):
    portfolio = _load_portfolio()
    portfolio["cash"] = round(body.cash, 2)
    # Lock in the starting budget when cash is set with no positions yet
    if not portfolio.get("positions"):
        portfolio["initial_capital"] = round(body.cash, 2)
    _save_portfolio(portfolio)
    return portfolio


def _record_history(total_value: float, total_invested: float):
    history = []
    if os.path.exists(_HISTORY_FILE):
        with open(_HISTORY_FILE) as f:
            history = json.load(f)
    today = datetime.today().strftime("%Y-%m-%d")
    # only one entry per day — update if already exists
    if history and history[-1]["date"] == today:
        history[-1]["value"]    = round(total_value, 2)
        history[-1]["invested"] = round(total_invested, 2)
    else:
        history.append({"date": today, "value": round(total_value, 2), "invested": round(total_invested, 2)})
    with open(_HISTORY_FILE, "w") as f:
        json.dump(history, f)


@app.get("/api/portfolio/history")
async def portfolio_history():
    if not os.path.exists(_HISTORY_FILE):
        return {"history": []}
    with open(_HISTORY_FILE) as f:
        return {"history": json.load(f)}


@app.get("/api/portfolio/value")
async def portfolio_value():
    portfolio = _load_portfolio()
    positions = portfolio.get("positions", [])

    enriched   = []
    total_mktval = 0.0
    total_cost   = 0.0

    # Fetch all prices in one batch request instead of one call per stock
    symbols = [pos["symbol"] for pos in positions]
    live_prices = _batch_prices(symbols) if symbols else {}

    for pos in positions:
        sym = pos["symbol"]
        try:
            price = live_prices.get(sym) or _current_price(sym)
        except Exception:
            price = pos["avg_price"]

        mktval  = pos["shares"] * price
        cost    = pos["shares"] * pos["avg_price"]
        pnl     = mktval - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0

        total_mktval += mktval
        total_cost   += cost
        # Pull sector from info cache if available (no extra API call)
        cached_info = _info_cache.get(sym)
        sector = cached_info[1].get("sector", "Other") if cached_info else "Other"

        enriched.append({
            **pos,
            "current_price":  round(price, 2),
            "market_value":   round(mktval, 2),
            "cost_basis":     round(cost, 2),
            "pnl":            round(pnl, 2),
            "pnl_pct":        round(pnl_pct, 2),
            "sector":         sector or "Other",
        })

    cash             = portfolio.get("cash", 0.0)
    total_with_cash  = total_mktval + cash

    # initial_capital = cash set by user before any buying
    # stored in portfolio.json; if missing, infer as cash + cost basis
    initial_capital  = portfolio.get("initial_capital") or round(cash + total_cost, 2)

    # P&L vs cost basis (stock performance only)
    stock_pnl     = total_mktval - total_cost
    stock_pnl_pct = (stock_pnl / total_cost * 100) if total_cost > 0 else 0

    # P&L vs starting budget (true overall performance)
    total_pnl     = total_with_cash - initial_capital
    total_pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0

    if total_with_cash > 0:
        _record_history(total_with_cash, initial_capital)

    return {
        "positions":        enriched,
        "cash":             round(cash, 2),
        "total_value":      round(total_with_cash, 2),
        "initial_capital":  round(initial_capital, 2),
        "invested":         round(total_cost, 2),
        "overall_pnl":      round(total_pnl, 2),
        "overall_pct":      round(total_pnl_pct, 2),
        "stock_pnl":        round(stock_pnl, 2),
        "stock_pnl_pct":    round(stock_pnl_pct, 2),
    }


# ── signals / recommendations ─────────────────────────────────────────────────

def _pct_change(df, days):
    n = len(df)
    if n <= days:
        return None
    start_price = float(df["Close"].iloc[-(days + 1)])
    end_price   = float(df["Close"].iloc[-1])
    return round((end_price - start_price) / start_price * 100, 2) if start_price else None


def _calc_mdd(close_series):
    cum  = (1 + close_series.pct_change().fillna(0)).cumprod()
    peak = cum.cummax()
    dd   = (cum - peak) / peak
    return round(float(dd.min()) * 100, 2)


@app.get("/api/recommend/{symbol}")
async def recommend(symbol: str):
    symbol = symbol.upper()
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=365 * 6)).strftime("%Y-%m-%d")

    try:
        df = fetch_price_history(symbol, start, end)
    except Exception as e:
        raise HTTPException(400, str(e))

    if len(df) < 50:
        raise HTTPException(400, "Not enough history (need 50+ trading days)")

    n = len(df)

    # ── indicators ──────────────────────────────────────────────────────────
    sma20  = simple_moving_average(df["Close"], 20)
    sma50  = simple_moving_average(df["Close"], 50)
    sma200 = simple_moving_average(df["Close"], min(200, n - 1))
    rsi14  = relative_strength_index(df["Close"])
    bb     = bollinger_bands(df["Close"])

    ema12     = exponential_moving_average(df["Close"], 12)
    ema26     = exponential_moving_average(df["Close"], 26)
    macd_line = ema12 - ema26
    macd_sig  = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_sig

    price    = float(df["Close"].iloc[-1])
    rsi      = float(rsi14.iloc[-1])
    s20      = float(sma20.iloc[-1])
    s50      = float(sma50.iloc[-1])
    s200     = float(sma200.iloc[-1])
    bb_upper = float(bb["bb_upper"].iloc[-1])
    bb_lower = float(bb["bb_lower"].iloc[-1])
    bb_mid   = float(bb["bb_mid"].iloc[-1])
    macd_val = float(macd_line.iloc[-1])
    macd_s   = float(macd_sig.iloc[-1])
    macd_h   = float(macd_hist.iloc[-1])
    macd_h_prev = float(macd_hist.iloc[-2]) if n >= 2 else macd_h

    above_s50  = price > s50
    above_s200 = price > s200
    golden     = s50 > s200
    macd_bull  = macd_val > macd_s
    macd_rising = macd_h > macd_h_prev

    # position within Bollinger Band
    bb_range = bb_upper - bb_lower
    bb_pct   = round((price - bb_lower) / bb_range * 100, 1) if bb_range > 0 else 50.0

    # ── returns across timeframes ────────────────────────────────────────────
    def ytd_change():
        yr_start = datetime(datetime.today().year, 1, 1)
        ytd_df   = df[df.index >= yr_start]
        if len(ytd_df) < 2: return None
        return round((float(ytd_df["Close"].iloc[-1]) / float(ytd_df["Close"].iloc[0]) - 1) * 100, 2)

    perf = {
        "1W":  _pct_change(df, 5),
        "1M":  _pct_change(df, 21),
        "3M":  _pct_change(df, 63),
        "6M":  _pct_change(df, 126),
        "YTD": ytd_change(),
        "1Y":  _pct_change(df, 252),
        "3Y":  _pct_change(df, 756),
        "5Y":  _pct_change(df, 1260),
    }

    # ── annual returns (year by year) ────────────────────────────────────────
    annual_returns = {}
    for yr in range(datetime.today().year - 5, datetime.today().year):
        yr_df = df[df.index.year == yr]
        if len(yr_df) >= 20:
            r = round((float(yr_df["Close"].iloc[-1]) / float(yr_df["Close"].iloc[0]) - 1) * 100, 2)
            annual_returns[str(yr)] = r

    # ── volatility ───────────────────────────────────────────────────────────
    daily_ret = df["Close"].pct_change()
    vol_1m  = round(float(daily_ret.tail(21).std())  * (252 ** 0.5) * 100, 2)
    vol_3m  = round(float(daily_ret.tail(63).std())  * (252 ** 0.5) * 100, 2)
    vol_1y  = round(float(daily_ret.tail(252).std()) * (252 ** 0.5) * 100, 2)

    # ── drawdowns ────────────────────────────────────────────────────────────
    mdd_1y = _calc_mdd(df["Close"].tail(252))
    mdd_3y = _calc_mdd(df["Close"].tail(min(756, n)))
    mdd_all = _calc_mdd(df["Close"])

    # ── volume ───────────────────────────────────────────────────────────────
    avg_vol_20  = float(df["Volume"].tail(20).mean())
    avg_vol_5   = float(df["Volume"].tail(5).mean())
    vol_ratio   = round(avg_vol_5 / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
    vol_trend   = "High" if vol_ratio > 1.3 else "Low" if vol_ratio < 0.7 else "Normal"

    # ── price levels ─────────────────────────────────────────────────────────
    h52 = float(df["High"].max())
    l52 = float(df["Low"].min())
    pct_from_high = round((price - h52) / h52 * 100, 2)
    pct_from_low  = round((price - l52) / l52 * 100, 2)
    support_3m    = round(float(df["Low"].tail(63).min()), 2)
    resist_3m     = round(float(df["High"].tail(63).max()), 2)
    support_1m    = round(float(df["Low"].tail(21).min()), 2)

    # ── fundamentals from yfinance (cached 24h to avoid rate limits) ─────────
    fund = {"name": symbol, "sector": "—", "industry": "—",
            "market_cap": None, "pe": None, "fwd_pe": None,
            "dividend_yield": None, "beta": None, "avg_volume": None,
            "description": ""}
    _cached = _info_cache.get(symbol)
    if _cached and time.time() - _cached[0] < 86400:
        fund = _cached[1]
    else:
        try:
            info = yf.Ticker(symbol).info
            fund["name"]           = info.get("longName", symbol)
            fund["sector"]         = info.get("sector", "—")
            fund["industry"]       = info.get("industry", "—")
            fund["market_cap"]     = info.get("marketCap")
            fund["pe"]             = info.get("trailingPE")
            fund["fwd_pe"]         = info.get("forwardPE")
            fund["dividend_yield"] = info.get("dividendYield")
            fund["beta"]           = info.get("beta")
            fund["avg_volume"]     = info.get("averageVolume")
            fund["description"]    = info.get("longBusinessSummary", "")[:500]
            _info_cache[symbol]    = (time.time(), fund)
        except Exception:
            pass

    # ── OBV (On Balance Volume) ──────────────────────────────────────────────
    close_arr = df["Close"].values
    vol_arr   = df["Volume"].values
    obv_arr = [0.0]
    for _i in range(1, len(close_arr)):
        if close_arr[_i] > close_arr[_i - 1]:
            obv_arr.append(obv_arr[-1] + vol_arr[_i])
        elif close_arr[_i] < close_arr[_i - 1]:
            obv_arr.append(obv_arr[-1] - vol_arr[_i])
        else:
            obv_arr.append(obv_arr[-1])
    obv_series = pd.Series(obv_arr, index=df.index)
    obv_ma20   = obv_series.rolling(20).mean()
    obv_rising = float(obv_series.iloc[-1]) > float(obv_ma20.iloc[-1])

    # ── Relative Strength vs S&P 500 ────────────────────────────────────────
    rs_1m = rs_3m = 0.0
    try:
        spy_url = "https://query2.finance.yahoo.com/v8/finance/chart/SPY?interval=1d&range=90d"
        spy_r   = _requests.get(spy_url, headers=_PRICE_HEADERS, timeout=5)
        spy_raw = spy_r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        spy_cl  = [c for c in spy_raw if c is not None]
        if len(spy_cl) > 21:
            rs_1m = round((perf.get("1M") or 0) - (spy_cl[-1] - spy_cl[-21]) / spy_cl[-21] * 100, 2)
        if len(spy_cl) > 63:
            rs_3m = round((perf.get("3M") or 0) - (spy_cl[-1] - spy_cl[-63]) / spy_cl[-63] * 100, 2)
    except Exception:
        pass

    # ── signal scoring (weighted) ────────────────────────────────────────────
    bull = 0.0
    bear = 0.0
    reasons = []

    # Trend — 200-day MA most important (weight 2), 50-day (weight 1.5), Golden Cross (weight 2)
    if above_s50:
        bull += 1.5; reasons.append(("bullish", "Price above 50-day average — short-term uptrend intact"))
    else:
        bear += 1.5; reasons.append(("bearish", "Price below 50-day average — short-term downtrend"))

    if above_s200:
        bull += 2; reasons.append(("bullish", "Price above 200-day average — long-term trend is up"))
    else:
        bear += 2; reasons.append(("bearish", "Price below 200-day average — long-term trend is down"))

    if golden:
        bull += 2; reasons.append(("bullish", "Golden Cross — 50-day MA above 200-day MA, classic long-term buy signal"))
    else:
        bear += 2; reasons.append(("bearish", "Death Cross — 50-day MA below 200-day MA, classic long-term warning"))

    # RSI — highest weight (up to 4 pts), most predictive indicator
    if rsi < 25:
        bull += 4; reasons.append(("strong_bullish", f"RSI {rsi:.0f} — heavily oversold. Historically the strongest buy signal in this model"))
    elif rsi < 35:
        bull += 2.5; reasons.append(("strong_bullish", f"RSI {rsi:.0f} — oversold, historically a buying opportunity"))
    elif rsi < 45:
        bull += 1; reasons.append(("mild_bullish", f"RSI {rsi:.0f} — leaning oversold, mild bullish lean"))
    elif rsi > 80:
        bear += 4; reasons.append(("strong_bearish", f"RSI {rsi:.0f} — extremely overbought, strong caution warning"))
    elif rsi > 70:
        bear += 2.5; reasons.append(("strong_bearish", f"RSI {rsi:.0f} — overbought, price may pull back"))
    elif rsi > 60:
        bear += 1; reasons.append(("mild_bearish", f"RSI {rsi:.0f} — elevated but not extreme"))
    else:
        reasons.append(("neutral", f"RSI {rsi:.0f} — neutral zone, no strong momentum signal"))

    # MACD — weight 1 each
    if macd_bull:
        bull += 1; reasons.append(("bullish", "MACD above signal line — upward momentum confirmed"))
    else:
        bear += 1; reasons.append(("bearish", "MACD below signal line — downward momentum"))

    if macd_rising:
        bull += 1; reasons.append(("bullish", "MACD histogram expanding — momentum is strengthening"))
    else:
        bear += 1; reasons.append(("bearish", "MACD histogram contracting — momentum is weakening"))

    # Volume confirmation
    if vol_ratio > 1.3 and (perf.get("1W") or 0) > 0:
        bull += 1; reasons.append(("bullish", f"Volume {vol_ratio:.1f}x above average on an up week — institutions buying"))
    elif vol_ratio > 1.3 and (perf.get("1W") or 0) < 0:
        bear += 1; reasons.append(("bearish", f"Volume {vol_ratio:.1f}x above average on a down week — institutions selling"))
    else:
        reasons.append(("neutral", f"Volume near normal ({vol_ratio:.1f}x average) — no unusual activity"))

    # Bollinger Bands position
    if bb_pct > 85:
        bear += 1.5; reasons.append(("mild_bearish", f"Near upper Bollinger Band ({bb_pct:.0f}%) — price stretched, may pull back"))
    elif bb_pct > 75:
        bear += 0.5; reasons.append(("mild_bearish", f"Approaching upper Bollinger Band ({bb_pct:.0f}%) — getting stretched"))
    elif bb_pct < 15:
        bull += 1.5; reasons.append(("mild_bullish", f"Near lower Bollinger Band ({bb_pct:.0f}%) — deeply stretched down, potential bounce zone"))
    elif bb_pct < 25:
        bull += 0.5; reasons.append(("mild_bullish", f"Approaching lower Bollinger Band ({bb_pct:.0f}%) — potential support nearby"))
    else:
        reasons.append(("neutral", f"Inside Bollinger Bands ({bb_pct:.0f}%) — price in normal range"))

    # OBV — money flow direction
    if obv_rising:
        bull += 1; reasons.append(("bullish", "On Balance Volume rising — more money flowing INTO the stock than out (accumulation)"))
    else:
        bear += 1; reasons.append(("bearish", "On Balance Volume falling — more money flowing OUT of the stock than in (distribution)"))

    # Relative Strength vs S&P 500
    if rs_1m > 5:
        bull += 1.5; reasons.append(("bullish", f"Outperforming S&P 500 by {rs_1m:+.1f}% over 1 month — strong relative strength"))
    elif rs_1m > 2:
        bull += 0.5; reasons.append(("mild_bullish", f"Slightly outperforming S&P 500 ({rs_1m:+.1f}% over 1M)"))
    elif rs_1m < -5:
        bear += 1.5; reasons.append(("bearish", f"Underperforming S&P 500 by {abs(rs_1m):.1f}% over 1 month — weak relative strength"))
    elif rs_1m < -2:
        bear += 0.5; reasons.append(("mild_bearish", f"Slightly underperforming S&P 500 ({rs_1m:+.1f}% over 1M)"))
    else:
        reasons.append(("neutral", f"Performing in line with S&P 500 over 1 month ({rs_1m:+.1f}%)"))

    total = bull + bear
    ratio = bull / total if total > 0 else 0.5

    if rsi < 25 and ratio >= 0.55:  sig, scol = "STRONG BUY", "green"
    elif ratio >= 0.68:             sig, scol = "BUY",         "green"
    elif ratio >= 0.54:             sig, scol = "HOLD",        "yellow"
    elif ratio >= 0.40:             sig, scol = "CAUTION",     "yellow"
    elif rsi > 78:                  sig, scol = "OVERBOUGHT",  "red"
    else:                           sig, scol = "AVOID",       "red"

    # ── plain-English summary ────────────────────────────────────────────────
    if scol == "green":
        summary = (
            f"{fund['name']} is showing mostly bullish signals right now. "
            + (f"The stock is oversold (RSI {rsi:.0f}) — it has been sold off heavily and historically tends to bounce from these levels. " if rsi < 35 else "")
            + ("Both moving averages are pointing up, confirming an uptrend. " if golden and above_s200 else "")
            + ("MACD confirms upward momentum. " if macd_bull else "")
            + "On balance, the technical picture supports holding or adding to a position. Always use sensible position sizes."
        )
        action_own  = f"Hold your position — trend is in your favour. If RSI rises above 70 consider trimming. A stop-loss below ${support_1m:.2f} (recent 1-month low) would limit downside."
        action_want = f"This could be a reasonable entry point. Consider buying in stages rather than all at once. Watch the ${support_3m:.2f} support level — if it breaks, re-evaluate."
    elif scol == "red":
        summary = (
            f"{fund['name']} is showing mostly bearish signals. "
            + (f"RSI is {rsi:.0f} — the stock has run up a lot recently and may be due for a rest or pullback. " if rsi > 65 else "")
            + ("The trend is down — moving averages are bearish. " if not golden and not above_s200 else "")
            + ("MACD confirms downward momentum. " if not macd_bull else "")
            + "This is generally not a good time to be buying."
        )
        action_own  = f"Think carefully about whether you'd buy this today at current prices. Consider a stop-loss below ${support_3m:.2f}. Don't add to a losing position without a clear reason."
        action_want = f"Wait for more bullish signals before entering. Specifically: RSI dropping below 45, price crossing back above ${s50:.2f} (50-day average), and MACD turning positive."
    else:
        summary = (
            f"{fund['name']} has mixed signals — {bull} bullish vs {bear} bearish indicators. "
            + (f"Price is above the long-term 200-day average (${s200:.2f}), suggesting the overall trend is still up, but short-term momentum is uncertain. " if above_s200 else "Price is below both key moving averages — no strong trend. ")
            + f"RSI at {rsi:.0f} is in neutral territory. "
            + "This is a 'watch and wait' situation — no strong reason to rush in either direction."
        )
        action_own  = f"Hold and watch. Set a clear stop-loss level in your mind — something like ${support_3m:.2f} (3-month low). If it breaks that level decisively, consider exiting."
        action_want = f"Wait for a clearer signal. Either RSI drops below 35 and price bounces (potential buy), or trend clears above ${s50:.2f} with volume confirmation."

    # risk description (plain English)
    risk_desc = ""
    if vol_1y < 15:
        risk_desc = "Low volatility stock. Moves slowly and steadily — lower risk, lower potential reward."
    elif vol_1y < 30:
        risk_desc = "Medium volatility. Normal for individual stocks. Expect swings of 1-2% on a typical day."
    elif vol_1y < 50:
        risk_desc = "Higher volatility. Can move 2-4%+ in a single day. Higher risk but more potential upside."
    else:
        risk_desc = "Very high volatility. Can move 5%+ in a day. Only hold a small position if adding this to a portfolio."

    return {
        "symbol":         symbol,
        "name":           fund["name"],
        "sector":         fund["sector"],
        "industry":       fund["industry"],
        "description":    fund["description"],
        "market_cap":     fund["market_cap"],
        "pe":             fund["pe"],
        "fwd_pe":         fund["fwd_pe"],
        "dividend_yield": fund["dividend_yield"],
        "beta":           fund["beta"],
        "signal":         sig,
        "signal_color":   scol,
        "price":          round(price, 2),
        "rsi":            round(rsi, 1),
        "macd":           round(macd_val, 3),
        "macd_signal":    round(macd_s, 3),
        "macd_hist":      round(macd_h, 3),
        "macd_bull":      macd_bull,
        "macd_rising":    macd_rising,
        "sma20":          round(s20, 2),
        "sma50":          round(s50, 2),
        "sma200":         round(s200, 2),
        "bb_upper":       round(bb_upper, 2),
        "bb_lower":       round(bb_lower, 2),
        "bb_mid":         round(bb_mid, 2),
        "bb_pct":         bb_pct,
        "above_sma50":    above_s50,
        "above_sma200":   above_s200,
        "golden_cross":   golden,
        "perf":           perf,
        "annual_returns": annual_returns,
        "vol_1m":         vol_1m,
        "vol_3m":         vol_3m,
        "vol_1y":         vol_1y,
        "risk_desc":      risk_desc,
        "mdd_1y":         mdd_1y,
        "mdd_3y":         mdd_3y,
        "mdd_all":        mdd_all,
        "high_52w":       round(h52, 2),
        "low_52w":        round(l52, 2),
        "pct_from_high":  pct_from_high,
        "pct_from_low":   pct_from_low,
        "support_1m":     support_1m,
        "support_3m":     support_3m,
        "resist_3m":      resist_3m,
        "volume_ratio":   vol_ratio,
        "volume_trend":   vol_trend,
        "obv_rising":     obv_rising,
        "rs_1m":          rs_1m,
        "rs_3m":          rs_3m,
        "bull_signals":   round(bull, 1),
        "bear_signals":   round(bear, 1),
        "reasons":        reasons,
        "summary":        summary,
        "action_own":     action_own,
        "action_want":    action_want,
        # ── stock classification ──
        **_classify_stock(symbol, fund["sector"], fund.get("beta"), fund.get("market_cap")),
    }


# ── optimizer ─────────────────────────────────────────────────────────────────

@app.get("/api/optimize")
async def optimize(
    symbol: str = Query("AAPL"),
    start:  str = Query("2020-01-01"),
    end:    str = Query(None),
    initial_capital: float = Query(10000),
):
    if end is None:
        end = datetime.today().strftime("%Y-%m-%d")
    symbol = symbol.upper()

    try:
        df_base = fetch_price_history(symbol, start, end)
    except ValueError as e:
        raise HTTPException(400, str(e))

    short_windows = [10, 20, 50, 100]
    long_windows  = [50, 100, 200, 300]
    results = []

    for sw in short_windows:
        for lw in long_windows:
            if sw >= lw or lw > len(df_base) - 5:
                continue
            df = df_base.copy()
            df = moving_average_crossover_signals(df, short_window=sw, long_window=lw)
            df["returns"]          = compute_daily_returns(df["Close"])
            df["strat_returns"]    = df["position"] * df["returns"]
            df["cum_returns"]      = compute_cumulative_returns(df["strat_returns"])
            df["bh_returns"]       = compute_cumulative_returns(df["returns"])

            tr    = float(df["cum_returns"].iloc[-1]) * 100
            bh    = float(df["bh_returns"].iloc[-1])  * 100
            ar    = float(annualized_return(df["strat_returns"])) * 100
            vol   = float(annualized_volatility(df["strat_returns"])) * 100
            sh    = float(sharpe_ratio(df["strat_returns"]))
            dd    = float(max_drawdown(df["cum_returns"])) * 100
            tr_n  = int((df["signal"].diff().fillna(0) != 0).sum())
            fv    = initial_capital * (1 + float(df["cum_returns"].iloc[-1]))
            vs_bh = round(tr - bh, 2)

            results.append({
                "short": sw, "long": lw,
                "total_return": round(tr, 2),
                "buyhold_return": round(bh, 2),
                "vs_buyhold": vs_bh,
                "ann_return": round(ar, 2),
                "volatility": round(vol, 2),
                "sharpe": round(sh, 3),
                "max_drawdown": round(dd, 2),
                "trades": tr_n,
                "final_value": round(fv, 2),
                "grade": _grade(sh, dd, vs_bh),
            })

    results.sort(key=lambda x: x["sharpe"], reverse=True)
    bh = results[0]["buyhold_return"] if results else 0

    return {
        "results": results,
        "buyhold_return": round(bh, 2),
        "symbol": symbol,
        "start": start,
        "end": end,
        "initial_capital": initial_capital,
    }


# ── research ──────────────────────────────────────────────────────────────────

_INDEX_SYMBOLS = [
    ("SPY",  "S&P 500"),
    ("QQQ",  "Nasdaq 100"),
    ("DIA",  "Dow Jones"),
    ("IWM",  "Russell 2000"),
    ("^VIX", "VIX Fear Index"),
]
_SECTOR_SYMBOLS = [
    ("XLK",  "Technology"),
    ("XLF",  "Financials"),
    ("XLV",  "Healthcare"),
    ("XLE",  "Energy"),
    ("XLI",  "Industrials"),
    ("XLY",  "Consumer Disc."),
    ("XLP",  "Consumer Staples"),
    ("XLC",  "Communications"),
    ("XLU",  "Utilities"),
    ("XLB",  "Materials"),
    ("XLRE", "Real Estate"),
]
_MOVERS_LIST = [
    "AAPL","MSFT","NVDA","AMD","TSLA","META","GOOGL","AMZN","PLTR","ARM",
    "SMCI","IONQ","OKLO","ACHR","CRWD","NET","NFLX","UBER","COIN","INTC",
    "MU","AVGO","SOFI","RIVN","NEM","GOLD","AEM","GLD","SPY","QQQ",
]
_HEDGE_FUNDS = {
    "Berkshire Hathaway (Buffett)": "0001067983",
    "Pershing Square (Ackman)":     "0001336528",
    "Third Point (Loeb)":           "0001040273",
    "Viking Global":                "0001103804",
    "Appaloosa (Tepper)":           "0001070154",
}
_SEC_UA = {"User-Agent": "quant-dashboard research@example.com"}


@app.get("/api/research/overview")
async def research_overview():
    indices, sectors = [], []
    for sym, name in _INDEX_SYMBOLS:
        try:
            q = _chart_quote(sym)
            indices.append({"symbol": sym, "name": name, **q})
        except Exception:
            pass
    for sym, name in _SECTOR_SYMBOLS:
        try:
            q = _chart_quote(sym)
            sectors.append({"symbol": sym, "name": name, **q})
        except Exception:
            pass
    return {"indices": indices, "sectors": sectors}


@app.get("/api/research/movers")
async def research_movers():
    movers = []
    for sym in _MOVERS_LIST:
        try:
            q = _chart_quote(sym)
            movers.append({"symbol": sym, **q})
        except Exception:
            pass
    movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return {"movers": movers[:15]}


_analyst_cache: dict = {}   # in-memory, 24h TTL
_earnings_cache: dict = {}  # in-memory, 24h TTL
_info_cache: dict = {}      # company fundamentals (name, sector, P/E etc.), 24h TTL
_hedge_cache: dict = {}     # SEC 13F holdings, 6h TTL (changes quarterly)

def _yahoo_quotesummary(symbol: str, modules: str) -> dict:
    """Fetch Yahoo Finance quoteSummary with proper crumb auth."""
    s = _requests.Session()
    s.headers.update(_PRICE_HEADERS)
    # Step 1: get cookies from Yahoo Finance
    s.get("https://finance.yahoo.com", timeout=6)
    # Step 2: get crumb
    cr = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=6)
    crumb = cr.text.strip() if cr.ok and "{" not in cr.text else ""
    # Step 3: call quoteSummary
    params = {"modules": modules, "crumb": crumb} if crumb else {"modules": modules}
    r = s.get(
        f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
        params=params, timeout=10
    )
    return r.json()["quoteSummary"]["result"][0]


@app.get("/api/research/analyst/{symbol}")
async def research_analyst(symbol: str):
    symbol = symbol.upper()

    if symbol in _analyst_cache:
        age, data = _analyst_cache[symbol]
        if time.time() - age < 86400:
            return data

    def _build_out(name, rec, n, cur, mean_t, low_t, high_t):
        upside = round((mean_t - cur) / cur * 100, 1) if (cur and mean_t) else None
        return {
            "symbol": symbol, "name": name,
            "recommendation": rec, "num_analysts": n or 0,
            "mean_target": mean_t, "low_target": low_t, "high_target": high_t,
            "current_price": cur, "upside_pct": upside,
        }

    # Method 1: yfinance .info — most reliable, handles auth internally
    try:
        info = yf.Ticker(symbol).info
        cur    = info.get("regularMarketPrice") or info.get("currentPrice")
        mean_t = info.get("targetMeanPrice")
        rec    = (info.get("recommendationKey") or "—").replace("_", " ").title()
        n      = info.get("numberOfAnalystOpinions")
        if cur or mean_t or n:
            out = _build_out(info.get("longName", symbol), rec, n, cur, mean_t,
                             info.get("targetLowPrice"), info.get("targetHighPrice"))
            _analyst_cache[symbol] = (time.time(), out)
            return out
    except Exception:
        pass

    # Method 2: quoteSummary with crumb auth
    try:
        result_data = _yahoo_quotesummary(symbol, "financialData,summaryDetail,price")
        fd  = result_data.get("financialData", {})
        pr  = result_data.get("price", {})
        cur = (fd.get("currentPrice") or {}).get("raw")
        mean_t = (fd.get("targetMeanPrice") or {}).get("raw")
        out = _build_out(
            pr.get("longName") or pr.get("shortName") or symbol,
            (fd.get("recommendationKey") or "—").replace("_", " ").title(),
            (fd.get("numberOfAnalystOpinions") or {}).get("raw"),
            cur, mean_t,
            (fd.get("targetLowPrice")  or {}).get("raw"),
            (fd.get("targetHighPrice") or {}).get("raw"),
        )
        _analyst_cache[symbol] = (time.time(), out)
        return out
    except Exception:
        pass

    raise HTTPException(503, "Analyst data temporarily unavailable — Yahoo Finance is rate-limiting. Try again in a few minutes.")


@app.get("/api/research/hedgefunds")
async def research_hedge_funds(fund: str = Query("Berkshire Hathaway (Buffett)")):
    cik = _HEDGE_FUNDS.get(fund)
    if not cik:
        raise HTTPException(400, f"Unknown fund. Options: {list(_HEDGE_FUNDS.keys())}")

    # 1. Get SEC submissions list
    subs = _requests.get(
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        headers=_SEC_UA, timeout=12
    ).json()

    filings  = subs["filings"]["recent"]
    forms    = filings["form"]
    acc_nums = filings["accessionNumber"]
    dates    = filings["filingDate"]

    # 2. Find latest 13F-HR filing
    latest_acc, latest_date = None, ""
    for i, form in enumerate(forms):
        if form == "13F-HR" and dates[i] > latest_date:
            latest_acc, latest_date = acc_nums[i], dates[i]
    if not latest_acc:
        raise HTTPException(404, "No 13F-HR filing found")

    # 3. Locate infotable XML via HTML directory listing (JSON index doesn't exist on all filings)
    cik_int   = int(cik)
    acc_clean = latest_acc.replace("-", "")
    dir_r = _requests.get(
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/",
        headers=_SEC_UA, timeout=10
    )

    import re as _re
    # Extract all XML hrefs from the directory listing HTML
    all_xml = _re.findall(r'href="([^"]+\.xml)"', dir_r.text, _re.IGNORECASE)
    all_xml_names = [x.split("/")[-1] for x in all_xml]

    xml_name = None
    # Prefer files with "infotable", "holding", or "13f" in name
    for nm in all_xml_names:
        if any(k in nm.lower() for k in ("infotable", "holding", "13fholding")):
            xml_name = nm; break
    # Fall back to any XML that isn't the primary doc
    if not xml_name:
        for nm in all_xml_names:
            if "primary" not in nm.lower() and "summary" not in nm.lower():
                xml_name = nm; break
    if not xml_name and all_xml_names:
        xml_name = all_xml_names[-1]
    if not xml_name:
        raise HTTPException(404, f"Could not locate holdings XML. Files found: {all_xml_names}")

    # 4. Parse holdings XML
    xml_txt = _requests.get(
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{xml_name}",
        headers=_SEC_UA, timeout=15
    ).content
    root = ET.fromstring(xml_txt)

    # Auto-detect namespace from root tag (varies by filing year/filer)
    ns = ""
    if "}" in root.tag:
        ns = root.tag.split("}")[0].lstrip("{")
    # Also check child tags
    if not ns:
        for child in root:
            if "}" in child.tag:
                ns = child.tag.split("}")[0].lstrip("{")
                break

    def _tx(el, tag):
        ch = el.find(f"{{{ns}}}{tag}") if ns else None
        if ch is None:
            ch = el.find(tag)
        return ch.text.strip() if ch is not None and ch.text else ""

    ns_prefix = f"{{{ns}}}" if ns else ""
    tables = root.findall(f".//{ns_prefix}infoTable")
    if not tables:
        tables = root.findall(".//infoTable")
    holdings, total_val = [], 0
    for tbl in tables:
        try:
            name = _tx(tbl, "nameOfIssuer")
            val  = int(_tx(tbl, "value")) * 1000
            sh_el = tbl.find(f"{ns_prefix}shrsOrPrnAmt") or tbl.find("shrsOrPrnAmt")
            shares = 0
            if sh_el is not None:
                s = sh_el.find(f"{ns_prefix}sshPrnamt") or sh_el.find("sshPrnamt")
                if s is not None and s.text:
                    shares = int(s.text.strip())
            total_val += val
            holdings.append({"name": name, "value": val, "shares": shares})
        except Exception:
            continue

    holdings.sort(key=lambda x: x["value"], reverse=True)
    top = holdings[:25]
    for h in top:
        h["pct"]       = round(h["value"] / total_val * 100, 2) if total_val else 0
        h["value_fmt"] = f"${h['value']/1e9:.2f}B" if h["value"] >= 1e9 else f"${h['value']/1e6:.0f}M"

    return {
        "fund":           fund,
        "filing_date":    latest_date,
        "total_holdings": len(holdings),
        "total_value":    f"${total_val/1e9:.1f}B",
        "top_holdings":   top,
        "fund_list":      list(_HEDGE_FUNDS.keys()),
    }


@app.get("/api/research/news/{symbol}")
async def research_news(symbol: str, count: int = Query(6)):
    symbol = symbol.upper()
    try:
        url = (f"https://query2.finance.yahoo.com/v1/finance/search"
               f"?q={symbol}&newsCount={count}&quotesCount=0&enableFuzzyQuery=false&enableNavLinks=false")
        r = _requests.get(url, headers=_PRICE_HEADERS, timeout=8)
        items = r.json().get("news", [])
        news = []
        for n in items:
            ts = n.get("providerPublishTime", 0)
            try:
                dt_str = datetime.fromtimestamp(ts).strftime("%d %b %H:%M")
            except Exception:
                dt_str = ""
            news.append({
                "title":     n.get("title", ""),
                "link":      n.get("link", ""),
                "publisher": n.get("publisher", ""),
                "time_str":  dt_str,
                "age_hours": round((datetime.now().timestamp() - ts) / 3600, 1) if ts else 99,
            })
        return {"symbol": symbol, "news": news}
    except Exception as e:
        raise HTTPException(400, str(e))


def _fetch_earnings_info(symbol: str) -> dict:
    """Return normalised earnings/dividend calendar. Tries three sources in order."""
    result = {
        "earnings_date": None, "eps_estimate": None,
        "eps_low": None, "eps_high": None, "revenue_estimate": None,
        "ex_dividend_date": None, "dividend_date": None,
    }

    ticker = yf.Ticker(symbol)

    # Method 1: earnings_dates DataFrame (yfinance ≥ 0.2)
    try:
        import pandas as _pd
        ed = ticker.earnings_dates
        if ed is not None and len(ed) > 0:
            now_utc = _pd.Timestamp.now(tz="UTC")
            future  = ed[ed.index > now_utc].sort_index()
            if not future.empty:
                result["earnings_date"] = future.index[0].strftime("%Y-%m-%d")
                eps_cols = [c for c in future.columns
                            if "EPS" in str(c).upper() and "ESTIMATE" in str(c).upper()]
                if eps_cols:
                    v = future.iloc[0][eps_cols[0]]
                    if v is not None and not _pd.isna(v):
                        result["eps_estimate"] = f"{float(v):.2f}"
                return result
    except Exception:
        pass

    # Method 2: .calendar (dict in newer yfinance, DataFrame in older)
    try:
        import pandas as _pd
        cal = ticker.calendar
        if cal is not None:
            if isinstance(cal, dict):
                ed_val = cal.get("Earnings Date")
                if ed_val is not None:
                    dates = ed_val if isinstance(ed_val, list) else [ed_val]
                    result["earnings_date"] = str(dates[0])[:10]
                eps = cal.get("EPS Estimate")
                if eps is not None:
                    try: result["eps_estimate"] = f"{float(eps):.2f}"
                    except Exception: pass
                ex_d = cal.get("Ex-Dividend Date")
                if ex_d: result["ex_dividend_date"] = str(ex_d)[:10]
                div_d = cal.get("Dividend Date")
                if div_d: result["dividend_date"] = str(div_d)[:10]
                if result["earnings_date"]:
                    return result
            elif hasattr(cal, "columns"):
                for col in cal.columns:
                    cs = str(col)
                    for v in cal[col]:
                        if v is None or (hasattr(_pd, "isna") and _pd.isna(v)):
                            continue
                        if "Earnings Date" in cs and not result["earnings_date"]:
                            result["earnings_date"] = str(v)[:10]
                        elif "EPS Estimate" in cs and not result["eps_estimate"]:
                            try: result["eps_estimate"] = f"{float(v):.2f}"
                            except Exception: pass
                if result["earnings_date"]:
                    return result
    except Exception:
        pass

    # Method 3: quoteSummary crumb auth
    try:
        data = _yahoo_quotesummary(symbol, "calendarEvents")
        cal  = data.get("calendarEvents", {})
        earn = cal.get("earnings", {})
        dates = earn.get("earningsDate", [])
        if dates:
            d0 = dates[0]
            result["earnings_date"] = d0.get("fmt") if isinstance(d0, dict) else str(d0)[:10]
        avg = earn.get("earningsAverage")
        if avg: result["eps_estimate"]     = avg.get("fmt") if isinstance(avg, dict) else str(avg)
        lo  = earn.get("earningsLow")
        if lo:  result["eps_low"]          = lo.get("fmt")  if isinstance(lo,  dict) else str(lo)
        hi  = earn.get("earningsHigh")
        if hi:  result["eps_high"]         = hi.get("fmt")  if isinstance(hi,  dict) else str(hi)
        rev = earn.get("revenueAverage")
        if rev: result["revenue_estimate"] = rev.get("longFmt") if isinstance(rev, dict) else str(rev)
        exd = cal.get("exDividendDate")
        if exd: result["ex_dividend_date"] = exd.get("fmt") if isinstance(exd, dict) else str(exd)[:10]
        divd = cal.get("dividendDate")
        if divd: result["dividend_date"]   = divd.get("fmt") if isinstance(divd, dict) else str(divd)[:10]
    except Exception:
        pass

    return result


@app.get("/api/research/catalysts/{symbol}")
async def research_catalysts(symbol: str):
    symbol = symbol.upper()
    info = _fetch_earnings_info(symbol)
    return {"symbol": symbol, **info}


@app.get("/api/research/earnings")
async def research_earnings(symbols: str = Query("")):
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()][:20]
    if not sym_list:
        raise HTTPException(400, "Provide symbols as comma-separated query param")

    uncached = [s for s in sym_list if s not in _earnings_cache or
                time.time() - _earnings_cache[s][0] > 86400]

    for sym in uncached:
        try:
            info = _fetch_earnings_info(sym)
            _earnings_cache[sym] = (time.time(), {"symbol": sym,
                                                   "earnings_date": info["earnings_date"],
                                                   "eps_estimate":  info["eps_estimate"]})
        except Exception:
            _earnings_cache[sym] = (time.time(), {"symbol": sym, "earnings_date": None})

    results = []
    for sym in sym_list:
        data = _earnings_cache.get(sym, (0, {}))[1]
        if data.get("earnings_date"):
            try:
                days = (datetime.strptime(data["earnings_date"], "%Y-%m-%d") - datetime.today()).days
                results.append({**data, "days_away": days})
            except Exception:
                pass

    results.sort(key=lambda x: x.get("days_away", 9999))
    return {"earnings": results}


@app.get("/api/portfolio/morning")
async def portfolio_morning():
    portfolio = _load_portfolio()
    positions = portfolio.get("positions", [])
    if not positions:
        return {"summary": "No positions yet.", "movers": [], "total_change": 0}

    movers = []
    for pos in positions:
        sym = pos["symbol"]
        try:
            q = _chart_quote(sym)
            val_change = round(q["change_pct"] / 100 * q["prev"] * pos["shares"], 2)
            movers.append({
                "symbol":     sym,
                "price":      q["price"],
                "change_pct": q["change_pct"],
                "change_val": val_change,
                "shares":     pos["shares"],
            })
        except Exception:
            pass

    movers.sort(key=lambda x: x["change_val"], reverse=True)
    total_change = round(sum(m["change_val"] for m in movers), 2)
    gainers = [m for m in movers if m["change_pct"] > 0]
    losers  = [m for m in movers if m["change_pct"] < 0]
    flat    = [m for m in movers if m["change_pct"] == 0]

    direction = "up" if total_change > 0 else "down"
    top_g = movers[0]  if movers else None
    top_l = movers[-1] if movers else None

    summary = (
        f"Your portfolio is {direction} ${abs(total_change):,.2f} today. "
        + (f"{top_g['symbol']} is your best performer (+{top_g['change_pct']:.1f}%). " if top_g and top_g["change_pct"] > 0 else "")
        + (f"{top_l['symbol']} is your biggest drag ({top_l['change_pct']:.1f}%). " if top_l and top_l["change_pct"] < 0 else "")
        + f"{len(gainers)} stock{'s' if len(gainers)!=1 else ''} up, "
        + f"{len(losers)} down"
        + (f", {len(flat)} flat" if flat else "") + "."
    )

    return {"summary": summary, "movers": movers, "total_change": total_change}


# ── radar / stock screener ────────────────────────────────────────────────────

_RADAR_UNIVERSE = {
    "🤖 AI & Semiconductors": {
        "desc": "Companies building the chips and software that power artificial intelligence.",
        "stocks": ["NVDA","AMD","AVGO","TSM","ARM","INTC","MU","SMCI"],
    },
    "☁️ Cloud & Cybersecurity": {
        "desc": "Software companies growing fast — cloud computing, data, and security.",
        "stocks": ["MSFT","GOOGL","META","AMZN","PLTR","CRWD","NET","SNOW"],
    },
    "🥇 Gold & Precious Metals": {
        "desc": "Gold miners and royalty companies. Rise when markets crash — a safe hedge.",
        "stocks": ["NEM","GOLD","AEM","WPM","FNV","RGLD","GLD"],
    },
    "⚡ Emerging & Speculative": {
        "desc": "Early-stage companies. High risk, but could multiply if they succeed.",
        "stocks": ["IONQ","OKLO","ACHR","RKLB","COIN","MSTR","SOFI","BBAI"],
    },
    "🛡️ Long-Term & Dividend": {
        "desc": "Stable companies that pay regular dividends and grow slowly and steadily over decades.",
        "stocks": ["AAPL","JNJ","KO","JPM","PG","ABBV","VZ","T"],
    },
    "📈 Core Index ETFs": {
        "desc": "Safest option — these track the whole market. Recommended for beginners as a base.",
        "stocks": ["SPY","QQQ","IVV","VTI","XLK","XLE","GLD","VWRP.L"],
    },
}


def _quick_signal(symbol: str) -> dict:
    """Run a fast technical scan on recent data. Uses disk cache."""
    import time as _t
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=400)).strftime("%Y-%m-%d")

    try:
        df = fetch_price_history(symbol, start, end)
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    if len(df) < 30:
        return {"symbol": symbol, "error": "Not enough data"}

    n     = len(df)
    close = df["Close"]
    price = float(close.iloc[-1])

    # Moving averages
    sma50  = float(simple_moving_average(close, min(50, n - 1)).iloc[-1])
    sma200 = float(simple_moving_average(close, min(200, n - 1)).iloc[-1]) if n >= 60 else sma50

    # RSI
    rsi = float(relative_strength_index(close).iloc[-1])

    # MACD
    ema12    = exponential_moving_average(close, 12)
    ema26    = exponential_moving_average(close, 26)
    macd_line = ema12 - ema26
    macd_sig  = macd_line.ewm(span=9, adjust=False).mean()
    macd_bull = float(macd_line.iloc[-1]) > float(macd_sig.iloc[-1])
    macd_hist = float((macd_line - macd_sig).iloc[-1])
    macd_prev = float((macd_line - macd_sig).iloc[-2]) if n >= 2 else macd_hist
    macd_rising = macd_hist > macd_prev

    # Momentum
    mom_1w  = round((price / float(close.iloc[-6])  - 1) * 100, 2) if n > 6  else 0
    mom_1m  = round((price / float(close.iloc[-22]) - 1) * 100, 2) if n > 22 else 0
    mom_3m  = round((price / float(close.iloc[-63]) - 1) * 100, 2) if n > 63 else 0
    mom_1y  = round((price / float(close.iloc[-252])- 1) * 100, 2) if n >= 252 else None

    # Volatility (annualised)
    vol = round(float(df["Close"].pct_change().std()) * (252 ** 0.5) * 100, 1)

    # Bollinger % position
    bb = bollinger_bands(close)
    bb_upper = float(bb["bb_upper"].iloc[-1])
    bb_lower = float(bb["bb_lower"].iloc[-1])
    bb_range = bb_upper - bb_lower
    bb_pct   = round((price - bb_lower) / bb_range * 100, 1) if bb_range > 0 else 50.0

    # Max drawdown 1Y
    cum  = (1 + df["Close"].pct_change().fillna(0)).cumprod()
    peak = cum.cummax()
    mdd  = round(float(((cum - peak) / peak).min()) * 100, 1)

    above_50  = price > sma50
    above_200 = price > sma200
    golden    = sma50 > sma200

    # Scoring (0–100)
    score = 50
    # Trend (±20)
    if above_50:  score += 7
    else:         score -= 7
    if above_200: score += 8
    else:         score -= 8
    if golden:    score += 5
    else:         score -= 5
    # RSI (±15)
    if rsi < 30:  score += 15
    elif rsi < 40: score += 8
    elif rsi > 75: score -= 15
    elif rsi > 65: score -= 8
    # MACD (±10)
    if macd_bull:    score += 5
    else:            score -= 5
    if macd_rising:  score += 5
    else:            score -= 5
    # Momentum (±10)
    if mom_1m > 5:  score += 5
    elif mom_1m > 0: score += 2
    elif mom_1m < -10: score -= 5
    elif mom_1m < 0:   score -= 2
    if mom_3m > 10: score += 5
    elif mom_3m < -15: score -= 5
    # BB stretch
    if bb_pct > 85: score -= 5
    elif bb_pct < 15: score += 5

    score = max(0, min(100, round(score)))

    if score >= 70:   verdict, vcol = "STRONG BUY",  "green"
    elif score >= 57: verdict, vcol = "BUY",          "green"
    elif score >= 43: verdict, vcol = "HOLD",         "yellow"
    elif score >= 30: verdict, vcol = "RESEARCH",     "yellow"
    else:             verdict, vcol = "AVOID",        "red"

    # Risk level
    if vol < 15:   risk = "Low"
    elif vol < 30: risk = "Medium"
    elif vol < 50: risk = "High"
    else:          risk = "Very High"

    # Plain-English action note
    if vcol == "green" and rsi < 40:
        action = "Oversold and in an uptrend — historically a strong entry point."
    elif vcol == "green":
        action = "Technicals are bullish. Momentum and trend both pointing up."
    elif vcol == "yellow" and score >= 50:
        action = "Mixed signals. No rush — watch for RSI to dip below 45 or a MACD crossover."
    elif vcol == "yellow":
        action = "More bearish than bullish right now. Wait for clearer signals before entering."
    elif rsi > 70:
        action = "Overbought — has run up a lot recently. May need to rest before the next move."
    else:
        action = "Mostly bearish signals. Consider waiting or researching the fundamentals first."

    # Live price
    try:
        q = _chart_quote(symbol)
        price = q["price"]
        daily_chg = q["change_pct"]
    except Exception:
        daily_chg = mom_1w

    return {
        "symbol":      symbol,
        "price":       round(price, 2),
        "daily_chg":   daily_chg,
        "score":       score,
        "verdict":     verdict,
        "verdict_col": vcol,
        "action":      action,
        "rsi":         round(rsi, 1),
        "above_50":    above_50,
        "above_200":   above_200,
        "golden":      golden,
        "macd_bull":   macd_bull,
        "macd_rising": macd_rising,
        "bb_pct":      bb_pct,
        "mom_1w":      mom_1w,
        "mom_1m":      mom_1m,
        "mom_3m":      mom_3m,
        "mom_1y":      mom_1y,
        "vol":         vol,
        "risk":        risk,
        "mdd_1y":      mdd,
        "sma50":       round(sma50, 2),
        "sma200":      round(sma200, 2),
    }


_radar_cache: dict = {}  # {symbol: (timestamp, data)}, 2h TTL

@app.get("/api/radar")
async def radar_scan(category: str = Query("all")):
    if category == "all":
        symbols = []
        for cat in _RADAR_UNIVERSE.values():
            symbols += cat["stocks"]
        symbols = list(dict.fromkeys(symbols))  # deduplicate
    else:
        cat_data = _RADAR_UNIVERSE.get(category)
        if not cat_data:
            raise HTTPException(400, f"Unknown category: {category}")
        symbols = cat_data["stocks"]

    results = []
    now = time.time()
    for sym in symbols:
        cached = _radar_cache.get(sym)
        if cached and now - cached[0] < 7200:  # 2-hour cache
            results.append(cached[1])
        else:
            data = _quick_signal(sym)
            if "error" not in data:
                _radar_cache[sym] = (now, data)
            results.append(data)

    # Enrich every result with fundamentals — serve from _info_cache (24h TTL)
    # or fetch fresh via yfinance. This ensures market cap, P/E, yield, beta
    # are always populated in radar cards.
    for r in results:
        if "error" in r:
            continue
        sym = r["symbol"]
        cached_info = _info_cache.get(sym)
        if cached_info and now - cached_info[0] < 86400:
            fd = cached_info[1]
        else:
            fd = {}
            try:
                info = yf.Ticker(sym).info
                fd = {
                    "name":           info.get("longName", sym),
                    "sector":         info.get("sector", "—"),
                    "industry":       info.get("industry", "—"),
                    "market_cap":     info.get("marketCap"),
                    "pe":             info.get("trailingPE"),
                    "fwd_pe":         info.get("forwardPE"),
                    "dividend_yield": info.get("dividendYield"),
                    "beta":           info.get("beta"),
                    "avg_volume":     info.get("averageVolume"),
                    "description":    info.get("longBusinessSummary", "")[:500],
                }
                _info_cache[sym] = (now, fd)
            except Exception:
                pass
        r["name"]           = fd.get("name", sym)
        r["market_cap"]     = fd.get("market_cap")
        r["pe"]             = fd.get("pe")
        r["fwd_pe"]         = fd.get("fwd_pe")
        r["dividend_yield"] = fd.get("dividend_yield")
        r["beta"]           = fd.get("beta")
        # Update radar cache with enriched data
        if "error" not in r:
            _radar_cache[sym] = (now, r)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {
        "results":    results,
        "categories": {k: v["desc"] for k, v in _RADAR_UNIVERSE.items()},
        "category":   category,
    }


# ── Stock Quality Metrics ─────────────────────────────────────────────────────

@app.get("/api/quality/{symbol}")
async def get_quality_metrics(symbol: str):
    """
    Comprehensive stock quality score combining technical & fundamental metrics.
    Returns: score (0-100), quality breakdown, and investment thesis.
    """
    symbol = symbol.upper()
    
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")
    
    try:
        df = fetch_price_history(symbol, start, end)
    except Exception as e:
        raise HTTPException(400, f"Could not fetch data for {symbol}")
    
    if len(df) < 60:
        raise HTTPException(400, "Not enough historical data (need 60+ days)")
    
    close = df["Close"]
    price = float(close.iloc[-1])
    n = len(df)
    
    # ─── Technical Metrics ───
    sma50 = float(simple_moving_average(close, min(50, n-1)).iloc[-1])
    sma200 = float(simple_moving_average(close, min(200, n-1)).iloc[-1]) if n >= 60 else sma50
    rsi = float(relative_strength_index(close).iloc[-1])
    vol = float(df["Close"].pct_change().std()) * (252 ** 0.5) * 100
    
    # Trend strength (0-20)
    trend_score = 10
    if price > sma50: trend_score += 5
    if price > sma200: trend_score += 5
    
    # Momentum quality (0-20)
    mom_1m = (price / float(close.iloc[-22]) - 1) * 100 if n > 22 else 0
    mom_3m = (price / float(close.iloc[-63]) - 1) * 100 if n > 63 else 0
    mom_score = 10
    if mom_1m > 10: mom_score += 5
    elif mom_1m > 0: mom_score += 2
    if mom_3m > 20: mom_score += 5
    elif mom_3m > 0: mom_score += 2
    
    # Volatility risk penalty (0-15)
    vol_score = 15
    if vol > 50: vol_score -= 10
    elif vol > 35: vol_score -= 5
    elif vol < 15: vol_score += 5
    
    # RSI health (0-20)
    rsi_score = 10
    if 30 <= rsi <= 70: rsi_score += 10
    elif 25 <= rsi <= 75: rsi_score += 5
    if rsi < 30: rsi_score += 10  # Oversold = potential
    
    # Drawdown risk (0-25)
    cum = (1 + close.pct_change().fillna(0)).cumprod()
    peak = cum.cummax()
    mdd = float(((cum - peak) / peak).min()) * 100
    dd_score = 25
    if mdd > -30: dd_score -= 10
    elif mdd > -50: dd_score -= 5
    elif mdd < -50: dd_score -= 15
    
    # Price action patterns (0-15)
    bb = bollinger_bands(close)
    bb_pct = (price - float(bb["bb_lower"].iloc[-1])) / (float(bb["bb_upper"].iloc[-1]) - float(bb["bb_lower"].iloc[-1])) * 100
    pattern_score = 10
    if 20 < bb_pct < 80: pattern_score += 5  # Within bands = healthy
    
    total_score = min(100, int(trend_score + mom_score + vol_score + rsi_score + dd_score + pattern_score))
    
    # Quality tier
    if total_score >= 80: quality = "Excellent"
    elif total_score >= 65: quality = "Good"
    elif total_score >= 50: quality = "Fair"
    else: quality = "Caution"
    
    # Thesis
    bullish_signals = sum([
        price > sma50,
        price > sma200,
        mom_1m > 5,
        mom_3m > 10,
        rsi < 50,
        bb_pct < 30,
    ])
    
    if bullish_signals >= 5:
        thesis = "Strong uptrend with solid momentum. Low RSI suggests room to run."
    elif bullish_signals >= 3:
        thesis = "Positive trend. Wait for pullback or confirmation before entering."
    elif bullish_signals >= 1:
        thesis = "Mixed signals. Research fundamentals before deciding."
    else:
        thesis = "Weak technicals. Consider waiting for a reversal signal."
    
    try:
        q = _chart_quote(symbol)
        daily_chg = q["change_pct"]
    except:
        daily_chg = 0
    
    return {
        "symbol": symbol,
        "price": round(price, 2),
        "daily_change": daily_chg,
        "quality_score": total_score,
        "quality_tier": quality,
        "thesis": thesis,
        "metrics": {
            "trend_score": trend_score,
            "momentum_score": mom_score,
            "volatility_score": vol_score,
            "rsi_score": rsi_score,
            "drawdown_score": dd_score,
            "pattern_score": pattern_score,
        },
        "technical": {
            "price": round(price, 2),
            "sma_50": round(sma50, 2),
            "sma_200": round(sma200, 2),
            "rsi": round(rsi, 1),
            "volatility_pct": round(vol, 1),
            "momentum_1m_pct": round(mom_1m, 1),
            "momentum_3m_pct": round(mom_3m, 1),
            "max_drawdown_1y_pct": round(mdd, 1),
            "bollinger_pct": round(bb_pct, 1),
        }
    }


# ── Add to Watchlist from Radar ───────────────────────────────────────────────

@app.post("/api/watchlist/add/{symbol}")
async def add_to_watchlist(symbol: str):
    """Add a symbol from Radar to the watchlist."""
    symbol = symbol.upper().strip()
    if not symbol:
        raise HTTPException(400, "Symbol cannot be empty")
    
    watchlist = _load_watchlist()
    if symbol not in watchlist:
        watchlist.append(symbol)
        _save_watchlist(watchlist)
    
    return {"symbols": watchlist, "added": symbol}


# ── Portfolio scenario analysis ───────────────────────────────────────────────

@app.get("/api/portfolio/scenario")
async def portfolio_scenario():
    portfolio = _load_portfolio()
    positions = portfolio.get("positions", [])
    if not positions:
        return {"error": "No positions in portfolio"}

    symbols = [pos["symbol"] for pos in positions]
    live_prices = _batch_prices(symbols) if symbols else {}

    classified = []
    total_value = 0.0

    for pos in positions:
        sym = pos["symbol"]
        price = live_prices.get(sym) or pos["avg_price"]
        mkt_val = pos["shares"] * price

        cached = _info_cache.get(sym)
        if cached and time.time() - cached[0] < 86400:
            fd = cached[1]
        else:
            fd = {"sector": "—", "market_cap": None, "beta": None, "dividend_yield": None}
            try:
                info = yf.Ticker(sym).info
                fd = {
                    "name":           info.get("longName", sym),
                    "sector":         info.get("sector", "—"),
                    "industry":       info.get("industry", "—"),
                    "market_cap":     info.get("marketCap"),
                    "beta":           info.get("beta"),
                    "pe":             info.get("trailingPE"),
                    "fwd_pe":         info.get("forwardPE"),
                    "dividend_yield": info.get("dividendYield"),
                    "avg_volume":     info.get("averageVolume"),
                    "description":    info.get("longBusinessSummary", "")[:500],
                }
                _info_cache[sym] = (time.time(), fd)
            except Exception:
                pass

        cls = _classify_stock(sym, fd.get("sector", "—"), fd.get("beta"), fd.get("market_cap"))
        classified.append({
            "symbol":        sym,
            "shares":        pos["shares"],
            "avg_price":     pos["avg_price"],
            "current_price": round(price, 2),
            "market_value":  round(mkt_val, 2),
            "sector":        fd.get("sector", "—"),
            "beta":          fd.get("beta"),
            **cls,
        })
        total_value += mkt_val

    if total_value <= 0:
        return {"error": "Could not calculate portfolio value"}

    # Portfolio-level allocation by type
    type_alloc: dict = {}
    for c in classified:
        t = c["stock_type"]
        type_alloc[t] = type_alloc.get(t, 0) + c["market_value"]
    type_pcts = {t: round(v / total_value * 100, 1) for t, v in type_alloc.items()}

    weighted_beta = sum((c["beta"] or 1.0) * c["market_value"] / total_value for c in classified)
    defensive_pct = sum(type_pcts.get(t, 0) for t in ("Defensive", "Counter-Cyclical"))
    cyclical_pct  = sum(type_pcts.get(t, 0) for t in ("Cyclical", "Growth ETF", "Sector ETF"))
    spec_pct      = type_pcts.get("Speculative", 0)
    etf_pct       = sum(type_pcts.get(t, 0) for t in ("Broad Market ETF", "ESG ETF", "Emerging Market ETF"))

    def _fmt_impact(low, high):
        def _s(v):
            return f"+{v}%" if v > 0 else f"{v}%"
        return f"{_s(low)} to {_s(high)}"

    scenarios = []
    for key, (icon, name, desc) in _SCENARIO_META.items():
        impacts = _SCENARIO_IMPACTS[key]
        pos_ratings = []
        total_weighted = 0.0
        for c in classified:
            imp = impacts.get(c["stock_type"], impacts["Unknown"])
            low_pct, high_pct, rating, note = imp
            midpoint = (low_pct + high_pct) / 2
            weight = c["market_value"] / total_value
            total_weighted += midpoint * weight
            pos_ratings.append({
                "symbol":       c["symbol"],
                "rating":       rating,
                "impact":       _fmt_impact(low_pct, high_pct),
                "note":         note,
                "weight_pct":   round(weight * 100, 1),
                "stock_type":   c["stock_type"],
                "market_value": c["market_value"],
            })
        rating_order = {"high_risk": 0, "risk": 1, "moderate": 2, "safe": 3}
        pos_ratings.sort(key=lambda x: rating_order.get(x["rating"], 2))

        # Portfolio-level advice per scenario
        gold_syms = {"NEM", "GOLD", "AEM", "WPM", "FNV", "RGLD", "GLD"}
        gold_pct = sum(c["market_value"] for c in classified if c["symbol"] in gold_syms) / total_value * 100

        if key == "recession":
            if spec_pct > 15:
                advice = f"Your speculative positions ({spec_pct:.0f}% of portfolio) are the biggest risk — consider reducing these first. They can fall 60-80%+ in a recession. "
            else:
                advice = ""
            if gold_pct > 3:
                advice += f"Your gold holdings ({gold_pct:.0f}%) are a solid recession hedge — consider holding or adding. "
            elif defensive_pct < 20:
                advice += "You have limited defensive exposure. Consider adding gold miners (NEM, AEM) or healthcare stocks. "
            advice += "Tip: In recessions, cash gives you the power to buy great companies at discounted prices."
        elif key == "tech_crash":
            tech_exp = sum(type_pcts.get(t, 0) for t in ("Growth ETF", "Sector ETF", "Cyclical", "Speculative"))
            if tech_exp > 50:
                advice = f"Your portfolio has heavy tech/growth exposure ({tech_exp:.0f}%). A tech crash of 35-50% would significantly impact your portfolio. "
            else:
                advice = "Your portfolio has some tech exposure but meaningful diversification elsewhere. "
            if gold_pct < 10:
                advice += "Adding gold (GLD, NEM, GOLD) as a hedge would cushion tech crashes — gold often rises when tech falls."
        elif key == "inflation":
            if gold_pct > 5:
                advice = f"Your gold holdings ({gold_pct:.0f}%) are an excellent inflation hedge — gold historically preserves purchasing power. "
            else:
                advice = "Consider adding gold exposure (GLD, NEM, GOLD) — gold is one of the best inflation hedges historically. "
            advice += "High-growth tech stocks suffer most when rates rise to fight inflation — their future earnings are worth less at higher discount rates."
        elif key == "recovery":
            if spec_pct > 5:
                advice = f"Your speculative positions ({spec_pct:.0f}%) could be big winners in a recovery — some may multiply 2-4x. "
            else:
                advice = ""
            if cyclical_pct > 20:
                advice += f"Your cyclical tech holdings ({cyclical_pct:.0f}%) are well-positioned to lead a recovery. "
            if etf_pct > 20:
                advice += f"Your broad ETFs ({etf_pct:.0f}%) will capture the full market upswing. "
            advice += "In a strong recovery, reducing gold/defensive exposure and holding more cyclicals can enhance gains."
        else:  # geopolitical
            advice = "Geopolitical shocks tend to cause sharp, fast drops followed by equally fast recovery — often within weeks. "
            if spec_pct > 10:
                advice += f"Speculative positions ({spec_pct:.0f}%) are dumped first in risk-off events — be prepared for sharp drops. "
            advice += "Gold is the most reliable geopolitical hedge. If tensions look prolonged, consider adding GLD or gold miners."

        scenarios.append({
            "key":              key,
            "name":             name,
            "icon":             icon,
            "description":      desc,
            "portfolio_impact": round(total_weighted, 1),
            "advice":           advice.strip(),
            "positions":        pos_ratings,
        })

    return {
        "total_value":   round(total_value, 2),
        "weighted_beta": round(weighted_beta, 2),
        "type_pcts":     type_pcts,
        "defensive_pct": round(defensive_pct, 1),
        "cyclical_pct":  round(cyclical_pct, 1),
        "spec_pct":      round(spec_pct, 1),
        "etf_pct":       round(etf_pct, 1),
        "scenarios":     scenarios,
    }


# ── Sentiment & Trends Analysis ────────────────────────────────────────────────

_PRODUCT_TERMS: dict = {
    "NVDA":  ["nvidia gpu", "buy rtx gpu", "ai accelerator chip"],
    "AAPL":  ["buy iphone", "apple iphone 16", "macbook pro new"],
    "MSFT":  ["microsoft copilot", "github copilot", "azure ai"],
    "GOOGL": ["google search ads", "youtube premium", "google workspace"],
    "AMZN":  ["amazon prime membership", "amazon delivery", "aws cloud"],
    "META":  ["facebook ads", "instagram shopping", "meta quest headset"],
    "TSLA":  ["buy tesla car", "tesla model y", "tesla cybertruck"],
    "NKE":   ["buy nike shoes", "nike air max", "nike running"],
    "CROX":  ["buy crocs", "crocs clog", "crocs shoes"],
    "SBUX":  ["starbucks order", "starbucks new drink", "starbucks reward"],
    "MCD":   ["mcdonalds delivery", "mcdonalds new menu", "big mac"],
    "COST":  ["costco membership", "costco deals", "costco shopping"],
    "WMT":   ["walmart grocery delivery", "walmart plus"],
    "DIS":   ["disney plus subscription", "disney world ticket", "disneyland"],
    "NFLX":  ["netflix new show", "netflix subscription", "netflix series"],
    "UBER":  ["uber ride", "uber eats order", "uber discount"],
    "COIN":  ["coinbase buy crypto", "coinbase app", "buy bitcoin coinbase"],
    "PLTR":  ["palantir ai software", "palantir government"],
    "AMD":   ["amd ryzen cpu", "amd rx gpu", "amd vs nvidia"],
    "INTC":  ["intel processor", "intel core ultra", "intel chip"],
    "SMCI":  ["supermicro ai server", "supermicro gpu rack"],
    "CRWD":  ["crowdstrike security", "crowdstrike falcon"],
    "NET":   ["cloudflare cdn", "cloudflare ddos"],
    "SOFI":  ["sofi bank account", "sofi student loan"],
    "IONQ":  ["quantum computer cloud", "quantum computing service"],
    "OKLO":  ["small nuclear reactor", "nuclear microreactor"],
    "ACHR":  ["electric air taxi", "evtol aircraft"],
    "RKLB":  ["rocket lab launch", "neutron rocket"],
    "NEM":   ["buy gold bars", "gold price today"],
    "GOLD":  ["buy gold investment", "gold etf"],
    "SPY":   ["sp500 index fund", "passive investing etf"],
    "QQQ":   ["nasdaq 100 etf", "tech stocks etf"],
    "SMH":   ["semiconductor etf", "chip stocks buy"],
}

_POS_WORDS = {
    "surge","rally","beat","record","growth","bullish","gain","strong","positive",
    "outperform","exceeded","higher","rise","soar","breakthrough","profit","revenue",
    "expand","innovative","demand","popular","viral","trending","hot","launch",
    "success","boom","upgrade","buy","love","great","amazing","best",
}
_NEG_WORDS = {
    "crash","drop","miss","decline","bearish","loss","warning","fail","weak",
    "concern","risk","recession","lawsuit","investigation","layoff","cut",
    "disappoint","sell","lower","plunge","downgrade","scandal","fraud","recall",
    "shortage","ban","regulation","penalty","slump","tumble","fear","short",
}


def _google_trends_data(symbol: str, extra_terms: list) -> dict:
    try:
        from pytrends.request import TrendReq
    except ImportError:
        return {"error": "pytrends not installed"}

    try:
        pt = TrendReq(hl="en-US", tz=0, timeout=(8, 30), retries=2, backoff_factor=0.5)
        product_list = extra_terms + _PRODUCT_TERMS.get(symbol, [])
        all_terms = list(dict.fromkeys([symbol] + product_list))[:5]

        pt.build_payload(all_terms, timeframe="today 12-m")
        df = pt.interest_over_time()
        if df.empty:
            return {"error": "No trend data from Google Trends"}
        df = df.drop(columns=["isPartial"], errors="ignore")

        series = {}
        for term in all_terms:
            if term not in df.columns:
                continue
            vals = df[term].tolist()
            avg = round(sum(vals) / len(vals), 1) if vals else 0
            recent  = sum(vals[-4:]) / 4 if len(vals) >= 4 else (vals[-1] if vals else 0)
            prev    = sum(vals[-8:-4]) / 4 if len(vals) >= 8 else avg
            trend_p = round((recent - prev) / prev * 100, 1) if prev > 0 else 0
            series[term] = {
                "values":    vals,
                "avg":       avg,
                "current":   vals[-1] if vals else 0,
                "peak":      max(vals) if vals else 0,
                "trend_pct": trend_p,
                "trending":  "up" if trend_p > 5 else "down" if trend_p < -5 else "flat",
            }

        # Rising queries for the primary product term
        rising = []
        primary = product_list[0] if product_list else symbol
        try:
            pt.build_payload([primary], timeframe="today 12-m")
            rel = pt.related_queries()
            rdf = (rel.get(primary) or {}).get("rising")
            if rdf is not None and not rdf.empty:
                rising = [{"query": r["query"], "value": str(r["value"])}
                          for _, r in rdf.head(8).iterrows()]
        except Exception:
            pass

        return {
            "terms": all_terms,
            "dates": df.index.strftime("%Y-%m-%d").tolist(),
            "series": series,
            "rising_queries": rising,
        }
    except Exception as e:
        return {"error": str(e)}


def _reddit_mentions(symbol: str) -> dict:
    headers = {"User-Agent": "QuantDash/1.0"}
    posts, seen = [], set()
    for sub in ["stocks", "investing", "wallstreetbets", "stockmarket"]:
        try:
            r = _requests.get(
                f"https://www.reddit.com/r/{sub}/search.json",
                params={"q": symbol, "sort": "new", "limit": 10, "t": "week"},
                headers=headers, timeout=8,
            )
            if not r.ok:
                continue
            for child in r.json().get("data", {}).get("children", []):
                p = child.get("data", {})
                url = p.get("url", "")
                if url in seen:
                    continue
                seen.add(url)
                title = p.get("title", "")
                tl = title.lower()
                pos = sum(1 for w in _POS_WORDS if w in tl)
                neg = sum(1 for w in _NEG_WORDS if w in tl)
                posts.append({
                    "title":     title,
                    "score":     p.get("score", 0),
                    "comments":  p.get("num_comments", 0),
                    "url":       f"https://reddit.com{p.get('permalink', '')}",
                    "subreddit": p.get("subreddit", sub),
                    "sentiment": "positive" if pos > neg else "negative" if neg > pos else "neutral",
                })
        except Exception:
            continue

    if not posts:
        return {"error": "No Reddit posts found", "posts": [], "sentiment_label": "Unknown", "sentiment_score": 0.5, "post_count": 0}

    pos_c = sum(1 for p in posts if p["sentiment"] == "positive")
    score = pos_c / len(posts)
    return {
        "post_count": len(posts),
        "sentiment_score": round(score, 2),
        "sentiment_label": "Bullish" if score > 0.6 else "Bearish" if score < 0.4 else "Mixed",
        "posts": sorted(posts, key=lambda x: x["score"], reverse=True)[:6],
    }


def _news_sentiment_rss(symbol: str) -> dict:
    try:
        url = f"https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
        r = _requests.get(url, timeout=10, headers=_PRICE_HEADERS)
        root = ET.fromstring(r.content)
        articles = []
        for item in root.findall(".//item")[:12]:
            title = (item.findtext("title") or "").strip()
            tl = title.lower()
            pos = sum(1 for w in _POS_WORDS if w in tl)
            neg = sum(1 for w in _NEG_WORDS if w in tl)
            articles.append({
                "title":     title,
                "link":      (item.findtext("link") or "").strip(),
                "date":      (item.findtext("pubDate") or "")[:22].strip(),
                "sentiment": "positive" if pos > neg else "negative" if neg > pos else "neutral",
            })
        pos_c = sum(1 for a in articles if a["sentiment"] == "positive")
        neg_c = sum(1 for a in articles if a["sentiment"] == "negative")
        total = len(articles)
        score = pos_c / total if total else 0.5
        return {
            "article_count":   total,
            "positive_count":  pos_c,
            "negative_count":  neg_c,
            "neutral_count":   total - pos_c - neg_c,
            "sentiment_score": round(score, 2),
            "sentiment_label": "Bullish" if score > 0.6 else "Bearish" if score < 0.4 else "Mixed",
            "articles":        articles,
        }
    except Exception as e:
        return {"error": str(e), "articles": [], "sentiment_label": "Unknown", "sentiment_score": 0.5}


def _claude_scan(symbol: str, company_name: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"available": False}
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = (
            f"You are a market intelligence analyst. Search the internet and analyse current sentiment for "
            f"{symbol} ({company_name}).\n\n"
            f"Search for: recent Reddit discussions about {symbol}, recent news about {company_name}, "
            f"and product demand signals — e.g. are people searching for or buying {company_name}'s products? "
            f"Any viral trends, Google Trends spikes, social media momentum?\n\n"
            f"Return a clear structured analysis:\n"
            f"OVERALL SENTIMENT: [Bullish/Bearish/Neutral] (confidence %)\n"
            f"KEY SIGNALS: 3 bullet points of what is driving sentiment right now\n"
            f"WHAT THE INTERNET IS SAYING: Key themes from Reddit, Twitter/X, forums\n"
            f"PRODUCT DEMAND SIGNALS: Real-world consumer demand evidence (search trends, reviews, viral moments)\n"
            f"EARNINGS IMPLICATION: What product trends suggest for the next earnings report\n"
            f"RED FLAGS: Any negative signals worth monitoring\n\n"
            f"Be specific, cite what you found, keep it concise and actionable for an investor."
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1400,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in response.content if hasattr(b, "text")), "")
        return {"available": True, "analysis": text}
    except Exception as e:
        return {"available": False, "error": str(e)}


_sentiment_cache: dict = {}  # 1-hour TTL per symbol


@app.get("/api/sentiment/{symbol}")
async def get_sentiment(symbol: str, product: str = Query(None), fresh: bool = Query(False)):
    symbol = symbol.upper()
    cache_key = f"{symbol}:{product or ''}"

    if not fresh and cache_key in _sentiment_cache:
        age, data = _sentiment_cache[cache_key]
        if time.time() - age < 3600:
            return data

    extra = [product] if product else []
    cached_info = _info_cache.get(symbol)
    company_name = cached_info[1].get("name", symbol) if cached_info else symbol

    trends = _google_trends_data(symbol, extra)
    reddit = _reddit_mentions(symbol)
    news   = _news_sentiment_rss(symbol)
    claude = _claude_scan(symbol, company_name)

    scores = [s for s in [reddit.get("sentiment_score"), news.get("sentiment_score")] if s is not None]
    combined = round(sum(scores) / len(scores), 2) if scores else 0.5

    result = {
        "symbol":        symbol,
        "company_name":  company_name,
        "combined_score": combined,
        "combined_label": "Bullish" if combined > 0.6 else "Bearish" if combined < 0.4 else "Mixed",
        "trends":  trends,
        "reddit":  reddit,
        "news":    news,
        "claude":  claude,
        "product_suggestions": _PRODUCT_TERMS.get(symbol, []),
    }

    _sentiment_cache[cache_key] = (time.time(), result)
    return result


@app.get("/api/sentiment/product/search")
async def product_trend_search(term: str = Query(...)):
    """Pure product Google Trends search — spot demand before it hits earnings."""
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=0, timeout=(8, 30), retries=2, backoff_factor=0.5)
        pt.build_payload([term], timeframe="today 12-m")
        df = pt.interest_over_time()
        if df.empty:
            return {"error": "No data found for that term", "term": term}
        df = df.drop(columns=["isPartial"], errors="ignore")
        vals = df[term].tolist() if term in df.columns else []
        rising = []
        try:
            rel = pt.related_queries()
            rdf = (rel.get(term) or {}).get("rising")
            if rdf is not None and not rdf.empty:
                rising = [{"query": r["query"], "value": str(r["value"])} for _, r in rdf.head(10).iterrows()]
        except Exception:
            pass
        avg = round(sum(vals) / len(vals), 1) if vals else 0
        recent  = sum(vals[-4:]) / 4 if len(vals) >= 4 else (vals[-1] if vals else 0)
        prev    = sum(vals[-8:-4]) / 4 if len(vals) >= 8 else avg
        trend_p = round((recent - prev) / prev * 100, 1) if prev > 0 else 0
        return {
            "term": term,
            "dates": df.index.strftime("%Y-%m-%d").tolist(),
            "values": vals,
            "peak": max(vals) if vals else 0,
            "current": vals[-1] if vals else 0,
            "avg": avg,
            "trend_pct": trend_p,
            "trending": "up" if trend_p > 5 else "down" if trend_p < -5 else "flat",
            "rising_queries": rising,
        }
    except ImportError:
        return {"error": "pytrends not installed — run: pip install pytrends"}
    except Exception as e:
        return {"error": str(e), "term": term}
