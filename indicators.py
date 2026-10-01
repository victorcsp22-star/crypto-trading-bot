import pandas as pd
import numpy as np

def compute_donchian_channel(df: pd.DataFrame, window: int = 14):
    """
    Calcula el techo y piso Donchian de los últimos `window` periodos (excluyendo la vela actual).
    """
    df = df.copy()
    df[f"techo_{window}d"] = df["high"].shift(1).rolling(window=window).max()
    df[f"piso_{window}d"] = df["low"].shift(1).rolling(window=window).min()
    return df

def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    """
    Calcula MACD Line, Signal Line y MACD Histogram.
    """
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd_line"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd_line"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd_line"] - df["macd_signal"]
    
    # Señal de cruce contrario (MACD Line cruza por debajo de Signal Line)
    # Cruce bajista: macd_line previo > signal previo Y macd_line actual < signal actual
    df["macd_bear_cross"] = (df["macd_line"].shift(1) >= df["macd_signal"].shift(1)) & (df["macd_line"] < df["macd_signal"])
    return df

def compute_atr(df: pd.DataFrame, window: int = 14):
    """
    Calcula el Average True Range (ATR).
    """
    df = df.copy()
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=window).mean()
    return df
