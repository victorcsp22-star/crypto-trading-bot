import os
import glob
import sys
import pandas as pd
import numpy as np
from data_loader import load_symbol_data
import indicators

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Lista Blanca Validada de Criptomonedas de la Base de Datos Local (Top 15-20)
WHITELIST_101 = [
    "DOGEUSDT", "ADAUSDT", "SHIBUSDT", "BNBUSDT", "AXSUSDT", 
    "FILUSDT", "DASHUSDT", "XLMUSDT", "ZECUSDT", "XRPUSDT", 
    "ETHUSDT", "MKRUSDT", "ORDIUSDT", "AVAXUSDT", "WLDUSDT", 
    "SOLUSDT", "FLOKIUSDT", "GALAUSDT", "LUNCUSDT", "PEPEUSDT"
]

def run_1d4h_backtest_symbol(data_dict, initial_capital=1000.0, donchian_window=10, fee_pct=0.001, slippage_pct=0.0005):
    df_1d = data_dict["1d"].copy()
    df_4h = data_dict["4h"].copy()

    # Indicadores 1D y 4H
    df_1d = indicators.compute_donchian_channel(df_1d, window=donchian_window)
    df_1d = indicators.compute_macd(df_1d)
    df_1d = indicators.compute_atr(df_1d, window=14)
    df_1d['ema_50'] = df_1d['close'].ewm(span=50, adjust=False).mean()
    df_1d['vol_sma20'] = df_1d['volume'].rolling(20).mean()
    df_1d['vol_ratio'] = df_1d['volume'] / df_1d['vol_sma20']

    df_4h = indicators.compute_macd(df_4h)
    df_4h['vol_sma20'] = df_4h['volume'].rolling(20).mean()
    df_4h['vol_ratio'] = df_4h['volume'] / df_4h['vol_sma20']

    df_1d_valid = df_1d.dropna(subset=[f"techo_{donchian_window}d", "atr", "vol_ratio"]).copy()
    if len(df_1d_valid) < 30:
        return None

    df_4h_clean = df_4h.copy()
    lows_4h = df_4h_clean["low"].values
    closes_4h = df_4h_clean["close"].values
    macd_cross_4h = df_4h_clean["macd_bear_cross"].values
    time_to_idx_4h = {t: idx for idx, t in enumerate(df_4h_clean.index)}

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
        atr_val = day_row["atr"]
        vol_ratio_1d = day_row["vol_ratio"]
        close_1d = day_row["close"]
        ema50_1d = day_row["ema_50"]

        if in_position:
            if day_ts < df_4h_clean.index[current_exit_idx]:
                i += 1
                continue
            else:
                in_position = False

        # Filtro de Régimen y Volumen en 1D/4H
        if high_1d >= techo_val and close_1d > ema50_1d and vol_ratio_1d >= 1.15:
            next_day_ts = day_ts + pd.Timedelta(days=1)
            sub_4h = df_4h.loc[(df_4h.index >= day_ts) & (df_4h.index < next_day_ts)]

            entry_ts = None
            entry_price = None

            # Evaluar Cierre 4H
            break_4h = sub_4h[sub_4h["close"] > techo_val]
            if not break_4h.empty:
                first_break_ts = break_4h.index[0]
                sub_4h_after = df_4h.loc[df_4h.index > first_break_ts]
                if not sub_4h_after.empty:
                    entry_ts = sub_4h_after.index[0]
                    entry_price = sub_4h_after.iloc[0]["open"]
            else:
                # Cierre 1D
                if day_row["close"] > techo_val:
                    if i + 1 < n_days:
                        entry_ts = df_1d_valid.index[i + 1]
                        entry_price = df_1d_valid.iloc[i + 1]["open"]

            if entry_price is not None and entry_ts is not None and entry_ts in time_to_idx_4h:
                entry_price = entry_price * (1 + slippage_pct)
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

                    start_idx = time_to_idx_4h[entry_ts]
                    total_4h_len = len(df_4h_clean)

                    exit_ts = None
                    exit_price = None
                    exit_reason = None
                    exit_idx = total_4h_len - 1
                    is_be = False

                    for idx in range(start_idx, total_4h_len):
                        low_val = lows_4h[idx]
                        close_val = closes_4h[idx]
                        is_macd_bear = macd_cross_4h[idx]

                        # Breakeven Lock (+1.2 ATR)
                        if not is_be and closes_4h[idx] >= (entry_price + 1.2 * atr_val):
                            sl_price = entry_price
                            is_be = True

                        if low_val <= sl_price:
                            exit_ts = df_4h_clean.index[idx]
                            exit_price = sl_price * (1 - slippage_pct)
                            exit_reason = "Breakeven (Riesgo 0)" if is_be else "Stop Loss ATR (4H)"
                            exit_idx = idx
                            break

                        if is_macd_bear and idx > start_idx:
                            exit_ts = df_4h_clean.index[idx]
                            exit_price = close_val * (1 - slippage_pct)
                            exit_reason = "Cruce MACD 4H (Exit)"
                            exit_idx = idx
                            break

                    if exit_price is None:
                        exit_idx = total_4h_len - 1
                        exit_ts = df_4h_clean.index[exit_idx]
                        exit_price = closes_4h[exit_idx]
                        exit_reason = "Fin de Datos"

                    raw_return = (exit_price - entry_price) / entry_price
                    raw_pnl = (exit_price - entry_price) * units
                    fees = (entry_price * units + exit_price * units) * fee_pct
                    net_pnl = raw_pnl - fees
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
                        "capital_after": capital
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
    print(" 📊 BACKTEST DE LA ESTRATEGIA 1D/4H SOBRE LA LISTA BLANCA DE MONEDAS (101 DATASET)")
    print(" Parámetros: Techo Donchian 10D | Cierre 4H | Salida MACD 4H | SL ATR 1.5x + Breakeven")
    print("="*85 + "\n")

    results = []
    
    for sym in WHITELIST_101:
        try:
            data = load_symbol_data(sym)
            res = run_1d4h_backtest_symbol(data, initial_capital=1000.0, donchian_window=10)
            if res:
                results.append({
                    "Symbol": sym,
                    "Capital Inicial ($)": 1000.0,
                    "Capital Final ($)": res["final_capital"],
                    "Ganancia Neta ($)": res["final_capital"] - 1000.0,
                    "Retorno Neto (%)": res["net_profit_pct"],
                    "Win Rate (%)": res["win_rate_pct"],
                    "Profit Factor": res["profit_factor"],
                    "Max Drawdown (%)": res["max_drawdown_pct"],
                    "Trades": res["total_trades"]
                })
        except Exception:
            continue

    df_res = pd.DataFrame(results)
    if df_res.empty:
        print("❌ No se pudieron generar resultados.")
        return

    df_sorted = df_res.sort_values("Retorno Neto (%)", ascending=False).reset_index(drop=True)

    print("┌" + "─"*12 + "┬" + "─"*15 + "┬" + "─"*16 + "┬" + "─"*14 + "┬" + "─"*15 + "┬" + "─"*16 + "┬" + "─"*8 + "┐")
    print(f"│ {'SIMBOLO':<10} │ {'CAPITAL FINAL':<13} │ {'GANANCIA NET ($)':<14} │ {'RETORNO (%)':<12} │ {'WIN RATE (%)':<13} │ {'PROFIT FACTOR':<14} │ {'TRADES':<6} │")
    print("├" + "─"*12 + "┼" + "─"*15 + "┼" + "─"*16 + "┼" + "─"*14 + "┼" + "─"*15 + "┼" + "─"*16 + "┼" + "─"*8 + "┤")

    for idx, row in df_sorted.iterrows():
        print(f"│ {row['Symbol']:<10} │ ${row['Capital Final ($)']:<13.2f} │ ${row['Ganancia Neta ($)']:>+13.2f} │ {row['Retorno Neto (%)']:>+11.2f}% │ {row['Win Rate (%)']:<13.1f}% │ {row['Profit Factor']:<14.2f} │ {row['Trades']:<6} │")

    print("└" + "─"*12 + "┴" + "─"*15 + "┴" + "─"*16 + "┴" + "─"*14 + "┴" + "─"*15 + "┴" + "─"*16 + "┴" + "─"*8 + "┘\n")

    print("=" * 85)
    print(" 📈 RESUMEN PROMEDIO DE LA ESTRATEGIA 1D/4H SOBRE LA LISTA BLANCA:")
    print("=" * 85)
    print(f" • Promedio de Retorno Neto (%) : {df_sorted['Retorno Neto (%)'].mean():>+,.2f}%")
    print(f" • Mediana de Retorno Neto (%)  : {df_sorted['Retorno Neto (%)'].median():>+,.2f}%")
    print(f" • Tasa de Acierto Promedio     : {df_sorted['Win Rate (%)'].mean():.1f}%")
    print(f" • Factor de Beneficio Promedio : {df_sorted['Profit Factor'].mean():.2f}")
    print(f" • Máximo Drawdown Promedio     : {df_sorted['Max Drawdown (%)'].mean():.2f}%")
    print(f" • Monedas Ganadoras (>0%)      : {len(df_sorted[df_sorted['Retorno Neto (%)'] > 0])} / {len(df_sorted)} ({len(df_sorted[df_sorted['Retorno Neto (%)'] > 0])/len(df_sorted)*100:.1f}%)")
    print("=" * 85 + "\n")

if __name__ == "__main__":
    main()
