"""
Fetches market-wide macro regime indicators (NIFTY 50, INDIA VIX) into
`macro_regime_indicators`, AND per-sector price benchmarks into
`sector_daily_benchmarks` (2026-07-16 -- Step 3 of the macro/sector shock
feature build). One row per trading day per table -- no point-in-time
derivation needed (an index close on date D is known on date D by
construction), unlike the disclosure-based tables elsewhere in this repo.

Both tables come from the SAME daily snapshot fetch -- every sector
index's close is already in the one file fetch_range() downloads per day,
so adding sector_daily_benchmarks costs zero extra HTTP requests, just
more parsed rows. sector_name values match sector_membership.sector_name
exactly (see fetch_sector_membership.py, imported below as the single
source of truth for the sector name list so the two scripts can't drift).

SOURCING NOTE (2026-07-16) -- read before touching this file:
macro_sector_shock_features.md (the original design doc) specified
jugaad_data.nse.index_df() per-symbol against niftyindices.com's
Backpage.aspx AJAX endpoint. Confirmed live that endpoint is broken --
niftyindices.com has been redesigned (now on Sitefinity CMS) and
Backpage.aspx returns the site's homepage HTML instead of JSON, for every
symbol, with or without a session/cookies/proper headers -- not a
symbol-string problem. Uses jugaad_data.nse.NSEIndicesArchives.bhavcopy_index_raw(date)
instead -- a static daily CSV snapshot
(niftyindices.com/Daily_Snapshot/ind_close_all_DDMMYYYY.csv) covering ALL
~161 NSE indices in one request per day, confirmed live back to at least
2021-01-04. Also a better fit for the future sector_daily_benchmarks
companion script, since the same daily file already contains every sector
index's close too -- no separate per-sector fetch needed.

GOTCHA, confirmed live: non-trading days (weekends/holidays) return HTTP
200 with the SAME homepage HTML as a broken request, not a clean 404 --
_parse_snapshot() detects a real CSV via the header row, never trusts the
status code alone.

Usage:
    python src/fetch_macro_sector.py --years 5
    python src/fetch_macro_sector.py --from-date 01-01-2024 --to-date 31-12-2024
    python src/fetch_macro_sector.py    # defaults to today only (nightly-run friendly)
"""
import argparse
import csv
import io
import sys
import time
from datetime import datetime, timedelta

from jugaad_data.nse import NSEIndicesArchives

from core.db import get_conn
from metadata.fetch_sector_membership import SECTOR_CSV_FILES

# Exact 'Index Name' values confirmed live 2026-07-16 -- 'Nifty 50' and
# 'India VIX', NOT the all-caps 'NIFTY 50'/'INDIA VIX' the design doc assumed.
NIFTY50_NAME = "Nifty 50"
VIX_NAME = "India VIX"
SECTOR_NAMES = list(SECTOR_CSV_FILES.keys())  # single source of truth, shared with fetch_sector_membership.py
TRACKED_INDEX_NAMES = {NIFTY50_NAME, VIX_NAME} | set(SECTOR_NAMES)

# Extra calendar days fetched before --from-date so the first requested
# row's rolling window (up to 50 trading days for the moving average) has
# real history to compute against, instead of every row in the first ~2
# months of a fresh backfill being NULL for lack of lookback data.
LOOKBACK_BUFFER_DAYS = 90


def _parse_snapshot(text: str) -> dict:
    """Returns {real_index_name: close} for every index we track (NIFTY 50,
    INDIA VIX, and all 15 sector indices) on a real trading day, or None if
    this wasn't one -- non-trading days return HTTP 200 with the site's
    homepage HTML (same status as success), so the CSV header row is the
    only reliable signal, not the status code."""
    if not text.lstrip().startswith("Index Name,"):
        return None
    reader = csv.DictReader(io.StringIO(text))
    result = {}
    for row in reader:
        name = row.get("Index Name", "").strip()
        if name in TRACKED_INDEX_NAMES:
            try:
                result[name] = float(row["Closing Index Value"])
            except (ValueError, KeyError):
                pass
    return result if result else None


def fetch_range(from_date, to_date, sleep: float) -> dict:
    """Returns {date_str: {real_index_name: close}} for every real trading
    day in [from_date, to_date]. Non-trading days are silently absent from
    the result, not stored as NULL rows -- there's nothing to join against
    on a day the market didn't trade."""
    arc = NSEIndicesArchives()
    series = {}
    d = from_date
    while d <= to_date:
        try:
            parsed = _parse_snapshot(arc.bhavcopy_index_raw(d))
        except Exception as e:
            print(f"    FAILED for {d.date()}: {e}", file=sys.stderr)
            parsed = None
        if parsed:
            series[d.strftime("%Y-%m-%d")] = parsed
        d += timedelta(days=1)
        time.sleep(sleep)
    return series


def _lookback_value(by_date: dict, sorted_dates: list, d: str, n: int):
    """Value n TRADING days before d (not n calendar days), or None if
    there isn't enough history yet."""
    idx = sorted_dates.index(d)
    if idx - n < 0:
        return None
    return by_date[sorted_dates[idx - n]]


def _pct_return(by_date, sorted_dates, d, n):
    cur, prev = by_date.get(d), _lookback_value(by_date, sorted_dates, d, n)
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 4)


def _dist_from_ma(by_date, sorted_dates, d, window):
    idx = sorted_dates.index(d)
    if idx - window + 1 < 0:
        return None
    vals = [by_date[sorted_dates[i]] for i in range(idx - window + 1, idx + 1)]
    ma = sum(vals) / len(vals)
    if ma == 0:
        return None
    return round((by_date[d] - ma) / ma * 100, 4)


def _vix_change(by_date, sorted_dates, d, n):
    cur, prev = by_date.get(d), _lookback_value(by_date, sorted_dates, d, n)
    if cur is None or prev is None:
        return None, None
    pts = round(cur - prev, 4)
    pct = round((cur - prev) / prev * 100, 4) if prev != 0 else None
    return pts, pct


def compute_rows(series: dict, write_from: str) -> list:
    """series: {date_str: {real_index_name: close}}. Rolling windows are
    computed against the FULL series (including the pre-write_from
    buffer), but rows are only emitted for dates >= write_from -- buffer
    days exist purely to seed the rolling calcs, not to be persisted twice
    across overlapping backfill runs."""
    nifty_by_date = {d: v[NIFTY50_NAME] for d, v in series.items() if NIFTY50_NAME in v}
    vix_by_date = {d: v[VIX_NAME] for d, v in series.items() if VIX_NAME in v}
    nifty_dates = sorted(nifty_by_date.keys())
    vix_dates = sorted(vix_by_date.keys())

    fetched_at = datetime.now().isoformat()
    rows = []
    for d in sorted(series.keys()):
        if d < write_from:
            continue
        vix_pts_5d, vix_pct_5d = _vix_change(vix_by_date, vix_dates, d, 5)
        rows.append((
            d,
            nifty_by_date.get(d),
            _pct_return(nifty_by_date, nifty_dates, d, 5),
            _pct_return(nifty_by_date, nifty_dates, d, 10),
            _dist_from_ma(nifty_by_date, nifty_dates, d, 50),
            vix_by_date.get(d),
            vix_pts_5d,
            vix_pct_5d,
            "NSE_INDICES_ARCHIVE",
            fetched_at,
        ))
    return rows


def compute_sector_rows(series: dict, write_from: str) -> list:
    """series: {date_str: {real_index_name: close}}. sector_relative_alpha_14d
    is sector_return_14d minus NIFTY 50's 14d return over the identical
    window -- computed here at write time per macro_sector_shock_features.md
    Section 3, so it doesn't need recomputing per-query later."""
    nifty_by_date = {d: v[NIFTY50_NAME] for d, v in series.items() if NIFTY50_NAME in v}
    nifty_dates = sorted(nifty_by_date.keys())

    fetched_at = datetime.now().isoformat()
    rows = []
    for sector_name in SECTOR_NAMES:
        sector_by_date = {d: v[sector_name] for d, v in series.items() if sector_name in v}
        sector_dates = sorted(sector_by_date.keys())
        for d in sector_dates:
            if d < write_from:
                continue
            return_3d = _pct_return(sector_by_date, sector_dates, d, 3)
            return_5d = _pct_return(sector_by_date, sector_dates, d, 5)
            return_14d = _pct_return(sector_by_date, sector_dates, d, 14)
            nifty_return_14d = _pct_return(nifty_by_date, nifty_dates, d, 14)
            relative_alpha_14d = (round(return_14d - nifty_return_14d, 4)
                                   if return_14d is not None and nifty_return_14d is not None
                                   else None)
            rows.append((
                sector_name, d, sector_by_date[d], return_3d, return_5d,
                return_14d, relative_alpha_14d, "NSE_INDICES_ARCHIVE", fetched_at,
            ))
    return rows


def upsert(conn, rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO macro_regime_indicators
            (date, nifty50_close, nifty50_return_5d, nifty50_return_10d,
             nifty50_dist_50dma_pct, india_vix_close, vix_change_5d_pts,
             vix_change_5d_pct, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            nifty50_close=excluded.nifty50_close,
            nifty50_return_5d=excluded.nifty50_return_5d,
            nifty50_return_10d=excluded.nifty50_return_10d,
            nifty50_dist_50dma_pct=excluded.nifty50_dist_50dma_pct,
            india_vix_close=excluded.india_vix_close,
            vix_change_5d_pts=excluded.vix_change_5d_pts,
            vix_change_5d_pct=excluded.vix_change_5d_pct,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def upsert_sector_rows(conn, rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO sector_daily_benchmarks
            (sector_name, date, sector_close, sector_return_3d, sector_return_5d,
             sector_return_14d, sector_relative_alpha_14d, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sector_name, date) DO UPDATE SET
            sector_close=excluded.sector_close,
            sector_return_3d=excluded.sector_return_3d,
            sector_return_5d=excluded.sector_return_5d,
            sector_return_14d=excluded.sector_return_14d,
            sector_relative_alpha_14d=excluded.sector_relative_alpha_14d,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", help="DD-MM-YYYY, defaults to today (nightly-run friendly)")
    parser.add_argument("--to-date", help="DD-MM-YYYY, defaults to today")
    parser.add_argument("--years", type=float,
                         help="shortcut for --from-date N years back from today, "
                              "overrides --from-date/--to-date")
    parser.add_argument("--sleep", type=float, default=0.3,
                         help="seconds to sleep between daily requests (default 0.3 -- "
                              "this is a lightweight static file per day, not screener.in)")
    args = parser.parse_args()

    today = datetime.now()
    if args.years:
        from_date = today - timedelta(days=int(args.years * 365.25))
        to_date = today
    else:
        from_date = datetime.strptime(args.from_date, "%d-%m-%Y") if args.from_date else today
        to_date = datetime.strptime(args.to_date, "%d-%m-%Y") if args.to_date else today

    write_from_str = from_date.strftime("%Y-%m-%d")
    fetch_from = from_date - timedelta(days=LOOKBACK_BUFFER_DAYS)

    print(f"Fetching {fetch_from.date()} to {to_date.date()} "
          f"({LOOKBACK_BUFFER_DAYS}-day buffer before {from_date.date()} to seed rolling windows)...")
    series = fetch_range(fetch_from, to_date, args.sleep)
    print(f"Got {len(series)} real trading days.")

    if not series:
        print("No trading days found in range -- nothing to upsert.", file=sys.stderr)
        sys.exit(1)

    conn = get_conn()

    rows = compute_rows(series, write_from_str)
    upsert(conn, rows)
    print(f"Upserted {len(rows)} macro_regime_indicators rows.")

    sector_rows = compute_sector_rows(series, write_from_str)
    upsert_sector_rows(conn, sector_rows)
    print(f"Upserted {len(sector_rows)} sector_daily_benchmarks rows "
          f"across {len(SECTOR_NAMES)} sectors.")
    print("Done.")


if __name__ == "__main__":
    main()
