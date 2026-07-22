#!/usr/bin/env bash
# Nightly job: refresh surveillance flags, index membership snapshot,
# today's prices, today's corporate announcements, today's macro/sector
# indicators + sector membership snapshot, then recompute the derived
# label/feature tables so they stay current with the new day's data.
#
# NOT YET SCHEDULED (2026-07-16) -- deliberately run by hand until the
# model itself is built; will be added to crontab once that's ready:
#
#   0 21 * * 1-5 cd /path/to/nifty-pipeline && ./run_nightly.sh >> logs/nightly.log 2>&1
#
# (9pm IST, Mon-Fri only -- no point running on weekends, market's closed)
#
# NOTE: the fetch steps below pull just the LATEST trading day (fast,
# meant for nightly use) -- NOT a backfill. For one-time historical
# backfills:
#   python3 src/backfill_prices.py --years 5
#   python3 src/fetch_corporate_announcements.py --years 5
#   python3 src/fetch_macro_sector.py --years 5
#
# CADENCE NOTE: the FULL universe is deliberately NOT swept nightly for
# financial_results/balance_sheet/cash_flow/ratios -- screener.in-sourced
# fundamentals only change ~4x/year (quarterly results season), so hitting
# all ~500 symbols nightly would be wasted load for no new data most days.
# Instead, src/trigger_quarterly_refetch.py below reacts same-day to a
# results-disclosure announcement (reusing screener_common.find_disclosure()'s
# patterns) and re-fetches just that symbol. See run_periodic.sh for the
# full-universe safety-net sweep that catches whatever the nightly trigger
# misses -- run separately, not part of this script.

set -e
cd "$(dirname "$0")"

mkdir -p logs

echo "=== Nightly run: $(date) ==="

echo "--- Refreshing surveillance flags (ASM/GSM) ---"
python3 src/fetch_surveillance.py

echo "--- Refreshing index membership snapshot ---"
python3 src/fetch_index_membership.py

echo "--- Pulling today's prices for full universe ---"
# no args -- defaults to the full index_membership universe and today's
# date (fails loudly if index_membership is empty)
python3 src/fetch_daily_prices.py

echo "--- Pulling today's corporate announcements ---"
# no args -- defaults to today only
python3 src/fetch_corporate_announcements.py

echo "--- Checking today's announcements for results disclosures, targeted re-fetch if any ---"
# reacts same-day to a results-disclosure announcement instead of
# sweeping the full universe -- see run_periodic.sh for the safety-net
# sweep that catches whatever this misses
python3 src/trigger_quarterly_refetch.py

echo "--- Refreshing macro regime indicators + sector benchmarks ---"
# no args -- defaults to today only
python3 src/fetch_macro_sector.py

echo "--- Refreshing sector membership snapshot ---"
python3 src/fetch_sector_membership.py

echo "--- Recomputing target labels (today's new price data extends existing windows) ---"
python3 src/compute_target_labels.py

echo "--- Recomputing feature matrix ---"
python3 src/assemble_feature_matrix.py

echo "--- Resolving tracked picks past their hold period ---"
# tracking_dashboard_spec.md Section 4 -- cheap, idempotent (only touches
# still-open rows), safe to run every night even though shortlists are weekly
python3 src/resolve_tracked_picks.py

echo "--- Freezing any new lot (no-op most nights) ---"
# freeze_lot.py only does something on the (rare, explicitly-triggered)
# nights weekly_shortlist.py was just run -- otherwise it finds the latest
# tracked_picks pick_date already frozen and exits immediately. Safe/cheap
# to run every night rather than remembering to call it manually.
python3 src/freeze_lot.py

echo "--- Refreshing site data (nifty ticker, model_meta, Compare universe snapshot) ---"
# does NOT create or change lots -- just re-reads whichever lots already
# exist on disk (models/lots/) and refreshes the live pieces around them
# (today's Nifty close, tracked_picks hit-rate note, full-universe Compare
# lookup data). The picks themselves stay frozen until weekly_shortlist.py
# + freeze_lot.py are next run, on the user's own explicit cadence -- never
# automatically from this script.
python3 src/export_screener_data.py

echo "=== Nightly run complete: $(date) ==="
