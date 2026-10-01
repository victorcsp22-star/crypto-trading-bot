import sys
import os
import time
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def compute_indicators(df):
    df = df.copy()
    df['techo_10d'] = df['high'].shift(1).rolling(10).max()
    df['piso_10d'] = df['low'].shift(1).rolling(10).min()
    
    # Volumen
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_sma20']
    
    # MACD (12, 26, 9)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd_line'] = ema12 - ema26
    df['signal_line'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macd_cross_bear'] = (df['macd_line'] < df['signal_line']) & (df['macd_line'].shift(1) >= df['signal_line'].shift(1))
    
    # ATR (14)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    # Régimen
    df['sma_50'] = df['close'].rolling(50, min_periods=20).mean()
    df['sma_200'] = df['close'].rolling(200, min_periods=50).mean()
    df['regime_bullish'] = (df['close'] > df['sma_50']) | (df['sma_50'] > df['sma_200'])
    
    return df

def run_1year_universe_backtest(initial_capital=1000.0, fee_pct=0.001, slippage_pct=0.0005, min_vol_usd=3000000.0):
    okx = OKXPublicClient()
    
    print("\n" + "="*85)
    print(f" 🚀 DESCARGANDO UNIVERSO SPOT DE OKX (VOLUMEN > ${min_vol_usd:,.0f} USD)")
    print("="*85)
    
    tickers = okx.get_usdt_symbols(min_volume_usd=min_vol_usd)
    print(f" • Se encontraron {len(tickers)} pares SPOT en OKX que cumplen con el volumen mínimo.")
    print(" • Descargando 365 días (1 año) de velas diarias para cada activo...")
    
    symbol_dfs = {}
    downloaded_count = 0
    for idx, t in enumerate(tickers):
        inst_id = t["inst_id"]
        sym_name = t["symbol"]
        df_klines = okx.get_1year_daily_klines(inst_id=inst_id, days=365)
        
        if df_klines is not None and len(df_klines) >= 60:
            df_klines = compute_indicators(df_klines)
            df_klines = df_klines.dropna(subset=['techo_10d', 'atr', 'vol_ratio']).copy()
            symbol_dfs[sym_name] = df_klines
            downloaded_count += 1
            if downloaded_count % 10 == 0:
                print(f"   [+] Descargados {downloaded_count}/{len(tickers)} pares...", flush=True)
                
    print(f"\n✅ Descarga completada: {len(symbol_dfs)} criptomonedas listas para el backtest de 1 AÑO.")
    
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    active_positions = {}
    trade_history = []
    
    all_dates = sorted(list(set.union(*[set(df['open_time']) for df in symbol_dfs.values()])))
    print(f" • Rango de simulación: Desde {str(all_dates[0])[:10]} hasta {str(all_dates[-1])[:10]} ({len(all_dates)} días de mercado en vivo).\n")
    
    for date in all_dates:
        # 1. Monitoreo de Posiciones Activas (Salidas por MACD o Stop Loss ATR)
        for sym in list(active_positions.keys()):
            pos = active_positions[sym]
            df = symbol_dfs[sym]
            
            # Buscar la vela del día exacto
            day_matches = df[df['open_time'] == date]
            if day_matches.empty:
                continue
                
            row = day_matches.iloc[0]
            low = row['low']
            close = row['close']
            macd_bear = row['macd_cross_bear']
            
            entry_price = pos['entry_price']
            sl_price = pos['sl_price']
            units = pos['units']
            size_usd = pos['size_usd']
            
            # Condición de Stop Loss Dinámico ATR
            if low <= sl_price:
                exit_price = sl_price * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({
                    "symbol": sym,
                    "entry_date": pos['entry_date'],
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "size_usd": size_usd,
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "capital_after": capital,
                    "reason": "Stop Loss Dinámico ATR"
                })
                del active_positions[sym]
                continue
                
            # Condición de Take Profit por Cruce Bajista MACD
            elif macd_bear:
                exit_price = close * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({
                    "symbol": sym,
                    "entry_date": pos['entry_date'],
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "size_usd": size_usd,
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "capital_after": capital,
                    "reason": "Cruce MACD (Take Profit)"
                })
                del active_positions[sym]
                continue

        # 2. Entrada de Nuevas Posiciones con Interés Compuesto
        for sym, df in symbol_dfs.items():
            if sym in active_positions:
                continue
                
            day_matches = df[df['open_time'] == date]
            if day_matches.empty:
                continue
                
            row = day_matches.iloc[0]
            high = row['high']
            techo = row['techo_10d']
            atr = row['atr']
            vol_ratio = row['vol_ratio']
            regime_ok = row['regime_bullish']
            
            # Entrada: Régimen Alcista + Roce Techo 10D + Volumen Spike >= 1.15x
            if regime_ok and high >= techo and vol_ratio >= 1.15:
                entry_price = max(techo, row['open']) * (1 + slippage_pct)
                sl_price = entry_price - (1.5 * atr)
                if sl_price >= entry_price:
                    sl_price = entry_price * 0.95
                    
                # Tamaño de posición optimizado (20% del capital compuesto total por trade)
                pos_size_usd = min(capital * 0.20, capital)
                
                if pos_size_usd >= 15.0:
                    units = pos_size_usd / entry_price
                    capital -= pos_size_usd
                    active_positions[sym] = {
                        "entry_date": date,
                        "entry_price": entry_price,
                        "sl_price": sl_price,
                        "size_usd": pos_size_usd,
                        "units": units
                    }

        current_equity = capital + sum(p['size_usd'] for p in active_positions.values())
        if current_equity > peak_capital:
            peak_capital = current_equity
        dd = (peak_capital - current_equity) / peak_capital * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd
            
    final_equity = capital + sum(p['size_usd'] for p in active_positions.values())
    return initial_capital, final_equity, trade_history, max_drawdown_pct, len(symbol_dfs)

def main():
    initial_cap = 1000.0
    inic, fin, trades, max_dd, total_symbols = run_1year_universe_backtest(
        initial_capital=initial_cap,
        min_vol_usd=3000000.0 # Filtrar monedas activas con > $3M USD de volumen 24h
    )
    
    net_pnl = fin - inic
    ret_pct = (net_pnl / inic) * 100
    df_trades = pd.DataFrame(trades)
    
    print("=" * 85)
    print(f" 📊 RESULTADO DEL BACKTEST DE 1 AÑO COMPLETO (UNIVERSO OKX SPOT: {total_symbols} MONEDAS)")
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
        
        print(f" • Win Rate (%)            : {win_rate:.1f}% ({len(wins)} Ganadas / {len(losses)} Perdidas)")
        print(f" • Factor de Beneficio (PF): {pf:.2f}")
        print("=" * 85 + "\n")
        
        print(" 🏆 TOP 5 MEJORES OPERACIONES DEL AÑO:")
        df_sorted = df_trades.sort_values("pnl_pct", ascending=False)
        for idx, t in df_sorted.head(5).iterrows():
            d_ent = str(t['entry_date'])[:10]
            d_exit = str(t['exit_date'])[:10]
            print(f"  • {t['symbol']:<10} | Entrada: {d_ent} @ ${t['entry_price']:<8.4f} | Salida: {d_exit} @ ${t['exit_price']:<8.4f} | PnL: ${t['pnl_usd']:>+7.2f} ({t['pnl_pct']:>+6.2f}%)")
            
        print("\n 🔍 ÚLTIMAS 5 OPERACIONES EJECUTADAS:")
        for idx, t in df_trades.tail(5).iterrows():
            d_ent = str(t['entry_date'])[:10]
            d_exit = str(t['exit_date'])[:10]
            print(f"  • {t['symbol']:<10} | Entrada: {d_ent} @ ${t['entry_price']:<8.4f} | Salida: {d_exit} @ ${t['exit_price']:<8.4f} | PnL: ${t['pnl_usd']:>+7.2f} ({t['pnl_pct']:>+6.2f}%)")

if __name__ == "__main__":
    main()
