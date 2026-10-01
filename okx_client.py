import sys
import requests
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

OKX_BASE_URL = "https://www.okx.com"

class OKXPublicClient:
    def __init__(self, inst_type: str = "SPOT"):
        self.inst_type = inst_type
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def get_usdt_symbols(self, min_volume_usd: float = 5000000.0):
        url = f"{OKX_BASE_URL}/api/v5/market/tickers"
        params = {"instType": self.inst_type}
        
        try:
            resp = requests.get(url, params=params, headers=self.headers, timeout=10)
            data = resp.json()
            if data.get("code") != "0":
                print(f"Error OKX Tickers API: {data.get('msg')}", flush=True)
                return []

            tickers = data.get("data", [])
            valid_symbols = []
            exclude_keywords = ["USDC", "FDUSD", "USDE", "EUR", "GBP", "BUSD", "TUSD", "DAI", "DOWN", "UP", "3L", "3S", "BEAR", "BULL"]

            for t in tickers:
                inst_id = t["instId"]
                if not inst_id.endswith("-USDT"):
                    continue

                symbol_raw = inst_id.replace("-", "")
                if any(k in symbol_raw for k in exclude_keywords):
                    continue

                volume_usd = float(t.get("volCcy24h", 0.0))
                if volume_usd >= min_volume_usd:
                    valid_symbols.append({
                        "inst_id": inst_id,
                        "symbol": symbol_raw,
                        "last_price": float(t.get("last", 0.0)),
                        "high_24h": float(t.get("high24h", 0.0)),
                        "low_24h": float(t.get("low24h", 0.0)),
                        "turnover_usd": volume_usd
                    })

            valid_symbols = sorted(valid_symbols, key=lambda x: x["turnover_usd"], reverse=True)
            return valid_symbols

        except Exception as e:
            print(f"Excepción al conectar con OKX Tickers API: {e}", flush=True)
            return []

    def get_klines(self, inst_id: str, bar: str = "1D", limit: int = 40):
        """
        Obtiene klines en tiempo real de OKX para cualquier temporalidad ('1D', '4H', '1H', '15m').
        """
        url = f"{OKX_BASE_URL}/api/v5/market/candles"
        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": limit
        }

        try:
            resp = requests.get(url, params=params, headers=self.headers, timeout=10)
            data = resp.json()
            if data.get("code") != "0":
                return None

            candles = data.get("data", [])
            records = []
            for c in reversed(candles):
                records.append({
                    "open_time": pd.to_datetime(int(c[0]), unit="ms", utc=True),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5])
                })

            df = pd.DataFrame(records)
            df = df.sort_values("open_time").reset_index(drop=True)
            return df

        except Exception as e:
            print(f"Excepción al obtener klines ({bar}) OKX para {inst_id}: {e}", flush=True)
            return None

    def get_daily_klines(self, inst_id: str, limit: int = 40):
        return self.get_klines(inst_id=inst_id, bar="1D", limit=limit)

    def get_1year_daily_klines(self, inst_id: str, days: int = 365):
        all_candles = []
        after_ts = ""
        url = f"{OKX_BASE_URL}/api/v5/market/history-candles"
        
        while len(all_candles) < days:
            params = {
                "instId": inst_id,
                "bar": "1D",
                "limit": "100"
            }
            if after_ts:
                params["after"] = after_ts
                
            try:
                resp = requests.get(url, params=params, headers=self.headers, timeout=10)
                data = resp.json()
                if data.get("code") != "0" or not data.get("data"):
                    if not all_candles:
                        url_alt = f"{OKX_BASE_URL}/api/v5/market/candles"
                        resp = requests.get(url_alt, params=params, headers=self.headers, timeout=10)
                        data = resp.json()
                        if data.get("code") != "0" or not data.get("data"):
                            break
                    else:
                        break

                candles = data.get("data", [])
                if not candles:
                    break
                    
                all_candles.extend(candles)
                after_ts = candles[-1][0]
                
                if len(candles) < 100:
                    break
            except Exception as e:
                break

        if not all_candles:
            return None

        records = []
        for c in reversed(all_candles):
            records.append({
                "open_time": pd.to_datetime(int(c[0]), unit="ms", utc=True),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5])
            })

        df = pd.DataFrame(records)
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
        return df.tail(days).reset_index(drop=True)

if __name__ == "__main__":
    client = OKXPublicClient()
    df_4h = client.get_klines("BTC-USDT", bar="4H", limit=10)
    print(f"✅ Velas 4H cargadas: {len(df_4h)} barras. Última: {df_4h.iloc[-1]['open_time']}")
