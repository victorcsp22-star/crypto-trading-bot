import sys
import os
import time
import datetime
from dotenv import load_dotenv
from signal_engine import SignalEngine
from telegram_notifier import TelegramNotifier

# Configurar soporte UTF-8 en consola de Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

def main():
    check_interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "180"))
    donchian_window = int(os.getenv("DONCHIAN_WINDOW", "10"))
    min_vol = float(os.getenv("MIN_VOLUME_USD", "5000000"))

    notifier = TelegramNotifier()
    
    print("\n" + "="*85, flush=True)
    print(" 🚀 BOT DE SEÑALES TELEGRAM (TECHO 10D OKX SPOT)", flush=True)
    print(f" • Parámetro Techo Donchian  : {donchian_window} Días", flush=True)
    print(f" • Volumen Mínimo 24h       : ${min_vol:,.0f} USD", flush=True)
    print(f" • Frecuencia de Escaneo    : Cada {check_interval} segundos", flush=True)
    
    if notifier.is_configured():
        print(" • Estado Telegram           : 🟢 CONECTADO (Mensajes en vivo a Telegram)", flush=True)
    else:
        print(" • Estado Telegram           : 🟡 MODO CONSOLA / SIMULACIÓN (Configurar .env)", flush=True)
        print("   -> Para recibir alertas en tu móvil, edita el archivo .env con tu Token y Chat ID", flush=True)
    print("="*85 + "\n", flush=True)

    engine = SignalEngine(donchian_window=donchian_window, min_volume_usd=min_vol)

    loop_count = 0
    while True:
        try:
            loop_count += 1
            print(f"--- Ciclo de Monitoreo #{loop_count} ---", flush=True)
            engine.scan_and_notify()
        except KeyboardInterrupt:
            print("\n[DETENIENDO BOT] Ejecución interrumpida por el usuario.", flush=True)
            break
        except Exception as e:
            print(f"[ERROR EN CICLO] {e}. Reintentando en el próximo ciclo...", flush=True)

        time.sleep(check_interval)

if __name__ == "__main__":
    main()
