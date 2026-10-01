import pandas as pd
import numpy as np
from data_loader import load_symbol_data
import indicators

class MTFBacktester:
    def __init__(self, data_dict: dict, fee_pct: float = 0.001, initial_capital: float = 10000.0):
        self.df_1d = data_dict["1d"].copy()
        self.df_4h = data_dict["4h"].copy()
        self.df_1h = data_dict["1h"].copy()
        self.fee_pct = fee_pct
        self.initial_capital = initial_capital
        
        self._prepare_indicators()

    def _prepare_indicators(self):
        # 1D Indicators
        self.df_1d = indicators.compute_donchian_channel(self.df_1d, window=14)
        self.df_1d = indicators.compute_macd(self.df_1d)
        self.df_1d = indicators.compute_atr(self.df_1d, window=14)

        # 4H Indicators
        self.df_4h = indicators.compute_macd(self.df_4h)
        self.df_4h = indicators.compute_atr(self.df_4h, window=14)

        # 1H Indicators
        self.df_1h = indicators.compute_macd(self.df_1h)
        self.df_1h = indicators.compute_atr(self.df_1h, window=14)

    def run_backtest(self, sl_mode: str = "atr_1.5", macd_exit_tf: str = "1h"):
        trades = []
        in_position = False

        df_1d_valid = self.df_1d.dropna(subset=["techo_14d"]).copy()
        n_days = len(df_1d_valid)

        # Pre-convert 1h dataframe columns to numpy arrays for hyper-fast iteration
        df_1h_clean = self.df_1h.copy()
        times_1h = df_1h_clean.index.values
        opens_1h = df_1h_clean["open"].values
        highs_1h = df_1h_clean["high"].values
        lows_1h = df_1h_clean["low"].values
        closes_1h = df_1h_clean["close"].values
        macd_cross_1h = df_1h_clean["macd_bear_cross"].values

        # Index lookup
        time_to_idx_1h = {t: idx for idx, t in enumerate(df_1h_clean.index)}

        i = 0
        current_exit_idx = 0

        while i < n_days:
            day_ts = df_1d_valid.index[i]
            day_row = df_1d_valid.iloc[i]
            techo_14d = day_row["techo_14d"]
            high_1d = day_row["high"]

            # Saltear si aún estaríamos dentro de una operación activa
            if in_position:
                if day_ts < df_1h_clean.index[current_exit_idx]:
                    i += 1
                    continue
                else:
                    in_position = False

            if high_1d >= techo_14d:
                next_day_ts = day_ts + pd.Timedelta(days=1)
                
                sub_1h = self.df_1h.loc[(self.df_1h.index >= day_ts) & (self.df_1h.index < next_day_ts)]
                sub_4h = self.df_4h.loc[(self.df_4h.index >= day_ts) & (self.df_4h.index < next_day_ts)]

                entry_tf = None
                entry_ts = None
                entry_price = None
                trigger_candle_low = None

                # Paso 1: Evaluar 1h
                break_1h = sub_1h[sub_1h["close"] > techo_14d]
                if not break_1h.empty:
                    entry_tf = "1h"
                    first_break = break_1h.iloc[0]
                    first_break_ts = break_1h.index[0]
                    sub_1h_after = self.df_1h.loc[self.df_1h.index > first_break_ts]
                    if not sub_1h_after.empty:
                        entry_ts = sub_1h_after.index[0]
                        entry_price = sub_1h_after.iloc[0]["open"]
                        trigger_candle_low = first_break["low"]

                # Paso 2: Evaluar 4h
                if entry_price is None:
                    break_4h = sub_4h[sub_4h["close"] > techo_14d]
                    if not break_4h.empty:
                        entry_tf = "4h"
                        first_break = break_4h.iloc[0]
                        first_break_ts = break_4h.index[0]
                        sub_4h_after = self.df_4h.loc[self.df_4h.index > first_break_ts]
                        if not sub_4h_after.empty:
                            entry_ts = sub_4h_after.index[0]
                            entry_price = sub_4h_after.iloc[0]["open"]
                            trigger_candle_low = first_break["low"]

                # Paso 3: Evaluar 1D
                if entry_price is None:
                    if day_row["close"] > techo_14d:
                        entry_tf = "1d"
                        if i + 1 < n_days:
                            entry_ts = df_1d_valid.index[i + 1]
                            entry_price = df_1d_valid.iloc[i + 1]["open"]
                            trigger_candle_low = day_row["low"]

                if entry_price is not None and entry_ts is not None and entry_ts in time_to_idx_1h:
                    atr_val = day_row["atr"] if not np.isnan(day_row["atr"]) else (entry_price * 0.02)
                    piso_14d = day_row["piso_14d"] if not np.isnan(day_row["piso_14d"]) else (entry_price * 0.95)

                    if sl_mode == "candle_low":
                        sl_price = trigger_candle_low if trigger_candle_low < entry_price else entry_price * 0.98
                    elif sl_mode == "atr_1.0":
                        sl_price = entry_price - (1.0 * atr_val)
                    elif sl_mode == "atr_1.5":
                        sl_price = entry_price - (1.5 * atr_val)
                    elif sl_mode == "atr_2.0":
                        sl_price = entry_price - (2.0 * atr_val)
                    elif sl_mode == "donchian_low":
                        sl_price = piso_14d
                    else:
                        sl_price = entry_price - (1.5 * atr_val)

                    if sl_price >= entry_price:
                        sl_price = entry_price * 0.98

                    # Simulación súper rápida con arrays de numpy
                    start_idx = time_to_idx_1h[entry_ts]
                    total_1h_len = len(df_1h_clean)

                    exit_ts = None
                    exit_price = None
                    exit_reason = None
                    exit_idx = total_1h_len - 1

                    for idx in range(start_idx, total_1h_len):
                        low_val = lows_1h[idx]
                        close_val = closes_1h[idx]
                        is_macd_bear = macd_cross_1h[idx]

                        # Stop Loss trigger
                        if low_val <= sl_price:
                            exit_ts = df_1h_clean.index[idx]
                            exit_price = sl_price
                            exit_reason = "Stop Loss"
                            exit_idx = idx
                            break
                        
                        # Take profit MACD Bear Cross
                        if is_macd_bear and idx > start_idx:
                            exit_ts = df_1h_clean.index[idx]
                            exit_price = close_val
                            exit_reason = "MACD Exit (TP)"
                            exit_idx = idx
                            break

                    if exit_price is None:
                        exit_idx = total_1h_len - 1
                        exit_ts = df_1h_clean.index[exit_idx]
                        exit_price = closes_1h[exit_idx]
                        exit_reason = "End of Data"

                    raw_return = (exit_price - entry_price) / entry_price
                    net_return = (1 + raw_return) * ((1 - self.fee_pct) ** 2) - 1

                    trades.append({
                        "entry_time": entry_ts,
                        "exit_time": exit_ts,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "sl_price": sl_price,
                        "entry_tf": entry_tf,
                        "sl_mode": sl_mode,
                        "raw_return_pct": raw_return * 100,
                        "net_return_pct": net_return * 100,
                        "exit_reason": exit_reason,
                        "duration_hours": (exit_ts - entry_ts).total_seconds() / 3600
                    })

                    in_position = True
                    current_exit_idx = exit_idx

            i += 1

        return self._compute_summary(trades, sl_mode)

    def _compute_summary(self, trades: list, sl_mode: str):
        if not trades:
            return {
                "sl_mode": sl_mode,
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "net_profit_pct": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "trades": []
            }

        df_trades = pd.DataFrame(trades)
        wins = df_trades[df_trades["net_return_pct"] > 0]
        losses = df_trades[df_trades["net_return_pct"] <= 0]

        total_trades = len(df_trades)
        win_rate = (len(wins) / total_trades) * 100

        gross_profit = wins["net_return_pct"].sum() if not wins.empty else 0.0
        gross_loss = abs(losses["net_return_pct"].sum()) if not losses.empty else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

        capital = self.initial_capital
        equity_curve = [capital]
        for ret in df_trades["net_return_pct"]:
            capital *= (1 + ret / 100.0)
            equity_curve.append(capital)

        equity_series = pd.Series(equity_curve)
        peak = equity_series.cummax()
        drawdown = (equity_series - peak) / peak
        max_drawdown = abs(drawdown.min()) * 100

        net_profit_pct = ((capital - self.initial_capital) / self.initial_capital) * 100

        return {
            "sl_mode": sl_mode,
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "net_profit_pct": net_profit_pct,
            "final_capital": capital,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown,
            "avg_trade_return_pct": df_trades["net_return_pct"].mean(),
            "trades": trades
        }
