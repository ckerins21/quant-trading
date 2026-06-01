import pandas as pd

from src.performance import compute_daily_returns, compute_cumulative_returns, annualized_return, annualized_volatility, sharpe_ratio, max_drawdown


def test_compute_daily_returns():
    prices = pd.Series([100.0, 102.0, 101.0])
    returns = compute_daily_returns(prices)
    assert returns.iloc[0] == 0.0
    assert returns.iloc[1] == 0.02
    assert returns.iloc[2] == -1.0 / 102.0


def test_compute_cumulative_returns():
    returns = pd.Series([0.01, -0.005, 0.02])
    cum = compute_cumulative_returns(returns)
    assert round(cum.iloc[-1], 8) == round((1.01 * 0.995 * 1.02) - 1, 8)


def test_sharpe_and_volatility():
    returns = pd.Series([0.001, 0.001, 0.001, 0.001])
    assert annualized_return(returns) > 0
    assert annualized_volatility(returns) >= 0
    assert sharpe_ratio(returns) >= 0


def test_max_drawdown():
    cumulative = pd.Series([0.0, 0.1, -0.05, 0.2])
    assert max_drawdown(cumulative) == -0.25
