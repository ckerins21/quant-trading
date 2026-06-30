import pandas as pd
try:
    from .indicators import simple_moving_average
except ImportError:
    from indicators import simple_moving_average


def moving_average_crossover_signals(
    df: pd.DataFrame,
    short_window: int = 50,
    long_window: int = 200,
    mode: str = "long_only",
) -> pd.DataFrame:
    """
    Generate SMA crossover signals.

    mode="long_only"  — flat (0) when SMA short < SMA long; never short
    mode="long_short" — +1 long / -1 short; requires margin/short-selling ability
    """
    df = df.copy()
    df["sma_short"] = simple_moving_average(df["Close"], short_window)
    df["sma_long"]  = simple_moving_average(df["Close"], long_window)

    df["signal"] = 0
    df.loc[df["sma_short"] > df["sma_long"], "signal"] = 1
    if mode == "long_short":
        df.loc[df["sma_short"] < df["sma_long"], "signal"] = -1

    # Position is what we hold starting the next bar (1-day execution lag)
    df["position"] = df["signal"].shift(1).fillna(0)
    return df
