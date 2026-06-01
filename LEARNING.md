# Learning Guide for Quant Trading

This project is a quant trading starter designed for someone who already knows Python. It focuses on strategy design, backtest behavior, and the specific insights you need to build better trading systems.

## What this project teaches

- **Market-data workflow**
  - fetching historical OHLCV data from `yfinance`
  - normalizing and cleaning time-series data with `pandas`
- **Indicator engineering**
  - moving averages (SMA, EMA)
  - RSI and Bollinger Bands
  - signal creation from indicator crossovers and thresholds
- **Strategy formation**
  - building a crossover strategy from rule definitions
  - translating signals into positions and delayed execution
  - comparing strategy returns to buy-and-hold benchmarks
- **Performance evaluation**
  - daily returns, cumulative returns, and annualized metrics
  - volatility and Sharpe ratio
  - drawdown and risk assessment
- **Backtesting discipline**
  - avoiding lookahead bias and false signal timing
  - understanding the limitations of simple historic simulations
  - introducing cost, slippage, and realistic execution later

## What to focus on first

1. **Strategy types and edge**
   - trend-following vs mean-reversion
   - momentum signals, breakout filters, and mean-reversion entries
   - why simple moving-average crossovers are a starting point, not the end point
2. **Signal structure**
   - how indicator windows change sensitivity and signal frequency
   - using short-window / long-window as tuning parameters
   - why you often need a second filter (e.g. RSI, volatility, trend strength)
3. **Risk and trade management**
   - position sizing and risk per trade
   - max drawdown and maximum adverse excursion
   - stops, profit targets, and reducing tail risk
4. **Backtest quality**
   - out-of-sample testing and walk-forward analysis
   - transaction costs, slippage, and execution assumptions
   - overfitting and the difference between observed fit and live edge

## How to change the strategy in this project

This project is built to be changed. The main place to update behavior is `src/strategy.py`.

- Replace SMA with `EMA` for a faster trend signal
- Add RSI rules so the strategy only trades when momentum supports it
- Use Bollinger Bands or volatility bands for mean-reversion entries
- Add a simple stop-loss or take-profit in `src/backtest.py`

### Example strategy ideas

- **EMA crossover**
  - `short_window` and `long_window` become EMA lengths
  - use `ema_short > ema_long` for long bias
- **RSI filter**
  - only go long when `rsi < 30` or only exit when `rsi > 70`
- **Bollinger mean reversion**
  - buy near the lower band and exit near the moving average
- **Hybrid trend/risk strategy**
  - only take crossovers when volatility is below a threshold
  - reduce position size during high drawdowns

## Recommended resources for advanced quant work

- **Ernest P. Chan**
  - *Algorithmic Trading*
  - *Quantitative Trading*
  - *Machine Trading*
- **Marcos López de Prado**
  - *Advances in Financial Machine Learning*
- **Python quant libraries**
  - `pandas`, `numpy`, `scipy`, `statsmodels`
- **Online references**
  - QuantStart, Quantpedia, Investopedia for practical strategy motifs
  - `yfinance` documentation for market data caveats

## Practical next builds

### Step 1: replace the current strategy
- change `src/strategy.py` to an EMA or RSI-based rule
- run the backtest again and compare results
- record the parameters and performance in `ALGORITHMS.md`

### Step 2: make the backtest more realistic
- add a fixed cost per trade
- add a spread/slippage assumption
- print the realized trade list or PnL per trade

### Step 3: move toward research workflows
- test multiple symbols or window settings
- build a CSV export of backtest results
- use walk-forward or rolling validation instead of one static period

## What to study next

- **Edge identification**: how to tell a signal that is likely to persist
- **Statistical validation**: correlation, t-tests, p-values, and out-of-sample consistency
- **Execution modeling**: what happens when orders move the market or need partial fills
- **Risk management**: portfolio-level drawdown control and capital allocation

## Notes

- `ALGORITHMS.md` is where you should capture strategy ideas, reference sources, and parameter notes.
- Use this file to document the next strategy you want to build, the hypothesis behind it, and the evidence you gather.
