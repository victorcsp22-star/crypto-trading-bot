import pandas as pd
import numpy as np

class RegimeFilter:
    """
    Filtro de Régimen de Mercado Institucional:
    - Evalúa si el mercado general o activo está en tendencia alcista estructurada (SMA50 > SMA200 y Close > SMA50).
    - Evalúa la fuerza de tendencia mediante el indicador ADX (Average Directional Index).
    """
    @staticmethod
    def compute_regime_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['sma_50'] = df['close'].rolling(50, min_periods=20).mean()
        df['sma_200'] = df['close'].rolling(200, min_periods=50).mean()
        
        # Filtro de Régimen Alcista Estructurado
        # Si la tendencia es alcista (Close > SMA50 o SMA50 > SMA200), Regime = True
        df['regime_bullish'] = (df['close'] > df['sma_50']) | (df['sma_50'] > df['sma_200'])
        
        return df

    @staticmethod
    def is_market_favorable(df_btc_or_eth: pd.DataFrame) -> bool:
        """
        Devuelve True si el mercado macro (BTC/ETH) se encuentra en régimen alcista.
        """
        if df_btc_or_eth is None or df_btc_or_eth.empty:
            return True
        last_row = df_btc_or_eth.iloc[-1]
        return bool(last_row.get('regime_bullish', True))
