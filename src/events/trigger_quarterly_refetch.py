"""
Detects results-disclosure announcements in corporate_announcements and
triggers a targeted screener.in re-fetch for just those symbols, instead
of sweeping the full ~500-symbol universe on a blind schedule.

WHY: financial_results/balance_sheet/cash_flow/ratios only change ~4x/year,
but different companies report on different days spread across a ~6-week
results season each quarter. Reuses the SAME two disclosure patterns
screener_common.find_disclosure() already validated live (see that
function's docstring for the full story): subject 'Outcome of Board
Meeting%' with details mentioning financial results/statements, or
subject 'Financial Result(s) Updates' directly. Considered using the
earlier "Board Meeting Intimation" (advance notice) instead, but confirmed
live it's far too sparse to rely on (376 rows vs. 51,048 "Outcome of Board
Meeting" rows in the 5-year corpus) -- most companies either don't file a
matching intimation subject or use different phrasing we haven't found.

This is a REACTIVE, same-day-or-catch-up trigger, not a full replacement
for periodic coverage -- see run_periodic.sh for the full-universe safety
net sweep that catches whatever this misses (a third disclosure pattern
not yet discovered, a transient fetch failure, etc).

Each triggered symbol gets a full re-fetch across all 4 tables (screener.in
returns a symbol's ENTIRE available history every call, not just the new
quarter -- there's no cheaper "just the new period" request), reusing
fetch_symbol()/upsert() directly from each script rather than
subprocessing, same pattern backfill_prices.py uses for fetch_daily_prices.

Usage:
    python src/trigger_quarterly_refetch.py                       # today only, nightly use
    python src/trigger_quarterly_refetch.py --from-date 01-06-2026 --to-date 16-07-2026  # catch up a backlog
    python src/trigger_quarterly_refetch.py --dry-run              # just show matched symbols
"""
import argparse
import sys
import time
from datetime import datetime

from core.db import get_conn, get_universe
from fundamentals.screenerScraper import ScreenerScrape

import fundamentals.fetch_financial_results as ffr
import fundamentals.fetch_balance_sheet as fbs
import fundamentals.fetch_cash_flow as fcf
import fundamentals.fetch_ratios as fratio

# Same views loop every screener.in script uses by default (both consolidated
# and standalone) -- deliberately not exposing --consolidated-only/--standalone-only
# here since a results-season catch-up run wants complete data, not reduced volume.
VIEWS = [(True, "CONSOLIDATED"), (False, "STANDALONE")]

FETCH_MODULES = [
    ("financial_results", ffr),
    ("balance_sheet", fbs),
    ("cash_flow", fcf),
    ("ratios", fratio),
]


def find_triggered_symbols(conn, from_date: str, to_date: str, universe: set) -> list:
    rows = conn.execute(
        """
        SELECT DISTINCT symbol FROM corporate_announcements
        WHERE announcement_date BETWEEN ? AND ?
          AND (
            (subject LIKE 'Outcome of Board Meeting%'
             AND (details LIKE '%financial result%' OR details LIKE '%financial statement%'))
            OR subject IN ('Financial Result Updates', 'Financial Results Updates')
          )
        """,
        (from_date, to_date),
    ).fetchall()
    return sorted(s[0] for s in rows if s[0] in universe)


def refetch_symbol(conn, scraper, symbol: str, sleep: float, fetched_at: str) -> dict:
    counts = {}
    for label, module in FETCH_MODULES:
        try:
            rows = module.fetch_symbol(scraper, conn, symbol, VIEWS, sleep, fetched_at)
            if rows:
                module.upsert(conn, rows)
            counts[label] = len(rows)
        except Exception as e:
            print(f"    FAILED {label} for {symbol}: {e}", file=sys.stderr)
            counts[label] = None
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", help="DD-MM-YYYY, defaults to today (nightly-run friendly)")
    parser.add_argument("--to-date", help="DD-MM-YYYY, defaults to today")
    parser.add_argument("--sleep", type=float, default=3.0,
                         help="seconds to sleep between requests -- screener.in throttles "
                              "aggressive scraping")
    parser.add_argument("--dry-run", action="store_true",
                         help="only show matched symbols, don't actually fetch")
    args = parser.parse_args()

    today = datetime.now()
    from_date = (datetime.strptime(args.from_date, "%d-%m-%Y") if args.from_date
                 else today).strftime("%Y-%m-%d")
    to_date = (datetime.strptime(args.to_date, "%d-%m-%Y") if args.to_date
               else today).strftime("%Y-%m-%d")

    conn = get_conn()
    universe = set(get_universe(conn))
    symbols = find_triggered_symbols(conn, from_date, to_date, universe)

    print(f"Found {len(symbols)} symbols with a results-disclosure announcement "
          f"between {from_date} and {to_date}.")
    if symbols:
        print(f"  {symbols}")

    if args.dry_run or not symbols:
        return

    scraper = ScreenerScrape()
    fetched_at = datetime.now().isoformat()

    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] refetching {symbol} ...")
        counts = refetch_symbol(conn, scraper, symbol, args.sleep, fetched_at)
        print(f"    {counts}")
        time.sleep(args.sleep)

    print(f"Done. Refetched {len(symbols)} triggered symbols.")


if __name__ == "__main__":
    main()
