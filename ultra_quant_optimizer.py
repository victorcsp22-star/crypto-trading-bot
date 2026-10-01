import sys
import os
import time
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

FALLBACK_SYMBOLS = [
    "DOGE-USDT", "ADA-USDT", "BNB-USDT", "SHIB-USDT", "ETH-USDT", "XRP-USDT", 
    "SOL-USDT", "AVAX-USDT", "SUI-USDT", "NEAR-USDT", "PEPE-USDT", "LINK-USDT",
    "PUMP-USDT", "ZEC-USDT", "WLD-USDT", "HYPE-USDT", "FIL-USDT", "XLM-USDT"
]

def simulate_strategy_combination(symbol_dfs, config, initial_capital=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    donchian_window = config["donchian_window"]
    vol_min_ratio = config["vol_min_ratio"]
    atr_sl_mult = config["atr_sl_mult"]
    breakeven_atr_mult = config["breakeven_atr_mult"]
    risk_pct = config["risk_pct"]
    max_pos_pct = config["max_pos_pct"]
    
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    active_positions = {}
    trade_history = []
    
    date_sets = [set(df['open_time']) for df in symbol_dfs.values() if not df.empty]
    if not date_sets:
        return None
        
    all_dates = sorted(list(set.union(*date_sets)))
    
    for date in all_dates:
        # 1. Salidas
        for sym in list(active_positions.keys()):
            pos = active_positions[sym]
            df = symbol_dfs[sym]
            day_matches = df[df['open_time'] == date]
            if day_matches.empty:
                continue
                
            row = day_matches.iloc[0]
            high = row['high']
            low = row['low']
            close = row['close']
            atr = row['atr']
            macd_bear = row['macd_cross_bear']
            
            entry_price = pos['entry_price']
            sl_price = pos['sl_price']
            units = pos['units']
            size_usd = pos['size_usd']
            is_be = pos['is_be']
            
            # Breakeven Lock
            if breakeven_atr_mult > 0 and not is_be and high >= (entry_price + breakeven_atr_mult * atr):
                pos['sl_price'] = entry_price
                pos['is_be'] = True
                sl_price = entry_price
                
            if low <= sl_price:
                exit_price = sl_price * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({"pnl_usd": net_pnl, "pnl_pct": (net_pnl / size_usd) * 100})
                del active_positions[sym]
                continue
            elif macd_bear:
                exit_price = close * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({"pnl_usd": net_pnl, "pnl_pct": (net_pnl / size_usd) * 100})
                del active_positions[sym]
                continue

        # 2. Entradas
        for sym, df in symbol_dfs.items():
            if sym in active_positions:
                continue
            day_matches = df[df['open_time'] == date]
            if day_matches.empty:
                continue
                
            row = day_matches.iloc[0]
            high = row['high']
            techo = row[f'techo_{donchian_window}d'] if f'techo_{donchian_window}d' in row else row['high']
            atr = row['atr']
            vol_ratio = row['vol_ratio']
            regime_ok = row['regime_bullish']
            
            if pd.isna(techo) or pd.isna(atr):
                continue
                
            if regime_ok and high >= techo and vol_ratio >= vol_min_ratio:
                entry_price = max(techo, row['open']) * (1 + slippage_pct)
                sl_price = entry_price - (atr_sl_mult * atr)
                if sl_price >= entry_price:
                    sl_price = entry_price * 0.95
                    
                sl_dist_pct = (entry_price - sl_price) / entry_price
                risk_usd = capital * risk_pct
                pos_size_usd = min(risk_usd / sl_dist_pct, capital * max_pos_pct)
                
                if pos_size_usd >= 15.0 and pos_size_usd <= capital:
                    units = pos_size_usd / entry_price
                    capital -= pos_size_usd
                    active_positions[sym] = {
                        "entry_price": entry_price,
                        "sl_price": sl_price,
                        "size_usd": pos_size_usd,
                        "units": units,
                        "is_be": False
                    }

        current_equity = capital + sum(p['size_usd'] for p in active_positions.values())
        if current_equity > peak_capital:
            peak_capital = current_equity
        dd = (peak_capital - current_equity) / peak_capital * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd
            
    final_equity = capital + sum(p['size_usd'] for p in active_positions.values())
    net_pnl = final_equity - initial_capital
    ret_pct = (net_pnl / initial_capital) * 100
    
    df_tr = pd.DataFrame(trade_history)
    if not df_tr.empty:
        wins = df_tr[df_tr['pnl_usd'] > 0]
        losses = df_tr[df_tr['pnl_usd'] <= 0]
        win_rate = (len(wins) / len(df_tr)) * 100
        gross_p = wins['pnl_usd'].sum() if not wins.empty else 0.0
        gross_l = abs(losses['pnl_usd'].sum()) if not losses.empty else 0.0
        pf = (gross_p / gross_l) if gross_l > 0 else 999.0
    else:
        win_rate = 0.0
        pf = 0.0
        
    calmar_ratio = (ret_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0
    
    return {
        "config": config,
        "final_capital": final_equity,
        "net_pnl": net_pnl,
        "ret_pct": ret_pct,
        "max_dd_pct": max_drawdown_pct,
        "total_trades": len(trade_history),
        "win_rate": win_rate,
        "profit_factor": pf,
        "calmar_ratio": calmar_ratio
    }

def main():
    print("\n" + "="*85)
    print(" ⚡ MOTOR HIPERPARAMÉTRICO DE OPTIMIZACIÓN MATEMÁTICA Y MAXIMIZACIÓN TEÓRICA")
    print(" Grid-Search de Batería de Bots sobre el Universo SPOT de OKX (1 AÑO COMPLETO)")
    print("="*85 + "\n")
    
    okx = OKXPublicClient()
    tickers = []
    
    # Intentar obtener tickers dinámicos con reintentos
    for _ in range(3):
        tickers = okx.get_usdt_symbols(min_volume_usd=3000000.0)
        if tickers:
            break
        time.sleep(2)
        
    if not tickers:
        print("⚠️ Usando lista de respaldo de símbolos OKX de alto volumen...")
        tickers = [{"inst_id": s, "symbol": s.replace("-USDT", "USDT")} for s in FALLBACK_SYMBOLS]
        
    print(f" 📥 Descargando 365 días (1 año) de velas para {len(tickers)} activos SPOT de OKX...")
    raw_dfs = {}
    for t in tickers:
        inst_id = t["inst_id"]
        sym_name = t["symbol"]
        df_klines = okx.get_1year_daily_klines(inst_id=inst_id, days=365)
        if df_klines is not None and len(df_klines) >= 60:
            raw_dfs[sym_name] = df_klines

    if not raw_dfs:
        print("❌ No se pudieron descargar datos de OKX.")
        return

    print(f"✅ Datos descargados ({len(raw_dfs)} activos). Precalculando Techos Donchian (8, 10, 14)...")
    
    symbol_dfs_by_dw = {}
    for dw in [8, 10, 14]:
        symbol_dfs_by_dw[dw] = {}
        for sym, df_raw in raw_dfs.items():
            df_ind = df_raw.copy()
            df_ind[f'techo_{dw}d'] = df_ind['high'].shift(1).rolling(dw).max()
            df_ind[f'piso_{dw}d'] = df_ind['low'].shift(1).rolling(dw).min()
            
            df_ind['vol_sma20'] = df_ind['volume'].rolling(20).mean()
            df_ind['vol_ratio'] = df_ind['volume'] / df_ind['vol_sma20']
            
            high_low = df_ind['high'] - df_ind['low']
            high_close = (df_ind['high'] - df_ind['close'].shift(1)).abs()
            low_close = (df_ind['low'] - df_ind['close'].shift(1)).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df_ind['atr'] = tr.rolling(14).mean()
            
            ema12 = df_ind['close'].ewm(span=12, adjust=False).mean()
            ema26 = df_ind['close'].ewm(span=26, adjust=False).mean()
            df_ind['macd_line'] = ema12 - ema26
            df_ind['signal_line'] = df_ind['macd_line'].ewm(span=9, adjust=False).mean()
            df_ind['macd_cross_bear'] = (df_ind['macd_line'] < df_ind['signal_line']) & (df_ind['macd_line'].shift(1) >= df_ind['signal_line'].shift(1))
            
            df_ind['sma_50'] = df_ind['close'].rolling(50, min_periods=20).mean()
            df_ind['sma_200'] = df_ind['close'].rolling(200, min_periods=50).mean()
            df_ind['regime_bullish'] = (df_ind['close'] > df_ind['sma_50']) | (df_ind['sma_50'] > df_ind['sma_200'])
            
            df_clean = df_ind.dropna(subset=[f'techo_{dw}d', 'atr', 'vol_ratio']).copy()
            symbol_dfs_by_dw[dw][sym] = df_clean

    param_grid = []
    for dw in [8, 10, 14]:
        for vr in [1.10, 1.25, 1.40]:
            for atr_sl in [1.2, 1.5, 2.0]:
                for be_atr in [1.2, 1.5, 2.0]:
                    for r_pct in [0.02, 0.03]:
                        for max_pos in [0.25, 0.35]:
                            param_grid.append({
                                "donchian_window": dw,
                                "vol_min_ratio": vr,
                                "atr_sl_mult": atr_sl,
                                "breakeven_atr_mult": be_atr,
                                "risk_pct": r_pct,
                                "max_pos_pct": max_pos
                            })
                            
    print(f" 🧪 Evaluando {len(param_grid)} combinaciones matemáticas en paralelo...\n")
    
    results = []
    for idx, cfg in enumerate(param_grid):
        dw = cfg["donchian_window"]
        sym_dfs = symbol_dfs_by_dw[dw]
        res = simulate_strategy_combination(sym_dfs, cfg, initial_capital=1000.0)
        if res:
            results.append(res)
            
    df_results = pd.DataFrame(results)
    if df_results.empty:
        print("❌ No se generaron resultados en la simulación.")
        return

    df_sorted_calmar = df_results.sort_values("calmar_ratio", ascending=False)
    df_sorted_ret = df_results.sort_values("ret_pct", ascending=False)
    
    top_opt = df_sorted_calmar.iloc[0]
    top_ret = df_sorted_ret.iloc[0]
    
    print("\n" + "="*85)
    print(" 🏆 CONFIGURACIÓN DE MAXIMIZACIÓN MATEMÁTICA (MÁXIMO RATIO CALMAR - MENOR RIESGO):")
    print("="*85)
    cfg_opt = top_opt['config']
    print(f" • Ventana Donchian Techo   : {cfg_opt['donchian_window']} Días")
    print(f" • Filtro Mínimo de Volumen : {cfg_opt['vol_min_ratio']}x Promedio 20D")
    print(f" • Stop Loss Multiplicador  : {cfg_opt['atr_sl_mult']}x ATR")
    print(f" • Candado Breakeven Lock   : {cfg_opt['breakeven_atr_mult']}x ATR")
    print(f" • Riesgo por Trade (Kelly) : {cfg_opt['risk_pct']*100}% de la Cartera")
    print(f" • Asignación Máxima Pos.   : {cfg_opt['max_pos_pct']*100}% de la Cartera")
    print("-" * 85)
    print(f" 💰 Capital Inicial         : $1,000.00 USD")
    print(f" 💰 Capital Final Optimizado: ${top_opt['final_capital']:,.2f} USD")
    print(f" 📈 Retorno Neto Anual      : +${top_opt['net_pnl']:,.2f} USD (+{top_opt['ret_pct']:.2f}%)")
    print(f" 🛡️ Máximo Drawdown (Riesgo): {top_opt['max_dd_pct']:.2f}%")
    print(f" ⚖️ Ratio de Calmar (Ret/DD): {top_opt['calmar_ratio']:.2f}")
    print(f" 📊 Profit Factor           : {top_opt['profit_factor']:.2f}")
    print(f" 🎯 Win Rate                : {top_opt['win_rate']:.1f}% ({top_opt['total_trades']} trades)")
    print("="*85 + "\n")
    
    print("="*85)
    print(" 🚀 CONFIGURACIÓN DE MÁXIMA RENTABILIDAD ABSOLUTA:")
    print("="*85)
    cfg_r = top_ret['config']
    print(f" • Ventana Donchian Techo   : {cfg_r['donchian_window']} Días")
    print(f" • Filtro Mínimo de Volumen : {cfg_r['vol_min_ratio']}x Promedio 20D")
    print(f" • Stop Loss Multiplicador  : {cfg_r['atr_sl_mult']}x ATR")
    print(f" • Candado Breakeven Lock   : {cfg_r['breakeven_atr_mult']}x ATR")
    print(f" • Riesgo por Trade (Kelly) : {cfg_r['risk_pct']*100}% de la Cartera")
    print(f" • Asignación Máxima Pos.   : {cfg_r['max_pos_pct']*100}% de la Cartera")
    print("-" * 85)
    print(f" 💰 Capital Final Máximo    : ${top_ret['final_capital']:,.2f} USD")
    print(f" 📈 Retorno Neto Anual      : +${top_ret['net_pnl']:,.2f} USD (+{top_ret['ret_pct']:.2f}%)")
    print(f" 🛡️ Máximo Drawdown (Riesgo): {top_ret['max_dd_pct']:.2f}%")
    print("="*85 + "\n")

if __name__ == "__main__":
    main()
