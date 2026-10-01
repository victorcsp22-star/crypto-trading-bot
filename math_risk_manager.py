import numpy as np

class MathRiskManager:
    """
    Gestor Matemático de Riesgo y Asignación de Capital con Interés Compuesto.
    - Aplica Paridad de Volatilidad (ATR Risk Sizing).
    - Aplica Interés Compuesto Reinvirtiendo Beneficios.
    - Limita la exposición máxima por posición.
    """
    def __init__(self, risk_per_trade_pct: float = 0.02, max_position_pct: float = 0.35):
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_position_pct = max_position_pct

    def calculate_position_size(self, current_capital: float, entry_price: float, sl_price: float) -> dict:
        if current_capital <= 0 or entry_price <= 0 or sl_price >= entry_price:
            return {"size_usd": 0.0, "units": 0.0, "risk_usd": 0.0}

        sl_dist_pct = (entry_price - sl_price) / entry_price
        
        # 1. Riesgo en dólares según Interés Compuesto (2% del capital actual)
        risk_usd = current_capital * self.risk_per_trade_pct
        
        # 2. Tamaño de posición en USD necesario para arriesgar exactamente risk_usd
        position_size_usd = risk_usd / sl_dist_pct
        
        # 3. Límite máximo de capital invertido por posición (35% del balance)
        max_allowed_usd = current_capital * self.max_position_pct
        if position_size_usd > max_allowed_usd:
            position_size_usd = max_allowed_usd

        units = position_size_usd / entry_price
        
        return {
            "size_usd": position_size_usd,
            "units": units,
            "risk_usd": risk_usd,
            "sl_dist_pct": sl_dist_pct * 100
        }
