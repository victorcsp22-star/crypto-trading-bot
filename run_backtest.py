import sys
import os
import pandas as pd
from data_loader import load_symbol_data
from backtester import MTFBacktester

def format_percentage(val):
    return f"{val:+.2f}%"

def run_optimization(symbols: list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
    sl_modes = ["candle_low", "atr_1.0", "atr_1.5", "atr_2.0", "donchian_low"]
    
    print("\n" + "="*85, flush=True)
    print(" INICIANDO BACKTEST: ESTRATEGIA MULTI-TIMEFRAME (1D -> 1H -> 4H -> 1D)", flush=True)
    print(" REGLA DE ENTRADA : Roce de Techo 14D + Cierre en 1h / 4h / 1D", flush=True)
    print(" REGLA DE SALIDA  : Cruce bajista en MACD (Signal Line)", flush=True)
    print(" COMISIÓN BINANCE : 0.1% por operación (Maker/Taker)", flush=True)
    print("="*85 + "\n", flush=True)

    for symbol in symbols:
        print(f"\n>>> ANALIZANDO PAR: {symbol} <<<", flush=True)
        try:
            data = load_symbol_data(symbol)
        except Exception as e:
            print(f"Error cargando datos para {symbol}: {e}", flush=True)
            continue

        backtester = MTFBacktester(data_dict=data, fee_pct=0.001, initial_capital=10000.0)
        
        results = []
        for sl_mode in sl_modes:
            res = backtester.run_backtest(sl_mode=sl_mode, macd_exit_tf="1h")
            results.append(res)

        # Formatear tabla de comparación
        table_data = []
        for r in results:
            table_data.append({
                "Modo Stop Loss": r["sl_mode"],
                "Total Trades": r["total_trades"],
                "Win Rate (%)": f"{r['win_rate_pct']:.1f}%",
                "Profit Factor": f"{r['profit_factor']:.2f}",
                "Max Drawdown (%)": f"{r['max_drawdown_pct']:.2f}%",
                "Retorno Promedio": f"{r.get('avg_trade_return_pct', 0.0):+.2f}%",
                "Retorno Neto Total": format_percentage(r["net_profit_pct"])
            })

        df_res = pd.DataFrame(table_data)
        print(df_res.to_string(index=False), flush=True)

        # Encontrar la mejor configuración basada en Retorno Neto
        best_res = max(results, key=lambda x: x["net_profit_pct"])
        print("\n" + "-"*65, flush=True)
        print(f" MEJOR CONFIGURACIÓN PARA {symbol}: [{best_res['sl_mode'].upper()}]", flush=True)
        print(f"   • Retorno Total: {best_res['net_profit_pct']:+.2f}%", flush=True)
        print(f"   • Win Rate     : {best_res['win_rate_pct']:.1f}%", flush=True)
        print(f"   • Profit Factor: {best_res['profit_factor']:.2f}", flush=True)
        print(f"   • Max Drawdown : {best_res['max_drawdown_pct']:.2f}%", flush=True)
        print("-"*65, flush=True)

        # Mostrar detalle de las últimas 5 operaciones de la mejor configuración
        if best_res["trades"]:
            print(f"\n ÚLTIMAS OPERACIONES DE LA MEJOR CONFIGURACIÓN ({best_res['sl_mode']}):", flush=True)
            df_trades = pd.DataFrame(best_res["trades"])
            cols_show = ["entry_time", "exit_time", "entry_price", "exit_price", "entry_tf", "net_return_pct", "exit_reason"]
            print(df_trades[cols_show].tail(8).to_string(index=False), flush=True)

if __name__ == "__main__":
    symbols_to_test = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT"]
    if len(sys.argv) > 1:
        symbols_to_test = sys.argv[1:]
    run_optimization(symbols_to_test)
