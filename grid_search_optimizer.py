import pandas as pd
import numpy as np
from data_loader import load_symbol_data
import indicators

def run_fast_grid_search():
    symbols = ["DOGEUSDT", "ADAUSDT", "BNBUSDT", "SHIBUSDT", "ETHUSDT", "XRPUSDT"]
    
    donchian_windows = [10, 14, 20]
    macd_params = [
        (6, 19, 9),   # Solicitado por el usuario
        (8, 21, 9),   # Rápido alternativo
        (12, 26, 9),  # Estándar
        (5, 35, 5)    # Sensible
    ]
    sl_modes = ["donchian_low", "atr_1.0", "atr_1.5"]

    print("\n" + "="*85, flush=True)
    print(" INICIANDO GRID SEARCH OPTIMIZADO (PRE-CÁLCULO DE INDICADORES)", flush=True)
    print(f" Simbols Portfolio: {symbols}", flush=True)
    print(" MACD Inicial: (6, 19, 9)", flush=True)
    print("="*85 + "\n", flush=True)

    # 1. Cargar datos y precalcular indicadores para todos los parámetros de una sola vez
    prepared_symbols = {}
    for sym in symbols:
        try:
            data = load_symbol_data(sym)
            p_data = {"1d": {}, "4h": {}, "1h": {}}

            # Precalcular Donchians en 1D
            df_1d = data["1d"].copy()
            for d_win in donchian_windows:
                df_1d = indicators.compute_donchian_channel(df_1d, window=d_win)
            df_1d = indicators.compute_atr(df_1d, window=14)
            p_data["1d"] = df_1d

            # Precalcular MACD para cada temporalidad y cada combinación de (fast, slow, signal)
            for tf in ["1d", "4h", "1h"]:
                df_tf = df_1d.copy() if tf == "1d" else data[tf].copy()
                for fast, slow, sig in macd_params:
                    macd_col = f"macd_cross_{fast}_{slow}_{sig}"
                    ema_fast = df_tf["close"].ewm(span=fast, adjust=False).mean()
                    ema_slow = df_tf["close"].ewm(span=slow, adjust=False).mean()
                    macd_line = ema_fast - ema_slow
                    macd_signal = macd_line.ewm(span=sig, adjust=False).mean()
                    df_tf[macd_col] = (macd_line.shift(1) >= macd_signal.shift(1)) & (macd_line < macd_signal)
                p_data[tf] = df_tf

            prepared_symbols[sym] = p_data
        except Exception as e:
            print(f"Error cargando {sym}: {e}", flush=True)

    grid_results = []

    # 2. Bucle ultra-rápido de simulación
    for d_win in donchian_windows:
        techo_col = f"techo_{d_win}d"
        piso_col = f"piso_{d_win}d"

        for fast, slow, sig in macd_params:
            macd_col = f"macd_cross_{fast}_{slow}_{sig}"

            for sl_m in sl_modes:
                portfolio_profits = []
                portfolio_win_rates = []
                portfolio_pfs = []
                portfolio_dds = []
                total_trades_count = 0

                for sym, p_data in prepared_symbols.items():
                    df_1d = p_data["1d"]
                    df_4h = p_data["4h"]
                    df_1h = p_data["1h"]

                    trades = []
                    in_position = False

                    df_1d_valid = df_1d.dropna(subset=[techo_col]).copy()
                    n_days = len(df_1d_valid)

                    times_1h = df_1h.index
                    lows_1h = df_1h["low"].values
                    closes_1h = df_1h["close"].values
                    macd_cross_1h = df_1h[macd_col].values
                    time_to_idx_1h = {t: idx for idx, t in enumerate(times_1h)}

                    i = 0
                    current_exit_idx = 0

                    while i < n_days:
                        day_ts = df_1d_valid.index[i]
                        day_row = df_1d_valid.iloc[i]
                        techo_val = day_row[techo_col]
                        high_1d = day_row["high"]

                        if in_position:
                            if day_ts < times_1h[current_exit_idx]:
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
                            trigger_candle_low = None

                            break_1h = sub_1h[sub_1h["close"] > techo_val]
                            if not break_1h.empty:
                                entry_tf = "1h"
                                first_break = break_1h.iloc[0]
                                first_break_ts = break_1h.index[0]
                                sub_1h_after = df_1h.loc[df_1h.index > first_break_ts]
                                if not sub_1h_after.empty:
                                    entry_ts = sub_1h_after.index[0]
                                    entry_price = sub_1h_after.iloc[0]["open"]
                                    trigger_candle_low = first_break["low"]

                            if entry_price is None:
                                break_4h = sub_4h[sub_4h["close"] > techo_val]
                                if not break_4h.empty:
                                    entry_tf = "4h"
                                    first_break = break_4h.iloc[0]
                                    first_break_ts = break_4h.index[0]
                                    sub_4h_after = df_4h.loc[df_4h.index > first_break_ts]
                                    if not sub_4h_after.empty:
                                        entry_ts = sub_4h_after.index[0]
                                        entry_price = sub_4h_after.iloc[0]["open"]
                                        trigger_candle_low = first_break["low"]

                            if entry_price is None:
                                if day_row["close"] > techo_val:
                                    entry_tf = "1d"
                                    if i + 1 < n_days:
                                        entry_ts = df_1d_valid.index[i + 1]
                                        entry_price = df_1d_valid.iloc[i + 1]["open"]
                                        trigger_candle_low = day_row["low"]

                            if entry_price is not None and entry_ts is not None and entry_ts in time_to_idx_1h:
                                atr_val = day_row["atr"] if not np.isnan(day_row["atr"]) else (entry_price * 0.02)
                                piso_val = day_row[piso_col] if not np.isnan(day_row[piso_col]) else (entry_price * 0.95)

                                if sl_m == "candle_low":
                                    sl_price = trigger_candle_low if trigger_candle_low < entry_price else entry_price * 0.98
                                elif sl_m == "atr_1.0":
                                    sl_price = entry_price - (1.0 * atr_val)
                                elif sl_m == "atr_1.5":
                                    sl_price = entry_price - (1.5 * atr_val)
                                elif sl_m == "donchian_low":
                                    sl_price = piso_val
                                else:
                                    sl_price = entry_price - (1.5 * atr_val)

                                if sl_price >= entry_price:
                                    sl_price = entry_price * 0.98

                                start_idx = time_to_idx_1h[entry_ts]
                                total_1h_len = len(df_1h)

                                exit_ts = None
                                exit_price = None
                                exit_reason = None
                                exit_idx = total_1h_len - 1

                                for idx in range(start_idx, total_1h_len):
                                    low_val = lows_1h[idx]
                                    close_val = closes_1h[idx]
                                    is_macd_bear = macd_cross_1h[idx]

                                    if low_val <= sl_price:
                                        exit_ts = times_1h[idx]
                                        exit_price = sl_price
                                        exit_reason = "Stop Loss"
                                        exit_idx = idx
                                        break
                                    
                                    if is_macd_bear and idx > start_idx:
                                        exit_ts = times_1h[idx]
                                        exit_price = close_val
                                        exit_reason = "MACD Exit (TP)"
                                        exit_idx = idx
                                        break

                                if exit_price is None:
                                    exit_idx = total_1h_len - 1
                                    exit_ts = times_1h[exit_idx]
                                    exit_price = closes_1h[exit_idx]
                                    exit_reason = "End of Data"

                                raw_return = (exit_price - entry_price) / entry_price
                                net_return = (1 + raw_return) * ((1 - 0.001) ** 2) - 1

                                trades.append(net_return * 100)

                                in_position = True
                                current_exit_idx = exit_idx

                        i += 1

                    if trades:
                        trades_arr = np.array(trades)
                        wins = trades_arr[trades_arr > 0]
                        losses = trades_arr[trades_arr <= 0]
                        
                        win_rate = (len(wins) / len(trades_arr)) * 100
                        gross_profit = wins.sum() if len(wins) > 0 else 0.0
                        gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0
                        pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

                        cap = 10000.0
                        eq = [cap]
                        for r in trades_arr:
                            cap *= (1 + r / 100.0)
                            eq.append(cap)
                        eq_s = pd.Series(eq)
                        dd = abs(((eq_s - eq_s.cummax()) / eq_s.cummax()).min()) * 100
                        net_profit_pct = ((cap - 10000.0) / 10000.0) * 100

                        portfolio_profits.append(net_profit_pct)
                        portfolio_win_rates.append(win_rate)
                        portfolio_pfs.append(pf)
                        portfolio_dds.append(dd)
                        total_trades_count += len(trades_arr)

                if portfolio_profits:
                    grid_results.append({
                        "Ventana Techo": f"{d_win}D",
                        "MACD (Fast,Slow,Sig)": f"({fast},{slow},{sig})",
                        "Modo SL": sl_m,
                        "Retorno Promedio Portfolio": np.mean(portfolio_profits),
                        "Win Rate Promedio": np.mean(portfolio_win_rates),
                        "Profit Factor Promedio": np.mean(portfolio_pfs),
                        "Max DD Promedio": np.mean(portfolio_dds),
                        "Total Trades": total_trades_count
                    })

    df_grid = pd.DataFrame(grid_results)
    df_grid = df_grid.sort_values("Retorno Promedio Portfolio", ascending=False).reset_index(drop=True)

    print(" TOP 10 MEJORES CONFIGURACIONES DE INDICADORES (PORTFOLIO COMPLETO):\n", flush=True)
    df_show = df_grid.copy()
    df_show["Retorno Promedio Portfolio"] = df_show["Retorno Promedio Portfolio"].map(lambda x: f"{x:+.2f}%")
    df_show["Win Rate Promedio"] = df_show["Win Rate Promedio"].map(lambda x: f"{x:.1f}%")
    df_show["Profit Factor Promedio"] = df_show["Profit Factor Promedio"].map(lambda x: f"{x:.2f}")
    df_show["Max DD Promedio"] = df_show["Max DD Promedio"].map(lambda x: f"{x:.2f}%")

    print(df_show.head(10).to_string(index=False), flush=True)

    best_config = df_grid.iloc[0]
    print("\n" + "="*70, flush=True)
    print(" GANADOR ABSOLUTO DE LA OPTIMIZACIÓN EN BUCLE:", flush=True)
    print(f"   • Ventana Techo Donchian: {best_config['Ventana Techo']}", flush=True)
    print(f"   • Configuración MACD    : {best_config['MACD (Fast,Slow,Sig)']}", flush=True)
    print(f"   • Modo de Stop Loss     : {best_config['Modo SL']}", flush=True)
    print(f"   • Retorno Promedio      : {best_config['Retorno Promedio Portfolio']:+.2f}% por moneda", flush=True)
    print(f"   • Win Rate Promedio     : {best_config['Win Rate Promedio']:.1f}%", flush=True)
    print(f"   • Profit Factor         : {best_config['Profit Factor Promedio']:.2f}", flush=True)
    print("="*70 + "\n", flush=True)

if __name__ == "__main__":
    run_fast_grid_search()
