import os
import sys
import time
import threading
import datetime
from flask import Flask, jsonify
from config import CHECK_INTERVAL_SECONDS, INSTITUTIONAL_WHITELIST, INITIAL_CAPITAL
from institutional_engine import InstitutionalEngine
from telegram_notifier import TelegramNotifier

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

app = Flask(__name__)
engine = None
notifier = None
bot_thread = None
is_running = False

def background_trading_loop():
    global engine, is_running
    loop_count = 0
    last_daily_report = None
    
    print("\n" + "="*85, flush=True)
    print(" 🏛️ BOT CUANTITATIVO INSTITUCIONAL (RENDER CLOUD RUNNER)", flush=True)
    print("="*85, flush=True)
    
    while is_running:
        try:
            loop_count += 1
            print(f"--- [CICLO EN LA NUBE RENDER #{loop_count}] ---", flush=True)
            if engine:
                engine.evaluate_market_cycle()
                
                now_utc_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
                if last_daily_report != now_utc_date:
                    daily_summary = engine.paper_trader.get_portfolio_summary()
                    if notifier:
                        notifier.notify_daily_dashboard(daily_summary)
                    last_daily_report = now_utc_date
                    
        except Exception as e:
            print(f"⚠️ [EXCEPCIÓN EN NUBE RENDER #{loop_count}] {e}", flush=True)
            
        time.sleep(CHECK_INTERVAL_SECONDS)

def start_bot_background():
    global engine, notifier, bot_thread, is_running
    if not is_running:
        notifier = TelegramNotifier()
        engine = InstitutionalEngine()
        is_running = True
        bot_thread = threading.Thread(target=background_trading_loop, daemon=True)
        bot_thread.start()
        print("🚀 [RENDER CLOUD] Hilo secundario del bot iniciado correctamente.", flush=True)

# Iniciar hilo de trading al arrancar el servidor web
start_bot_background()

@app.route("/")
def home():
    if not engine:
        return jsonify({"status": "initializing", "message": "Iniciando motor de trading..."})
    
    summary = engine.paper_trader.get_portfolio_summary()
    active_positions = engine.paper_trader.state.get("active_positions", {})
    
    return jsonify({
        "status": "online",
        "service": "Bot Cuantitativo Institucional (Render Cloud)",
        "utc_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "portfolio": summary,
        "active_positions_count": len(active_positions),
        "active_positions_detail": active_positions
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "is_running": is_running,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
