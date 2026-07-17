#!/usr/bin/env bash
# Periodic full-universe SAFETY NET sweep for screener.in/NSE-sourced
# quarterly data (financial_results, balance_sheet, cash_flow, ratios,
# shareholding_pattern).
#
# The primary mechanism for keeping financial_results/balance_sheet/
# cash_flow/ratios current is the announcement-triggered refetch in
# run_nightly.sh (src/events/trigger_quarterly_refetch.py), which reacts
# same-day to a results disclosure instead of blindly sweeping ~500
# symbols on a schedule. This script exists to catch whatever that
# trigger misses -- a disclosure subject pattern not yet discovered, a
# transient fetch failure, a symbol whose corporate_announcements fetch
# had a gap that day, etc. Idempotent upsert makes re-running against
# companies with nothing new cheap -- it just re-confirms.
#
# shareholding_pattern has no same-day trigger (NSE always returns the
# latest filing, not a diff), so it lives here as its only scheduled
# refresh -- previously only ran at one-time seed.
#
# Render Cron Job schedule: 0 22 * * 0  (Sunday 10pm IST, weekly). Suggested
# cadence: weekly during results season (roughly the 6 weeks after each
# quarter-end -- mid-Apr to early-Jun, mid-Jul to early-Sep, mid-Oct to
# early-Dec, mid-Jan to early-Mar), monthly otherwise.

set -uo pipefail  # NOT -e -- one script failing shouldn't skip the rest
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)/src:${PYTHONPATH:-}"
mkdir -p logs

echo "=== Periodic sweep: $(date) ==="

echo "--- financial_results ---"
python3 src/fundamentals/fetch_financial_results.py --sleep 3

echo "--- balance_sheet ---"
python3 src/fundamentals/fetch_balance_sheet.py --sleep 3

echo "--- cash_flow ---"
python3 src/fundamentals/fetch_cash_flow.py --sleep 3

echo "--- ratios ---"
python3 src/fundamentals/fetch_ratios.py --sleep 3

echo "--- shareholding_pattern ---"
python3 src/fundamentals/fetch_shareholding_pattern.py --sleep 2

echo "=== Periodic sweep complete: $(date) ==="
