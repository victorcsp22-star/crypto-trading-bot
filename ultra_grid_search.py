import pandas as pd
import numpy as np
from data_loader import load_symbol_data
import indicators

def run_ultra_fast_grid_search():
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
    print(" INICIANDO ULTRA-GRID SEARCH (OPTIMIZACIÓN HIPER-RÁPIDA EN NUMPY)", flush=True)
    print(f" Portfolio: {symbols}", flush=True)
    print(" Configuración MACD inicial requerida: (6, 19, 9)", flush=True)
    print("="*85 + "\n", flush=True)

    prepared_symbols = {}

    for sym in symbols:
        try:
            data = load_symbol_data(sym)
            df_1d = data["1d"].copy()
            df_4h = data["4h"].copy()
            df_1h = data["1h"].copy()

            # Precalcular Donchians y ATR
            for d_win in donchian_windows:
                df_1d = indicators.compute_donchian_channel(df_1d, window=d_win)
            df_1d = indicators.compute_atr(df_1d, window=14)

            # Precalcular MACD
            for fast, slow, sig in macd_params:
                col = f"macd_cross_{fast}_{slow}_{sig}"
                for df_tf in [df_1d, df_4h, df_1h]:
                    ema_f = df_tf["close"].ewm(span=fast, adjust=False).mean()
                    ema_s = df_tf["close"].ewm(span=slow, adjust=False).mean()
                    m_line = ema_f - ema_s
                    m_sig = m_line.ewm(span=sig, adjust=False).mean()
                    df_tf[col] = (m_line.shift(1) >= m_sig.shift(1)) & (m_line < m_sig)

            # Pre-Mapear límites intradía para evitar pandas slicing
            df_1d_valid = df_1d.dropna(subset=["techo_14d"]).copy()
            days_info = []

            for i in range(len(df_1d_valid)):
                day_ts = df_1d_valid.index[i]
                day_row = df_1d_valid.iloc[i]
                next_day_ts = day_ts + pd.Timedelta(days=1)

                sub_1h = df_1h.loc[(df_1h.index >= day_ts) & (df_1h.index < next_day_ts)]
                sub_4h = df_4h.loc[(df_4h.index >= day_ts) & (df_4h.index < next_day_ts)]

                days_info.append({
                    "day_ts": day_ts,
                    "high_1d": day_row["high"],
                    "close_1d": day_row["close"],
                    "atr": day_row["atr"] if not np.isnan(day_row["atr"]) else (day_row["close"] * 0.02),
                    "sub_1h_times": sub_1h.index,
                    "sub_1h_closes": sub_1h["close"].values,
                    "sub_1h_lows": sub_1h["low"].values,
                    "sub_4h_times": sub_4h.index,
                    "sub_4h_closes": sub_4h["close"].values,
                    "sub_4h_lows": sub_4h["low"].values,
                    "day_row": day_row
                })

            time_to_idx_1h = {t: idx for idx, t in enumerate(df_1h.index)}

            prepared_symbols[sym] = {
                "days_info": days_info,
                "df_1h": df_1h,
                "times_1h": df_1h.index,
                "lows_1h": df_1h["low"].values,
                "closes_1h": df_1h["close"].values,
                "time_to_idx_1h": time_to_idx_1h
            }
        except Exception as e:
            print(f"Error preparando {sym}: {e}", flush=True)

    grid_results = []

    # Bucle principal de Grid Search
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
                total_trades = 0

                for sym, p_data in prepared_symbols.items():
                    days_info = p_data["days_info"]
                    df_1h = p_data["df_1h"]
                    times_1h = p_data["times_1h"]
                    lows_1h = p_data["lows_1h"]
                    closes_1h = p_data["closes_1h"]
                    macd_cross_1h = df_1h[macd_col].values
                    time_to_idx_1h = p_data["time_to_idx_1h"]

                    trades = []
                    in_position = False
                    current_exit_idx = 0
                    n_days = len(days_info)

                    for i in range(n_days):
                        d = days_info[i]
                        day_ts = d["day_ts"]
                        techo_val = d["day_row"][techo_col]
                        piso_val = d["day_row"][piso_col]
                        high_1d = d["high_1d"]

                        if in_position:
                            if day_ts < times_1h[current_exit_idx]:
                                continue
                            else:
                                in_position = False

                        if high_1d >= techo_val:
                            entry_ts = None
                            entry_price = None
                            trigger_candle_low = None

                            # 1h check
                            sub_1h_c = d["sub_1h_closes"]
                            mask_1h = sub_1h_c > techo_val
                            if np.any(mask_1h):
                                first_idx = np.argmax(mask_1h)
                                first_break_ts = d["sub_1h_times"][first_idx]
                                idx_global = time_to_idx_1h[first_break_ts]
                                if idx_global + 1 < len(times_1h):
                                    entry_ts = times_1h[idx_global + 1]
                                    entry_price = df_1h["open"].values[idx_global + 1]
                                    trigger_candle_low = d["sub_1h_lows"][first_idx]

                            # 4h check
                            if entry_price is None:
                                sub_4h_c = d["sub_4h_closes"]
                                mask_4h = sub_4h_c > techo_val
                                if np.any(mask_4h):
                                    first_idx = np.argmax(mask_4h)
                                    first_break_ts = d["sub_4h_times"][first_idx]
                                    idx_global = time_to_idx_1h.get(first_break_ts, 0)
                                    if idx_global + 1 < len(times_1h):
                                        entry_ts = times_1h[idx_global + 1]
                                        entry_price = df_1h["open"].values[idx_global + 1]
                                        trigger_candle_low = d["sub_4h_lows"][first_idx]

                            # 1D check
                            if entry_price is None:
                                if d["close_1d"] > techo_val and i + 1 < n_days:
                                    next_d = days_info[i + 1]
                                    entry_ts = next_d["day_ts"]
                                    if entry_ts in time_to_idx_1h:
                                        idx_g = time_to_idx_1h[entry_ts]
                                        entry_price = df_1h["open"].values[idx_g]
                                        trigger_candle_low = d["day_row"]["low"]

                            if entry_price is not None and entry_ts is not None and entry_ts in time_to_idx_1h:
                                atr_val = d["atr"]

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
                                total_len = len(times_1h)
                                exit_price = None
                                exit_idx = total_len - 1

                                for idx in range(start_idx, total_len):
                                    low_val = lows_1h[idx]
                                    close_val = closes_1h[idx]
                                    is_macd_bear = macd_cross_1h[idx]

                                    if low_val <= sl_price:
                                        exit_price = sl_price
                                        exit_idx = idx
                                        break
                                    if is_macd_bear and idx > start_idx:
                                        exit_price = close_val
                                        exit_idx = idx
                                        break

                                if exit_price is None:
                                    exit_idx = total_len - 1
                                    exit_price = closes_1h[exit_idx]

                                raw_ret = (exit_price - entry_price) / entry_price
                                net_ret = (1 + raw_ret) * ((1 - 0.001) ** 2) - 1

                                trades.append(net_ret * 100)
                                in_position = True
                                current_exit_idx = exit_idx

                    if trades:
                        t_arr = np.array(trades)
                        wins = t_arr[t_arr > 0]
                        losses = t_arr[t_arr <= 0]
                        
                        win_rate = (len(wins) / len(t_arr)) * 100
                        gp = wins.sum() if len(wins) > 0 else 0.0
                        gl = abs(losses.sum()) if len(losses) > 0 else 0.0
                        pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)

                        cap = 10000.0
                        eq = [cap]
                        for r in t_arr:
                            cap *= (1 + r / 100.0)
                            eq.append(cap)
                        eq_s = pd.Series(eq)
                        dd = abs(((eq_s - eq_s.cummax()) / eq_s.cummax()).min()) * 100
                        net_p = ((cap - 10000.0) / 10000.0) * 100

                        portfolio_profits.append(net_p)
                        portfolio_win_rates.append(win_rate)
                        portfolio_pfs.append(pf)
                        portfolio_dds.append(dd)
                        total_trades += len(t_arr)

                if portfolio_profits:
                    grid_results.append({
                        "Ventana Techo": f"{d_win}D",
                        "MACD (Fast,Slow,Sig)": f"({fast},{slow},{sig})",
                        "Modo SL": sl_m,
                        "Retorno Promedio Portfolio": np.mean(portfolio_profits),
                        "Win Rate Promedio": np.mean(portfolio_win_rates),
                        "Profit Factor Promedio": np.mean(portfolio_pfs),
                        "Max DD Promedio": np.mean(portfolio_dds),
                        "Total Trades": total_trades
                    })

    df_res = pd.DataFrame(grid_results)
    df_res = df_res.sort_values("Retorno Promedio Portfolio", ascending=False).reset_index(drop=True)

    print(" TOP 10 MEJORES CONFIGURACIONES DE INDICADORES (GRID SEARCH RESULT):\n", flush=True)
    df_show = df_res.copy()
    df_show["Retorno Promedio Portfolio"] = df_show["Retorno Promedio Portfolio"].map(lambda x: f"{x:+.2f}%")
    df_show["Win Rate Promedio"] = df_show["Win Rate Promedio"].map(lambda x: f"{x:.1f}%")
    df_show["Profit Factor Promedio"] = df_show["Profit Factor Promedio"].map(lambda x: f"{x:.2f}")
    df_show["Max DD Promedio"] = df_show["Max DD Promedio"].map(lambda x: f"{x:.2f}%")

    print(df_show.head(10).to_string(index=False), flush=True)

    best = df_res.iloc[0]
    print("\n" + "="*70, flush=True)
    print(" GANADOR ABSOLUTO DE LA OPTIMIZACIÓN EN BUCLE:", flush=True)
    print(f"   • Ventana Techo Donchian: {best['Ventana Techo']}", flush=True)
    print(f"   • Configuración MACD    : {best['MACD (Fast,Slow,Sig)']}", flush=True)
    print(f"   • Modo de Stop Loss     : {best['Modo SL']}", flush=True)
    print(f"   • Retorno Promedio      : {best['Retorno Promedio Portfolio']:+.2f}% por moneda", flush=True)
    print(f"   • Win Rate Promedio     : {best['Win Rate Promedio']:.1f}%", flush=True)
    print(f"   • Profit Factor         : {best['Profit Factor Promedio']:.2f}", flush=True)
    print("="*70 + "\n", flush=True)

if __name__ == "__main__":
    run_ultra_fast_grid_search()
