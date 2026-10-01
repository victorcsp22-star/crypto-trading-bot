import sys
import os
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

SYMBOLS = [
    "DOGE-USDT", "ADA-USDT", "BNB-USDT", "SHIB-USDT", 
    "ETH-USDT", "XRP-USDT", "SOL-USDT", "AVAX-USDT",
    "SUI-USDT", "NEAR-USDT", "PEPE-USDT", "LINK-USDT"
]

def prepare_indicators(df):
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

def run_original_strategy(symbol_dfs, initial_capital=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    """
    Estrategia Original Pura:
    - Sin filtro de régimen ni filtro de volumen.
    - Entra a todo toque de Techo 10D.
    - Stop Loss amplio en Suelo 10D.
    - Salida por MACD.
    - Reparto de capital estándar entre posiciones.
    """
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    active_positions = {}
    trade_history = []
    
    all_dates = sorted(list(set.union(*[set(df.index) for df in symbol_dfs.values()])))
    
    for date in all_dates:
        # 1. Monitorear Salidas
        for sym in list(active_positions.keys()):
            pos = active_positions[sym]
            df = symbol_dfs[sym]
            if date not in df.index:
                continue
                
            row = df.loc[date]
            low = row['low']
            close = row['close']
            macd_bear = row['macd_cross_bear']
            
            entry_price = pos['entry_price']
            sl_price = pos['sl_price']
            units = pos['units']
            size_usd = pos['size_usd']
            
            if low <= sl_price:
                exit_price = sl_price * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({
                    "symbol": sym,
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "reason": "Stop Loss (Suelo 10D)"
                })
                del active_positions[sym]
                continue
            elif macd_bear:
                exit_price = close * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({
                    "symbol": sym,
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "reason": "Cruce MACD Bajista"
                })
                del active_positions[sym]
                continue

        # 2. Entradas sin Filtros
        for sym, df in symbol_dfs.items():
            if sym in active_positions or date not in df.index:
                continue
                
            row = df.loc[date]
            high = row['high']
            techo = row['techo_10d']
            piso = row['piso_10d']
            
            # Entrada directa sin filtros
            if high >= techo:
                entry_price = max(techo, row['open']) * (1 + slippage_pct)
                sl_price = piso if piso < entry_price else entry_price * 0.95
                
                # Tamaño asignado (hasta 25% del capital por trade)
                pos_size_usd = min(capital * 0.25, capital)
                if pos_size_usd >= 15.0:
                    units = pos_size_usd / entry_price
                    capital -= pos_size_usd
                    active_positions[sym] = {
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
    return initial_capital, final_equity, trade_history, max_drawdown_pct

def run_system_strategy(symbol_dfs, initial_capital=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    """
    Sistema Cuantitativo Optimizado:
    - Filtro de Régimen + Filtro de Volumen Spike.
    - Stop Loss Dinámico por ATR (1.5x ATR).
    - Protección Breakeven Lock (+1.5x ATR).
    - ATR Risk Sizing (2% del capital).
    """
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0
    
    active_positions = {}
    trade_history = []
    
    all_dates = sorted(list(set.union(*[set(df.index) for df in symbol_dfs.values()])))
    
    for date in all_dates:
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
            
            # Breakeven Lock
            if not is_be and high >= (entry_price + 1.5 * atr):
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
                trade_history.append({"symbol": sym, "pnl_usd": net_pnl, "pnl_pct": (net_pnl / size_usd) * 100, "reason": reason})
                del active_positions[sym]
                continue
            elif macd_bear:
                exit_price = close * (1 - slippage_pct)
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                capital += size_usd + net_pnl
                
                trade_history.append({"symbol": sym, "pnl_usd": net_pnl, "pnl_pct": (net_pnl / size_usd) * 100, "reason": "Cruce MACD"})
                del active_positions[sym]
                continue

        for sym, df in symbol_dfs.items():
            if sym in active_positions or date not in df.index:
                continue
                
            row = df.loc[date]
            high = row['high']
            techo = row['techo_10d']
            atr = row['atr']
            vol_ratio = row['vol_ratio']
            regime_ok = row['regime_bullish']
            
            if regime_ok and high >= techo and vol_ratio >= 1.15:
                entry_price = max(techo, row['open']) * (1 + slippage_pct)
                sl_price = entry_price - (1.5 * atr)
                if sl_price >= entry_price:
                    sl_price = entry_price * 0.95
                    
                sl_dist_pct = (entry_price - sl_price) / entry_price
                risk_usd = capital * 0.02
                pos_size_usd = min(risk_usd / sl_dist_pct, capital * 0.35)
                
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
    return initial_capital, final_equity, trade_history, max_drawdown_pct

def main():
    print("\n" + "="*85)
    print(" ⚔️ COMPARATORIA: ESTRATEGIA ORIGINAL VS. SISTEMA CUANTITATIVO OPTIMIZADO")
    print(" Mismos 12 activos OKX Spot | Mismo periodo (112 días) | Mismo Capital ($1,000 USD)")
    print("="*85 + "\n")
    
    okx = OKXPublicClient()
    symbol_dfs = {}
    for inst_id in SYMBOLS:
        df = okx.get_daily_klines(inst_id=inst_id, limit=120)
        if df is not None and len(df) >= 40:
            df = prepare_indicators(df)
            df = df.dropna(subset=['techo_10d', 'atr', 'vol_ratio']).copy()
            symbol_dfs[inst_id.replace("-USDT", "USDT")] = df
            
    # Run Original Strategy
    orig_inic, orig_fin, orig_trades, orig_dd = run_original_strategy(symbol_dfs, initial_capital=1000.0)
    orig_pnl = orig_fin - orig_inic
    orig_ret = (orig_pnl / orig_inic) * 100
    orig_df = pd.DataFrame(orig_trades)
    orig_wins = orig_df[orig_df['pnl_usd'] > 0] if not orig_df.empty else pd.DataFrame()
    orig_wr = (len(orig_wins) / len(orig_df) * 100) if not orig_df.empty else 0.0
    orig_pf = (orig_wins['pnl_usd'].sum() / abs(orig_df[orig_df['pnl_usd'] <= 0]['pnl_usd'].sum())) if not orig_wins.empty and abs(orig_df[orig_df['pnl_usd'] <= 0]['pnl_usd'].sum()) > 0 else 0.0
    
    # Run Quant System Strategy
    sys_inic, sys_fin, sys_trades, sys_dd = run_system_strategy(symbol_dfs, initial_capital=1000.0)
    sys_pnl = sys_fin - sys_inic
    sys_ret = (sys_pnl / sys_inic) * 100
    sys_df = pd.DataFrame(sys_trades)
    sys_wins = sys_df[sys_df['pnl_usd'] > 0] if not sys_df.empty else pd.DataFrame()
    sys_wr = (len(sys_wins) / len(sys_df) * 100) if not sys_df.empty else 0.0
    sys_pf = (sys_wins['pnl_usd'].sum() / abs(sys_df[sys_df['pnl_usd'] <= 0]['pnl_usd'].sum())) if not sys_wins.empty and abs(sys_df[sys_df['pnl_usd'] <= 0]['pnl_usd'].sum()) > 0 else 0.0

    print("┌" + "─"*38 + "┬" + "─"*22 + "┬" + "─"*22 + "┐")
    print(f"│ {'MÉTRICA CUANTITATIVA':<36} │ {'ESTRATEGIA ORIGINAL':<20} │ {'SISTEMA OPTIMIZADO':<20} │")
    print("├" + "─"*38 + "┼" + "─"*22 + "┼" + "─"*22 + "┤")
    print(f"│ Capital Inicial                       │ ${orig_inic:<19.2f} │ ${sys_inic:<19.2f} │")
    print(f"│ Capital Final Acumulado               │ ${orig_fin:<19.2f} │ ${sys_fin:<19.2f} │")
    print(f"│ Beneficio Neto ($)                    │ ${orig_pnl:>+19.2f} │ ${sys_pnl:>+19.2f} │")
    print(f"│ Retorno Porcentual (%)                │ {orig_ret:>+19.2f}% │ {sys_ret:>+19.2f}% │")
    print(f"│ Máximo Drawdown (Riesgo)             │ {orig_dd:<19.2f}% │ {sys_dd:<19.2f}% │")
    print(f"│ Total de Operaciones                  │ {len(orig_trades):<20} │ {len(sys_trades):<20} │")
    print(f"│ Win Rate (%)                          │ {orig_wr:<19.1f}% │ {sys_wr:<19.1f}% │")
    print(f"│ Factor de Beneficio (Profit Factor)   │ {orig_pf:<20.2f} │ {sys_pf:<20.2f} │")
    print("└" + "─"*38 + "┴" + "─"*22 + "┴" + "─"*22 + "┘\n")

if __name__ == "__main__":
    main()
