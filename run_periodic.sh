#!/usr/bin/env bash
# Periodic full-universe SAFETY NET sweep for screener.in-sourced
# quarterly data (financial_results, balance_sheet, cash_flow, ratios).
#
# The primary mechanism for keeping these tables current is the
# announcement-triggered refetch in run_nightly.sh
# (src/trigger_quarterly_refetch.py), which reacts same-day to a results
# disclosure instead of blindly sweeping ~500 symbols on a schedule. This
# script exists to catch whatever that trigger misses -- a disclosure
# subject pattern not yet discovered (screener_common.find_disclosure()
# already needed two rounds of live-data fixes to get this far), a
# transient fetch failure, a symbol whose corporate_announcements fetch
# had a gap that day, etc. Idempotent upsert makes re-running against
# companies with nothing new cheap -- it just re-confirms.
#
# NOT YET SCHEDULED (2026-07-16) -- run by hand for now, same as
# run_nightly.sh. Suggested cadence once scheduled: weekly during results
# season (roughly the 6 weeks after each quarter-end -- mid-Apr to
# early-Jun, mid-Jul to early-Sep, mid-Oct to early-Dec, mid-Jan to
# early-Mar), monthly otherwise.
#
#   0 22 * * 0 cd /path/to/nifty-pipeline && ./run_periodic.sh >> logs/periodic.log 2>&1
#   (Sunday 10pm IST, weekly -- adjust cadence once actually scheduled)

set -uo pipefail  # NOT -e -- one script failing shouldn't abort the rest
cd "$(dirname "$0")"
mkdir -p logs

echo "=== Periodic sweep: $(date) ==="

echo "--- financial_results ---"
python3 src/fetch_financial_results.py --sleep 3

echo "--- balance_sheet ---"
python3 src/fetch_balance_sheet.py --sleep 3

echo "--- cash_flow ---"
python3 src/fetch_cash_flow.py --sleep 3

echo "--- ratios ---"
python3 src/fetch_ratios.py --sleep 3

echo "=== Periodic sweep complete: $(date) ==="
