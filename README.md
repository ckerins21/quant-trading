# Quant Trading Starter Project

This project is a simple Python-based quant trading starter that helps you learn the fundamentals by building a moving average crossover backtest.

## What is included

- `src/data.py`: download historical price data from Yahoo Finance
- `src/indicators.py`: calculate moving averages and additional technical indicators
- `src/strategy.py`: generate entry and exit signals for a simple strategy
- `src/backtest.py`: run a backtest and print performance metrics
- `src/performance.py`: compute return, Sharpe ratio, and max drawdown
- `ALGORITHMS.md`: reference list of common quant trading strategies with sources and study notes
- `LEARNING.md`: study guide for the project, concepts, and next steps

## Setup

1. Open a terminal in `C:\Users\caola\Documents\quant-trading`
2. Create a Python virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

## Run the first backtest

From the repository root, you can now run either:

```powershell
python backtest.py
```

or directly with the source script:

```powershell
python src\backtest.py
```

For custom inputs:

```powershell
python backtest.py AAPL --start 2021-01-01 --end 2021-12-31 --short-window 50 --long-window 200 --no-plot
```

## Notes

- Start simple: this project uses a classic moving average crossover strategy.
- Once it works, add more indicators, strategies, and risk controls.
- Paper trade before using real money.

## Running tests

After installing dependencies in your virtual environment, run:

```powershell
pytest -q
```

This will execute the test scaffold in `tests/test_performance.py`.
