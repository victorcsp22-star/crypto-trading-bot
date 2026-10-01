import sys
import os
import requests
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

OKX_BASE_URL = "https://www.okx.com"

def get_okx_klines_history(inst_id: str, bar: str = "1D", limit_total: int = 365):
    """
    Descarga datos de OKX para cualquier temporalidad (1D, 4H, 1H) con paginación.
    """
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
                # Fallback
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

def compute_mtf_indicators(df_1d, df_4h, df_1h, donchian_window=14):
    # 1D Indicators
    df_1d = df_1d.copy()
    df_1d['techo_14d'] = df_1d['high'].shift(1).rolling(donchian_window).max()
    df_1d['piso_14d'] = df_1d['low'].shift(1).rolling(donchian_window).min()
    
    high_low = df_1d['high'] - df_1d['low']
    high_close = (df_1d['high'] - df_1d['close'].shift(1)).abs()
    low_close = (df_1d['low'] - df_1d['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df_1d['atr'] = tr.rolling(14).mean()

    # 1H MACD
    df_1h = df_1h.copy()
    ema12 = df_1h['close'].ewm(span=12, adjust=False).mean()
    ema26 = df_1h['close'].ewm(span=26, adjust=False).mean()
    df_1h['macd_line'] = ema12 - ema26
    df_1h['signal_line'] = df_1h['macd_line'].ewm(span=9, adjust=False).mean()
    df_1h['macd_cross_bear'] = (df_1h['macd_line'] < df_1h['signal_line']) & (df_1h['macd_line'].shift(1) >= df_1h['signal_line'].shift(1))

    return df_1d, df_4h, df_1h

def run_mtf_cascade_backtest(symbols, initial_capital=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    print("\n" + "="*85)
    print(" 🚀 BACKTEST EN CASCADA MULTITEMPORAL EXACTO (MTF: 1D -> 1H -> 4H -> 1D)")
    print(" Regla: 1D toca Techo 14D -> Evalúa Cierre 1H -> Si no, 4H -> Si no, 1D -> Salida MACD 1H")
    print("="*85 + "\n")
    
    okx = OKXPublicClient()
    symbol_data = {}
    
    print(" 📥 Descargando datos MTF (1D, 4H, 1H) para el universo de criptomonedas...")
    for sym in symbols:
        inst_id = sym if "-" in sym else f"{sym[:-4]}-USDT"
        sym_name = inst_id.replace("-USDT", "USDT")
        
        df_1d = get_okx_klines_history(inst_id=inst_id, bar="1D", limit_total=365)
        df_4h = get_okx_klines_history(inst_id=inst_id, bar="4H", limit_total=1000)
        df_1h = get_okx_klines_history(inst_id=inst_id, bar="1H", limit_total=2000)
        
        if df_1d is not None and df_4h is not None and df_1h is not None:
            df_1d, df_4h, df_1h = compute_mtf_indicators(df_1d, df_4h, df_1h, donchian_window=14)
            df_1d = df_1d.dropna(subset=['techo_14d', 'atr']).copy()
            symbol_data[sym_name] = {
                "1d": df_1d,
                "4h": df_4h,
                "1h": df_1h
            }
            print(f"  [+] Datos MTF cargados correctamente para {sym_name}")

    if not symbol_data:
        print("❌ No se pudieron cargar los datos MTF.")
        return

    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    active_positions = {}
    trade_history = []
    
    # Evaluar por cada día en df_1d
    sample_df = list(symbol_data.values())[0]["1d"]
    all_days = list(sample_df['open_time'])
    
    for i in range(len(sample_df)):
        day_ts = sample_df.iloc[i]['open_time']
        next_day_ts = day_ts + pd.Timedelta(days=1)
        
        # 1. Chequear Salidas en 1H para posiciones activas
        for sym in list(active_positions.keys()):
            pos = active_positions[sym]
            df_1h = symbol_data[sym]["1h"]
            
            # Sub-dataframe 1H para el día actual
            sub_1h = df_1h[(df_1h['open_time'] >= day_ts) & (df_1h['open_time'] < next_day_ts)]
            if sub_1h.empty:
                continue
                
            entry_price = pos['entry_price']
            sl_price = pos['sl_price']
            units = pos['units']
            size_usd = pos['size_usd']
            
            for idx, bar_1h in sub_1h.iterrows():
                low_val = bar_1h['low']
                close_val = bar_1h['close']
                is_macd_bear = bar_1h['macd_cross_bear']
                time_1h = bar_1h['open_time']
                
                # A) Stop Loss en 1H
                if low_val <= sl_price:
                    exit_price = sl_price * (1 - slippage_pct)
                    raw_pnl = (exit_price - entry_price) * units
                    fees = (entry_price * units + exit_price * units) * fee_pct
                    net_pnl = raw_pnl - fees
                    capital += size_usd + net_pnl
                    
                    trade_history.append({
                        "symbol": sym,
                        "entry_time": pos['entry_time'],
                        "exit_time": time_1h,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "entry_tf": pos['entry_tf'],
                        "size_usd": size_usd,
                        "pnl_usd": net_pnl,
                        "pnl_pct": (net_pnl / size_usd) * 100,
                        "reason": "Stop Loss Dinámico ATR (1H)"
                    })
                    del active_positions[sym]
                    break
                    
                # B) Salida por Cruce MACD Bajista en 1H
                elif is_macd_bear and time_1h > pos['entry_time']:
                    exit_price = close_val * (1 - slippage_pct)
                    raw_pnl = (exit_price - entry_price) * units
                    fees = (entry_price * units + exit_price * units) * fee_pct
                    net_pnl = raw_pnl - fees
                    capital += size_usd + net_pnl
                    
                    trade_history.append({
                        "symbol": sym,
                        "entry_time": pos['entry_time'],
                        "exit_time": time_1h,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "entry_tf": pos['entry_tf'],
                        "size_usd": size_usd,
                        "pnl_usd": net_pnl,
                        "pnl_pct": (net_pnl / size_usd) * 100,
                        "reason": "Cruce MACD Bajista (1H Exit)"
                    })
                    del active_positions[sym]
                    break

        # 2. EVALUAR ENTRADAS CON LA CASCADA MTF (1D -> 1H -> 4H -> 1D)
        for sym, mtf in symbol_data.items():
            if sym in active_positions:
                continue
                
            df_1d = mtf["1d"]
            df_4h = mtf["4h"]
            df_1h = mtf["1h"]
            
            day_matches = df_1d[df_1d['open_time'] == day_ts]
            if day_matches.empty:
                continue
                
            day_row = day_matches.iloc[0]
            high_1d = day_row['high']
            techo_14d = day_row['techo_14d']
            atr_val = day_row['atr']
            piso_14d = day_row['piso_14d']
            
            # PASO OBLIGATORIO 1: La vela diaria (1D) roza o toca el Techo 14D
            if high_1d >= techo_14d:
                entry_tf = None
                entry_ts = None
                entry_price = None
                
                sub_1h = df_1h[(df_1h['open_time'] >= day_ts) & (df_1h['open_time'] < next_day_ts)]
                sub_4h = df_4h[(df_4h['open_time'] >= day_ts) & (df_4h['open_time'] < next_day_ts)]
                
                # ETAPA 1 DE LA CASCADA: ¿Hay cierre por encima del Techo 14D en 1H?
                break_1h = sub_1h[sub_1h['close'] > techo_14d]
                if not break_1h.empty:
                    entry_tf = "1H (Cascada Nivel 1)"
                    first_break_ts = break_1h.iloc[0]['open_time']
                    after_1h = df_1h[df_1h['open_time'] > first_break_ts]
                    if not after_1h.empty:
                        entry_ts = after_1h.iloc[0]['open_time']
                        entry_price = after_1h.iloc[0]['open']
                        
                # ETAPA 2 DE LA CASCADA: Si no cerró en 1H, ¿hay cierre por encima del Techo 14D en 4H?
                if entry_price is None:
                    break_4h = sub_4h[sub_4h['close'] > techo_14d]
                    if not break_4h.empty:
                        entry_tf = "4H (Cascada Nivel 2)"
                        first_break_ts = break_4h.iloc[0]['open_time']
                        after_4h = df_4h[df_4h['open_time'] > first_break_ts]
                        if not after_4h.empty:
                            entry_ts = after_4h.iloc[0]['open_time']
                            entry_price = after_4h.iloc[0]['open']
                            
                # ETAPA 3 DE LA CASCADA: Si no cerró en 4H, ¿hay cierre por encima del Techo 14D en 1D?
                if entry_price is None:
                    if day_row['close'] > techo_14d:
                        entry_tf = "1D (Cascada Nivel 3)"
                        entry_ts = next_day_ts
                        entry_price = day_row['close']

                # Si alguna etapa de la cascada confirmó la rotura, ejecutamos la orden
                if entry_price is not None and entry_ts is not None:
                    entry_price = entry_price * (1 + slippage_pct)
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
                            "entry_tf": entry_tf
                        }

        current_equity = capital + sum(p['size_usd'] for p in active_positions.values())
        if current_equity > peak_capital:
            peak_capital = current_equity
        dd = (peak_capital - current_equity) / peak_capital * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd
            
    final_equity = capital + sum(p['size_usd'] for p in active_positions.values())
    return initial_capital, final_equity, trade_history, max_drawdown_pct

def main():
    whitelist = [
        "DOGE-USDT", "ADA-USDT", "BNB-USDT", "SHIB-USDT", 
        "ETH-USDT", "XRP-USDT", "SOL-USDT", "AVAX-USDT",
        "SUI-USDT", "NEAR-USDT", "PEPE-USDT", "LINK-USDT"
    ]
    
    initial_cap = 1000.0
    inic, fin, trades, max_dd = run_mtf_cascade_backtest(
        symbols=whitelist,
        initial_capital=initial_cap
    )
    
    net_pnl = fin - inic
    ret_pct = (net_pnl / inic) * 100
    df_trades = pd.DataFrame(trades)
    
    print("=" * 85)
    print(" 📊 RESULTADO DEL BACKTEST MTF EN CASCADA (1D -> 1H -> 4H -> 1D)")
    print("=" * 85)
    print(f" • Capital Inicial         : ${inic:,.2f} USD")
    print(f" • Capital Final Acumulado : ${fin:,.2f} USD")
    print(f" • Ganancia Neta Total     : ${net_pnl:>+,.2f} USD ({ret_pct:>+,.2f}%)")
    print(f" • Máximo Drawdown (Riesgo): {max_dd:.2f}%")
    print(f" • Total de Operaciones   : {len(trades)} trades")
    
    if not df_trades.empty:
        wins = df_trades[df_trades['pnl_usd'] > 0]
        losses = df_trades[df_trades['pnl_usd'] <= 0]
        win_rate = (len(wins) / len(df_trades)) * 100
        gross_p = wins['pnl_usd'].sum() if not wins.empty else 0.0
        gross_l = abs(losses['pnl_usd'].sum()) if not losses.empty else 0.0
        pf = (gross_p / gross_l) if gross_l > 0 else 999.0
        
        print(f" • Win Rate (%)            : {win_rate:.1f}% ({len(wins)} W / {len(losses)} L)")
        print(f" • Factor de Beneficio (PF): {pf:.2f}")
        print("-" * 85 + "\n")
        
        print(" 🎯 DESGLOSE DE ENTRADAS POR NIVEL DE CASCADA MTF:")
        print(df_trades['entry_tf'].value_counts().to_string())
        print("-" * 85 + "\n")
        
        print(" 🔍 DETALLE DE LAS ÚLTIMAS 8 OPERACIONES EN CASCADA:")
        for idx, t in df_trades.tail(8).iterrows():
            d_ent = str(t['entry_time'])[:16]
            d_exit = str(t['exit_time'])[:16]
            print(f"  • {t['symbol']:<9} | Nivel: {t['entry_tf']:<20} | Entr: {d_ent} @ ${t['entry_price']:<7.4f} | Sal: {d_exit} @ ${t['exit_price']:<7.4f} | PnL: ${t['pnl_usd']:>+6.2f} ({t['pnl_pct']:>+5.2f}%)")

if __name__ == "__main__":
    main()
