import pytest
import pandas as pd

from src.performance import (
    compute_daily_returns, compute_cumulative_returns,
    annualized_return, annualized_volatility,
    sharpe_ratio, sortino_ratio, calmar_ratio, max_drawdown,
)


def test_compute_daily_returns():
    prices = pd.Series([100.0, 102.0, 101.0])
    returns = compute_daily_returns(prices)
    assert returns.iloc[0] == pytest.approx(0.0)
    assert returns.iloc[1] == pytest.approx(0.02)
    assert returns.iloc[2] == pytest.approx(-1.0 / 102.0)


def test_compute_cumulative_returns():
    returns = pd.Series([0.01, -0.005, 0.02])
    cum = compute_cumulative_returns(returns)
    assert cum.iloc[-1] == pytest.approx((1.01 * 0.995 * 1.02) - 1)


def test_sharpe_and_volatility():
    # Alternating returns so std is well-defined
    returns = pd.Series([0.01, -0.005] * 126)
    assert annualized_return(returns) > 0
    assert annualized_volatility(returns) > 0
    sh = sharpe_ratio(returns)
    assert sh >= 0
    # Negative-mean series → Sharpe < 0
    neg = pd.Series([-0.01, -0.005] * 126)
    assert sharpe_ratio(neg) < 0


def test_sortino_ratio():
    # All positive returns → no downside → Sortino = 0 (no downside vol)
    returns = pd.Series([0.001] * 20)
    assert sortino_ratio(returns) == pytest.approx(0.0)
    # Mix of up/down should produce a finite positive number
    mixed = pd.Series([0.01, -0.005, 0.02, -0.003, 0.015])
    assert sortino_ratio(mixed) > 0


def test_max_drawdown_path_dependent():
    # Proper drawdown: peak must precede trough in time.
    # Peak = 1.1 (at index 1), trough = 0.95 (at index 2)
    # MDD = (0.95 - 1.1) / 1.1 ≈ -0.1364
    cumulative = pd.Series([0.0, 0.1, -0.05, 0.2])
    mdd = max_drawdown(cumulative)
    assert mdd == pytest.approx(-0.15 / 1.1, rel=1e-6)
    assert mdd < 0


def test_max_drawdown_no_drawdown():
    # Monotonically increasing → drawdown = 0
    cum = pd.Series([0.0, 0.05, 0.12, 0.20])
    assert max_drawdown(cum) == pytest.approx(0.0, abs=1e-9)


def test_calmar_ratio():
    returns = pd.Series([0.001, -0.002, 0.003, -0.001, 0.002] * 50)
    cum = compute_cumulative_returns(returns)
    cal = calmar_ratio(returns, cum)
    assert isinstance(cal, float)
