import os
import glob
import pandas as pd
import numpy as np
from data_loader import load_symbol_data
from backtester import MTFBacktester

DATA_DIR = r"C:\Users\victo\crypto_signals\data\processed\ohlcv\spot\1d"

def get_available_symbols():
    files = glob.glob(os.path.join(DATA_DIR, "*_1d.parquet"))
    if not files:
        files = glob.glob(os.path.join(DATA_DIR, "*_1d.csv"))
    
    symbols = []
    for f in files:
        basename = os.path.basename(f)
        symbol = basename.split("_")[0]
        if symbol.endswith("USDT") and symbol != "Consolidado":
            symbols.append(symbol)
    return sorted(list(set(symbols)))

def analyze_crypto_universe():
    symbols = get_available_symbols()
    print(f"Encontrados {len(symbols)} pares USDT para análisis de universo.", flush=True)

    records = []

    for sym in symbols:
        try:
            print(f"Procesando {sym}...", flush=True)
            data = load_symbol_data(sym)
            df_1d = data["1d"]
            
            if len(df_1d) < 180: # Requerir al menos 6 meses de datos
                continue

            # Calcular características de la moneda
            close_1d = df_1d["close"]
            returns = close_1d.pct_change().dropna()
            
            volatility_atr_pct = (df_1d["high"] - df_1d["low"]).mean() / close_1d.mean() * 100
            daily_volatility_std = returns.std() * 100
            avg_daily_volume_usd = (df_1d["close"] * df_1d["volume"]).mean()
            
            # Tendencialidad (Ratio de continuidad de tendencia)
            up_moves = (returns > 0).mean()

            # Correr backtest
            backtester = MTFBacktester(data_dict=data, fee_pct=0.001, initial_capital=10000.0)
            res = backtester.run_backtest(sl_mode="donchian_low", macd_exit_tf="1h")

            if res["total_trades"] >= 10:
                records.append({
                    "symbol": sym,
                    "net_profit_pct": res["net_profit_pct"],
                    "win_rate_pct": res["win_rate_pct"],
                    "profit_factor": res["profit_factor"],
                    "max_drawdown_pct": res["max_drawdown_pct"],
                    "total_trades": res["total_trades"],
                    "volatility_atr_pct": volatility_atr_pct,
                    "daily_volatility_std": daily_volatility_std,
                    "avg_volume_usd": avg_daily_volume_usd
                })
        except Exception as e:
            continue

    df_res = pd.DataFrame(records)
    if df_res.empty:
        print("No se generaron resultados.", flush=True)
        return

    # Ordenar por Retorno Neto
    df_res = df_res.sort_values("net_profit_pct", ascending=False).reset_index(drop=True)
    
    # Clasificación por cuartiles de rendimiento
    df_res["categoria"] = pd.qcut(df_res["net_profit_pct"], q=3, labels=["Bajo Rendimiento", "Rendimiento Medio", "Alto Rendimiento"])

    print("\n" + "="*85, flush=True)
    print(" TOP 15 CRIPTOMONEDAS MÁS COMPATIBLES CON LA ESTRATEGIA (14D TECHO + MACD)", flush=True)
    print("="*85, flush=True)
    top15 = df_res.head(15)[["symbol", "net_profit_pct", "win_rate_pct", "profit_factor", "max_drawdown_pct", "volatility_atr_pct", "total_trades"]]
    print(top15.to_string(index=False), flush=True)

    print("\n" + "="*85, flush=True)
    print(" TOP 15 CRIPTOMONEDAS MENOS COMPATIBLES (PEOR RENDIMIENTO)", flush=True)
    print("="*85, flush=True)
    bottom15 = df_res.tail(15)[["symbol", "net_profit_pct", "win_rate_pct", "profit_factor", "max_drawdown_pct", "volatility_atr_pct", "total_trades"]]
    print(bottom15.to_string(index=False), flush=True)

    # Análisis estadístico de características por categoría
    print("\n" + "="*85, flush=True)
    print(" ANÁLISIS DE CARACTERÍSTICAS SEGÚN RENDIMIENTO DE LA ESTRATEGIA", flush=True)
    print("="*85, flush=True)
    summary_stats = df_res.groupby("categoria")[["volatility_atr_pct", "daily_volatility_std", "win_rate_pct", "profit_factor", "net_profit_pct"]].mean()
    print(summary_stats.to_string(), flush=True)

    df_res.to_csv("universe_analysis_results.csv", index=False)
    print("\nResultados completos guardados en 'universe_analysis_results.csv'.", flush=True)

if __name__ == "__main__":
    analyze_crypto_universe()
