#!/usr/bin/env bash
# One-time comprehensive historical seed -- Stage 1 of RUNBOOK.md.
#
# NOT meant to be scheduled/recurring -- this is a one-off "fetch everything
# for the full Nifty 500 universe, see what breaks or lags" run. Re-run only
# if rebuilding the DB from scratch or extending history further back.
#
# Order matters: index_membership populates the universe every other script
# defaults to. Screener.in-sourced scripts use a conservative --sleep since
# this hits ~500 symbols x 4 scripts over several hours -- see RUNBOOK.md's
# rate-limit discipline section.
#
# Run this from Render's Shell tab (or any box with SUPABASE_DB_URL set)
# as a one-off -- do NOT add it as a Cron Job.
#
# Usage: ./run_historical_seed.sh
# Logs: logs/seed_<script>_<timestamp>.log, one per script, plus a summary
# printed (and logged) at the end.
# Requires: SUPABASE_DB_URL env var set, and the psql client available
# (apt-get install postgresql-client) for the final row-count summary.

set -uo pipefail  # NOT -e -- one script failing shouldn't abort the rest
cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"
export PYTHONPATH="$PROJECT_ROOT/src:${PYTHONPATH:-}"
LOGS_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOGS_DIR"

STAMP=$(date +%Y%m%d_%H%M%S)
SEED_LOG="$LOGS_DIR/seed_summary_${STAMP}.log"

run_step() {
    local label="$1"; shift
    local logfile="$LOGS_DIR/seed_${label}_${STAMP}.log"
    echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] START ${label} ===" | tee -a "$SEED_LOG"
    if "$@" > "$logfile" 2>&1; then
        echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] OK    ${label} (log: $logfile) ===" | tee -a "$SEED_LOG"
    else
        local rc=$?
        echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] FAILED ${label} (exit $rc, log: $logfile) ===" | tee -a "$SEED_LOG"
    fi
}

run_step index_membership        python3 src/metadata/fetch_index_membership.py
run_step surveillance            python3 src/risk/fetch_surveillance.py
run_step backfill_prices         python3 src/prices/backfill_prices.py --years 5
run_step corporate_announcements python3 src/events/fetch_corporate_announcements.py --years 5
run_step shareholding_pattern    python3 src/fundamentals/fetch_shareholding_pattern.py --sleep 2
run_step financial_results       python3 src/fundamentals/fetch_financial_results.py --sleep 3
run_step balance_sheet           python3 src/fundamentals/fetch_balance_sheet.py --sleep 3
run_step cash_flow               python3 src/fundamentals/fetch_cash_flow.py --sleep 3
run_step ratios                  python3 src/fundamentals/fetch_ratios.py --sleep 3

echo "" | tee -a "$SEED_LOG"
echo "=== Row counts after seed ===" | tee -a "$SEED_LOG"
if command -v psql > /dev/null && [ -n "${SUPABASE_DB_URL:-}" ]; then
    psql "$SUPABASE_DB_URL" -c "
SELECT 'daily_prices' t, COUNT(*) n FROM daily_prices
UNION ALL SELECT 'surveillance_flags', COUNT(*) FROM surveillance_flags
UNION ALL SELECT 'index_membership', COUNT(*) FROM index_membership
UNION ALL SELECT 'corporate_announcements', COUNT(*) FROM corporate_announcements
UNION ALL SELECT 'shareholding_pattern', COUNT(*) FROM shareholding_pattern
UNION ALL SELECT 'financial_results', COUNT(*) FROM financial_results
UNION ALL SELECT 'balance_sheet', COUNT(*) FROM balance_sheet
UNION ALL SELECT 'cash_flow', COUNT(*) FROM cash_flow
UNION ALL SELECT 'ratios', COUNT(*) FROM ratios;
" | tee -a "$SEED_LOG"
else
    echo "psql not available or SUPABASE_DB_URL not set -- skipping row-count summary. Check the Supabase table editor instead." | tee -a "$SEED_LOG"
fi

echo "" | tee -a "$SEED_LOG"
echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] SEED COMPLETE -- summary: $SEED_LOG ===" | tee -a "$SEED_LOG"
