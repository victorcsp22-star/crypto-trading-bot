import sys
import datetime
import pandas as pd
import numpy as np
from okx_client import OKXPublicClient
from paper_trader import PaperTrader
from telegram_notifier import TelegramNotifier
from config import (
    INSTITUTIONAL_WHITELIST, SYMBOL_TO_INST_ID, DONCHIAN_WINDOW,
    ATR_WINDOW, ATR_SL_MULT, BREAKEVEN_ATR_MULT, VOL_MIN_RATIO,
    RISK_PER_TRADE_PCT, MAX_POSITION_ALLOCATION_PCT, MIN_POSITION_USD,
    EMA_TREND_PERIOD
)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

class InstitutionalEngine:
    """
    Motor Cuantitativo e Institucional de Señales y Ejecución en Cascada (MTF 1D -> 4H -> 1H).
    """
    def __init__(self):
        self.okx_client = OKXPublicClient(inst_type="SPOT")
        self.paper_trader = PaperTrader()
        self.notifier = TelegramNotifier()
        self.closed_symbols_cooldown = set() # Evita re-entradas el mismo día de cierre
        self.current_utc_day = None

    def _reset_cooldown_if_new_day(self):
        today_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if self.current_utc_day != today_utc:
            self.current_utc_day = today_utc
            self.closed_symbols_cooldown.clear()

    def _compute_indicators(self, df_1d, df_4h, df_1h):
        # 1D Indicators
        df_1d = df_1d.copy()
        df_1d['techo_14d'] = df_1d['high'].shift(1).rolling(DONCHIAN_WINDOW).max()
        df_1d['piso_14d'] = df_1d['low'].shift(1).rolling(DONCHIAN_WINDOW).min()
        df_1d['vol_sma20'] = df_1d['volume'].rolling(20).mean()
        df_1d['vol_ratio'] = df_1d['volume'] / df_1d['vol_sma20']
        df_1d['ema_50'] = df_1d['close'].ewm(span=EMA_TREND_PERIOD, adjust=False).mean()
        
        high_low = df_1d['high'] - df_1d['low']
        high_close = (df_1d['high'] - df_1d['close'].shift(1)).abs()
        low_close = (df_1d['low'] - df_1d['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df_1d['atr'] = tr.rolling(ATR_WINDOW).mean()

        # 4H Indicators (Reales)
        df_4h = df_4h.copy()
        ema12_4h = df_4h['close'].ewm(span=12, adjust=False).mean()
        ema26_4h = df_4h['close'].ewm(span=26, adjust=False).mean()
        df_4h['macd_line'] = ema12_4h - ema26_4h
        df_4h['signal_line'] = df_4h['macd_line'].ewm(span=9, adjust=False).mean()
        df_4h['macd_cross_bear'] = (df_4h['macd_line'] < df_4h['signal_line']) & (df_4h['macd_line'].shift(1) >= df_4h['signal_line'].shift(1))

        # 1H Indicators (Reales)
        df_1h = df_1h.copy()
        ema12_1h = df_1h['close'].ewm(span=12, adjust=False).mean()
        ema26_1h = df_1h['close'].ewm(span=26, adjust=False).mean()
        df_1h['macd_line'] = ema12_1h - ema26_1h
        df_1h['signal_line'] = df_1h['macd_line'].ewm(span=9, adjust=False).mean()
        df_1h['macd_cross_bear'] = (df_1h['macd_line'] < df_1h['signal_line']) & (df_1h['macd_line'].shift(1) >= df_1h['signal_line'].shift(1))

        return df_1d, df_4h, df_1h

    def evaluate_market_cycle(self):
        self._reset_cooldown_if_new_day()
        print(f"\n[{datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S UTC')}] 🔍 Escaneando Whitelist Institucional de OKX SPOT...", flush=True)
        
        summary = self.paper_trader.get_portfolio_summary()
        active_positions = self.paper_trader.state["active_positions"]
        
        for symbol in INSTITUTIONAL_WHITELIST:
            inst_id = SYMBOL_TO_INST_ID[symbol]
            
            # 1. Obtener velas reales 1D (necesitamos ≥ DONCHIAN_WINDOW+2 para tener al menos 1 vela cerrada útil)
            df_1d = self.okx_client.get_klines(inst_id, bar="1D", limit=60)
            if df_1d is None or len(df_1d) < DONCHIAN_WINDOW + 3:
                continue
                
            df_4h = self.okx_client.get_klines(inst_id, bar="4H", limit=60)
            if df_4h is None or len(df_4h) < 10:
                df_4h = df_1d.copy()
                
            df_1h = self.okx_client.get_klines(inst_id, bar="1H", limit=60)
            if df_1h is None or len(df_1h) < 10:
                df_1h = df_4h.copy()
            
            df_1d, df_4h, df_1h = self._compute_indicators(df_1d, df_4h, df_1h)
            
            # ---------------------------------------------------------------
            # IMPORTANTE: Usamos iloc[-2] (última vela CERRADA confirmada)
            # para todas las decisiones de señal — igual que el backtest.
            # iloc[-1] es la vela actual EN FORMACIÓN (no cerrada aún).
            # ---------------------------------------------------------------
            prev_1d = df_1d.iloc[-2]   # Vela diaria cerrada (señal)
            last_1d = df_1d.iloc[-1]   # Vela diaria actual (precio de mercado)
            last_4h = df_4h.iloc[-2]   # Última vela 4H cerrada
            last_1h = df_1h.iloc[-2]   # Última vela 1H cerrada
            
            # Precios de referencia: señal desde vela cerrada, precio actual para SL checks
            current_price  = last_1d['close']   # Precio actual de mercado (vela en formación)
            signal_high    = prev_1d['high']     # High de la vela diaria cerrada
            signal_close   = prev_1d['close']    # Close de la vela diaria cerrada
            signal_low     = prev_1d['low']      # Low de la vela diaria cerrada (para SL check)

            techo_14d = prev_1d['techo_14d']
            atr_val   = prev_1d['atr'] if not pd.isna(prev_1d['atr']) else current_price * 0.02
            vol_ratio = prev_1d['vol_ratio']
            ema50_1d  = prev_1d['ema_50']
            
            # Skip si indicadores no están disponibles
            if pd.isna(techo_14d) or pd.isna(ema50_1d) or pd.isna(vol_ratio):
                continue
            
            # -------------------------------------------------------------
            # A) GESTIÓN DE POSICIONES ACTIVAS (SALIDAS Y BREAKEVEN)
            # Aquí sí usamos current_price para verificar SL/Breakeven en tiempo real
            # -------------------------------------------------------------
            if symbol in active_positions:
                pos = active_positions[symbol]
                entry_price = pos["entry_price"]
                sl_price    = pos["sl_price"]
                is_be       = pos["is_breakeven"]
                
                # Breakeven: activar si el precio actual ya superó +1.2x ATR desde entrada
                if not is_be and current_price >= (entry_price + BREAKEVEN_ATR_MULT * atr_val):
                    unlocked = self.paper_trader.update_breakeven_status(symbol, new_sl_price=entry_price)
                    if unlocked:
                        print(f" 🛡️ [BREAKEVEN ACTIVADO] {symbol} protegió capital a ${entry_price:.4f}", flush=True)
                        self.notifier.notify_breakeven_unlocked(symbol, new_sl_price=entry_price)
                        sl_price = entry_price
                
                # Stop Loss: comparar low de la última vela 1H cerrada contra SL
                low_1h_closed = df_1h.iloc[-2]['low'] if len(df_1h) >= 2 else current_price
                if low_1h_closed <= sl_price:
                    reason = "Breakeven Lock (Riesgo 0)" if is_be else "Stop Loss ATR Dinámico"
                    sell_info = self.paper_trader.execute_sell_order(symbol, exit_price=sl_price, exit_reason=reason)
                    if sell_info:
                        print(f" 🔴 [VENTA DEMO] {symbol} cerró por {reason} @ ${sl_price:.4f} (PnL: ${sell_info['net_pnl_usd']:+.2f})", flush=True)
                        self.closed_symbols_cooldown.add(symbol)
                        new_summary = self.paper_trader.get_portfolio_summary()
                        self.notifier.notify_sell_order(sell_info, new_summary)
                        continue
                        
                # MACD Exit: cruce bajista en vela 4H o 1H cerrada (iloc[-2] = última cerrada)
                macd_cross = False
                if len(df_4h) >= 3 and df_4h.iloc[-2].get('macd_cross_bear', False):
                    macd_cross = True
                elif len(df_1h) >= 3 and df_1h.iloc[-2].get('macd_cross_bear', False):
                    macd_cross = True
                    
                if macd_cross:
                    exit_price = last_1h['close'] if len(df_1h) >= 1 else current_price
                    sell_info = self.paper_trader.execute_sell_order(symbol, exit_price=exit_price, exit_reason="Cruce MACD Bajista (Exit)")
                    if sell_info:
                        print(f" 🔴 [VENTA DEMO] {symbol} cerró por Cruce MACD @ ${exit_price:.4f} (PnL: ${sell_info['net_pnl_usd']:+.2f})", flush=True)
                        self.closed_symbols_cooldown.add(symbol)
                        new_summary = self.paper_trader.get_portfolio_summary()
                        self.notifier.notify_sell_order(sell_info, new_summary)
                        continue

            # -------------------------------------------------------------
            # B) EVALUACIÓN DE NUEVAS ENTRADAS INSTITUCIONALES (MTF BREAKOUT)
            # Señal basada en la vela diaria CERRADA (prev_1d) — idéntico al backtest.
            # -------------------------------------------------------------
            if symbol not in active_positions and symbol not in self.closed_symbols_cooldown:
                # Filtro de tendencia y volumen (sobre vela cerrada, igual que backtest línea 91)
                if signal_close > ema50_1d and vol_ratio >= VOL_MIN_RATIO:
                    # Condición Donchian: el HIGH de la vela cerrada toca o supera el Techo 14D
                    if signal_high >= techo_14d:
                        entry_tf    = None
                        entry_price = None
                        
                        # PASO 1: Confirmación 4H / 1H — cierre de la última vela 4H/1H cerrada supera el Techo
                        # (equivalente al backtest: break_4h = sub_4h[sub_4h["close"] > techo_val])
                        if last_4h['close'] > techo_14d or last_1h['close'] > techo_14d:
                            entry_tf = "4H/1H Maker Limit"
                            # Orden Límite Pasiva: ejecuta al precio exacto del Techo 14D
                            # si el precio 1H retrocedió hasta el nivel, o al precio actual si ya está arriba
                            if last_1h['low'] <= techo_14d:
                                entry_price = techo_14d          # Ejecutado al nivel exacto (sin slippage)
                            else:
                                entry_price = max(techo_14d, current_price)  # Entrada al mercado si consolidó arriba
                        
                        # PASO 2: Fallback 1D — si el cierre diario rompió el Techo, entra al precio actual
                        # (backtest usa open del día siguiente; en live usamos current_price como aproximación)
                        elif signal_close > techo_14d:
                            entry_tf    = "1D Fallback"
                            entry_price = current_price          # Precio actual de apertura del siguiente ciclo
                            
                        if entry_price is not None:
                            sl_price = entry_price - (ATR_SL_MULT * atr_val)
                            if sl_price >= entry_price:
                                sl_price = entry_price * 0.95
                                
                            sl_dist_pct  = (entry_price - sl_price) / entry_price
                            if sl_dist_pct <= 0:
                                continue
                            current_equity = summary["total_equity"]
                            risk_usd       = current_equity * RISK_PER_TRADE_PCT
                            size_usd       = min(risk_usd / sl_dist_pct, current_equity * MAX_POSITION_ALLOCATION_PCT)
                            
                            # Verificar mínimo igual que el backtest (línea 137: pos_size_usd >= 15.0)
                            if size_usd < MIN_POSITION_USD:
                                continue
                            
                            buy_info = self.paper_trader.execute_buy_order(
                                symbol=symbol,
                                entry_price=entry_price,
                                sl_price=sl_price,
                                size_usd=size_usd,
                                entry_tf=entry_tf
                            )
                            
                            if buy_info:
                                print(f" 🟢 [COMPRA DEMO MAKER] {symbol} @ ${entry_price:.4f} | SL: ${sl_price:.4f} | Monto: ${size_usd:.2f} USD | TF: {entry_tf}", flush=True)
                                new_summary = self.paper_trader.get_portfolio_summary()
                                self.notifier.notify_buy_order(buy_info, new_summary)

        print(f"[{datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%S UTC')}] Escaneo completado. Posiciones activas: {len(self.paper_trader.state['active_positions'])}.", flush=True)

