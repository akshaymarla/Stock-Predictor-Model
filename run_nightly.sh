#!/usr/bin/env bash
# Nightly job: refresh surveillance flags, index membership snapshot,
# today's prices, today's corporate announcements, today's macro/sector
# indicators + sector membership snapshot, then recompute the derived
# label/feature tables so they stay current with the new day's data.
#
# Render Cron Job schedule: 0 21 * * 1-5  (9pm IST, Mon-Fri only -- market's
# closed weekends). NOTE from RUNBOOK.md: a 9pm attempt has shown NSE's
# historical price data isn't always live that early -- push later if
# fetch_daily_prices.py errors out regularly at this slot.
#
# NOTE: the fetch steps below pull just the LATEST trading day (fast,
# meant for nightly use) -- NOT a backfill. For one-time historical
# backfills:
#   python3 src/prices/backfill_prices.py --years 5
#   python3 src/events/fetch_corporate_announcements.py --years 5
#   python3 src/macro/fetch_macro_sector.py --years 5
#
# CADENCE NOTE: the FULL universe is deliberately NOT swept nightly for
# financial_results/balance_sheet/cash_flow/ratios -- screener.in-sourced
# fundamentals only change ~4x/year (quarterly results season), so hitting
# all ~500 symbols nightly would be wasted load for no new data most days.
# Instead, src/events/trigger_quarterly_refetch.py below reacts same-day to a
# results-disclosure announcement (reusing screener_common.find_disclosure()'s
# patterns) and re-fetches just that symbol. See run_periodic.sh for the
# full-universe safety-net sweep that catches whatever the nightly trigger
# misses -- run separately, not part of this script.

set -e
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)/src:${PYTHONPATH:-}"

mkdir -p logs

echo "=== Nightly run: $(date) ==="

echo "--- Refreshing surveillance flags (ASM/GSM) ---"
python3 src/risk/fetch_surveillance.py

echo "--- Refreshing index membership snapshot ---"
python3 src/metadata/fetch_index_membership.py

echo "--- Pulling today's prices for full universe ---"
python3 src/prices/fetch_daily_prices.py

echo "--- Pulling today's corporate announcements ---"
python3 src/events/fetch_corporate_announcements.py

echo "--- Checking today's announcements for results disclosures, targeted re-fetch if any ---"
python3 src/events/trigger_quarterly_refetch.py

echo "--- Refreshing macro regime indicators + sector benchmarks ---"
python3 src/macro/fetch_macro_sector.py

echo "--- Refreshing sector membership snapshot ---"
python3 src/metadata/fetch_sector_membership.py

echo "--- Recomputing target labels (today's new price data extends existing windows) ---"
python3 src/derived/compute_target_labels.py

echo "--- Recomputing feature matrix ---"
python3 src/derived/assemble_feature_matrix.py

echo "=== Nightly run complete: $(date) ==="
