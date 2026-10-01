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

def run_proposed_architecture_symbol(data_dict, initial_capital=1000.0, donchian_window=14, maker_fee_pct=0.0002, taker_fee_pct=0.0005):
    """
    Simula la Arquitectura Institucional Propuesta:
    - Techo 14D en 1D.
    - Confirmación de Cierre en 4H.
    - Gatillo con Orden Límite Pasiva (Maker) en 1H sobre el Techo 14D (Sin Slippage y Comisión Reducida de Maker 0.02%).
    - Fallback de Cierre en 1D si 4H no confirma.
    - Stop Loss Dinámico por ATR + Breakeven Lock + Salida por Cruce MACD en 4H/1H.
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
        return None

    # Arrays optimizados para 1H
    df_1h_clean = df_1h.copy()
    lows_1h = df_1h_clean["low"].values
    highs_1h = df_1h_clean["high"].values
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

        # Evaluación de Techo 14D + Filtro de Estructura Institucional
        if high_1d >= techo_val and close_1d > ema50_1d and vol_ratio_1d >= 1.15:
            next_day_ts = day_ts + pd.Timedelta(days=1)
            sub_4h = df_4h.loc[(df_4h.index >= day_ts) & (df_4h.index < next_day_ts)]
            sub_1h = df_1h.loc[(df_1h.index >= day_ts) & (df_1h.index < next_day_ts)]

            entry_tf = None
            entry_ts = None
            entry_price = None

            # PASO 1: Confirmación de Cierre en 4H
            break_4h = sub_4h[sub_4h["close"] > techo_val]
            if not break_4h.empty:
                entry_tf = "4H -> 1H Maker Limit Order"
                first_break_ts = break_4h.index[0]
                
                # GATILLO 1H: Colocar Orden Límite pasiva en Techo 14D tras el cierre de 4H
                sub_1h_after = df_1h.loc[df_1h.index > first_break_ts]
                for t_1h, bar_1h in sub_1h_after.iterrows():
                    # Si el precio en 1H retrocede a tocar el Techo 14D, la Orden Límite Maker ejecuta a precio exacto
                    if bar_1h["low"] <= techo_val:
                        entry_ts = t_1h
                        entry_price = techo_val # Orden Límite ejecutada al precio exacto sin slippage
                        break
                
                # Si no tocó el techo exacto pero la 1H se consolida arriba, entra al precio de apertura
                if entry_price is None and not sub_1h_after.empty:
                    entry_ts = sub_1h_after.index[0]
                    entry_price = sub_1h_after.iloc[0]["open"]

            # PASO 2: Fallback Cierre Diario 1D
            if entry_price is None:
                if day_row["close"] > techo_val:
                    entry_tf = "1D Fallback"
                    if i + 1 < n_days:
                        entry_ts = df_1d_valid.index[i + 1]
                        entry_price = df_1d_valid.iloc[i + 1]["open"]

            if entry_price is not None and entry_ts is not None and entry_ts in time_to_idx_1h:
                sl_price = entry_price - (1.5 * atr_val)
                if sl_price >= entry_price:
                    sl_price = entry_price * 0.95

                sl_dist_pct = (entry_price - sl_price) / entry_price
                risk_usd = capital * 0.02 # 2% del capital
                pos_size_usd = min(risk_usd / sl_dist_pct, capital * 0.25)

                if pos_size_usd >= 15.0 and pos_size_usd <= capital:
                    units = pos_size_usd / entry_price
                    capital_allocated = pos_size_usd
                    capital -= capital_allocated

                    start_idx = time_to_idx_1h[entry_ts]
                    total_1h_len = len(df_1h_clean)

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

                        # Breakeven Lock (+1.2 ATR)
                        if not is_be and high_val >= (entry_price + 1.2 * atr_val):
                            sl_price = entry_price
                            is_be = True

                        if low_val <= sl_price:
                            exit_ts = df_1h_clean.index[idx]
                            exit_price = sl_price
                            exit_reason = "Breakeven Lock (Riesgo 0)" if is_be else "Stop Loss ATR (1H)"
                            exit_idx = idx
                            break

                        if is_macd_bear and idx > start_idx:
                            exit_ts = df_1h_clean.index[idx]
                            exit_price = close_val
                            exit_reason = "Cruce MACD 1H (Exit)"
                            exit_idx = idx
                            break

                    if exit_price is None:
                        exit_idx = total_1h_len - 1
                        exit_ts = df_1h_clean.index[exit_idx]
                        exit_price = closes_1h[exit_idx]
                        exit_reason = "Fin de Datos"

                    # Aplicar tarifa Maker (0.02%) en entrada Límite y Taker (0.05%) en salida
                    fee_total = (entry_price * units * maker_fee_pct) + (exit_price * units * taker_fee_pct)
                    raw_pnl = (exit_price - entry_price) * units
                    net_pnl = raw_pnl - fee_total
                    capital += capital_allocated + net_pnl

                    trades.append({
                        "entry_time": entry_ts,
                        "exit_time": exit_ts,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "size_usd": capital_allocated,
                        "pnl_usd": net_pnl,
                        "pnl_pct": (net_pnl / capital_allocated) * 100,
                        "exit_reason": exit_reason,
                        "entry_tf": entry_tf
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

    return {
        "total_trades": total_trades,
        "win_rate_pct": win_rate,
        "net_profit_pct": net_profit_pct,
        "final_capital": capital,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "trades": trades
    }

def main():
    print("\n" + "="*85)
    print(" 🏛️ BACKTEST COMPLETO DE LA ARQUITECTURA INSTITUCIONAL PROPUESTA")
    print(" 101 Criptomonedas | Techo 14D (1D) -> Cierre 4H -> Gatillo Límite Maker 1H")
    print(" Ajustes: Órdenes Pasivas Maker (0.02% Fee) | SL ATR 1.5x + Breakeven Lock (+1.2x)")
    print("="*85 + "\n")

    symbols = get_all_symbols()
    print(f" Cargas listas para {len(symbols)} monedas en el dataset local.")
    print(" Ejecutando simulación de alta precisión...\n")

    results = []
    processed_count = 0

    for sym in symbols:
        try:
            data = load_symbol_data(sym)
            res = run_proposed_architecture_symbol(data, initial_capital=1000.0, donchian_window=14)
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
        print("❌ No se generaron resultados.")
        return

    df_sorted = df_res.sort_values("Net Profit (%)", ascending=False).reset_index(drop=True)

    print("\n" + "="*85)
    print(f" 📊 RESULTADOS GLOBALES DE LA ARQUITECTURA EN LAS {len(df_sorted)} CRIPTOMONEDAS:")
    print("="*85)
    print(f" • Promedio de Retorno Neto (%) : {df_sorted['Net Profit (%)'].mean():>+,.2f}%")
    print(f" • Mediana de Retorno Neto (%)  : {df_sorted['Net Profit (%)'].median():>+,.2f}%")
    print(f" • Tasa de Acierto Promedio     : {df_sorted['Win Rate (%)'].mean():.1f}%")
    print(f" • Factor de Beneficio Promedio : {df_sorted['Profit Factor'].mean():.2f}")
    print(f" • Máximo Drawdown Promedio     : {df_sorted['Max Drawdown (%)'].mean():.2f}%")
    print(f" • Monedas Ganadoras (>0%)      : {len(df_sorted[df_sorted['Net Profit (%)'] > 0])} / {len(df_sorted)} ({len(df_sorted[df_sorted['Net Profit (%)'] > 0])/len(df_sorted)*100:.1f}%)")
    print("="*85 + "\n")

    print(" 🏆 TOP 20 CRIPTOMONEDAS MÁS RENTABLES EN LA ARQUITECTURA INSTITUCIONAL:")
    print("-" * 85)
    top20 = df_sorted.head(20)[["Symbol", "Net Profit (%)", "Win Rate (%)", "Profit Factor", "Max Drawdown (%)", "Trades"]]
    print(top20.to_string(index=False))

    df_sorted.to_csv("proposed_architecture_backtest_results.csv", index=False)
    print(f"\n✅ Resultados detallados guardados en 'proposed_architecture_backtest_results.csv'.\n")

if __name__ == "__main__":
    main()
