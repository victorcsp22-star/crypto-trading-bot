import os
import pandas as pd

DATA_DIR = r"C:\Users\victo\crypto_signals\data\processed\ohlcv\spot"

def load_symbol_data(symbol: str = "BTCUSDT"):
    """
    Carga y alinea los datos de 1D, 4H y 1H para un símbolo desde los archivos parquet/csv locales.
    """
    data = {}
    timeframes = ["1d", "4h", "1h"]

    for tf in timeframes:
        folder = os.path.join(DATA_DIR, tf)
        parquet_path = os.path.join(folder, f"{symbol}_{tf}.parquet")
        csv_path = os.path.join(folder, f"{symbol}_{tf}.csv")

        if os.path.exists(parquet_path):
            df = pd.read_parquet(parquet_path)
        elif os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
        else:
            raise FileNotFoundError(f"No se encontró archivo para {symbol} en temporalidad {tf} en {folder}")

        # Estandarizar nombres de columnas
        df.columns = [c.lower() for c in df.columns]
        
        # timestamp/date conversion
        time_col = "open_time" if "open_time" in df.columns else ("date" if "date" in df.columns else df.columns[0])
        df[time_col] = pd.to_datetime(df[time_col], utc=True)
        df = df.sort_values(time_col).reset_index(drop=True)
        df = df.set_index(time_col)

        # Asegurar tipos numéricos
        cols = ["open", "high", "low", "close", "volume"]
        for c in cols:
            if c in df.columns:
                df[c] = df[c].astype(float)

        data[tf] = df[cols]

    return data

if __name__ == "__main__":
    symbol = "BTCUSDT"
    data = load_symbol_data(symbol)
    print(f"--- Datos cargados para {symbol} ---")
    for tf, df in data.items():
        print(f"TF {tf}: {len(df)} registros desde {df.index.min()} hasta {df.index.max()}")
