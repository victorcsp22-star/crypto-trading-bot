import os
import json
import datetime
import pandas as pd
from config import INITIAL_CAPITAL, PORTFOLIO_FILE_PATH, MAKER_FEE_PCT, TAKER_FEE_PCT

class PaperTrader:
    """
    Motor de Ejecución Simulado Local (Paper Trading Engine).
    Gestiona el saldo virtual, el historial de operaciones, el interés compuesto
    y persiste el estado en `paper_portfolio.json`.
    """
    def __init__(self, portfolio_path: str = PORTFOLIO_FILE_PATH):
        self.portfolio_path = portfolio_path
        self.state = self.load_state()

    def load_state(self) -> dict:
        if os.path.exists(self.portfolio_path):
            try:
                with open(self.portfolio_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ Error cargando {self.portfolio_path}: {e}. Inicializando nuevo estado.")
        
        # Estado Inicial de Cartera Ficticia
        initial_state = {
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "initial_capital": INITIAL_CAPITAL,
            "current_cash": INITIAL_CAPITAL,
            "peak_equity": INITIAL_CAPITAL,
            "max_drawdown_pct": 0.0,
            "active_positions": {}, # {symbol: position_dict}
            "closed_trades": []
        }
        self.save_state(initial_state)
        return initial_state

    def save_state(self, state: dict = None):
        if state is None:
            state = self.state
        try:
            with open(self.portfolio_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Error guardando estado en {self.portfolio_path}: {e}")

    def get_portfolio_summary(self) -> dict:
        cash = self.state["current_cash"]
        active = self.state["active_positions"]
        allocated_cash = sum(pos["size_usd"] for pos in active.values())
        total_equity = cash + allocated_cash
        
        if total_equity > self.state["peak_equity"]:
            self.state["peak_equity"] = total_equity
            
        peak = self.state["peak_equity"]
        dd_pct = ((peak - total_equity) / peak) * 100 if peak > 0 else 0.0
        if dd_pct > self.state["max_drawdown_pct"]:
            self.state["max_drawdown_pct"] = dd_pct
            
        closed_trades = self.state["closed_trades"]
        n_trades = len(closed_trades)
        wins = [t for t in closed_trades if t.get("net_pnl_usd", 0) > 0]
        losses = [t for t in closed_trades if t.get("net_pnl_usd", 0) <= 0]
        win_rate = (len(wins) / n_trades * 100) if n_trades > 0 else 0.0
        
        gross_profit = sum(t["net_pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        
        net_profit_usd = total_equity - self.state["initial_capital"]
        net_return_pct = (net_profit_usd / self.state["initial_capital"]) * 100

        return {
            "initial_capital": self.state["initial_capital"],
            "current_cash": cash,
            "allocated_cash": allocated_cash,
            "total_equity": total_equity,
            "net_profit_usd": net_profit_usd,
            "net_return_pct": net_return_pct,
            "peak_equity": peak,
            "max_drawdown_pct": self.state["max_drawdown_pct"],
            "total_closed_trades": n_trades,
            "wins_count": len(wins),
            "losses_count": len(losses),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor
        }

    def execute_buy_order(self, symbol: str, entry_price: float, sl_price: float, size_usd: float, entry_tf: str) -> dict:
        if symbol in self.state["active_positions"]:
            return None # Ya hay posición abierta

        cash = self.state["current_cash"]
        if size_usd > cash:
            size_usd = cash # Ajustar al saldo disponible

        if size_usd < 10.0:
            return None

        fee_usd = size_usd * MAKER_FEE_PCT # Tarifa de Orden Límite Maker Pasiva
        units = (size_usd - fee_usd) / entry_price
        self.state["current_cash"] -= size_usd

        pos_dict = {
            "symbol": symbol,
            "entry_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "entry_price": entry_price,
            "sl_price": sl_price,
            "initial_sl_price": sl_price,
            "size_usd": size_usd,
            "units": units,
            "fee_entry_usd": fee_usd,
            "entry_tf": entry_tf,
            "is_breakeven": False
        }

        self.state["active_positions"][symbol] = pos_dict
        self.save_state()
        return pos_dict

    def update_breakeven_status(self, symbol: str, new_sl_price: float) -> bool:
        if symbol not in self.state["active_positions"]:
            return False

        pos = self.state["active_positions"][symbol]
        if not pos["is_breakeven"]:
            pos["sl_price"] = new_sl_price
            pos["is_breakeven"] = True
            self.save_state()
            return True
        return False

    def execute_sell_order(self, symbol: str, exit_price: float, exit_reason: str) -> dict:
        if symbol not in self.state["active_positions"]:
            return None

        pos = self.state["active_positions"].pop(symbol)
        units = pos["units"]
        entry_price = pos["entry_price"]
        size_usd = pos["size_usd"]

        gross_value = units * exit_price
        fee_exit_usd = gross_value * TAKER_FEE_PCT
        net_value = gross_value - fee_exit_usd
        
        net_pnl_usd = net_value - size_usd
        net_pnl_pct = (net_pnl_usd / size_usd) * 100

        self.state["current_cash"] += net_value

        trade_record = {
            "symbol": symbol,
            "entry_time": pos["entry_time"],
            "exit_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_tf": pos["entry_tf"],
            "size_usd": size_usd,
            "net_pnl_usd": net_pnl_usd,
            "net_pnl_pct": net_pnl_pct,
            "exit_reason": exit_reason,
            "is_breakeven": pos["is_breakeven"],
            "equity_after": self.state["current_cash"] + sum(p["size_usd"] for p in self.state["active_positions"].values())
        }

        self.state["closed_trades"].append(trade_record)
        self.save_state()
        return trade_record
