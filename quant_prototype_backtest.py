import sys
import os
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient
from math_risk_manager import MathRiskManager
from regime_filter import RegimeFilter

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Lista de Monedas Seleccionadas para el Backtest Prototipo
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
    
    # Filtro de Régimen
    df = RegimeFilter.compute_regime_indicators(df)
    
    return df

def run_compounding_backtest(initial_capital=1000.0, fee_pct=0.001, slippage_pct=0.0005):
    okx = OKXPublicClient()
    risk_manager = MathRiskManager(risk_per_trade_pct=0.02, max_position_pct=0.35)
    
    symbol_dfs = {}
    print(" 📥 Obteniendo datos históricos en vivo desde OKX Spot...")
    for inst_id in SYMBOLS:
        sym_name = inst_id.replace("-USDT", "USDT")
        df = okx.get_daily_klines(inst_id=inst_id, limit=120)
        if df is not None and len(df) >= 40:
            df = prepare_indicators(df)
            df = df.dropna(subset=['techo_10d', 'atr', 'vol_ratio']).copy()
            symbol_dfs[sym_name] = df
            
    if not symbol_dfs:
        print("❌ No se pudieron cargar los datos de OKX.")
        return
        
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_usd = 0.0
    max_drawdown_pct = 0.0
    
    active_positions = {} # {symbol: pos_dict}
    trade_history = []
    daily_equity_curve = []
    
    # Lista unificada de fechas ordenadas cronológicamente
    all_dates = sorted(list(set.union(*[set(df.index) for df in symbol_dfs.values()])))
    
    for date in all_dates:
        # 1. Monitorear Posiciones Activas (Ajustes de Breakeven, Stop Loss y MACD Exit)
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
            is_breakeven = pos['is_breakeven']
            
            # A) Chequear si activamos la protección de Breakeven (+1.5 ATR de beneficio)
            target_breakeven_price = entry_price + (1.5 * atr)
            if not is_breakeven and high >= target_breakeven_price:
                pos['sl_price'] = entry_price # Mover SL al precio de entrada (Riesgo Cero)
                pos['is_breakeven'] = True
                sl_price = entry_price
                
            # B) Ejecutar Stop Loss / Breakeven Exit
            if low <= sl_price:
                exit_price = sl_price * (1 - slippage_pct) # Slippage simulación
                raw_pnl = (exit_price - entry_price) * units
                fees = (entry_price * units + exit_price * units) * fee_pct
                net_pnl = raw_pnl - fees
                
                capital += size_usd + net_pnl
                reason = "Breakeven Lock (Riesgo 0)" if is_breakeven else "Stop Loss Dinámico ATR"
                
                trade_history.append({
                    "symbol": sym,
                    "entry_date": pos['entry_date'],
                    "exit_date": date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "reason": reason,
                    "size_usd": size_usd,
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "capital_after": capital
                })
                del active_positions[sym]
                continue
                
            # C) Ejecutar Salida por Cruce MACD Bajista
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
                    "reason": "Cruce MACD Bajista (Take Profit)",
                    "size_usd": size_usd,
                    "pnl_usd": net_pnl,
                    "pnl_pct": (net_pnl / size_usd) * 100,
                    "capital_after": capital
                })
                del active_positions[sym]
                continue

        # 2. Evaluar Nuevas Entradas (Filtro de Régimen + Techo 10D + Volumen Spike)
        for sym, df in symbol_dfs.items():
            if sym in active_positions or date not in df.index:
                continue
                
            row = df.loc[date]
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
                    
                # Cálculo de posición por Interés Compuesto Reinvertido
                sizing = risk_manager.calculate_position_size(
                    current_capital=capital,
                    entry_price=entry_price,
                    sl_price=sl_price
                )
                
                pos_size_usd = sizing['size_usd']
                units = sizing['units']
                
                if pos_size_usd > 15.0 and pos_size_usd <= capital:
                    capital -= pos_size_usd
                    active_positions[sym] = {
                        "entry_date": date,
                        "entry_price": entry_price,
                        "sl_price": sl_price,
                        "size_usd": pos_size_usd,
                        "units": units,
                        "is_breakeven": False
                    }
                    
        # Trazabilidad de Curva de Capital y Drawdown
        current_total_equity = capital + sum(p['size_usd'] for p in active_positions.values())
        daily_equity_curve.append(current_total_equity)
        
        if current_total_equity > peak_capital:
            peak_capital = current_total_equity
        dd_usd = peak_capital - current_total_equity
        dd_pct = (dd_usd / peak_capital) * 100 if peak_capital > 0 else 0
        if dd_pct > max_drawdown_pct:
            max_drawdown_pct = dd_pct
            max_drawdown_usd = dd_usd

    final_total_equity = capital + sum(p['size_usd'] for p in active_positions.values())
    return initial_capital, final_total_equity, trade_history, max_drawdown_pct, daily_equity_curve

def main():
    print("\n" + "="*85)
    print(" 🚀 SIMULACIÓN PROTOTIPO: SISTEMA CUANTITATIVO CON INTERÉS COMPUESTO")
    print(" Parámetros: Capital $1,000 USD | Risk Sizing 2% | Breakeven Lock | Filtro Régimen")
    print("="*85 + "\n")
    
    initial_cap, final_cap, trades, max_dd_pct, equity_curve = run_compounding_backtest(
        initial_capital=1000.0
    )
    
    net_pnl = final_cap - initial_cap
    ret_pct = (net_pnl / initial_cap) * 100
    
    df_trades = pd.DataFrame(trades)
    
    print("-" * 85)
    print(" 📊 RESUMEN FINAL DEL BACKTEST PROTOTIPO CON INTERÉS COMPUESTO:")
    print("-" * 85)
    print(f" • Capital Inicial         : ${initial_cap:,.2f} USD")
    print(f" • Capital Final Acumulado : ${final_cap:,.2f} USD")
    print(f" • Rendimiento Neto Total  : ${net_pnl:>+,.2f} USD ({ret_pct:>+,.2f}%)")
    print(f" • Máximo Drawdown (Riesgo): {max_dd_pct:.2f}%")
    print(f" • Total de Operaciones   : {len(trades)}")
    
    if not df_trades.empty:
        wins = df_trades[df_trades['pnl_usd'] > 0]
        losses = df_trades[df_trades['pnl_usd'] <= 0]
        win_rate = (len(wins) / len(df_trades)) * 100
        gross_p = wins['pnl_usd'].sum() if not wins.empty else 0.0
        gross_l = abs(losses['pnl_usd'].sum()) if not losses.empty else 0.0
        profit_factor = (gross_p / gross_l) if gross_l > 0 else 999.0
        
        print(f" • Tasa de Acierto (Win Rate): {win_rate:.1f}% ({len(wins)} W / {len(losses)} L)")
        print(f" • Factor de Beneficio     : {profit_factor:.2f}")
        print("-" * 85 + "\n")
        
        print(" 🔍 REGISTRO COMPLETO DE OPERACIONES CON INTERÉS COMPUESTO:")
        for idx, t in df_trades.iterrows():
            date_str = str(t['entry_date'])[:10]
            print(f"  [{date_str}] {t['symbol']:<8} | Entr: ${t['entry_price']:<7.4f} | Sal: ${t['exit_price']:<7.4f} | Tam: ${t['size_usd']:<6.1f} | {t['reason']:<25} | PnL: ${t['pnl_usd']:>+6.2f} ({t['pnl_pct']:>+5.2f}%) | Cap: ${t['capital_after']:,.2f}")
    else:
        print(" Sin operaciones durante el periodo evaluado.")

if __name__ == "__main__":
    main()
