"""
Fetches EOD price/volume/delivery data per symbol from NSE and loads it
into the `daily_prices` table.

Data source: jugaad-data's stock_df(), which pulls from NSE's historical
data API (nseindia.com). Confirmed column mapping (checked against the
installed jugaad-data==0.33.1 source directly, not assumed):

    DATE, SERIES, OPEN, HIGH, LOW, PREV. CLOSE, LTP, CLOSE, VWAP,
    VOLUME, VALUE, NO OF TRADES, DELIVERY QTY, DELIVERY %, SYMBOL

CRITICAL BUG FOUND AND FIXED (2026-07-19, found while building models/backtest.py --
see README changelog for the full investigation): jugaad-data==0.33.1's own
`_stock()` method (jugaad_data/nse/history.py line 80) has an inverted
condition -- `'series': series if series != "EQ" else "ALL"` -- meaning
when THIS script (correctly) requests series="EQ", the library silently
sends series="ALL" to NSE's actual API. For any symbol with OTHER
NSE-listed instruments under the same symbol string (most commonly
corporate bonds/NCDs -- confirmed live for IFCI, PFC, NHPC, M&MFIN, NTPC
and others, mostly PSU/financial companies that are frequent bond
issuers), this silently mixed non-equity-series rows (low-volume,
wildly different price scale, e.g. bonds trading near face value
~Rs 1000-1400) into what should have been pure equity data. Confirmed via
direct inspection of the installed library's source, not assumed.
`stock_df()`'s output DOES still include a `SERIES` column per-row
(sourced from NSE's own CH_SERIES field) even though the REQUEST asked
for everything -- so this is fixable entirely on our side by filtering
the RESPONSE, without patching the library. fetch_symbol() below now
does exactly that. 91 of 539 symbols in the existing `daily_prices` table
were confirmed affected (4,508 anomalous rows, spanning the full
2021-2026 backfill history) before this fix -- see README changelog for
the full remediation.

NOTE ON NETWORK ACCESS:
This script talks to nseindia.com, which is NOT reachable from the sandbox
this was built in (only PyPI/GitHub/npm etc. are whitelisted there). Confirmed
working live 2026-07-13 for a full-year range (RELIANCE, TCS, 249-250 rows).

KNOWN GOTCHA (confirmed live 2026-07-13): requesting a same-day-only range
("today" for both --from-date and --to-date, e.g. the nightly default) fails
for ~all symbols with a cryptic pandas KeyError if run before NSE has
published that day's data (usually available a few hours after market
close, not immediately). Root cause: jugaad_data's stock_df() does
pd.DataFrame(raw)[stock_select_headers] internally, which throws that
KeyError when NSE's API returns an empty list. fetch_symbol() below
pre-checks with stock_raw() (cached, so no extra network cost) and raises a
clear ValueError instead. This isn't fixable on our end -- it's just NSE not
having same-day data yet -- but the error is at least readable now. If
run_nightly.sh's 9pm IST schedule still hits this, NSE's historical API may
be slower to update than assumed; try a later cron time.

Usage:
    # nightly use -- no args needed: full index_membership universe, today only
    python src/fetch_daily_prices.py

    # explicit symbols + range
    python src/fetch_daily_prices.py --symbols RELIANCE TCS INFY \
        --from-date 01-01-2024 --to-date 31-12-2024

    # one-time historical backfill shortcut for a symbol subset (for the
    # full universe, prefer backfill_prices.py -- it checkpoints progress
    # across ~500 symbols so an interrupted run can resume)
    python src/fetch_daily_prices.py --symbols RELIANCE TCS --years 5
"""
import argparse
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from db import get_conn, get_universe

try:
    from jugaad_data.nse import stock_df, stock_raw
except ImportError:
    print("Run: pip install -r requirements.txt", file=sys.stderr)
    raise


def fetch_symbol(symbol: str, from_date: datetime, to_date: datetime) -> pd.DataFrame:
    """Pull one symbol's EOD history and normalize to our schema's column names."""
    # jugaad_data's stock_df() does pd.DataFrame(raw)[stock_select_headers] internally,
    # which crashes with a cryptic pandas KeyError if NSE returns an empty list for this
    # range (e.g. today's data isn't published yet, or the range is a holiday/weekend).
    # Pre-checking with stock_raw() (which stock_df() calls internally and caches, so
    # this doesn't cost an extra network round-trip) lets us fail with a clear message
    # instead. Confirmed live 2026-07-13: a same-day "today only" run failed for ~all
    # symbols with this exact empty-data case, not a real bug in our column mapping.
    raw_check = stock_raw(symbol=symbol, from_date=from_date, to_date=to_date, series="EQ")
    if not raw_check:
        raise ValueError(
            f"NSE returned no trading data for {from_date:%d-%m-%Y} to {to_date:%d-%m-%Y}. "
            f"Common causes: today's data isn't published yet (usually available a few "
            f"hours after market close, not immediately), the range is a holiday/weekend, "
            f"or the symbol didn't trade in this window."
        )
    raw = stock_df(symbol=symbol, from_date=from_date, to_date=to_date, series="EQ")

    df = pd.DataFrame({
        "symbol": raw["SYMBOL"],
        "date": pd.to_datetime(raw["DATE"]).dt.strftime("%Y-%m-%d"),
        "series": raw["SERIES"],
        "open": raw["OPEN"],
        "high": raw["HIGH"],
        "low": raw["LOW"],
        "close": raw["CLOSE"],
        "prev_close": raw["PREV. CLOSE"],
        "volume": raw["VOLUME"],
        "delivery_qty": raw["DELIVERY QTY"],
        "delivery_pct": raw["DELIVERY %"],
    })
    # defensive filter for the jugaad-data "EQ"->"ALL" bug described above --
    # NSE's response itself correctly labels each row's real series even
    # though the library requested everything, so this is a reliable fix.
    n_before = len(df)
    df = df[df["series"] == "EQ"].drop(columns=["series"])
    n_dropped = n_before - len(df)
    if n_dropped:
        print(f"    [{symbol}] dropped {n_dropped} non-EQ-series row(s) "
              f"(jugaad-data series=\"ALL\" bug -- see module docstring)", file=sys.stderr)
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


def check_price_jump_anomalies(conn, symbols: list, jump_threshold: float = 0.5):
    """Scans daily_prices for any single-day close-to-close jump beyond
    jump_threshold (default 50%, generous given NSE circuit limits are
    typically 5-20%) for the given symbols -- the exact heuristic that
    found the jugaad-data series="ALL" corruption bug (2026-07-19, see
    README changelog and docs/next_phase_plan.md Section 0b), now run
    automatically after every fetch instead of only being discovered
    months later by a downstream symptom (an absurd backtest return).
    Loudly warns to stderr, doesn't block the upsert -- a genuine extreme
    move (rare but real, e.g. a demerger/delisting-adjacent price reset)
    is possible and shouldn't halt the pipeline, but it should never pass
    silently either."""
    if not symbols:
        return
    placeholders = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"SELECT symbol, date, close FROM daily_prices "
        f"WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
        symbols,
    ).fetchall()
    from collections import defaultdict
    by_symbol = defaultdict(list)
    for symbol, date, close in rows:
        by_symbol[symbol].append((date, close))

    anomalies = []
    for symbol, series in by_symbol.items():
        for i in range(1, len(series)):
            prev_date, prev_close = series[i - 1]
            date, close = series[i]
            if prev_close and prev_close > 0:
                if abs(close / prev_close - 1) > jump_threshold:
                    anomalies.append((symbol, prev_date, prev_close, date, close))

    if anomalies:
        print(f"\n  [SANITY CHECK] {len(anomalies)} single-day jump(s) >{jump_threshold:.0%} "
              f"found -- possible data corruption (e.g. the jugaad-data series=\"ALL\" bug, "
              f"see README changelog 2026-07-19), NOT auto-corrected:", file=sys.stderr)
        for symbol, prev_date, prev_close, date, close in anomalies:
            pct = (close / prev_close - 1) * 100
            print(f"    {symbol}: {prev_date} close={prev_close} -> {date} close={close} "
                  f"({pct:+.1f}%)", file=sys.stderr)


def upsert(conn, df: pd.DataFrame, fetched_at: str):
    df = df.copy()
    df["source"] = "NSE"
    df["fetched_at"] = fetched_at
    rows = df[[
        "symbol", "date", "open", "high", "low", "close", "prev_close",
        "volume", "delivery_qty", "delivery_pct", "avg_traded_value_20d",
        "source", "fetched_at",
    ]].values.tolist()

    conn.executemany(
        """
        INSERT INTO daily_prices
            (symbol, date, open, high, low, close, prev_close,
             volume, delivery_qty, delivery_pct, avg_traded_value_20d,
             source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, prev_close=excluded.prev_close,
            volume=excluded.volume, delivery_qty=excluded.delivery_qty,
            delivery_pct=excluded.delivery_pct,
            avg_traded_value_20d=excluded.avg_traded_value_20d,
            source=excluded.source, fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                         help="NSE symbols, e.g. RELIANCE TCS INFY. "
                              "Omit to use the full Nifty 500 universe from index_membership.")
    parser.add_argument("--from-date", help="DD-MM-YYYY, defaults to today (nightly-run friendly)")
    parser.add_argument("--to-date", help="DD-MM-YYYY, defaults to today")
    parser.add_argument("--years", type=float,
                         help="shortcut for a one-time historical backfill: fetch this "
                              "many years back from today, overrides --from-date/--to-date")
    parser.add_argument("--sleep", type=float, default=1.0,
                         help="seconds to sleep between symbols, be polite to NSE")
    args = parser.parse_args()

    today = datetime.now()
    if args.years:
        from_date = today - timedelta(days=int(args.years * 365.25))
        to_date = today
    else:
        from_date = (datetime.strptime(args.from_date, "%d-%m-%Y") if args.from_date
                     else today)
        to_date = (datetime.strptime(args.to_date, "%d-%m-%Y") if args.to_date
                   else today)

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
    upsert(conn, combined, datetime.now().isoformat())
    print(f"Done. Loaded {len(combined)} rows into daily_prices "
          f"({DB_PATH_MSG})")
    check_price_jump_anomalies(conn, combined["symbol"].unique().tolist())


DB_PATH_MSG = "data/nifty_pipeline.db"

if __name__ == "__main__":
    main()
