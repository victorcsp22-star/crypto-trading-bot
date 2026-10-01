import sys
import datetime
import pandas as pd
from okx_client import OKXPublicClient
from telegram_notifier import TelegramNotifier

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Lista Blanca de Criptomonedas de Alto Momentum probadas en el Backtest
WHITELIST_SYMBOLS = [
    "DOGEUSDT", "ADAUSDT", "BNBUSDT", "SHIBUSDT", "ETHUSDT", 
    "XRPUSDT", "FILUSDT", "DASHUSDT", "XLMUSDT", "GALAUSDT", 
    "CAKEUSDT", "PEPEUSDT", "LUNCUSDT", "AVAXUSDT", "SOLUSDT"
]

class SignalEngine:
    def __init__(self, donchian_window: int = 10, min_volume_usd: float = 5000000.0):
        self.okx_client = OKXPublicClient(inst_type="SPOT")
        self.notifier = TelegramNotifier()
        self.donchian_window = donchian_window
        self.min_volume_usd = min_volume_usd
        
        # Estructura de control para evitar spam diario por moneda
        self.current_utc_day = None
        self.notified_symbols_today = set()

    def _reset_daily_control_if_needed(self):
        """
        Verifica el día UTC actual y reinicia el conjunto de notificaciones al comenzar un nuevo día.
        """
        today_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if self.current_utc_day != today_utc:
            print(f"[NUEVO DÍA UTC: {today_utc}] Reiniciando control de señales para el universo OKX.", flush=True)
            self.current_utc_day = today_utc
            self.notified_symbols_today.clear()

    def scan_and_notify(self):
        """
        Escanea el universo SPOT de OKX y envía la señal a Telegram si alguna moneda roza el Techo 10D.
        """
        self._reset_daily_control_if_needed()

        print(f"\n[{datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S UTC')}] Escaneando OKX Spot...", flush=True)
        
        # 1. Obtener monedas activas de OKX con volumen amigable
        all_okx_tickers = self.okx_client.get_usdt_symbols(min_volume_usd=self.min_volume_usd)
        if not all_okx_tickers:
            print("No se obtuvieron tickers de OKX. Reintentando en el próximo ciclo.", flush=True)
            return

        target_symbols = []
        ticker_map = {t["symbol"]: t for t in all_okx_tickers}
        whitelist_set = set()

        # Añadir miembros de Whitelist disponibles en OKX
        for sym in WHITELIST_SYMBOLS:
            if sym in ticker_map:
                target_symbols.append(ticker_map[sym])
                whitelist_set.add(sym)

        # Añadir otras monedas OKX de alto volumen
        for t in all_okx_tickers:
            if t["symbol"] not in whitelist_set:
                target_symbols.append(t)

        print(f"Monedas OKX seleccionadas para monitoreo: {len(target_symbols)} pares.", flush=True)

        signals_sent = 0

        # 2. Iterar y calcular el Techo 10D para cada moneda en OKX
        for item in target_symbols:
            symbol = item["symbol"]
            inst_id = item["inst_id"]

            # Si ya se envió señal de esta moneda el día de hoy, suspender toque
            if symbol in self.notified_symbols_today:
                continue

            df_klines = self.okx_client.get_daily_klines(inst_id=inst_id, limit=self.donchian_window + 5)
            if df_klines is None or len(df_klines) < self.donchian_window + 1:
                continue

            # La última fila es la vela diaria actual (en curso)
            current_bar = df_klines.iloc[-1]
            current_price = current_bar["close"]
            current_high = current_bar["high"]

            # El Techo de 10D son los máximos de las 10 velas diarias anteriores cerradas
            completed_bars = df_klines.iloc[-(self.donchian_window + 1): -1]
            techo_10d = completed_bars["high"].max()

            # 3. Comprobar si la vela diaria actual tocó o rozó el Techo de 10D
            if current_high >= techo_10d:
                pct_over = ((current_high - techo_10d) / techo_10d) * 100
                print(f"🎯 ¡DISPARO DE SEÑAL OKX! {symbol} rozo el Techo 10D (High: ${current_high:,.4f} >= Techo: ${techo_10d:,.4f})", flush=True)

                # Registrar suspensión por el resto del día UTC
                self.notified_symbols_today.add(symbol)

                # Despachar notificación a Telegram
                self.notifier.send_signal(
                    symbol=symbol,
                    current_price=current_price,
                    high_price=current_high,
                    techo_10d=techo_10d,
                    pct_over=pct_over,
                    exchange_name="OKX"
                )
                signals_sent += 1

        if signals_sent == 0:
            print(f"Escaneo OKX completado. Ninguna moneda nueva ha rozado el Techo 10D en este ciclo.", flush=True)

if __name__ == "__main__":
    engine = SignalEngine(donchian_window=10, min_volume_usd=5000000.0)
    engine.scan_and_notify()
