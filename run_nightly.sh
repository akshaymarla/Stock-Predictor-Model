#!/usr/bin/env bash
# Nightly job: refresh surveillance flags, refresh today's index membership
# snapshot, and pull the latest day's prices for the whole universe.
#
# Intended to run once per night after market close (Akshay's stated plan
# is a nightly run). Add to crontab with something like:
#
#   0 21 * * 1-5 cd /path/to/nifty-pipeline && ./run_nightly.sh >> logs/nightly.log 2>&1
#
# (9pm IST, Mon-Fri only -- no point running on weekends, market's closed)
#
# NOTE: this pulls just the LATEST trading day of prices for the whole
# universe (fast, meant for nightly use) -- NOT a backfill. Use
# backfill_prices.py separately for historical data.

set -e
cd "$(dirname "$0")"

TODAY=$(date +%d-%m-%Y)
mkdir -p logs

echo "=== Nightly run: $(date) ==="

echo "--- Refreshing surveillance flags (ASM/GSM) ---"
python3 src/fetch_surveillance.py

echo "--- Refreshing index membership snapshot ---"
python3 src/fetch_index_membership.py

echo "--- Pulling today's prices for full universe ---"
SYMBOLS=$(cd src && python3 -c "
from db import get_conn
from backfill_prices import get_universe
conn = get_conn()
print(' '.join(get_universe(conn)))
")

if [ -z "$SYMBOLS" ]; then
    echo "No universe found -- run fetch_index_membership.py successfully first."
    exit 1
fi

python3 src/fetch_daily_prices.py --symbols $SYMBOLS --from-date "$TODAY" --to-date "$TODAY"

echo "=== Nightly run complete: $(date) ==="
