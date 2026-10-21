"""
indicators.py
-------------
Pure-pandas technical indicators. No ta-lib / pandas-ta dependency.

Used by:
    - get_history.py (attaches indicators to OHLCV output)
    - signals.py (scoring logic)
    - backtest.py (signal generation on historical bars)

Usage:
    import pandas as pd
    from indicators import add_all, latest_snapshot
    df = pd.DataFrame(...)  # columns: open, high, low, close, volume
    df = add_all(df)        # adds ema_20, ema_50, ema_200, rsi_14, macd, bb_*, vwap, atr_14
    snap = latest_snapshot(df)   # dict for the last bar
"""

from __future__ import annotations
import pandas as pd
import numpy as np


# ---------- Individual indicator functions ----------
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    sig_line = ema(macd_line, signal)
    hist = macd_line - sig_line
    return pd.DataFrame({"macd": macd_line, "macd_signal": sig_line, "macd_hist": hist})


def bollinger(series: pd.Series, length: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    ma = sma(series, length)
    std = series.rolling(window=length, min_periods=length).std()
    upper = ma + n_std * std
    lower = ma - n_std * std
    bandwidth = (upper - lower) / ma
    percent_b = (series - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame({
        "bb_upper": upper, "bb_middle": ma, "bb_lower": lower,
        "bb_bandwidth": bandwidth, "bb_percent_b": percent_b,
    })


def vwap_session(df: pd.DataFrame) -> pd.Series:
    """VWAP resetting each calendar day (intraday)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    # Group by date if index is datetime; else cumulative
    if isinstance(df.index, pd.DatetimeIndex):
        grp = df.index.date
        return pv.groupby(grp).cumsum() / df["volume"].groupby(grp).cumsum()
    return pv.cumsum() / df["volume"].cumsum()


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """
    Average Directional Index — trend strength (NOT direction).
    Returns the ADX series. Interpretation:
      < 20  = no/weak trend (choppy, avoid trend-following entries)
      20-25 = developing trend
      > 25  = strong trend
      > 40  = very strong (often near exhaustion)
    """
    high = df["high"]; low = df["low"]; close = df["close"]
    prev_close = close.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # +DM / -DM
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm  = ((down_move > up_move) & (down_move > 0)) * down_move

    # Smoothed
    atr_     = tr.ewm(alpha=1/length, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/length, adjust=False).mean()  / atr_.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/length, adjust=False).mean() / atr_.replace(0, np.nan)

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range — volatility measure."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()


def pivot_levels(df: pd.DataFrame, lookback: int = 20, *, exclude_last: bool = False) -> dict:
    """
    Simple pivot-based support/resistance from recent high/low.

    If exclude_last=True, compute pivots from bars *prior* to the latest bar.
    This is useful for breakout detection: if you include the latest bar,
    resistance often "moves up" on the breakout bar and becomes unusable
    as a reference.
    """
    if exclude_last and len(df) > 1:
        df = df.iloc[:-1]
    window = df.tail(lookback)
    return {
        "resistance": float(window["high"].max()),
        "support":    float(window["low"].min()),
        "mid":        float((window["high"].max() + window["low"].min()) / 2),
    }


# ---------- Crossover detection ----------
def crossed_above(fast: pd.Series, slow: pd.Series, within_bars: int = 3) -> bool:
    """Did `fast` cross above `slow` in the last `within_bars` bars?"""
    if len(fast) < within_bars + 1:
        return False
    diff = fast - slow
    sign = np.sign(diff)
    # Find any transition from negative to positive in last N bars
    recent = sign.tail(within_bars + 1).values
    for i in range(1, len(recent)):
        if recent[i-1] <= 0 and recent[i] > 0:
            return True
    return False


def crossed_below(fast: pd.Series, slow: pd.Series, within_bars: int = 3) -> bool:
    if len(fast) < within_bars + 1:
        return False
    diff = fast - slow
    sign = np.sign(diff)
    recent = sign.tail(within_bars + 1).values
    for i in range(1, len(recent)):
        if recent[i-1] >= 0 and recent[i] < 0:
            return True
    return False


# ---------- One-shot helper ----------
def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes an OHLCV DataFrame (lowercase columns: open, high, low, close, volume)
    and returns it with indicator columns appended.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    df["ema_20"]  = ema(df["close"], 20)
    df["ema_50"]  = ema(df["close"], 50)
    df["ema_200"] = ema(df["close"], 200)
    df["sma_50"]  = sma(df["close"], 50)
    df["rsi_14"]  = rsi(df["close"], 14)

    macd_df = macd(df["close"])
    df = df.join(macd_df)

    bb_df = bollinger(df["close"])
    df = df.join(bb_df)

    df["atr_14"] = atr(df, 14)
    df["adx_14"] = adx(df, 14)

    try:
        df["vwap"] = vwap_session(df)
    except Exception:
        df["vwap"] = np.nan

    # Volume ratio vs 20-day avg
    df["vol_sma_20"] = sma(df["volume"].astype(float), 20)
    df["vol_ratio"]  = df["volume"] / df["vol_sma_20"].replace(0, np.nan)

    return df


def latest_snapshot(df: pd.DataFrame) -> dict:
    """Return a dict of latest-bar indicator values + trend tags."""
    if df.empty:
        return {}
    last = df.iloc[-1]
    close = float(last["close"])

    # Trend classification
    trend = "neutral"
    if close > last.get("ema_50", np.nan) > last.get("ema_200", np.nan):
        trend = "bullish"
    elif close < last.get("ema_50", np.nan) < last.get("ema_200", np.nan):
        trend = "bearish"

    # Golden / death cross (within last 10 bars)
    golden = crossed_above(df["ema_50"], df["ema_200"], within_bars=10)
    death  = crossed_below(df["ema_50"], df["ema_200"], within_bars=10)

    # MACD cross (within last 3 bars)
    macd_bull = crossed_above(df["macd"], df["macd_signal"], within_bars=3)
    macd_bear = crossed_below(df["macd"], df["macd_signal"], within_bars=3)

    pivots = pivot_levels(df, lookback=20)
    pivots_prev = pivot_levels(df, lookback=20, exclude_last=True) if len(df) > 21 else pivots

    return {
        "close":       close,
        "ema_20":      float(last.get("ema_20", np.nan)),
        "ema_50":      float(last.get("ema_50", np.nan)),
        "ema_200":     float(last.get("ema_200", np.nan)),
        "rsi_14":      float(last.get("rsi_14", np.nan)),
        "macd":        float(last.get("macd", np.nan)),
        "macd_signal": float(last.get("macd_signal", np.nan)),
        "macd_hist":   float(last.get("macd_hist", np.nan)),
        "bb_upper":    float(last.get("bb_upper", np.nan)),
        "bb_lower":    float(last.get("bb_lower", np.nan)),
        "bb_percent_b":float(last.get("bb_percent_b", np.nan)),
        "atr_14":      float(last.get("atr_14", np.nan)),
        "adx_14":      float(last.get("adx_14", np.nan)),
        "vol_ratio":   float(last.get("vol_ratio", np.nan)),
        "trend":       trend,
        "golden_cross":golden,
        "death_cross": death,
        "macd_bull_cross": macd_bull,
        "macd_bear_cross": macd_bear,
        "support":     pivots["support"],
        "resistance":  pivots["resistance"],
        "support_prev":    pivots_prev["support"],
        "resistance_prev": pivots_prev["resistance"],
    }


# ---------- CLI: test on live IBKR history ----------
if __name__ == "__main__":
    import sys
    from ibkr_client import IBKRClient

    symbol   = sys.argv[1] if len(sys.argv) > 1 else "RY"
    duration = sys.argv[2] if len(sys.argv) > 2 else "60 D"
    bar_size = sys.argv[3] if len(sys.argv) > 3 else "1 day"
    currency = "USD" if "--usd" in sys.argv else "CAD"

    client = IBKRClient()
    client.connect()
    bars = client.get_historical_bars(symbol, duration, bar_size, currency)
    client.disconnect()

    df = pd.DataFrame(bars)
    if df.empty:
        print(f"No bars for {symbol}")
        sys.exit(1)
    df = add_all(df)
    snap = latest_snapshot(df)

    import json
    print(f"\n=== {symbol} ({currency}) — {duration} of {bar_size} ===\n")
    for k, v in snap.items():
        if isinstance(v, float):
            print(f"  {k:<18} {v:>10.4f}")
        else:
            print(f"  {k:<18} {v}")
