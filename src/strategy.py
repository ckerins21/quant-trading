import pandas as pd
try:
    from .indicators import simple_moving_average
except ImportError:
    from indicators import simple_moving_average


def moving_average_crossover_signals(df: pd.DataFrame, short_window: int = 50, long_window: int = 200) -> pd.DataFrame:
    df = df.copy()
    df["sma_short"] = simple_moving_average(df["Close"], short_window)
    df["sma_long"] = simple_moving_average(df["Close"], long_window)
    df["signal"] = 0
    df.loc[df["sma_short"] > df["sma_long"], "signal"] = 1
    df.loc[df["sma_short"] < df["sma_long"], "signal"] = -1
    df["position"] = df["signal"].shift(1).fillna(0)
    return df
