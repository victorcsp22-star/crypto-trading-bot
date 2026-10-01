import sys
import os
import requests
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

OKX_BASE_URL = "https://www.okx.com"

def get_okx_intraday_klines(inst_id: str, bar: str = "15m", limit_total: int = 1000):
    all_candles = []
    after_ts = ""
    url = f"{OKX_BASE_URL}/api/v5/market/history-candles"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    while len(all_candles) < limit_total:
        params = {"instId": inst_id, "bar": bar, "limit": "100"}
        if after_ts:
            params["after"] = after_ts
            
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            data = resp.json()
            if data.get("code") != "0" or not data.get("data"):
                url_alt = f"{OKX_BASE_URL}/api/v5/market/candles"
                resp = requests.get(url_alt, params=params, headers=headers, timeout=10)
                data = resp.json()
                if data.get("code") != "0" or not data.get("data"):
                    break
                    
            candles = data.get("data", [])
            if not candles:
                break
            all_candles.extend(candles)
            after_ts = candles[-1][0]
            if len(candles) < 100:
                break
        except Exception:
            break
            
    if not all_candles:
        return None
        
    records = []
    for c in reversed(all_candles):
        records.append({
            "open_time": pd.to_datetime(int(c[0]), unit="ms", utc=True),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5])
        })
        
    df = pd.DataFrame(records)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return df.tail(limit_total).reset_index(drop=True)

def compute_minor_fractal_indicators(df_macro, df_inter, df_trigger, donchian_periods=10):
    # Indicadores Macro
    df_macro = df_macro.copy()
    df_macro['techo_donchian'] = df_macro['high'].shift(1).rolling(donchian_periods).max()
    df_macro['piso_donchian'] = df_macro['low'].shift(1).rolling(donchian_periods).min()
    
    high_low = df_macro['high'] - df_macro['low']
    high_close = (df_macro['high'] - df_macro['close'].shift(1)).abs()
    low_close = (df_macro['low'] - df_macro['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df_macro['atr'] = tr.rolling(14).mean()

    # Indicadores del Gatillo (15M / 5M)
    df_trigger = df_trigger.copy()
    ema12 = df_trigger['close'].ewm(span=12, adjust=False).mean()
    ema26 = df_trigger['close'].ewm(span=26, adjust=False).mean()
    df_trigger['macd_line'] = ema12 - ema26
    df_trigger['signal_line'] = df_trigger['macd_line'].ewm(span=9, adjust=False).mean()
    df_trigger['macd_cross_bear'] = (df_trigger['macd_line'] < df_trigger['signal_line']) & (df_trigger['macd_line'].shift(1) >= df_trigger['signal_line'].shift(1))
    
    df_trigger['vol_sma20'] = df_trigger['volume'].rolling(20).mean()
    df_trigger['vol_ratio'] = df_trigger['volume'] / df_trigger['vol_sma20']

    return df_macro, df_inter, df_trigger

def run_minor_fractal_simulation(symbol_data, macro_tf_name="4H", trigger_tf_name="15M", initial_capital=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    active_positions = {}
    trade_history = []
    
    sample_df = list(symbol_data.values())[0]["macro"]
    all_macro_bars = list(sample_df['open_time'])
    
    for i in range(len(sample_df)):
        macro_ts = sample_df.iloc[i]['open_time']
        
        # Calcular siguiente barra macro
        if macro_tf_name == "4H":
            next_macro_ts = macro_ts + pd.Timedelta(hours=4)
        elif macro_tf_name == "1H":
            next_macro_ts = macro_ts + pd.Timedelta(hours=1)
        else:
            next_macro_ts = macro_ts + pd.Timedelta(hours=4)

        # 1. Monitorear Salidas en la temporalidad del Gatillo (15M o 5M)
        for sym in list(active_positions.keys()):
            pos = active_positions[sym]
            df_trigger = symbol_data[sym]["trigger"]
            
            sub_trigger = df_trigger[(df_trigger['open_time'] >= macro_ts) & (df_trigger['open_time'] < next_macro_ts)]
            if sub_trigger.empty:
                continue
                
            entry_price = pos['entry_price']
            sl_price = pos['sl_price']
            units = pos['units']
            size_usd = pos['size_usd']
            is_be = pos['is_be']
            
            for idx, bar_trig in sub_trigger.iterrows():
                low_val = bar_trig['low']
                high_val = bar_trig['high']
                close_val = bar_trig['close']
                is_macd_bear = bar_trig['macd_cross_bear']
                time_trig = bar_trig['open_time']
                
                # Breakeven Lock (+1.2 ATR)
                if not is_be and high_val >= (entry_price + 1.2 * pos['atr_val']):
                    pos['sl_price'] = entry_price
                    pos['is_be'] = True
                    sl_price = entry_price

                if low_val <= sl_price:
                    exit_price = sl_price * (1 - slippage_pct)
                    raw_pnl = (exit_price - entry_price) * units
                    fees = (entry_price * units + exit_price * units) * fee_pct
                    net_pnl = raw_pnl - fees
                    capital += size_usd + net_pnl
                    
                    reason = "Breakeven Lock (Riesgo 0)" if is_be else f"Stop Loss ATR ({trigger_tf_name})"
                    trade_history.append({
                        "symbol": sym,
                        "entry_time": pos['entry_time'],
                        "exit_time": time_trig,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "size_usd": size_usd,
                        "pnl_usd": net_pnl,
                        "pnl_pct": (net_pnl / size_usd) * 100,
                        "reason": reason
                    })
                    del active_positions[sym]
                    break
                    
                elif is_macd_bear and time_trig > pos['entry_time']:
                    exit_price = close_val * (1 - slippage_pct)
                    raw_pnl = (exit_price - entry_price) * units
                    fees = (entry_price * units + exit_price * units) * fee_pct
                    net_pnl = raw_pnl - fees
                    capital += size_usd + net_pnl
                    
                    trade_history.append({
                        "symbol": sym,
                        "entry_time": pos['entry_time'],
                        "exit_time": time_trig,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "size_usd": size_usd,
                        "pnl_usd": net_pnl,
                        "pnl_pct": (net_pnl / size_usd) * 100,
                        "reason": f"Cruce MACD ({trigger_tf_name} Exit)"
                    })
                    del active_positions[sym]
                    break

        # 2. EVALUAR ENTRADAS FRACTALES (Macro Techo Donchian -> Gatillo Menor)
        for sym, mtf in symbol_data.items():
            if sym in active_positions:
                continue
                
            df_macro = mtf["macro"]
            df_inter = mtf["inter"]
            df_trigger = mtf["trigger"]
            
            macro_matches = df_macro[df_macro['open_time'] == macro_ts]
            if macro_matches.empty:
                continue
                
            macro_row = macro_matches.iloc[0]
            high_macro = macro_row['high']
            techo_macro = macro_row['techo_donchian']
            atr_val = macro_row['atr']
            
            if pd.isna(techo_macro) or pd.isna(atr_val):
                continue
                
            # Roce del Techo Donchian en el gráfico Macro
            if high_macro >= techo_macro:
                sub_trigger = df_trigger[(df_trigger['open_time'] >= macro_ts) & (df_trigger['open_time'] < next_macro_ts)]
                if sub_trigger.empty:
                    continue
                    
                # Evaluar cierre por encima del Techo Macro en el gráfico del Gatillo + Filtro Volumen
                break_trig = sub_trigger[(sub_trigger['close'] > techo_macro) & (sub_trigger['vol_ratio'] >= 1.15)]
                if not break_trig.empty:
                    first_break = break_trig.iloc[0]
                    first_break_ts = first_break['open_time']
                    after_trig = df_trigger[df_trigger['open_time'] > first_break_ts]
                    if not after_trig.empty:
                        entry_ts = after_trig.iloc[0]['open_time']
                        entry_price = after_trig.iloc[0]['open'] * (1 + slippage_pct)
                        sl_price = entry_price - (1.5 * atr_val)
                        if sl_price >= entry_price:
                            sl_price = entry_price * 0.95
                            
                        pos_size_usd = min(capital * 0.20, capital)
                        if pos_size_usd >= 15.0:
                            units = pos_size_usd / entry_price
                            capital -= pos_size_usd
                            active_positions[sym] = {
                                "entry_time": entry_ts,
                                "entry_price": entry_price,
                                "sl_price": sl_price,
                                "size_usd": pos_size_usd,
                                "units": units,
                                "atr_val": atr_val,
                                "is_be": False
                            }

        current_equity = capital + sum(p['size_usd'] for p in active_positions.values())
        if current_equity > peak_capital:
            peak_capital = current_equity
        dd = (peak_capital - current_equity) / peak_capital * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd
            
    final_equity = capital + sum(p['size_usd'] for p in active_positions.values())
    
    df_tr = pd.DataFrame(trade_history)
    if df_tr.empty:
        return None
        
    start_date = df_tr['entry_time'].min()
    end_date = df_tr['exit_time'].max()
    total_days = (end_date - start_date).days if (end_date - start_date).days > 0 else 1
    
    wins = df_tr[df_tr['pnl_usd'] > 0]
    losses = df_tr[df_tr['pnl_usd'] <= 0]
    win_rate = (len(wins) / len(df_tr)) * 100
    gross_p = wins['pnl_usd'].sum() if not wins.empty else 0.0
    gross_l = abs(losses['pnl_usd'].sum()) if not losses.empty else 0.0
    pf = (gross_p / gross_l) if gross_l > 0 else 999.0
    
    daily_growth_rate = (final_equity / initial_capital) ** (1.0 / total_days) - 1.0
    projected_30d_capital = initial_capital * ((1.0 + daily_growth_rate) ** 30)
    projected_30d_return_pct = ((projected_30d_capital - initial_capital) / initial_capital) * 100

    return {
        "fractal_name": f"Macro {macro_tf_name} -> Gatillo {trigger_tf_name}",
        "total_days": total_days,
        "initial_capital": initial_capital,
        "final_capital": final_equity,
        "net_pnl": final_equity - initial_capital,
        "ret_pct": ((final_equity - initial_capital) / initial_capital) * 100,
        "max_dd_pct": max_drawdown_pct,
        "total_trades": len(df_tr),
        "trades_per_month": (len(df_tr) / total_days) * 30,
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "projected_30d_capital": projected_30d_capital,
        "projected_30d_return_pct": projected_30d_return_pct
    }

def main():
    print("\n" + "="*85)
    print(" 🔬 EXPERIMENTO FRACTAL MENOR (SCALPING INTRADÍA ACELERADO)")
    print(" Evaluación de Fractales Menores Equivalentes a 1D/4H -> Gatillo 1H:")
    print(" • FRACTAL MENOR 1: Filtro Macro 4H -> Intermedio 1H -> Gatillo 15M")
    print(" • FRACTAL MENOR 2: Filtro Macro 1H -> Intermedio 15M -> Gatillo 5M")
    print("="*85 + "\n")
    
    okx = OKXPublicClient()
    symbols = ["DOGE-USDT", "ADA-USDT", "SOL-USDT", "BTC-USDT", "ETH-USDT", "XRP-USDT", "BNB-USDT", "SUI-USDT"]
    
    print(" 📥 Descargando klines intradía (4H, 1H, 15M, 5M) para el experimento fractal...")
    
    # FRACTAL 1: 4H -> 1H -> 15M
    data_f1 = {}
    for sym in symbols:
        inst_id = sym
        sym_name = sym.replace("-USDT", "USDT")
        df_4h = get_okx_intraday_klines(inst_id, bar="4H", limit_total=500)
        df_1h = get_okx_intraday_klines(inst_id, bar="1H", limit_total=1000)
        df_15m = get_okx_intraday_klines(inst_id, bar="15m", limit_total=2000)
        
        if df_4h is not None and df_1h is not None and df_15m is not None:
            df_4h, df_1h, df_15m = compute_minor_fractal_indicators(df_4h, df_1h, df_15m, donchian_periods=10)
            data_f1[sym_name] = {"macro": df_4h.dropna(subset=['techo_donchian', 'atr']).copy(), "inter": df_1h, "trigger": df_15m.dropna(subset=['vol_ratio']).copy()}
            
    res_f1 = run_minor_fractal_simulation(data_f1, macro_tf_name="4H", trigger_tf_name="15M")
    
    # FRACTAL 2: 1H -> 15M -> 5M
    data_f2 = {}
    for sym in symbols:
        inst_id = sym
        sym_name = sym.replace("-USDT", "USDT")
        df_1h = get_okx_intraday_klines(inst_id, bar="1H", limit_total=800)
        df_15m = get_okx_intraday_klines(inst_id, bar="15m", limit_total=1500)
        df_5m = get_okx_intraday_klines(inst_id, bar="5m", limit_total=2000)
        
        if df_1h is not None and df_15m is not None and df_5m is not None:
            df_1h, df_15m, df_5m = compute_minor_fractal_indicators(df_1h, df_15m, df_5m, donchian_periods=10)
            data_f2[sym_name] = {"macro": df_1h.dropna(subset=['techo_donchian', 'atr']).copy(), "inter": df_15m, "trigger": df_5m.dropna(subset=['vol_ratio']).copy()}
            
    res_f2 = run_minor_fractal_simulation(data_f2, macro_tf_name="1H", trigger_tf_name="5M")

    print("\n" + "┌" + "─"*38 + "┬" + "─"*22 + "┬" + "─"*22 + "┐")
    print(f"│ {'MÉTRICA DE FRACTAL MENOR':<36} │ {'FRACTAL 1 (4H -> 15M)':<20} │ {'FRACTAL 2 (1H -> 5M)':<20} │")
    print("├" + "─"*38 + "┼" + "─"*22 + "┼" + "─"*22 + "┤")
    
    if res_f1 and res_f2:
        print(f"│ Capital Inicial                       │ ${res_f1['initial_capital']:<19.2f} │ ${res_f2['initial_capital']:<19.2f} │")
        print(f"│ Días Reales Evaluados                 │ {res_f1['total_days']:<20} │ {res_f2['total_days']:<20} │")
        print(f"│ Total Trades Ejecutados              │ {res_f1['total_trades']:<20} │ {res_f2['total_trades']:<20} │")
        print(f"│ Operaciones Promedio por Mes (30D)    │ {res_f1['trades_per_month']:<20.1f} │ {res_f2['trades_per_month']:<20.1f} │")
        print(f"│ Tasa de Acierto (Win Rate %)          │ {res_f1['win_rate_pct']:<19.1f}% │ {res_f2['win_rate_pct']:<19.1f}% │")
        print(f"│ Factor de Beneficio (Profit Factor)   │ {res_f1['profit_factor']:<20.2f} │ {res_f2['profit_factor']:<20.2f} │")
        print(f"│ Máximo Drawdown (Riesgo Máx. %)       │ {res_f1['max_dd_pct']:<19.2f}% │ {res_f2['max_dd_pct']:<19.2f}% │")
        print("├" + "─"*38 + "┼" + "─"*22 + "┼" + "─"*22 + "┤")
        print(f"│ Rendimiento del Periodo (%)          │ {res_f1['ret_pct']:>+19.2f}% │ {res_f2['ret_pct']:>+19.2f}% │")
        print(f"│ 🚀 RETORNO PROYECTADO A 1 MES (30D)   │ {res_f1['projected_30d_return_pct']:>+19.2f}% │ {res_f2['projected_30d_return_pct']:>+19.2f}% │")
        print(f"│ 💰 CAPITAL PROYECTADO A 1 MES (30D)   │ ${res_f1['projected_30d_capital']:<19.2f} │ ${res_f2['projected_30d_capital']:<19.2f} │")
        print("└" + "─"*38 + "┴" + "─"*22 + "┴" + "─"*22 + "┘\n")

if __name__ == "__main__":
    main()
