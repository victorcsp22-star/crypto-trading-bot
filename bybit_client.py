import sys
import requests
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Providers
BINANCE_VISION_URL = "https://data-api.binance.vision"
BYBIT_URL = "https://api.bybit.com"

class MarketPublicClient:
    def __init__(self, category: str = "spot"):
        self.category = category
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def get_usdt_symbols(self, min_volume_usd: float = 5000000.0):
        """
        Obtiene los pares spot en USDT con volumen 24h mínimo usando Binance Vision API (o Bybit fallback).
        """
        # Intentar primero Binance Vision API (Servidor público global sin restricciones de IP)
        try:
            url = f"{BINANCE_VISION_URL}/api/v3/ticker/24hr"
            resp = requests.get(url, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                tickers = resp.json()
                valid_symbols = []
                exclude_keywords = ["USDC", "FDUSD", "USDE", "EUR", "GBP", "BUSD", "TUSD", "DAI", "DOWN", "UP", "3L", "3S", "BEAR", "BULL"]

                for t in tickers:
                    symbol = t["symbol"]
                    if not symbol.endswith("USDT"):
                        continue
                    if any(k in symbol for k in exclude_keywords):
                        continue

                    volume_usd = float(t.get("quoteVolume", 0.0))
                    if volume_usd >= min_volume_usd:
                        valid_symbols.append({
                            "symbol": symbol,
                            "last_price": float(t.get("lastPrice", 0.0)),
                            "high_24h": float(t.get("highPrice", 0.0)),
                            "low_24h": float(t.get("lowPrice", 0.0)),
                            "turnover_usd": volume_usd
                        })

                valid_symbols = sorted(valid_symbols, key=lambda x: x["turnover_usd"], reverse=True)
                return valid_symbols
        except Exception as e:
            print(f"[BINANCE VISION FALLBACK] {e}")

        # Fallback a Bybit API v5
        try:
            url = f"{BYBIT_URL}/v5/market/tickers?category=spot"
            resp = requests.get(url, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("retCode") == 0:
                    tickers = data["result"]["list"]
                    valid_symbols = []
                    for t in tickers:
                        symbol = t["symbol"]
                        if not symbol.endswith("USDT"):
                            continue
                        volume_usd = float(t.get("turnover", 0.0))
                        if volume_usd >= min_volume_usd:
                            valid_symbols.append({
                                "symbol": symbol,
                                "last_price": float(t.get("lastPrice", 0.0)),
                                "high_24h": float(t.get("highPrice24h", 0.0)),
                                "low_24h": float(t.get("lowPrice24h", 0.0)),
                                "turnover_usd": volume_usd
                            })
                    return sorted(valid_symbols, key=lambda x: x["turnover_usd"], reverse=True)
        except Exception as e:
            print(f"[BYBIT API FALLBACK] {e}")

        return []

    def get_daily_klines(self, symbol: str, limit: int = 20):
        """
        Obtiene las velas 1D recientes para calcular el Techo Donchian de 10D.
        """
        # Binance Vision Klines
        try:
            url = f"{BINANCE_VISION_URL}/api/v3/klines"
            params = {"symbol": symbol, "interval": "1d", "limit": limit}
            resp = requests.get(url, params=params, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                klines = resp.json()
                records = []
                for k in klines:
                    records.append({
                        "open_time": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5])
                    })
                df = pd.DataFrame(records)
                return df.sort_values("open_time").reset_index(drop=True)
        except Exception as e:
            pass

        # Fallback a Bybit Klines
        try:
            url = f"{BYBIT_URL}/v5/market/kline"
            params = {"category": "spot", "symbol": symbol, "interval": "D", "limit": limit}
            resp = requests.get(url, params=params, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("retCode") == 0:
                    klines = data["result"]["list"]
                    records = []
                    for k in reversed(klines):
                        records.append({
                            "open_time": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "volume": float(k[5])
                        })
                    df = pd.DataFrame(records)
                    return df.sort_values("open_time").reset_index(drop=True)
        except Exception as e:
            pass

        return None

# Alias para retrocompatibilidad
BybitPublicClient = MarketPublicClient

if __name__ == "__main__":
    client = MarketPublicClient()
    symbols = client.get_usdt_symbols(min_volume_usd=5000000.0)
    print(f"✅ Obtenidos {len(symbols)} pares USDT spot con volumen > $5M USD.")
    if symbols:
        print("Top 5 pares:")
        for s in symbols[:5]:
            print(f"  • {s['symbol']}: Precio=${s['last_price']} | Vol 24h=${s['turnover_usd']:,.0f} USD")
        df_k = client.get_daily_klines(symbols[0]["symbol"])
        print(f"Velas 1D para {symbols[0]['symbol']}: {len(df_k)} registros.")
