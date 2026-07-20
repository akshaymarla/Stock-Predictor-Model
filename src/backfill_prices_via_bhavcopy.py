"""
Full re-backfill of `daily_prices` via NSE's bhavcopy settlement archive --
built 2026-07-19 as a REPLACEMENT strategy for the Section 0b remediation
re-backfill, after backfill_prices.py's per-symbol stock_history approach
proved unusable: repeated attempts today (5+ separate runs against the
same endpoint within a couple of hours) drove NSE into what looks like
IP-level throttling that manifests as the *session's cookie-refresh call*
hanging indefinitely (jugaad_data's NSEHistory._get() has no timeout on
that specific call -- see backfill_prices.py's HardTimeout docstring). A
30s hard-timeout guard made the hang non-fatal, but the hit rate reached
~100% (every symbol timing out), making forward progress impractical.

WHY THIS IS STRUCTURALLY DIFFERENT, NOT JUST A WORKAROUND: NSEArchives
(jugaad_data/nse/archives.py) is a completely separate class from
NSEHistory -- its own requests.Session, and every call explicitly passes
timeout=4. There is no untimed call anywhere in this path, so the exact
hang mechanism above cannot occur here. It's also a different NSE
endpoint entirely (the bhavcopy settlement archive, not the interactive
stock_history AJAX API), so today's per-symbol-endpoint throttling
doesn't apply to it -- confirmed by this project's own prior use
(backfill_price_gaps.py, 2026-07-16) succeeding without incident.

EFFICIENCY, not just reliability: one bhavcopy file = every symbol's EOD
data for that day. ~1,250 trading days across 5 years, one request each,
vs. 539 symbols x several date-chunked requests each under the old
approach -- fewer total requests, not more polite pacing around the same
volume.

Reuses fetch_bhavcopy_rows() from backfill_price_gaps.py verbatim (same
SERIES=="EQ" filter, same defense against bhavcopy serving a mismatched
DATE1 for special sessions) -- this script differs only in iterating
EVERY trading day in the requested range (a full re-backfill, to
guarantee every row is clean) rather than only currently-missing days.

Checkpointed by DATE (not by symbol, unlike backfill_prices.py) -- the
natural unit of work here is "one day, every symbol", so an interrupted
run resumes by day, not by symbol.

Usage:
    python src/backfill_prices_via_bhavcopy.py --years 5
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from jugaad_data.nse import NSEArchives

from db import get_conn
from backfill_price_gaps import fetch_bhavcopy_rows, recompute_rolling_avg
from fetch_daily_prices import check_price_jump_anomalies

CHECKPOINT_PATH = Path(__file__).resolve().parent.parent / "data" / "bhavcopy_backfill_checkpoint.json"


def load_checkpoint() -> set:
    if CHECKPOINT_PATH.exists():
        return set(json.loads(CHECKPOINT_PATH.read_text()).get("done", []))
    return set()


def save_checkpoint(done: set):
    CHECKPOINT_PATH.write_text(json.dumps({"done": sorted(done)}))


def upsert_rows(conn, rows: list):
    """rows already carry (..., source, fetched_at) as of fetch_bhavcopy_rows()'s
    2026-07-21 fix (docs/confirm_and_reconcile.md Part A) -- this used to
    append its own (NSE_BHAVCOPY, fetched_at) tuple on top, which would now
    double up the values and break the fixed-arity INSERT below. Don't
    re-add them here."""
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO daily_prices
            (symbol, date, open, high, low, close, prev_close, volume,
             delivery_qty, delivery_pct, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, prev_close=excluded.prev_close,
            volume=excluded.volume, delivery_qty=excluded.delivery_qty,
            delivery_pct=excluded.delivery_pct,
            source=excluded.source, fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=float, default=5)
    parser.add_argument("--from-date", help="DD-MM-YYYY, overrides --years")
    parser.add_argument("--to-date", help="DD-MM-YYYY, defaults to today")
    parser.add_argument("--sleep", type=float, default=0.3,
                         help="seconds between days -- backfill_price_gaps.py used this "
                              "successfully for ~300 requests against this same endpoint")
    parser.add_argument("--reset-checkpoint", action="store_true")
    args = parser.parse_args()

    to_date = datetime.strptime(args.to_date, "%d-%m-%Y") if args.to_date else datetime.now()
    from_date = (datetime.strptime(args.from_date, "%d-%m-%Y") if args.from_date
                 else to_date - timedelta(days=int(args.years * 365.25)))

    conn = get_conn()
    calendar = [r[0] for r in conn.execute(
        "SELECT date FROM macro_regime_indicators WHERE date BETWEEN ? AND ? ORDER BY date",
        (from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")),
    ).fetchall()]
    if not calendar:
        print("No trading days found in macro_regime_indicators for this range -- "
              "run fetch_macro_sector.py first.", file=sys.stderr)
        sys.exit(1)

    known_symbols = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_prices").fetchall()}
    print(f"Trading days in range: {len(calendar)}. Known symbols: {len(known_symbols)}.")

    done = set() if args.reset_checkpoint else load_checkpoint()
    remaining = [d for d in calendar if d not in done]
    print(f"Already done: {len(done)}. Remaining: {len(remaining)}.")

    arc = NSEArchives()
    total_rows = 0
    affected_symbols = set()
    failures = []

    for i, day_str in enumerate(remaining):
        dt = datetime.strptime(day_str, "%Y-%m-%d").date()
        try:
            rows = fetch_bhavcopy_rows(arc, dt, known_symbols)
            upsert_rows(conn, rows)
            total_rows += len(rows)
            affected_symbols.update(r[0] for r in rows)
            done.add(day_str)
            save_checkpoint(done)
            if (i + 1) % 25 == 0:
                print(f"  [{i+1}/{len(remaining)}] {total_rows} rows so far "
                      f"({len(affected_symbols)} distinct symbols touched)...")
        except Exception as e:
            print(f"    FAILED for {day_str}: {e}", file=sys.stderr)
            failures.append(day_str)
        time.sleep(args.sleep)

    print(f"\nDone. {total_rows} rows upserted across {len(done)}/{len(calendar)} days, "
          f"{len(affected_symbols)} distinct symbols touched.")
    if failures:
        print(f"{len(failures)} days failed (not in checkpoint, will retry on next run): "
              f"{failures}", file=sys.stderr)

    if affected_symbols:
        print(f"Recomputing avg_traded_value_20d for {len(affected_symbols)} symbols...")
        recompute_rolling_avg(conn, affected_symbols)

    print("Running full-universe price-jump sanity check...")
    check_price_jump_anomalies(conn, list(known_symbols))


if __name__ == "__main__":
    main()
