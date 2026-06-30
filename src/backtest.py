import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import argparse

try:
    from data import fetch_price_history
    from strategy import moving_average_crossover_signals
    from performance import (
        compute_daily_returns, compute_cumulative_returns,
        annualized_return, annualized_volatility,
        sharpe_ratio, sortino_ratio, calmar_ratio, max_drawdown,
    )
except ImportError:
    from .data import fetch_price_history
    from .strategy import moving_average_crossover_signals
    from .performance import (
        compute_daily_returns, compute_cumulative_returns,
        annualized_return, annualized_volatility,
        sharpe_ratio, sortino_ratio, calmar_ratio, max_drawdown,
    )


def _apply_stop_loss(positions: np.ndarray, prices: np.ndarray, stop_pct: float) -> np.ndarray:
    """
    Trailing stop-loss applied bar-by-bar.
    When long, exit if close falls more than stop_pct below the highest
    close since entry. Stay flat until the MA signal next turns positive.
    """
    positions = positions.copy().astype(float)
    stopped = False
    peak = None

    for i in range(len(positions)):
        if positions[i] > 0:
            if stopped:
                positions[i] = 0.0
                continue
            if peak is None:
                peak = prices[i]
            else:
                peak = max(peak, prices[i])
            if (prices[i] - peak) / peak < -stop_pct:
                positions[i] = 0.0
                stopped = True
                peak = None
        else:
            # MA signal has exited; clear stop so next long entry is fresh
            stopped = False
            peak = None

    return positions


def _apply_sizing(position: np.ndarray, returns: np.ndarray, sizing: str) -> np.ndarray:
    """Scale position by a sizing rule."""
    if sizing == "half":
        return position * 0.5

    if sizing == "vol_target":
        TARGET_VOL = 0.15          # 15 % annualised
        LOOKBACK   = 20
        sizes = np.ones(len(returns))
        for i in range(LOOKBACK, len(returns)):
            window = returns[i - LOOKBACK:i]
            rv = float(np.std(window, ddof=1)) * np.sqrt(252)
            sizes[i] = min(1.0, TARGET_VOL / rv) if rv > 0 else 1.0
        return position * sizes

    return position  # "full"


def _compute_trade_stats(positions: np.ndarray, prices: np.ndarray):
    """Walk position series to compute win rate and per-trade stats."""
    completed = []
    entry_price = None
    entry_side  = 0
    last_pos    = 0.0

    for i in range(len(positions)):
        pos = positions[i]
        # Entry
        if last_pos == 0 and pos != 0:
            entry_price = prices[i]
            entry_side  = pos
        # Exit
        elif last_pos != 0 and pos == 0:
            if entry_price is not None:
                pnl = (prices[i] - entry_price) / entry_price * entry_side
                completed.append(pnl)
            entry_price = None
            entry_side  = 0
        # Flip (long→short or short→long — only possible in long_short mode)
        elif last_pos != 0 and pos != 0 and pos != last_pos:
            if entry_price is not None:
                pnl = (prices[i] - entry_price) / entry_price * entry_side
                completed.append(pnl)
            entry_price = prices[i]
            entry_side  = pos
        last_pos = pos

    if not completed:
        return {"win_rate": None, "avg_trade_return": None, "num_closed_trades": 0}

    wins = sum(1 for p in completed if p > 0)
    return {
        "win_rate":          round(wins / len(completed), 3),
        "avg_trade_return":  round(sum(completed) / len(completed) * 100, 3),
        "num_closed_trades": len(completed),
    }


def backtest(
    symbol:         str,
    start:          str,
    end:            str,
    short_window:   int   = 50,
    long_window:    int   = 200,
    mode:           str   = "long_only",       # "long_only" | "long_short"
    commission_pct: float = 0.001,             # 0.1 % per one-way leg (10 bps)
    slippage_pct:   float = 0.001,             # 0.1 % per one-way leg (10 bps)
    sizing:         str   = "full",            # "full" | "half" | "vol_target"
    stop_loss_pct:  float | None = None,       # e.g. 0.08 = 8 % trailing stop
    show_plot:      bool  = True,
):
    df = fetch_price_history(symbol, start, end)
    df = moving_average_crossover_signals(df, short_window=short_window, long_window=long_window, mode=mode)

    df["returns"] = compute_daily_returns(df["Close"])

    # --- Apply stop-loss (before sizing so sizing respects exits) ---
    pos_arr = df["position"].values.copy()
    if stop_loss_pct is not None and stop_loss_pct > 0:
        pos_arr = _apply_stop_loss(pos_arr, df["Close"].values, stop_loss_pct)

    # --- Apply position sizing ---
    pos_arr = _apply_sizing(pos_arr, df["returns"].values, sizing)
    df["position"] = pos_arr

    # --- Strategy returns (position is already 1-bar lagged from signal) ---
    df["strategy_returns"] = df["position"] * df["returns"]

    # --- Deduct transaction costs (commission + slippage, one-way per leg) ---
    # Position change magnitude: 0→1 = 1 leg, -1→1 = 2 legs, etc.
    cost_per_leg = commission_pct + slippage_pct
    df["position_change"] = df["position"].diff().abs().fillna(0)
    df["cost"]            = df["position_change"] * cost_per_leg
    df["strategy_returns"] -= df["cost"]

    df["cumulative_returns"] = compute_cumulative_returns(df["strategy_returns"])
    df["buyhold_returns"]    = compute_cumulative_returns(df["returns"])

    # --- Metrics ---
    total_return  = float(df["cumulative_returns"].iloc[-1])
    bh_total      = float(df["buyhold_returns"].iloc[-1])
    ann_ret       = annualized_return(df["strategy_returns"])
    ann_vol       = annualized_volatility(df["strategy_returns"])
    sharpe        = sharpe_ratio(df["strategy_returns"])
    sortino       = sortino_ratio(df["strategy_returns"])
    mdd           = max_drawdown(df["cumulative_returns"])
    calmar        = calmar_ratio(df["strategy_returns"], df["cumulative_returns"])
    total_cost    = float(df["cost"].sum())
    num_trades    = int((df["position_change"] > 0).sum())
    trade_stats   = _compute_trade_stats(df["position"].values, df["Close"].values)

    # Benchmark (buy & hold) metrics
    bh_ann_ret = annualized_return(df["returns"])
    bh_sharpe  = sharpe_ratio(df["returns"])
    bh_mdd     = max_drawdown(df["buyhold_returns"])

    print(f"\nBacktest: {symbol}  |  {start} to {end}")
    print(f"Mode: {mode}  |  Sizing: {sizing}  |  Stop-loss: {f'{stop_loss_pct:.0%}' if stop_loss_pct else 'none'}")
    print(f"Cost model: {commission_pct:.2%} commission + {slippage_pct:.2%} slippage per leg")
    print()
    print(f"{'Metric':<28} {'Strategy':>12} {'Buy & Hold':>12}")
    print("-" * 54)
    print(f"{'Total return':<28} {total_return:>11.2%} {bh_total:>11.2%}")
    print(f"{'Ann. return':<28} {ann_ret:>11.2%} {bh_ann_ret:>11.2%}")
    print(f"{'Ann. volatility':<28} {annualized_volatility(df['strategy_returns']):>11.2%} {annualized_volatility(df['returns']):>11.2%}")
    print(f"{'Sharpe ratio':<28} {sharpe:>11.2f} {bh_sharpe:>11.2f}")
    print(f"{'Sortino ratio':<28} {sortino:>11.2f}")
    print(f"{'Calmar ratio':<28} {calmar:>11.2f}")
    print(f"{'Max drawdown':<28} {mdd:>11.2%} {bh_mdd:>11.2%}")
    print(f"{'vs Buy & Hold':<28} {(total_return - bh_total):>+11.2%}")
    print()
    print(f"Trades executed:       {num_trades}")
    print(f"Total cost drag:       {total_cost:.2%}")
    if trade_stats["num_closed_trades"]:
        print(f"Closed trades:         {trade_stats['num_closed_trades']}")
        print(f"Win rate:              {trade_stats['win_rate']:.1%}")
        print(f"Avg trade return:      {trade_stats['avg_trade_return']:.2f}%")

    if show_plot:
        plt.figure(figsize=(12, 6))
        plt.plot(df.index, df["cumulative_returns"],  label="Strategy (with costs)")
        plt.plot(df.index, df["buyhold_returns"],     label="Buy & hold", linestyle="--")
        plt.title(f"{symbol} SMA {short_window}/{long_window} Crossover — {mode}")
        plt.legend()
        plt.xlabel("Date")
        plt.ylabel("Cumulative return")
        plt.grid(True)
        plt.tight_layout()
        plt.show()

    return {
        "total_return":    total_return,
        "bh_total":        bh_total,
        "ann_return":      ann_ret,
        "ann_volatility":  annualized_volatility(df["strategy_returns"]),
        "sharpe":          sharpe,
        "sortino":         sortino,
        "calmar":          calmar,
        "max_drawdown":    mdd,
        "total_cost":      total_cost,
        "num_trades":      num_trades,
        **trade_stats,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Run a moving average crossover backtest.")
    parser.add_argument("symbol", nargs="?", default="AAPL")
    parser.add_argument("--start",        default="2018-01-01")
    parser.add_argument("--end",          default=datetime.today().strftime("%Y-%m-%d"))
    parser.add_argument("--short-window", type=int,   default=50)
    parser.add_argument("--long-window",  type=int,   default=200)
    parser.add_argument("--mode",         default="long_only", choices=["long_only", "long_short"])
    parser.add_argument("--commission",   type=float, default=0.001, help="One-way commission fraction (default 0.001 = 0.1%%)")
    parser.add_argument("--slippage",     type=float, default=0.001, help="One-way slippage fraction (default 0.001 = 0.1%%)")
    parser.add_argument("--sizing",       default="full", choices=["full", "half", "vol_target"])
    parser.add_argument("--stop-loss",    type=float, default=None, help="Trailing stop-loss fraction, e.g. 0.08 = 8%%")
    parser.add_argument("--no-plot",      action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    backtest(
        args.symbol,
        args.start,
        args.end,
        short_window=args.short_window,
        long_window=args.long_window,
        mode=args.mode,
        commission_pct=args.commission,
        slippage_pct=args.slippage,
        sizing=args.sizing,
        stop_loss_pct=args.stop_loss,
        show_plot=not args.no_plot,
    )
