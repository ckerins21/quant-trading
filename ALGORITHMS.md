# Algorithm Reference

This file lists common algorithmic trading strategies, notes on where they appear in the literature, and references for study.

## 1. Moving Average Crossover
- Description: Enter when a short-term moving average crosses above a long-term moving average; exit when it crosses below.
- Source: Ernest P. Chan, *Algorithmic Trading: Winning Strategies and Their Rationale*.
- Notes: One of the simplest trend-following models; good for understanding signal generation and backtesting basics.
- Reference: Chapter 6, "Trend Following and Mean Reversion".

## 2. Bollinger Bands Mean Reversion
- Description: Use upper and lower bands around a moving average to identify overbought and oversold conditions.
- Source: John Bollinger, also commonly referenced in Chan's strategy discussions.
- Notes: Often used as a mean-reversion strategy when price touches the band and then reverts toward the center.
- Reference: *Bollinger on Bollinger Bands* and general mean-reversion chapters in Chan.

## 3. RSI Mean Reversion
- Description: Use the Relative Strength Index (RSI) to identify overbought (>70) and oversold (<30) conditions.
- Source: Ernest P. Chan, *Algorithmic Trading* and *Quantitative Trading*.
- Notes: RSI is a momentum oscillator; combined with mean reversion it can form a simple entry/exit rule.

## 4. Pairs Trading / Statistical Arbitrage
- Description: Trade a pair of securities that historically move together; go long the underperformer and short the outperformer when the spread diverges.
- Source: Ernest P. Chan, *Algorithmic Trading* and *Machine Trading*.
- Notes: Requires cointegration testing, spread construction, and a mean-reversion entry/exit model.
- Reference: Chapter 8, "Statistical Arbitrage".

## 5. Momentum Breakout
- Description: Buy when the price breaks above a recent high and sell on a breakdown below a recent low.
- Source: Ernest P. Chan, *Machine Trading*.
- Notes: This is a pure trend-following approach and often uses multiple timeframes.

## 6. Mean-Reversion on Intraday Returns
- Description: Trade the overnight or intraday return reversal, assuming extreme short-term moves tend to revert.
- Source: Ernest P. Chan, *Machine Trading*.
- Notes: Useful for intraday strategies and pairs trades.

## 7. Volatility Breakout
- Description: Buy when price moves outside a volatility range and add filters based on volatility and trend.
- Source: *Machine Trading* and many practical trading blogs.
- Notes: Volatility breakouts are common in commodity and futures trading.

## 8. Kalman Filter Pairs Trading
- Description: Use a Kalman filter to dynamically estimate hedge ratios for a pairs-trading spread.
- Source: Advanced quant finance literature; sometimes referenced by Chan for improved statistical arbitrage.
- Notes: More advanced than simple OLS pairs trading.

## 9. Market Making / Mean Reversion
- Description: Post bid and ask quotes and profit from the bid-ask spread, while hedging inventory.
- Source: Quantitative market microstructure literature, not directly from Chan but important for algorithmic trading.
- Notes: Requires transaction cost modeling and fast execution.

## 10. Position Sizing and Risk Management
- Description: Use volatility, value-at-risk, or Kelly fraction for sizing positions.
- Source: Ernest P. Chan, *Algorithmic Trading* and *Machine Trading*.
- Notes: Position sizing is as important as signal generation; focus on drawdown control.

## How to use this file
- Use the algorithm names as a guide to search the books or online references.
- Add implementation notes or code snippets below each section as you explore them.
- This file is a living study guide; update it with new strategy ideas, parameter rules, and performance notes.
