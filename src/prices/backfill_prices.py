"""
Backfills historical daily prices for the full Nifty 500 universe (as
recorded in `index_membership`) over a given date range.

This is a thin orchestrator around fetch_daily_prices.fetch_symbol() --
it doesn't duplicate that logic, just loops it across ~500 symbols with:
  - a checkpoint file so a run that dies partway (network blip, NSE rate
    limit, laptop sleeps) can resume instead of restarting from symbol 1
  - per-symbol error handling so one bad symbol doesn't kill the whole run
  - a politeness delay between requests

Usage:
    # first run this so index_membership is populated:
    python src/fetch_index_membership.py

    # then backfill 5 years for everything in it:
    python src/backfill_prices.py --years 5

    # or backfill a custom range:
    python src/backfill_prices.py --from-date 01-01-2021 --to-date 13-07-2026

    # if a run was interrupted, just re-run the same command --
    # already-completed symbols are skipped automatically
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.db import get_conn, get_universe
from prices.fetch_daily_prices import fetch_symbol, add_rolling_avg_traded_value, upsert

CHECKPOINT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "backfill_checkpoint.json"


def load_checkpoint() -> set:
    if CHECKPOINT_PATH.exists():
        return set(json.loads(CHECKPOINT_PATH.read_text()).get("done", []))
    return set()


def save_checkpoint(done: set):
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps({"done": sorted(done)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=float, default=None,
                         help="backfill this many years back from today")
    parser.add_argument("--from-date", help="DD-MM-YYYY (alternative to --years)")
    parser.add_argument("--to-date", help="DD-MM-YYYY, defaults to today")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--reset-checkpoint", action="store_true",
                         help="ignore previous progress and start over")
    args = parser.parse_args()

    to_date = datetime.strptime(args.to_date, "%d-%m-%Y") if args.to_date else datetime.now()
    if args.years:
        from_date = to_date - timedelta(days=int(args.years * 365.25))
    elif args.from_date:
        from_date = datetime.strptime(args.from_date, "%d-%m-%Y")
    else:
        print("Specify either --years or --from-date", file=sys.stderr)
        sys.exit(1)

    conn = get_conn()
    universe = get_universe(conn)
    if not universe:
        print("index_membership is empty -- run fetch_index_membership.py first.",
              file=sys.stderr)
        sys.exit(1)

    done = set() if args.reset_checkpoint else load_checkpoint()
    remaining = [s for s in universe if s not in done]

    print(f"Universe: {len(universe)} symbols. Already done: {len(done)}. "
          f"Remaining: {len(remaining)}.")
    print(f"Range: {from_date:%d-%m-%Y} to {to_date:%d-%m-%Y}")

    failures = []
    for i, symbol in enumerate(remaining):
        print(f"[{i+1}/{len(remaining)}] {symbol} ...")
        try:
            df = fetch_symbol(symbol, from_date, to_date)
            df = add_rolling_avg_traded_value(df)
            upsert(conn, df)
            done.add(symbol)
            save_checkpoint(done)  # checkpoint after every symbol, not just at the end
            print(f"    OK, {len(df)} rows")
        except Exception as e:
            print(f"    FAILED: {e}", file=sys.stderr)
            failures.append(symbol)
        time.sleep(args.sleep)

    print(f"\nBackfill pass complete. {len(done)}/{len(universe)} symbols done.")
    if failures:
        print(f"{len(failures)} symbols failed this run (will retry on next "
              f"run since they're not in the checkpoint): {failures}")


if __name__ == "__main__":
    main()
