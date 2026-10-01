import os
import glob
import sys
import pandas as pd
import numpy as np
from data_loader import load_symbol_data

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Top 15 Criptomonedas Ganadoras Validadas en la Whitelist
WHITELIST = [
    "DOGEUSDT", "ADAUSDT", "SHIBUSDT", "BNBUSDT", "FILUSDT", 
    "XLMUSDT", "ZECUSDT", "XRPUSDT", "ETHUSDT", "MKRUSDT", 
    "ORDIUSDT", "AVAXUSDT", "WLDUSDT", "SOLUSDT", "FLOKIUSDT"
]

def compute_timeframe_indicators(df, donchian_window=14):
    df = df.copy()
    df['techo_donchian'] = df['high'].shift(1).rolling(donchian_window).max()
    df['piso_donchian'] = df['low'].shift(1).rolling(donchian_window).min()
    
    # Volumen
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_sma20']
    
    # ATR (14)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    # MACD (12, 26, 9)
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd_line'] = ema12 - ema26
    df['signal_line'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macd_cross_bear'] = (df['macd_line'] < df['signal_line']) & (df['macd_line'].shift(1) >= df['signal_line'].shift(1))
    
    # Tendencia
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['regime_ok'] = df['close'] > df['ema_50']
    
    return df

def run_fractal_timeframe_backtest(symbols, tf="1h", initial_capital=1000.0, donchian_window=14, fee_pct=0.001, slippage_pct=0.0005):
    """
    Ejecuta el backtest en la temporalidad especificada (1D, 4H, o 1H) sobre las monedas de la Whitelist.
    """
    symbol_dfs = {}
    for sym in symbols:
        try:
            data = load_symbol_data(sym)
            if tf in data:
                df = data[tf]
                if len(df) >= 100:
                    df = compute_timeframe_indicators(df, donchian_window=donchian_window)
                    df = df.dropna(subset=['techo_donchian', 'atr', 'vol_ratio']).copy()
                    symbol_dfs[sym] = df
        except Exception:
            continue

    if not symbol_dfs:
        return None

    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    active_positions = {}
    trade_history = []
    
    # Fechas ordenadas
    all_dates = sorted(list(set.union(*[set(df.index) for df in symbol_dfs.values()])))
    
    for date in all_dates:
        # 1. Chequear Salidas
        for sym in list(active_positions.keys()):
            pos = active_positions[sym]
            df = symbol_dfs[sym]
            if date not in df.index:
                continue
                
            row = df.loc[date]
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
            
            # Breakeven Lock (+1.2 ATR)
            if not is_be and high >= (entry_price + 1.2 * atr):
                pos['sl_price'] = entry_price
                pos['is_be'] = True
                sl_price = entry_price
                
            if low <= sl_price:
                exit_price = sl_price * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                reason = "Breakeven (Riesgo 0)" if is_be else "Stop Loss ATR"
                trade_history.append({"symbol": sym, "pnl_usd": net_pnl, "pnl_pct": (net_pnl / size_usd) * 100, "reason": reason, "date": date})
                del active_positions[sym]
                continue
            elif macd_bear:
                exit_price = close * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({"symbol": sym, "pnl_usd": net_pnl, "pnl_pct": (net_pnl / size_usd) * 100, "reason": "Cruce MACD", "date": date})
                del active_positions[sym]
                continue

        # 2. Chequear Entradas
        for sym, df in symbol_dfs.items():
            if sym in active_positions or date not in df.index:
                continue
                
            row = df.loc[date]
            high = row['high']
            techo = row['techo_donchian']
            atr = row['atr']
            vol_ratio = row['vol_ratio']
            regime_ok = row['regime_ok']
            
            # Entrada: Impulso + Filtro de Volumen (>= 1.20x) + Tendencia EMA50
            if regime_ok and high >= techo and vol_ratio >= 1.20:
                entry_price = max(techo, row['open']) * (1 + slippage_pct)
                sl_price = entry_price - (1.5 * atr)
                if sl_price >= entry_price:
                    sl_price = entry_price * 0.95
                    
                sl_dist_pct = (entry_price - sl_price) / entry_price
                risk_usd = capital * 0.02 # 2% del capital de la cartera
                pos_size_usd = min(risk_usd / sl_dist_pct, capital * 0.25)
                
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
    
    # Calcular métricas a 30 Días (Simulación Mensual)
    df_tr = pd.DataFrame(trade_history)
    if df_tr.empty:
        return None
        
    start_date = df_tr['date'].min()
    end_date = df_tr['date'].max()
    total_days = (end_date - start_date).days if (end_date - start_date).days > 0 else 1
    
    # Tasa de crecimiento diario promedio (CAGR diario)
    daily_growth_rate = (final_equity / initial_capital) ** (1.0 / total_days) - 1.0
    # Proyección a 30 Días (1 Mes)
    projected_30d_capital = initial_capital * ((1.0 + daily_growth_rate) ** 30)
    projected_30d_return_pct = ((projected_30d_capital - initial_capital) / initial_capital) * 100
    
    wins = df_tr[df_tr['pnl_usd'] > 0]
    losses = df_tr[df_tr['pnl_usd'] <= 0]
    win_rate = (len(wins) / len(df_tr)) * 100
    gross_p = wins['pnl_usd'].sum() if not wins.empty else 0.0
    gross_l = abs(losses['pnl_usd'].sum()) if not losses.empty else 0.0
    pf = (gross_p / gross_l) if gross_l > 0 else 999.0

    return {
        "timeframe": tf,
        "total_days_data": total_days,
        "initial_capital": initial_capital,
        "final_capital": final_equity,
        "total_net_pnl": final_equity - initial_capital,
        "total_ret_pct": ((final_equity - initial_capital) / initial_capital) * 100,
        "max_drawdown_pct": max_drawdown_pct,
        "total_trades": len(df_tr),
        "trades_per_month": (len(df_tr) / total_days) * 30,
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "projected_30d_capital": projected_30d_capital,
        "projected_30d_return_pct": projected_30d_return_pct
    }

def main():
    print("\n" + "="*85)
    print(" 🚀 SIMULACIÓN DE ESCALAMIENTO FRACTAL Y ACELERACIÓN DE MERCADO")
    print(" Comparación de Temporalidades: 1D (Diario) vs. 4H (4 Horas) vs. 1H (1 Hora)")
    print(" Objetivo: Proyectar el Rendimiento Anual Acelerado a un Periodo de 30 Días (1 Mes)")
    print("="*85 + "\n")
    
    timeframes = ["1d", "4h", "1h"]
    results = []
    
    for tf in timeframes:
        print(f" ⏳ Procesando Simulación Fractal en Temporalidad: [{tf.upper()}]...", flush=True)
        res = run_fractal_timeframe_backtest(WHITELIST, tf=tf, initial_capital=1000.0)
        if res:
            results.append(res)
            
    if not results:
        print("❌ No se generaron resultados.")
        return
        
    print("\n" + "┌" + "─"*38 + "┬" + "─"*18 + "┬" + "─"*18 + "┬" + "─"*18 + "┐")
    print(f"│ {'MÉTRICA DE ESCALAMIENTO FRACTAL':<36} │ {'GRÁFICO 1D (DIARIO)':<16} │ {'GRÁFICO 4H (4 HORAS)':<16} │ {'GRÁFICO 1H (1 HORA)':<16} │")
    print("├" + "─"*38 + "┼" + "─"*18 + "┼" + "─"*18 + "┼" + "─"*18 + "┤")
    
    res_1d = next(r for r in results if r['timeframe'] == '1d')
    res_4h = next(r for r in results if r['timeframe'] == '4h')
    res_1h = next(r for r in results if r['timeframe'] == '1h')
    
    print(f"│ Capital Inicial                       │ ${res_1d['initial_capital']:<15.2f} │ ${res_4h['initial_capital']:<15.2f} │ ${res_1h['initial_capital']:<15.2f} │")
    print(f"│ Total Trades Ejecutados              │ {res_1d['total_trades']:<16} │ {res_4h['total_trades']:<16} │ {res_1h['total_trades']:<16} │")
    print(f"│ Operaciones Promedio por Mes (30D)    │ {res_1d['trades_per_month']:<16.1f} │ {res_4h['trades_per_month']:<16.1f} │ {res_1h['trades_per_month']:<16.1f} │")
    print(f"│ Tasa de Acierto (Win Rate %)          │ {res_1d['win_rate_pct']:<15.1f}% │ {res_4h['win_rate_pct']:<15.1f}% │ {res_1h['win_rate_pct']:<15.1f}% │")
    print(f"│ Factor de Beneficio (Profit Factor)   │ {res_1d['profit_factor']:<16.2f} │ {res_4h['profit_factor']:<16.2f} │ {res_1h['profit_factor']:<16.2f} │")
    print(f"│ Máximo Drawdown (Riesgo Máx. %)       │ {res_1d['max_drawdown_pct']:<15.2f}% │ {res_4h['max_drawdown_pct']:<15.2f}% │ {res_1h['max_drawdown_pct']:<15.2f}% │")
    print("├" + "─"*38 + "┼" + "─"*18 + "┼" + "─"*18 + "┼" + "─"*18 + "┤")
    print(f"│ Rendimiento Total del Dataset (%)     │ {res_1d['total_ret_pct']:>+15.2f}% │ {res_4h['total_ret_pct']:>+15.2f}% │ {res_1h['total_ret_pct']:>+15.2f}% │")
    print(f"│ 🚀 RETORNO PROYECTADO A 1 MES (30D)   │ {res_1d['projected_30d_return_pct']:>+15.2f}% │ {res_4h['projected_30d_return_pct']:>+15.2f}% │ {res_1h['projected_30d_return_pct']:>+15.2f}% │")
    print(f"│ 💰 CAPITAL PROYECTADO A 1 MES (30D)   │ ${res_1d['projected_30d_capital']:<15.2f} │ ${res_4h['projected_30d_capital']:<15.2f} │ ${res_1h['projected_30d_capital']:<15.2f} │")
    print("└" + "─"*38 + "┴" + "─"*18 + "┴" + "─"*18 + "┴" + "─"*18 + "┘\n")

if __name__ == "__main__":
    main()
