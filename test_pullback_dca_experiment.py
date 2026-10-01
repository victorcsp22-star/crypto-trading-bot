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

def run_experiment_on_symbol(data_dict, initial_capital=1000.0, donchian_window=14, maker_fee_pct=0.0002, taker_fee_pct=0.0005):
    """
    Simula y compara 3 variantes de entrada sobre los mismos datos:
    - Strat A: Base Actual (Entrada Pasiva Límite en Techo 14D al confirmarse 4H/1H).
    - Strat B: Propuesta del Usuario (Entrada Inmediata 50% en Cierre 1H + Recompra 50% en Pullback al Techo 14D).
    - Strat C: Entrada Inmediata 100% en Cierre 1H + Recompra 100% Extra en Pullback al Techo (Duplicando tamaño).
    """
    df_1d = data_dict["1d"].copy()
    df_4h = data_dict["4h"].copy()
    df_1h = data_dict["1h"].copy()

    # Indicadores
    df_1d = indicators.compute_donchian_channel(df_1d, window=donchian_window)
    df_1d = indicators.compute_macd(df_1d)
    df_1d = indicators.compute_atr(df_1d, window=14)
    df_1d['vol_sma20'] = df_1d['volume'].rolling(20).mean()
    df_1d['vol_ratio'] = df_1d['volume'] / df_1d['vol_sma20']
    df_1d['ema_50'] = df_1d['close'].ewm(span=50, adjust=False).mean()

    df_4h = indicators.compute_macd(df_4h)
    df_1h = indicators.compute_macd(df_1h)

    df_1d_valid = df_1d.dropna(subset=[f"techo_{donchian_window}d", "atr", "vol_ratio"]).copy()
    if len(df_1d_valid) < 30:
        return None, None, None

    # Arrays para 1H
    df_1h_clean = df_1h.copy()
    lows_1h = df_1h_clean["low"].values
    highs_1h = df_1h_clean["high"].values
    closes_1h = df_1h_clean["close"].values
    opens_1h = df_1h_clean["open"].values
    macd_cross_1h = df_1h_clean["macd_bear_cross"].values
    time_to_idx_1h = {t: idx for idx, t in enumerate(df_1h_clean.index)}
    total_1h_len = len(df_1h_clean)

    # -------------------------------------------------------------
    # FUNCIÓN SIMULADORA DE ESTRATEGIA
    # -------------------------------------------------------------
    def simulate_strategy(mode="A"):
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
            high_1d = day_row["high"]
            atr_val = day_row["atr"]
            close_1d = day_row["close"]
            ema50_1d = day_row["ema_50"]
            vol_ratio_1d = day_row["vol_ratio"]

            if in_position:
                if day_ts < df_1h_clean.index[current_exit_idx]:
                    i += 1
                    continue
                else:
                    in_position = False

            if high_1d >= techo_val and close_1d > ema50_1d and vol_ratio_1d >= 1.15:
                next_day_ts = day_ts + pd.Timedelta(days=1)
                sub_4h = df_4h.loc[(df_4h.index >= day_ts) & (df_4h.index < next_day_ts)]
                sub_1h = df_1h.loc[(df_1h.index >= day_ts) & (df_1h.index < next_day_ts)]

                entry_price_1 = None
                entry_ts_1 = None

                break_4h = sub_4h[sub_4h["close"] > techo_val]
                if not break_4h.empty:
                    first_break_ts = break_4h.index[0]
                    sub_1h_after = df_1h.loc[df_1h.index > first_break_ts]

                    if mode == "A":
                        # Estrategia A: Espera toque a Techo 14D (Maker Limit Order)
                        for t_1h, bar_1h in sub_1h_after.iterrows():
                            if bar_1h["low"] <= techo_val:
                                entry_ts_1 = t_1h
                                entry_price_1 = techo_val
                                break
                        if entry_price_1 is None and not sub_1h_after.empty:
                            entry_ts_1 = sub_1h_after.index[0]
                            entry_price_1 = sub_1h_after.iloc[0]["open"]
                    elif mode in ["B", "C"]:
                        # Estrategia B/C: Entra INMEDIATO al cierre de la 1H que confirma arriba
                        break_1h = sub_1h_after[sub_1h_after["close"] > techo_val]
                        if not break_1h.empty:
                            entry_ts_1 = break_1h.index[0]
                            entry_price_1 = break_1h.iloc[0]["close"]
                        elif not sub_1h_after.empty:
                            entry_ts_1 = sub_1h_after.index[0]
                            entry_price_1 = sub_1h_after.iloc[0]["close"]

                if entry_price_1 is None and day_row["close"] > techo_val:
                    if i + 1 < n_days:
                        entry_ts_1 = df_1d_valid.index[i + 1]
                        entry_price_1 = df_1d_valid.iloc[i + 1]["open"]

                if entry_price_1 is not None and entry_ts_1 is not None and entry_ts_1 in time_to_idx_1h:
                    sl_dist_atr = 1.5 * atr_val
                    start_idx = time_to_idx_1h[entry_ts_1]

                    # Cálculo de Asignación y Tamaño de Posición
                    if mode == "A":
                        sl_price = entry_price_1 - sl_dist_atr
                        sl_dist_pct = (entry_price_1 - sl_price) / entry_price_1
                        risk_usd = capital * 0.02
                        pos_size_usd = min(risk_usd / sl_dist_pct, capital * 0.25)
                        if pos_size_usd < 15.0 or pos_size_usd > capital:
                            i += 1
                            continue
                        
                        units = pos_size_usd / entry_price_1
                        capital_allocated = pos_size_usd
                        capital -= capital_allocated
                        avg_entry_price = entry_price_1

                    elif mode == "B":
                        # 50% Entrada Inicial en 1H Close
                        sl_price_temp = entry_price_1 - sl_dist_atr
                        sl_dist_pct_temp = (entry_price_1 - sl_price_temp) / entry_price_1
                        total_intended_pos = min((capital * 0.02) / sl_dist_pct_temp, capital * 0.25)
                        if total_intended_pos < 15.0 or total_intended_pos > capital:
                            i += 1
                            continue

                        tranche1_usd = total_intended_pos * 0.5
                        units_tranche1 = tranche1_usd / entry_price_1
                        capital_allocated = tranche1_usd
                        capital -= capital_allocated
                        
                        units = units_tranche1
                        avg_entry_price = entry_price_1
                        sl_price = avg_entry_price - sl_dist_atr
                        tranche2_executed = False

                    elif mode == "C":
                        # 100% Entrada Inicial
                        sl_price_temp = entry_price_1 - sl_dist_atr
                        sl_dist_pct_temp = (entry_price_1 - sl_price_temp) / entry_price_1
                        pos_size_usd = min((capital * 0.02) / sl_dist_pct_temp, capital * 0.25)
                        if pos_size_usd < 15.0 or pos_size_usd > capital:
                            i += 1
                            continue

                        units_tranche1 = pos_size_usd / entry_price_1
                        capital_allocated = pos_size_usd
                        capital -= capital_allocated
                        units = units_tranche1
                        avg_entry_price = entry_price_1
                        sl_price = avg_entry_price - sl_dist_atr
                        tranche2_executed = False

                    exit_ts = None
                    exit_price = None
                    exit_reason = None
                    exit_idx = total_1h_len - 1
                    is_be = False

                    for idx in range(start_idx, total_1h_len):
                        low_val = lows_1h[idx]
                        high_val = highs_1h[idx]
                        close_val = closes_1h[idx]
                        is_macd_bear = macd_cross_1h[idx]

                        # EVALUAR RECOMPRA / SCALE-IN EN PULLBACK AL TECHO 14D (Para Modo B y C)
                        if mode in ["B", "C"] and not tranche2_executed and idx > start_idx:
                            if low_val <= techo_val:
                                # Pullback ocurrió: se compra el 2do Tranche a precio techo_val
                                tranche2_price = techo_val
                                if mode == "B":
                                    tranche2_usd = min(tranche1_usd, capital)
                                    if tranche2_usd >= 5.0:
                                        units_tranche2 = tranche2_usd / tranche2_price
                                        capital -= tranche2_usd
                                        capital_allocated += tranche2_usd
                                        units += units_tranche2
                                        avg_entry_price = (tranche1_usd + tranche2_usd) / units
                                        sl_price = avg_entry_price - sl_dist_atr
                                        tranche2_executed = True
                                elif mode == "C":
                                    tranche2_usd = min(pos_size_usd, capital)
                                    if tranche2_usd >= 5.0:
                                        units_tranche2 = tranche2_usd / tranche2_price
                                        capital -= tranche2_usd
                                        capital_allocated += tranche2_usd
                                        units += units_tranche2
                                        avg_entry_price = (pos_size_usd + tranche2_usd) / units
                                        sl_price = avg_entry_price - sl_dist_atr
                                        tranche2_executed = True

                        # Breakeven Lock (+1.2 ATR a favor del precio promedio)
                        if not is_be and high_val >= (avg_entry_price + 1.2 * atr_val):
                            sl_price = avg_entry_price
                            is_be = True

                        # Check Stop Loss
                        if low_val <= sl_price:
                            exit_ts = df_1h_clean.index[idx]
                            exit_price = sl_price
                            exit_reason = "Breakeven Lock" if is_be else "Stop Loss ATR (1H)"
                            exit_idx = idx
                            break

                        # Check Salida por MACD Bajista 1H
                        if is_macd_bear and idx > start_idx:
                            exit_ts = df_1h_clean.index[idx]
                            exit_price = close_val
                            exit_reason = "Cruce MACD 1H"
                            exit_idx = idx
                            break

                    if exit_price is None:
                        exit_idx = total_1h_len - 1
                        exit_ts = df_1h_clean.index[exit_idx]
                        exit_price = closes_1h[exit_idx]
                        exit_reason = "Fin de Datos"

                    # Aplicar tarifas (Maker en entradas límites/pullback, Taker en salida)
                    fee_total = (avg_entry_price * units * maker_fee_pct) + (exit_price * units * taker_fee_pct)
                    raw_pnl = (exit_price - avg_entry_price) * units
                    net_pnl = raw_pnl - fee_total
                    capital += capital_allocated + net_pnl

                    trades.append({
                        "entry_time": entry_ts_1,
                        "exit_time": exit_ts,
                        "entry_price": avg_entry_price,
                        "exit_price": exit_price,
                        "size_usd": capital_allocated,
                        "pnl_usd": net_pnl,
                        "pnl_pct": (net_pnl / capital_allocated) * 100,
                        "exit_reason": exit_reason,
                        "pullback_executed": tranche2_executed if mode in ["B", "C"] else False
                    })

                    in_position = True
                    current_exit_idx = exit_idx

                    current_equity = capital
                    if current_equity > peak_capital:
                        peak_capital = current_equity
                    dd = (peak_capital - current_equity) / peak_capital * 100
                    if dd > max_drawdown_pct:
                        max_drawdown_pct = dd

            i += 1

        if not trades:
            return None

        df_trades = pd.DataFrame(trades)
        wins = df_trades[df_trades["pnl_usd"] > 0]
        losses = df_trades[df_trades["pnl_usd"] <= 0]
        total_trades = len(df_trades)
        win_rate = (len(wins) / total_trades) * 100
        gross_p = wins["pnl_usd"].sum() if not wins.empty else 0.0
        gross_l = abs(losses["pnl_usd"].sum()) if not losses.empty else 0.0
        profit_factor = (gross_p / gross_l) if gross_l > 0 else (999.0 if gross_p > 0 else 0.0)
        net_profit_pct = ((capital - initial_capital) / initial_capital) * 100

        pullbacks_count = df_trades["pullback_executed"].sum() if "pullback_executed" in df_trades.columns else 0

        return {
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "net_profit_pct": net_profit_pct,
            "final_capital": capital,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown_pct,
            "pullbacks_count": pullbacks_count
        }

    res_A = simulate_strategy(mode="A")
    res_B = simulate_strategy(mode="B")
    res_C = simulate_strategy(mode="C")

    return res_A, res_B, res_C

def main():
    print("\n" + "="*90)
    print(" 🧪 EXPERIMENTO CUANTITATIVO: ENTRADA INMEDIATA 1H vs. COMPRA EN PULLBACK AL TECHO 14D")
    print(" Evaluando rentabilidad, win rate, drawdown y promediación de costo en 101 criptomonedas")
    print("="*90 + "\n")

    symbols = get_all_symbols()
    print(f" Cargando y evaluando dataset para {len(symbols)} monedas...\n")

    list_A, list_B, list_C = [], [], []

    for idx, sym in enumerate(symbols, 1):
        try:
            data = load_symbol_data(sym)
            res_A, res_B, res_C = run_experiment_on_symbol(data, initial_capital=1000.0, donchian_window=14)
            
            if res_A and res_A["total_trades"] >= 3:
                list_A.append({"Symbol": sym, **res_A})
            if res_B and res_B["total_trades"] >= 3:
                list_B.append({"Symbol": sym, **res_B})
            if res_C and res_C["total_trades"] >= 3:
                list_C.append({"Symbol": sym, **res_C})

            if idx % 20 == 0:
                print(f"   [+] Procesadas {idx}/{len(symbols)} monedas...", flush=True)
        except Exception:
            continue

    df_A = pd.DataFrame(list_A)
    df_B = pd.DataFrame(list_B)
    df_C = pd.DataFrame(list_C)

    print("\n" + "="*90)
    print(" 📊 RESULTADOS COMPARATIVOS GLOBALES ( PROMEDIO DE TODAS LAS CRIPTOMONEDAS ):")
    print("="*90)
    
    print("\n 🔹 ESTRATEGIA A: BASE ACTUAL (Orden Límite Pasiva Maker en Techo 14D - Sin Promediación)")
    print(f"   • Retorno Neto Promedio (%)  : {df_A['net_profit_pct'].mean():>+,.2f}%")
    print(f"   • Factor de Beneficio (PF)   : {df_A['profit_factor'].mean():.2f}")
    print(f"   • Tasa de Acierto (WinRate)  : {df_A['win_rate_pct'].mean():.1f}%")
    print(f"   • Máximo Drawdown Promedio   : {df_A['max_drawdown_pct'].mean():.2f}%")
    print(f"   • Monedas Ganadoras (>0%)    : {len(df_A[df_A['net_profit_pct'] > 0])} / {len(df_A)} ({len(df_A[df_A['net_profit_pct'] > 0])/len(df_A)*100:.1f}%)")

    print("\n 🔸 ESTRATEGIA B: PROPUESTA DEL USUARIO (Entrada 1H Inmediata 50% + Scale-In 50% en Pullback)")
    print(f"   • Retorno Neto Promedio (%)  : {df_B['net_profit_pct'].mean():>+,.2f}%")
    print(f"   • Factor de Beneficio (PF)   : {df_B['profit_factor'].mean():.2f}")
    print(f"   • Tasa de Acierto (WinRate)  : {df_B['win_rate_pct'].mean():.1f}%")
    print(f"   • Máximo Drawdown Promedio   : {df_B['max_drawdown_pct'].mean():.2f}%")
    print(f"   • Total Pullbacks Ejecutados : {df_B['pullbacks_count'].sum()} recompras exitosas")
    print(f"   • Monedas Ganadoras (>0%)    : {len(df_B[df_B['net_profit_pct'] > 0])} / {len(df_B)} ({len(df_B[df_B['net_profit_pct'] > 0])/len(df_B)*100:.1f}%)")

    print("\n ⚡ ESTRATEGIA C: AGRESIVA (Entrada 1H Inmediata 100% + Recompra 100% Extra en Pullback)")
    print(f"   • Retorno Neto Promedio (%)  : {df_C['net_profit_pct'].mean():>+,.2f}%")
    print(f"   • Factor de Beneficio (PF)   : {df_C['profit_factor'].mean():.2f}")
    print(f"   • Tasa de Acierto (WinRate)  : {df_C['win_rate_pct'].mean():.1f}%")
    print(f"   • Máximo Drawdown Promedio   : {df_C['max_drawdown_pct'].mean():.2f}%")
    print(f"   • Total Pullbacks Ejecutados : {df_C['pullbacks_count'].sum()} recompras exitosas")
    print(f"   • Monedas Ganadoras (>0%)    : {len(df_C[df_C['net_profit_pct'] > 0])} / {len(df_C)} ({len(df_C[df_C['net_profit_pct'] > 0])/len(df_C)*100:.1f}%)")
    print("="*90 + "\n")

if __name__ == "__main__":
    main()
