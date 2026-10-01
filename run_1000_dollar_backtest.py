import sys
import os
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

WHITELIST_SYMBOLS = [
    "DOGE-USDT", "ADA-USDT", "BNB-USDT", "SHIB-USDT", 
    "ETH-USDT", "XRP-USDT", "SOL-USDT", "AVAX-USDT"
]

def compute_indicators(df):
    df['techo_10d'] = df['high'].shift(1).rolling(10).max()
    df['piso_10d'] = df['low'].shift(1).rolling(10).min()
    
    # Volumen Promedio (20)
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
    
    return df

def run_portfolio_backtest(initial_capital=1000.0, risk_per_trade_pct=0.02, fee_pct=0.001):
    okx = OKXPublicClient()
    
    # Pre-cargar datos de todas las monedas
    symbol_dfs = {}
    for inst_id in WHITELIST_SYMBOLS:
        sym_name = inst_id.replace("-USDT", "USDT")
        df_klines = okx.get_daily_klines(inst_id=inst_id, limit=120)
        if df_klines is not None and len(df_klines) >= 40:
            df_klines = compute_indicators(df_klines)
            df_klines = df_klines.dropna(subset=['techo_10d', 'atr']).copy()
            symbol_dfs[sym_name] = df_klines

    capital = initial_capital
    active_positions = {} # {symbol: position_dict}
    trade_history = []
    
    # Obtener fechas comunes
    all_dates = sorted(list(set.union(*[set(df.index) for df in symbol_dfs.values()])))
    
    for date in all_dates:
        # 1. Chequear salidas en posiciones activas
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
            
            # Condición Stop Loss
            if low <= sl_price:
                exit_price = sl_price
                raw_pnl = (exit_price - entry_price) * units
                fee = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fee
                capital += size_usd + net_pnl
                
                trade_history.append({
                    "symbol": sym,
                    "entry_date": pos['entry_date'],
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "reason": "Stop Loss (ATR)",
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "capital_after": capital
                })
                del active_positions[sym]
                
            # Condición MACD Exit
            elif macd_bear:
                exit_price = close
                raw_pnl = (exit_price - entry_price) * units
                fee = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fee
                capital += size_usd + net_pnl
                
                trade_history.append({
                    "symbol": sym,
                    "entry_date": pos['entry_date'],
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "reason": "Cruce MACD (Take Profit)",
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "capital_after": capital
                })
                del active_positions[sym]
                
        # 2. Chequear entradas para símbolos sin posición activa
        for sym, df in symbol_dfs.items():
            if sym in active_positions or date not in df.index:
                continue
            row = df.loc[date]
            high = row['high']
            techo = row['techo_10d']
            atr = row['atr']
            vol_ratio = row['vol_ratio']
            
            # Entrada: Techo 10D y filtro de volumen
            if high >= techo and vol_ratio >= 1.15:
                entry_price = max(techo, row['open'])
                sl_price = entry_price - (1.5 * atr)
                
                if sl_price >= entry_price:
                    sl_price = entry_price * 0.95
                    
                sl_dist_pct = (entry_price - sl_price) / entry_price
                
                # FÓRMULA DE PARIDAD DE VOLATILIDAD (ATR RISK SIZING)
                # Arriesgar máximo el 2% del capital total ($20 en $1,000)
                risk_amount_usd = capital * risk_per_trade_pct
                position_size_usd = risk_amount_usd / sl_dist_pct
                
                # No asignar más del 25% del capital total a una sola posición
                max_allowed_pos = capital * 0.25
                if position_size_usd > max_allowed_pos:
                    position_size_usd = max_allowed_pos
                    
                if position_size_usd <= capital and position_size_usd >= 20.0:
                    units = position_size_usd / entry_price
                    capital -= position_size_usd
                    active_positions[sym] = {
                        "entry_date": date,
                        "entry_price": entry_price,
                        "sl_price": sl_price,
                        "size_usd": position_size_usd,
                        "units": units
                    }
                    
    return capital, trade_history

def main():
    print("\n" + "="*80)
    print(" 📊 BACKTEST DE CARTERA UNIFICADA ($1,000 USD INICIALES)")
    print(" Gestión de Riesgo Cuantitativa: 2% Risk Sizing por Trade + SL ATR")
    print("="*80 + "\n")
    
    initial_cap = 1000.0
    final_cap, trades = run_portfolio_backtest(initial_capital=initial_cap, risk_per_trade_pct=0.02)
    
    df_trades = pd.DataFrame(trades)
    
    print("-" * 80)
    print(f" 💰 RESULTADO DE LA CARTERA DE $1,000 USD:")
    print(f" • Capital Inicial Total : ${initial_cap:,.2f} USD")
    print(f" • Capital Final Total   : ${final_cap:,.2f} USD")
    pnl = final_cap - initial_cap
    pct = (pnl / initial_cap) * 100
    print(f" • Rendimiento Neto      : ${pnl:>+,.2f} USD ({pct:>+,.2f}%)")
    print(f" • Total de Operaciones  : {len(trades)}")
    
    if not df_trades.empty:
        wins = df_trades[df_trades['pnl_usd'] > 0]
        losses = df_trades[df_trades['pnl_usd'] <= 0]
        win_rate = (len(wins) / len(df_trades)) * 100
        gross_p = wins['pnl_usd'].sum() if not wins.empty else 0
        gross_l = abs(losses['pnl_usd'].sum()) if not losses.empty else 0
        pf = (gross_p / gross_l) if gross_l > 0 else 999.0
        
        print(f" • Win Rate              : {win_rate:.1f}%")
        print(f" • Factor de Beneficio   : {pf:.2f}")
        print("-" * 80 + "\n")
        
        print(" 🔍 DETALLE DE OPERACIONES EJECUTADAS:")
        for idx, t in df_trades.iterrows():
            print(f"  [{str(t['entry_date'])[:10]}] {t['symbol']:<8} | Entr: ${t['entry_price']:<7.4f} | Sal: ${t['exit_price']:<7.4f} | {t['reason']:<25} | PnL: ${t['pnl_usd']:>+6.2f} ({t['pnl_pct']:>+5.2f}%) | Cap: ${t['capital_after']:,.2f}")

if __name__ == "__main__":
    main()
