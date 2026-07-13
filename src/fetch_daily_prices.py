"""
Fetches EOD price/volume/delivery data per symbol from NSE and loads it
into the `daily_prices` table.

Data source: jugaad-data's stock_df(), which pulls from NSE's historical
data API (nseindia.com). Confirmed column mapping (checked against the
installed jugaad-data==0.33.1 source directly, not assumed):

    DATE, SERIES, OPEN, HIGH, LOW, PREV. CLOSE, LTP, CLOSE, VWAP,
    VOLUME, VALUE, NO OF TRADES, DELIVERY QTY, DELIVERY %, SYMBOL

NOTE ON NETWORK ACCESS:
This script talks to nseindia.com, which is NOT reachable from the sandbox
this was built in (only PyPI/GitHub/npm etc. are whitelisted there). It has
been checked for import errors and logic bugs using synthetic data, but the
live NSE call itself has not been executed end-to-end. Run it from your own
machine, where nseindia.com is reachable, and let me know what happens --
NSE occasionally changes response formats/headers and we may need to adjust.

Usage:
    python src/fetch_daily_prices.py --symbols RELIANCE TCS INFY \
        --from-date 01-01-2024 --to-date 31-12-2024
"""
import argparse
import sys
import time
from datetime import datetime

import pandas as pd

from db import get_conn, get_universe

try:
    from jugaad_data.nse import stock_df
except ImportError:
    print("Run: pip install -r requirements.txt", file=sys.stderr)
    raise


def fetch_symbol(symbol: str, from_date: datetime, to_date: datetime) -> pd.DataFrame:
    """Pull one symbol's EOD history and normalize to our schema's column names."""
    raw = stock_df(symbol=symbol, from_date=from_date, to_date=to_date, series="EQ")

    df = pd.DataFrame({
        "symbol": raw["SYMBOL"],
        "date": pd.to_datetime(raw["DATE"]).dt.strftime("%Y-%m-%d"),
        "open": raw["OPEN"],
        "high": raw["HIGH"],
        "low": raw["LOW"],
        "close": raw["CLOSE"],
        "prev_close": raw["PREV. CLOSE"],
        "volume": raw["VOLUME"],
        "delivery_qty": raw["DELIVERY QTY"],
        "delivery_pct": raw["DELIVERY %"],
    })
    return df.sort_values("date")


def add_rolling_avg_traded_value(df: pd.DataFrame) -> pd.DataFrame:
    """avg_traded_value_20d = rolling 20-day mean of (close * volume), per symbol."""
    df = df.sort_values(["symbol", "date"]).copy()
    df["traded_value"] = df["close"] * df["volume"]
    df["avg_traded_value_20d"] = (
        df.groupby("symbol")["traded_value"]
        .transform(lambda s: s.rolling(window=20, min_periods=1).mean())
    )
    return df.drop(columns=["traded_value"])


def upsert(conn, df: pd.DataFrame):
    rows = df[[
        "symbol", "date", "open", "high", "low", "close", "prev_close",
        "volume", "delivery_qty", "delivery_pct", "avg_traded_value_20d",
    ]].values.tolist()

    conn.executemany(
        """
        INSERT INTO daily_prices
            (symbol, date, open, high, low, close, prev_close,
             volume, delivery_qty, delivery_pct, avg_traded_value_20d)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, prev_close=excluded.prev_close,
            volume=excluded.volume, delivery_qty=excluded.delivery_qty,
            delivery_pct=excluded.delivery_pct,
            avg_traded_value_20d=excluded.avg_traded_value_20d
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                         help="NSE symbols, e.g. RELIANCE TCS INFY. "
                              "Omit to use the full Nifty 500 universe from index_membership.")
    parser.add_argument("--from-date", required=True, help="DD-MM-YYYY")
    parser.add_argument("--to-date", required=True, help="DD-MM-YYYY")
    parser.add_argument("--sleep", type=float, default=1.0,
                         help="seconds to sleep between symbols, be polite to NSE")
    args = parser.parse_args()

    from_date = datetime.strptime(args.from_date, "%d-%m-%Y")
    to_date = datetime.strptime(args.to_date, "%d-%m-%Y")

    conn = get_conn()

    symbols = args.symbols
    if not symbols:
        symbols = get_universe(conn)
        if not symbols:
            print("No --symbols given and index_membership is empty -- run "
                  "fetch_index_membership.py first, or pass --symbols explicitly.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"No --symbols given -- using the full Nifty 500 universe "
              f"from index_membership ({len(symbols)} symbols).")

    all_frames = []

    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] fetching {symbol} ...")
        try:
            df = fetch_symbol(symbol, from_date, to_date)
            all_frames.append(df)
            print(f"    got {len(df)} rows")
        except Exception as e:
            print(f"    FAILED for {symbol}: {e}", file=sys.stderr)
        time.sleep(args.sleep)

    if not all_frames:
        print("Nothing fetched, exiting.", file=sys.stderr)
        sys.exit(1)

    combined = pd.concat(all_frames, ignore_index=True)
    combined = add_rolling_avg_traded_value(combined)
    upsert(conn, combined)
    print(f"Done. Loaded {len(combined)} rows into daily_prices "
          f"({DB_PATH_MSG})")


DB_PATH_MSG = "data/nifty_pipeline.db"

if __name__ == "__main__":
    main()
