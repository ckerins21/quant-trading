import matplotlib.pyplot as plt
from datetime import datetime
import argparse

try:
    from data import fetch_price_history
    from strategy import moving_average_crossover_signals
    from performance import compute_daily_returns, compute_cumulative_returns, annualized_return, annualized_volatility, sharpe_ratio, max_drawdown
except ImportError:
    from .data import fetch_price_history
    from .strategy import moving_average_crossover_signals
    from .performance import compute_daily_returns, compute_cumulative_returns, annualized_return, annualized_volatility, sharpe_ratio, max_drawdown


def count_trades(position_series):
    changes = position_series != position_series.shift(1)
    return int(changes.iloc[1:].sum())


def backtest(symbol: str, start: str, end: str, short_window: int = 50, long_window: int = 200, show_plot: bool = True):
    df = fetch_price_history(symbol, start, end)
    df = moving_average_crossover_signals(df, short_window=short_window, long_window=long_window)

    df["returns"] = compute_daily_returns(df["Close"])
    df["strategy_returns"] = df["position"].shift(1).fillna(0) * df["returns"]
    df["cumulative_returns"] = compute_cumulative_returns(df["strategy_returns"])

    total_return = df["cumulative_returns"].iloc[-1]
    ann_return = annualized_return(df["strategy_returns"])
    ann_vol = annualized_volatility(df["strategy_returns"])
    sharpe = sharpe_ratio(df["strategy_returns"])
    mdd = max_drawdown(df["cumulative_returns"])
    trades = count_trades(df["position"])

    print(f"Backtest symbol: {symbol}")
    print(f"Period: {start} to {end}")
    print(f"Short window: {short_window}, long window: {long_window}")
    print(f"Total return: {total_return:.2%}")
    print(f"Annualized return: {ann_return:.2%}")
    print(f"Annualized volatility: {ann_vol:.2%}")
    print(f"Sharpe ratio: {sharpe:.2f}")
    print(f"Max drawdown: {mdd:.2%}")
    print(f"Number of trades: {trades}")

    if show_plot:
        plt.figure(figsize=(12, 6))
        plt.plot(df.index, df["cumulative_returns"], label="Strategy cumulative returns")
        plt.plot(df.index, compute_cumulative_returns(df["returns"]), label="Buy and hold")
        plt.title(f"{symbol} Moving Average Crossover Backtest")
        plt.legend()
        plt.xlabel("Date")
        plt.ylabel("Cumulative return")
        plt.grid(True)
        plt.tight_layout()
        plt.show()


def parse_args():
    parser = argparse.ArgumentParser(description="Run a moving average crossover backtest.")
    parser.add_argument("symbol", nargs="?", default="AAPL", help="Ticker symbol to backtest")
    parser.add_argument("--start", default="2018-01-01", help="Start date in YYYY-MM-DD format")
    parser.add_argument("--end", default=datetime.today().strftime("%Y-%m-%d"), help="End date in YYYY-MM-DD format")
    parser.add_argument("--short-window", type=int, default=50, help="Short moving average window")
    parser.add_argument("--long-window", type=int, default=200, help="Long moving average window")
    parser.add_argument("--no-plot", action="store_true", help="Do not show the performance plot")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    backtest(
        args.symbol,
        args.start,
        args.end,
        short_window=args.short_window,
        long_window=args.long_window,
        show_plot=not args.no_plot,
    )
