#!/usr/bin/env bash
# Nightly job: refresh surveillance flags, refresh today's index membership
# snapshot, pull the latest day's prices for the whole universe, and pull
# today's corporate announcements.
#
# Intended to run once per night after market close (Akshay's stated plan
# is a nightly run). Add to crontab with something like:
#
#   0 21 * * 1-5 cd /path/to/nifty-pipeline && ./run_nightly.sh >> logs/nightly.log 2>&1
#
# (9pm IST, Mon-Fri only -- no point running on weekends, market's closed)
#
# NOTE: this pulls just the LATEST trading day/announcements (fast, meant
# for nightly use) -- NOT a backfill. For one-time historical backfills:
#   python3 src/backfill_prices.py --years 5
#   python3 src/fetch_corporate_announcements.py --years 5

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

echo "=== Nightly run complete: $(date) ==="
