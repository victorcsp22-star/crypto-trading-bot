import requests
import datetime
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id and self.token != "YOUR_TELEGRAM_BOT_TOKEN")

    def _send_msg(self, text: str):
        if not self.is_configured():
            print(f"[TELEGRAM SIMULACIÓN] {text.replace('*', '').replace('`', '')}")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }

        try:
            resp = requests.post(self.base_url, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"[TELEGRAM OK] Alerta despachada con éxito.")
                return True
            else:
                print(f"[TELEGRAM ERROR {resp.status_code}] {resp.text}")
                return False
        except Exception as e:
            print(f"[TELEGRAM EXCEPCIÓN] {e}")
            return False

    def notify_buy_order(self, buy_info: dict, summary: dict):
        sym = buy_info["symbol"]
        price = buy_info["entry_price"]
        sl = buy_info["sl_price"]
        size_usd = buy_info["size_usd"]
        units = buy_info["units"]
        entry_tf = buy_info["entry_tf"]
        
        capital_total = summary["total_equity"]
        sl_dist_pct = ((price - sl) / price) * 100

        text = (
            f"🟢 *¡ORDEN LÍMITE COMPRA DEMO EJECUTADA (MAKER)!*\n\n"
            f"• *Activo*: `{sym}`\n"
            f"• *Precio Entrada*: `${price:,.4f}`\n"
            f"• *Stop Loss Inicial*: `${sl:,.4f}` (-{sl_dist_pct:.2f}%)\n"
            f"• *Monto Invertido*: `${size_usd:,.2f} USD` ({units:,.4f} unidades)\n"
            f"• *Nivel de Confirmación*: `{entry_tf}`\n"
            f"• *Tarifa Maker Pasiva*: `0.02%` (Cero Slippage)\n\n"
            f"📊 *Estado de Cartera Demo*:\n"
            f"• Capital Total Acumulado: `${capital_total:,.2f} USD`\n"
            f"• Saldo Libre Disponible : `${summary['current_cash']:,.2f} USD`\n"
            f"• Posiciones Activas    : `{summary['allocated_cash']:,.2f} USD`"
        )
        self._send_msg(text)

    def notify_breakeven_unlocked(self, symbol: str, new_sl_price: float):
        text = (
            f"🛡️ *¡CANDADO DE BREAKEVEN ACTIVADO (RIESGO CERO)!*\n\n"
            f"• *Activo*: `{symbol}`\n"
            f"• *Nuevo Stop Loss*: `${new_sl_price:,.4f}` (Precio de Entrada)\n"
            f"• *Estado*: La posición avanzó **+1.2x ATR** a favor. Pérdida neta eliminada al 100%."
        )
        self._send_msg(text)

    def notify_sell_order(self, sell_info: dict, summary: dict):
        sym = sell_info["symbol"]
        entry_p = sell_info["entry_price"]
        exit_p = sell_info["exit_price"]
        pnl_usd = sell_info["net_pnl_usd"]
        pnl_pct = sell_info["net_pnl_pct"]
        reason = sell_info["exit_reason"]
        
        icon = "🟢" if pnl_usd >= 0 else "🔴"
        sign = "+" if pnl_usd >= 0 else ""

        text = (
            f"{icon} *¡POSICIÓN CERRADA DEMO (EXIT)!*\n\n"
            f"• *Activo*: `{sym}`\n"
            f"• *Precio Entrada*: `${entry_p:,.4f}`\n"
            f"• *Precio Salida*: `${exit_p:,.4f}`\n"
            f"• *Motivo de Salida*: `{reason}`\n"
            f"• *Resultado Neto*: *{sign}${pnl_usd:,.2f} USD* ({sign}{pnl_pct:.2f}%)\n\n"
            f"📈 *Rendimiento Acumulado*:\n"
            f"• Balance Cartera Demo   : `${summary['total_equity']:,.2f} USD`\n"
            f"• Rendimiento Neto Total  : *{'+' if summary['net_profit_usd'] >= 0 else ''}${summary['net_profit_usd']:,.2f} USD* ({'+' if summary['net_return_pct'] >= 0 else ''}{summary['net_return_pct']:.2f}%)\n"
            f"• Tasa de Acierto (WinRate): `{summary['win_rate_pct']:.1f}%` ({summary['wins_count']} W / {summary['losses_count']} L)\n"
            f"• Factor de Beneficio     : `{summary['profit_factor']:.2f}`\n"
            f"• Máximo Drawdown        : `{summary['max_drawdown_pct']:.2f}%`"
        )
        self._send_msg(text)

    def notify_daily_dashboard(self, summary: dict):
        text = (
            f"📊 *DASHBOARD DIARIO DE RENDIMIENTO (BOT DEMO INSTITUCIONAL)*\n\n"
            f"• *Capital Inicial*: `${summary['initial_capital']:,.2f} USD`\n"
            f"• *Capital Total Actual*: `${summary['total_equity']:,.2f} USD`\n"
            f"• *Ganancia Neta Total*: *{'+' if summary['net_profit_usd'] >= 0 else ''}${summary['net_profit_usd']:,.2f} USD* ({'+' if summary['net_return_pct'] >= 0 else ''}{summary['net_return_pct']:.2f}%)\n"
            f"• *Tasa de Acierto*: `{summary['win_rate_pct']:.1f}%`\n"
            f"• *Factor de Beneficio*: `{summary['profit_factor']:.2f}`\n"
            f"• *Máximo Drawdown*: `{summary['max_drawdown_pct']:.2f}%`\n"
            f"• *Trades Cerrados*: `{summary['total_closed_trades']}`"
        )
        self._send_msg(text)
