import sys
import os
import time
import datetime
from config import CHECK_INTERVAL_SECONDS, INSTITUTIONAL_WHITELIST, INITIAL_CAPITAL
from institutional_engine import InstitutionalEngine
from telegram_notifier import TelegramNotifier

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    notifier = TelegramNotifier()
    engine = InstitutionalEngine()
    
    print("\n" + "="*85, flush=True)
    print(" 🏛️ BOT DE TRADING CUANTITATIVO INSTITUCIONAL (MODO DEMO / PAPER TRADING)", flush=True)
    print("="*85, flush=True)
    print(f" • Capital Inicial Demo       : ${INITIAL_CAPITAL:,.2f} USD", flush=True)
    print(f" • Criptomonedas Monitoreadas : {len(INSTITUTIONAL_WHITELIST)} Pares Whitelist (OKX Spot)", flush=True)
    print(f" • Arquitectura Multitemporal : Techo 14D (1D) -> Cierre 4H -> Gatillo Límite Maker 1H", flush=True)
    print(f" • Microestructura de Órdenes : Órdenes Límite Pasivas (Maker 0.02% Fee - Zero Slippage)", flush=True)
    print(f" • Frecuencia de Monitoreo    : Cada {CHECK_INTERVAL_SECONDS} segundos", flush=True)
    
    if notifier.is_configured():
        print(" • Estado Telegram           : 🟢 CONECTADO (Mensajes en vivo a Telegram)", flush=True)
    else:
        print(" • Estado Telegram           : 🟡 MODO CONSOLA / SIMULACIÓN (Verifica tu .env)", flush=True)
    print("="*85 + "\n", flush=True)

    summary = engine.paper_trader.get_portfolio_summary()
    print(f" 📊 ESTADO ACTUAL DE CARTERA DEMO:")
    print(f"   • Capital Total Acumulado : ${summary['total_equity']:,.2f} USD")
    print(f"   • Rendimiento Neto Actual  : ${summary['net_profit_usd']:+,.2f} USD ({summary['net_return_pct']:+.2f}%)")
    print(f"   • Posiciones Activas      : {len(engine.paper_trader.state['active_positions'])}")
    print(f"   • Tasa de Acierto (WinRate): {summary['win_rate_pct']:.1f}% ({summary['wins_count']} W / {summary['losses_count']} L)")
    print("="*85 + "\n", flush=True)

    loop_count = 0
    last_daily_report = None

    while True:
        try:
            loop_count += 1
            print(f"--- [CICLO DE MONITOREO INSTITUCIONAL #{loop_count}] ---", flush=True)
            engine.evaluate_market_cycle()

            # Enviar Dashboard Diario cada 24h
            now_utc_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            if last_daily_report != now_utc_date:
                daily_summary = engine.paper_trader.get_portfolio_summary()
                notifier.notify_daily_dashboard(daily_summary)
                last_daily_report = now_utc_date

        except KeyboardInterrupt:
            print("\n🔴 [DETENIENDO BOT INSTITUCIONAL] Ejecución interrumpida por el usuario.", flush=True)
            break
        except Exception as e:
            print(f"⚠️ [EXCEPCIÓN EN CICLO #{loop_count}] {e}. Reintentando en el próximo ciclo...", flush=True)

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
