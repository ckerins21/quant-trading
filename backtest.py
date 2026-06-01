import argparse
from datetime import datetime

from src.backtest import backtest


def parse_args():
    parser = argparse.ArgumentParser(description="Run the project backtest from the repository root.")
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
