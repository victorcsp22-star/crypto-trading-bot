import os
from dotenv import load_dotenv

load_dotenv()

# Credenciales de Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ===========================================================================
# PARÁMETROS VALIDADOS EN BACKTEST (101 monedas | DD: 2.81% | PF: 3.42)
# NOTA: NO modificar sin ejecutar nuevo backtest de validación
# ===========================================================================
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "1000.0"))
RISK_PER_TRADE_PCT    = 0.02   # 2% de riesgo por trade (validado en backtest)
MAX_POSITION_ALLOCATION_PCT = 0.25  # Máx 25% de equidad por posición (Kelly fraccional)
DONCHIAN_WINDOW       = 14     # Ventana Donchian (14 días) — Techo Crítico 1D
ATR_WINDOW            = 14     # ATR de 14 períodos diarios
ATR_SL_MULT           = 1.5    # Stop Loss = entry_price - 1.5 × ATR₁₄
BREAKEVEN_ATR_MULT    = 1.2    # Activar Breakeven al alcanzar +1.2 × ATR₁₄ a favor
VOL_MIN_RATIO         = 1.15   # Volumen del día >= 1.15× SMA20 de volumen
MIN_POSITION_USD      = 15.0   # Mínimo USD para abrir posición (igual que backtest)
EMA_TREND_PERIOD      = 50     # EMA50 diaria para filtro de tendencia estructural

# Tarifas de Microestructura OKX Spot (idéntico al backtest)
MAKER_FEE_PCT  = 0.0002   # 0.02% Fee para Órdenes Límite Pasivas (Maker)
TAKER_FEE_PCT  = 0.0005   # 0.05% Fee para Órdenes a Mercado (Taker, en salidas SL)
SLIPPAGE_PCT   = 0.0000   # Cero deslizamiento en órdenes Límite Pasivas

# Frecuencia de Escaneo: 180 segundos (3 minutos) — alineado con el runner
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "180"))

# Lista Blanca Validada de Alto Rendimiento (Top 25 Criptomonedas OKX Spot)
INSTITUTIONAL_WHITELIST = [
    "DOGEUSDT", "ADAUSDT", "NEARUSDT", "THETAUSDT", "BNBUSDT",
    "ETHUSDT",  "XRPUSDT", "FETUSDT",  "RUNEUSDT", "ETCUSDT",
    "CAKEUSDT", "ZECUSDT", "DASHUSDT", "SOLUSDT",  "BCHUSDT",
    "MKRUSDT",  "SHIBUSDT","AAVEUSDT", "ORDIUSDT", "ALGOUSDT",
    "AVAXUSDT", "WLDUSDT", "FILUSDT",  "XLMUSDT",  "FLOKIUSDT"
]

# Mapeo de Símbolo a ID de Instrumento en OKX
SYMBOL_TO_INST_ID  = {sym: f"{sym[:-4]}-USDT" for sym in INSTITUTIONAL_WHITELIST}
INST_ID_TO_SYMBOL  = {v: k for k, v in SYMBOL_TO_INST_ID.items()}

# Archivo Local Persistente de Cartera Demo
PORTFOLIO_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_portfolio.json")
