import numpy as np
import pandas as pd


def compute_daily_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0)


def compute_cumulative_returns(returns: pd.Series) -> pd.Series:
    return (1 + returns).cumprod() - 1


def annualized_return(returns: pd.Series, periods_per_year: int = 252) -> float:
    compounded = (1 + returns).prod()
    years = len(returns) / periods_per_year
    return compounded ** (1 / years) - 1 if years > 0 else 0.0


def annualized_volatility(returns: pd.Series, periods_per_year: int = 252) -> float:
    return np.std(returns, ddof=1) * np.sqrt(periods_per_year)


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
    excess = returns - risk_free_rate / periods_per_year
    vol = np.std(excess, ddof=1)
    return (np.mean(excess) * periods_per_year / vol) if vol > 0 else 0.0


def max_drawdown(cumulative_returns: pd.Series) -> float:
    wealth = 1 + cumulative_returns
    peak = wealth.cummax()
    drawdown = (wealth - peak) / peak
    return float(drawdown.min())
