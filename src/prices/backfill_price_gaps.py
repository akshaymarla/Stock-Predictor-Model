"""
Backfills fully-missing trading days in `daily_prices` from NSE's official
bhavcopy settlement archive (jugaad_data.nse.NSEArchives.bhavcopy_raw) --
a different NSE endpoint than fetch_daily_prices.py's stock_history API.

BACKGROUND -- confirmed live 2026-07-16: daily_prices has ~300 fully-missing
trading days across its 5-year history (0 rows for ALL 539 symbols on that
date), 79% of them Fridays, often in consecutive-week chains. Discovered as
a side effect of building compute_target_labels.py (macro_sector_shock_features.md
Step 4) -- it correctly returned NULL labels around these dates instead of
guessing, which is what surfaced the gap. Confirmed this is a real gap
specific to jugaad_data's stock_history AJAX API (used by
fetch_daily_prices.py): re-querying that exact API live, today, for
multiple symbols (RELIANCE, TCS) on two different known-missing dates still
returns nothing. NOT investigated further why stock_history specifically
drops these days, and not fixable on our end either way since it's NSE's
own API behavior -- this script routes around it via a different,
authoritative NSE source instead: the bhavcopy is NSE's own end-of-day
settlement file, confirmed live to have full data for both a post- and
pre- July 2024 UDiff-format-cutover date.

SCOPE: only fills FULLY-missing days (0 rows for every symbol on that
date) -- these are unambiguous gaps (NSE's official settlement file has
data, our table doesn't, for every single symbol). Deliberately does NOT
touch "partial coverage" days (some but not all symbols have a row) --
those are frequently legitimate (IPOs, delistings, individual trading
halts) and need per-symbol judgment, not a blanket backfill.

avg_traded_value_20d (a rolling calc that depends on surrounding days) is
left NULL for newly-inserted gap rows at insert time -- a single isolated
day's rolling average can't be computed correctly without the full
surrounding series. This script recomputes it properly across each
affected symbol's COMPLETE price history afterward (same rolling logic as
fetch_daily_prices.add_rolling_avg_traded_value), so nothing downstream of
a filled gap is left with a stale/wrong rolling value.

KNOWN REMAINING GAPS (confirmed live 2026-07-16, not chased further --
each is a rare, isolated NSE archive quirk, not a bug in this script):
- 2021-11-04 (Diwali Muhurat trading, a special limited session): NSE's
  bhavcopy_raw() returns real content, but its OWN 'DATE1' field is
  2021-11-03, not the requested date -- the fetch_bhavcopy_rows() date
  check below correctly drops these mislabeled rows rather than writing
  them under the wrong date, so this one day stays unfilled.
- 2022-08-08: the pre-UDiff BHAVDATA-FULL CSV endpoint serves a raw ZIP
  file instead of plain CSV text for this specific date (confirmed via a
  direct request -- response starts with the ZIP magic bytes 'PK\\x03\\x04'),
  which jugaad_data's full_bhavcopy_raw() doesn't unzip (unlike the UDiff
  path, which does). Not worth writing one-off zip-extraction code around
  a single historical date.

Usage:
    python src/backfill_price_gaps.py
"""
import csv
import io
import sys
import time
from datetime import datetime

import pandas as pd
from jugaad_data.nse import NSEArchives

from core.db import get_conn

BHAVCOPY_COLUMN_MAP = {
    "SYMBOL": "symbol",
    "SERIES": "series",
    "DATE1": "date",
    "PREV_CLOSE": "prev_close",
    "OPEN_PRICE": "open",
    "HIGH_PRICE": "high",
    "LOW_PRICE": "low",
    "CLOSE_PRICE": "close",
    "TTL_TRD_QNTY": "volume",
    "DELIV_QTY": "delivery_qty",
    "DELIV_PER": "delivery_pct",
}


def find_missing_days(conn) -> list:
    """Trading days (per macro_regime_indicators) with zero daily_prices
    rows across every symbol, within daily_prices' existing date range."""
    rows = conn.execute(
        """
        SELECT mri.date
        FROM macro_regime_indicators mri
        WHERE mri.date BETWEEN (SELECT MIN(date) FROM daily_prices)
                            AND (SELECT MAX(date) FROM daily_prices)
          AND NOT EXISTS (SELECT 1 FROM daily_prices dp WHERE dp.date = mri.date)
        ORDER BY mri.date
        """
    ).fetchall()
    return [r[0] for r in rows]


def _to_float(val):
    val = val.strip()
    if val in ("", "-"):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def fetch_bhavcopy_rows(arc: NSEArchives, dt, known_symbols: set) -> list:
    """Returns a list of (symbol, date, open, high, low, close, prev_close,
    volume, delivery_qty, delivery_pct) tuples for EQ-series symbols
    already tracked in daily_prices.

    Confirmed live 2026-07-16: for at least one special-session date
    (2021-11-04, Diwali Muhurat trading), bhavcopy_raw() returned content
    whose OWN 'DATE1' field was the PREVIOUS day (2021-11-03), not the date
    requested -- silently trusting the requested date instead of the
    response's actual date would have re-written the prior day's already-
    correct rows while leaving the real gap day still empty. Rows whose
    parsed date doesn't match what was requested are dropped rather than
    inserted under the wrong assumption."""
    text = arc.bhavcopy_raw(dt)
    reader = csv.DictReader(io.StringIO(text))
    # bhavcopy headers/values carry stray leading/trailing whitespace
    # (confirmed live: 'SYMBOL, SERIES, DATE1, ...' with a space after
    # every comma) -- strip both keys and values, don't assume clean CSV.
    field_map = {raw.strip(): BHAVCOPY_COLUMN_MAP[raw.strip()]
                 for raw in (reader.fieldnames or []) if raw.strip() in BHAVCOPY_COLUMN_MAP}
    expected_date_str = dt.strftime("%Y-%m-%d")

    rows = []
    for row in reader:
        normalized = {field_map[k.strip()]: v.strip() for k, v in row.items()
                      if k.strip() in field_map}
        if normalized.get("series") != "EQ":
            continue
        symbol = normalized.get("symbol", "")
        if symbol not in known_symbols:
            continue
        try:
            date_str = datetime.strptime(normalized["date"], "%d-%b-%Y").strftime("%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        if date_str != expected_date_str:
            # bhavcopy served a different day's file than requested (seen
            # on a special-session date) -- don't silently write it under
            # the wrong date.
            continue
        rows.append((
            symbol, date_str,
            _to_float(normalized.get("open", "")),
            _to_float(normalized.get("high", "")),
            _to_float(normalized.get("low", "")),
            _to_float(normalized.get("close", "")),
            _to_float(normalized.get("prev_close", "")),
            _to_float(normalized.get("volume", "")),
            _to_float(normalized.get("delivery_qty", "")),
            _to_float(normalized.get("delivery_pct", "")),
        ))
    return rows


def upsert_gap_rows(conn, rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO daily_prices
            (symbol, date, open, high, low, close, prev_close, volume,
             delivery_qty, delivery_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, prev_close=excluded.prev_close,
            volume=excluded.volume, delivery_qty=excluded.delivery_qty,
            delivery_pct=excluded.delivery_pct
        """,
        rows,
    )
    conn.commit()


def recompute_rolling_avg(conn, symbols: set):
    """Full recompute of avg_traded_value_20d for each affected symbol's
    COMPLETE history -- inserting gap-day rows shifts the rolling 20-day
    window for up to 20 trading days after each filled gap, so a partial
    recompute would leave nearby rows silently wrong."""
    for symbol in symbols:
        df = pd.read_sql_query(
            "SELECT date, close, volume FROM daily_prices WHERE symbol = ? ORDER BY date",
            conn, params=(symbol,),
        )
        if df.empty:
            continue
        df["traded_value"] = df["close"] * df["volume"]
        df["avg_traded_value_20d"] = df["traded_value"].rolling(window=20, min_periods=1).mean()
        conn.executemany(
            "UPDATE daily_prices SET avg_traded_value_20d = ? WHERE symbol = ? AND date = ?",
            list(zip(df["avg_traded_value_20d"], [symbol] * len(df), df["date"])),
        )
    conn.commit()


def main():
    conn = get_conn()
    missing_days = find_missing_days(conn)
    if not missing_days:
        print("No fully-missing trading days found -- nothing to backfill.")
        return
    print(f"Found {len(missing_days)} fully-missing trading days. Backfilling from bhavcopy...")

    known_symbols = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_prices").fetchall()}
    arc = NSEArchives()

    total_rows = 0
    affected_symbols = set()
    filled_days = 0
    for i, day_str in enumerate(missing_days):
        dt = datetime.strptime(day_str, "%Y-%m-%d").date()
        try:
            rows = fetch_bhavcopy_rows(arc, dt, known_symbols)
        except Exception as e:
            print(f"    FAILED for {day_str}: {e}", file=sys.stderr)
            continue
        if rows:
            upsert_gap_rows(conn, rows)
            total_rows += len(rows)
            affected_symbols.update(r[0] for r in rows)
            filled_days += 1
        else:
            print(f"    WARNING {day_str}: bhavcopy had 0 matching rows for our known symbols",
                  file=sys.stderr)
        if (i + 1) % 25 == 0:
            print(f"  [{i+1}/{len(missing_days)}] {total_rows} rows inserted so far...")
        time.sleep(0.3)

    print(f"Inserted {total_rows} price rows across {filled_days}/{len(missing_days)} "
          f"gap days, affecting {len(affected_symbols)} symbols.")

    if affected_symbols:
        print(f"Recomputing avg_traded_value_20d for {len(affected_symbols)} affected symbols...")
        recompute_rolling_avg(conn, affected_symbols)

    print("Done.")


if __name__ == "__main__":
    main()
