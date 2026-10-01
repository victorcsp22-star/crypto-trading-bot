import os
import glob
import sys
import pandas as pd
import numpy as np
from data_loader import load_symbol_data
import indicators

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

DATA_DIR = r"C:\Users\victo\crypto_signals\data\processed\ohlcv\spot\1d"

def get_all_symbols():
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

def run_backtest_on_single_symbol(data_dict, initial_capital=1000.0, donchian_window=10, fee_pct=0.001):
    df_1d = data_dict["1d"].copy()
    df_4h = data_dict["4h"].copy()
    df_1h = data_dict["1h"].copy()

    # Indicadores
    df_1d = indicators.compute_donchian_channel(df_1d, window=donchian_window)
    df_1d = indicators.compute_macd(df_1d)
    df_1d = indicators.compute_atr(df_1d, window=14)

    df_4h = indicators.compute_macd(df_4h)
    df_1h = indicators.compute_macd(df_1h)

    df_1d_valid = df_1d.dropna(subset=[f"techo_{donchian_window}d"]).copy()
    if len(df_1d_valid) < 30:
        return None

    # Arrays optimizados en numpy para 1h
    df_1h_clean = df_1h.copy()
    lows_1h = df_1h_clean["low"].values
    closes_1h = df_1h_clean["close"].values
    macd_cross_1h = df_1h_clean["macd_bear_cross"].values
    time_to_idx_1h = {t: idx for idx, t in enumerate(df_1h_clean.index)}

    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0

    trades = []
    in_position = False
    current_exit_idx = 0
    n_days = len(df_1d_valid)
    i = 0

    while i < n_days:
        day_ts = df_1d_valid.index[i]
        day_row = df_1d_valid.iloc[i]
        techo_val = day_row[f"techo_{donchian_window}d"]
        piso_val = day_row[f"piso_{donchian_window}d"]
        high_1d = day_row["high"]

        if in_position:
            if day_ts < df_1h_clean.index[current_exit_idx]:
                i += 1
                continue
            else:
                in_position = False

        if high_1d >= techo_val:
            next_day_ts = day_ts + pd.Timedelta(days=1)
            sub_1h = df_1h.loc[(df_1h.index >= day_ts) & (df_1h.index < next_day_ts)]
            sub_4h = df_4h.loc[(df_4h.index >= day_ts) & (df_4h.index < next_day_ts)]

            entry_tf = None
            entry_ts = None
            entry_price = None

            # Cascada Nivel 1: 1h
            break_1h = sub_1h[sub_1h["close"] > techo_val]
            if not break_1h.empty:
                entry_tf = "1h"
                first_break_ts = break_1h.index[0]
                sub_1h_after = df_1h.loc[df_1h.index > first_break_ts]
                if not sub_1h_after.empty:
                    entry_ts = sub_1h_after.index[0]
                    entry_price = sub_1h_after.iloc[0]["open"]

            # Cascada Nivel 2: 4h
            if entry_price is None:
                break_4h = sub_4h[sub_4h["close"] > techo_val]
                if not break_4h.empty:
                    entry_tf = "4h"
                    first_break_ts = break_4h.index[0]
                    sub_4h_after = df_4h.loc[df_4h.index > first_break_ts]
                    if not sub_4h_after.empty:
                        entry_ts = sub_4h_after.index[0]
                        entry_price = sub_4h_after.iloc[0]["open"]

            # Cascada Nivel 3: 1d
            if entry_price is None:
                if day_row["close"] > techo_val:
                    entry_tf = "1d"
                    if i + 1 < n_days:
                        entry_ts = df_1d_valid.index[i + 1]
                        entry_price = df_1d_valid.iloc[i + 1]["open"]

            if entry_price is not None and entry_ts is not None and entry_ts in time_to_idx_1h:
                sl_price = piso_val if piso_val < entry_price else entry_price * 0.95

                start_idx = time_to_idx_1h[entry_ts]
                total_1h_len = len(df_1h_clean)

                exit_ts = None
                exit_price = None
                exit_reason = None
                exit_idx = total_1h_len - 1

                for idx in range(start_idx, total_1h_len):
                    low_val = lows_1h[idx]
                    close_val = closes_1h[idx]
                    is_macd_bear = macd_cross_1h[idx]

                    if low_val <= sl_price:
                        exit_ts = df_1h_clean.index[idx]
                        exit_price = sl_price
                        exit_reason = "Stop Loss (Suelo Donchian)"
                        exit_idx = idx
                        break

                    if is_macd_bear and idx > start_idx:
                        exit_ts = df_1h_clean.index[idx]
                        exit_price = close_val
                        exit_reason = "Cruce MACD Bajista (Exit)"
                        exit_idx = idx
                        break

                if exit_price is None:
                    exit_idx = total_1h_len - 1
                    exit_ts = df_1h_clean.index[exit_idx]
                    exit_price = closes_1h[exit_idx]
                    exit_reason = "Fin de Datos"

                raw_return = (exit_price - entry_price) / entry_price
                net_return = (1 + raw_return) * ((1 - fee_pct) ** 2) - 1

                trades.append({
                    "entry_time": entry_ts,
                    "exit_time": exit_ts,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "entry_tf": entry_tf,
                    "raw_return_pct": raw_return * 100,
                    "net_return_pct": net_return * 100,
                    "exit_reason": exit_reason
                })

                in_position = True
                current_exit_idx = exit_idx

        i += 1

    if not trades:
        return None

    df_trades = pd.DataFrame(trades)
    wins = df_trades[df_trades["net_return_pct"] > 0]
    losses = df_trades[df_trades["net_return_pct"] <= 0]
    total_trades = len(df_trades)
    win_rate = (len(wins) / total_trades) * 100

    gross_p = wins["net_return_pct"].sum() if not wins.empty else 0.0
    gross_l = abs(losses["net_return_pct"].sum()) if not losses.empty else 0.0
    profit_factor = (gross_p / gross_l) if gross_l > 0 else (999.0 if gross_p > 0 else 0.0)

    # Interés compuesto sobre la moneda
    cap = initial_capital
    equity_curve = [cap]
    for ret in df_trades["net_return_pct"]:
        cap *= (1 + ret / 100.0)
        equity_curve.append(cap)

    equity_series = pd.Series(equity_curve)
    peak = equity_series.cummax()
    dd = (equity_series - peak) / peak
    max_dd = abs(dd.min()) * 100
    net_profit_pct = ((cap - initial_capital) / initial_capital) * 100

    return {
        "total_trades": total_trades,
        "win_rate_pct": win_rate,
        "net_profit_pct": net_profit_pct,
        "final_capital": cap,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_dd,
        "trades": trades
    }

def main():
    print("\n" + "="*85)
    print(" 🚀 BACKTEST OFFLINE DE LA ESTRATEGIA ORIGINAL SOBRE 101 CRIPTOMONEDAS")
    print(" Datos: Histórico Completo de 1D, 4H y 1H de la PC del Usuario")
    print(" Estrategia: Techo Donchian (10D) + Salida MACD 1H + Stop Loss Suelo Donchian")
    print("="*85 + "\n")

    symbols = get_all_symbols()
    print(f" Se encontraron {len(symbols)} criptomonedas en el dataset local (`crypto_signals`).")
    print(" Ejecutando backtest trade a trade sobre todo el historial...\n")

    results = []
    processed_count = 0

    for sym in symbols:
        try:
            data = load_symbol_data(sym)
            res = run_backtest_on_single_symbol(data, initial_capital=1000.0, donchian_window=10)
            if res and res["total_trades"] >= 5:
                results.append({
                    "Symbol": sym,
                    "Net Profit (%)": res["net_profit_pct"],
                    "Win Rate (%)": res["win_rate_pct"],
                    "Profit Factor": res["profit_factor"],
                    "Max Drawdown (%)": res["max_drawdown_pct"],
                    "Trades": res["total_trades"],
                    "Final Capital ($)": res["final_capital"]
                })
                processed_count += 1
                if processed_count % 15 == 0:
                    print(f"   [+] Procesadas {processed_count}/{len(symbols)} monedas...", flush=True)
        except Exception:
            continue

    df_res = pd.DataFrame(results)
    if df_res.empty:
        print("❌ No se pudieron procesar datos.")
        return

    # Ordenar por Retorno Neto (%)
    df_sorted = df_res.sort_values("Net Profit (%)", ascending=False).reset_index(drop=True)

    print("\n" + "="*85)
    print(f" 📊 RESUMEN GLOBAL DEL BACKTEST SOBRE {len(df_sorted)} CRIPTOMONEDAS:")
    print("="*85)
    print(f" • Promedio de Retorno Neto     : {df_sorted['Net Profit (%)'].mean():>+,.2f}%")
    print(f" • Mediana de Retorno Neto      : {df_sorted['Net Profit (%)'].median():>+,.2f}%")
    print(f" • Promedio de Win Rate         : {df_sorted['Win Rate (%)'].mean():.1f}%")
    print(f" • Promedio de Profit Factor    : {df_sorted['Profit Factor'].mean():.2f}")
    print(f" • Promedio de Max Drawdown     : {df_sorted['Max Drawdown (%)'].mean():.2f}%")
    print(f" • Criptomonedas Ganadoras (>0%): {len(df_sorted[df_sorted['Net Profit (%)'] > 0])} / {len(df_sorted)} ({len(df_sorted[df_sorted['Net Profit (%)'] > 0])/len(df_sorted)*100:.1f}%)")
    print("="*85 + "\n")

    print(" 🏆 TOP 20 CRIPTOMONEDAS MÁS RENTABLES CON LA ESTRATEGIA ORIGINAL:")
    print("-" * 85)
    top20 = df_sorted.head(20)[["Symbol", "Net Profit (%)", "Win Rate (%)", "Profit Factor", "Max Drawdown (%)", "Trades"]]
    print(top20.to_string(index=False))

    print("\n 🔴 TOP 15 CRIPTOMONEDAS CON PEOR DESEMPEÑO (BAJO O PÉRDIDA):")
    print("-" * 85)
    bottom15 = df_sorted.tail(15)[["Symbol", "Net Profit (%)", "Win Rate (%)", "Profit Factor", "Max Drawdown (%)", "Trades"]]
    print(bottom15.to_string(index=False))

    # Guardar CSV consolidado
    df_sorted.to_csv("backtest_101_coins_original_results.csv", index=False)
    print(f"\n✅ Resultados completos guardados en 'backtest_101_coins_original_results.csv'.\n")

if __name__ == "__main__":
    main()
