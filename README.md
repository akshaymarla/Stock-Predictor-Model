# Nifty Alpha Pipeline

Data pipeline for the Nifty 500 stock-screening project. Every code change
gets a changelog entry below so this stays an accurate running log of
progress — check the bottom of this file for the latest status.

## Tables implemented so far

| Table | Script | Status |
|---|---|---|
| `daily_prices` | `src/fetch_daily_prices.py` | Working (confirmed against live NSE data) |
| `surveillance_flags` | `src/fetch_surveillance.py` | Working (ASM + GSM both confirmed against live NSE data) |
| `index_membership` | `src/fetch_index_membership.py` | Built, untested live (current-snapshot only, see caveat below) |
| `corporate_announcements` | `src/fetch_corporate_announcements.py` | Built, **unverified** — field names are a best guess, needs your DevTools check |
| `financial_results` | `src/fetch_financial_results.py` | Built, **unverified** — field names are a best guess, needs your DevTools check |
| `shareholding_pattern` | — | Not started |

## Setup

```bash
pip install -r requirements.txt
```

## What's actually been tested vs. not

Being direct about this so you don't waste time chasing a bug that's
actually a network issue:

- **`fetch_daily_prices.py`**: uses the `jugaad-data` library. Its column
  mapping was checked against the *actual installed library source*, not
  assumed. The normalization, rolling 20-day average calc, and the
  idempotent upsert (safe to re-run nightly without duplicating rows) were
  all tested end-to-end with synthetic data shaped exactly like NSE's real
  response. The one thing NOT tested: the live call to NSE itself, since
  `nseindia.com` isn't reachable from the sandbox this was built in.

- **`fetch_surveillance.py`**: the ASM/GSM endpoint paths
  (`/api/reportASM`, `/api/reportGSM1`) are the commonly documented ones,
  but I could not verify them live for the same reason. The parsing,
  date-normalization, and upsert logic are tested with synthetic
  NSE-shaped data. If the live endpoint 401s, 403s, or returns a
  different JSON shape than expected, that's expected — see the
  "HONESTY NOTE" at the top of the file for how to fix it (open NSE's
  ASM/GSM page in a browser, check DevTools → Network for the real
  request, send it my way).

- **`fetch_financial_results.py`**: same situation as
  `fetch_corporate_announcements.py` — field names
  (`re_broadcast_timestamp`, `re_end_date`, `re_cons`, etc.) are a best
  guess, not confirmed against a live response. `parse_results()` and the
  idempotent upsert were tested end-to-end with synthetic NSE-shaped
  payloads (including a re-upsert to confirm no duplicate rows). The
  point-in-time discipline is baked into the schema itself:
  `disclosure_date` (broadcast timestamp) is the join key, `period_end_date`
  (fiscal quarter-end) is descriptive-only and must never be used to
  determine what was "known as of" a date. If the live endpoint 0-rows or
  errors, same fix path as the other unverified scripts — DevTools capture
  needed.

## Usage

```bash
# Daily prices — start small to sanity-check before scaling to 500 symbols
python src/fetch_daily_prices.py \
    --symbols RELIANCE TCS INFY HDFCBANK \
    --from-date 01-01-2024 --to-date 31-12-2024

# Surveillance flags (ASM/GSM) — no symbol list needed, pulls the whole flagged list
python src/fetch_surveillance.py

# Index membership -- today's Nifty 500 constituent snapshot
python src/fetch_index_membership.py

# Backfill full universe's price history (run fetch_index_membership.py first)
python src/backfill_prices.py --years 5
# safe to re-run if interrupted -- it resumes via data/backfill_checkpoint.json

# Corporate announcements -- UNVERIFIED, see status note in the script itself
python src/fetch_corporate_announcements.py --from-date 01-07-2026 --to-date 13-07-2026

# Financial results -- UNVERIFIED, see status note in the script itself
python src/fetch_financial_results.py --from-date 01-04-2026 --to-date 13-07-2026

# Nightly job (surveillance + membership snapshot + latest day's prices for whole universe)
./run_nightly.sh
```

## Inspecting the data

```bash
sqlite3 data/nifty_pipeline.db "SELECT * FROM daily_prices LIMIT 5;"
sqlite3 data/nifty_pipeline.db "SELECT * FROM surveillance_flags LIMIT 5;"
```

## Known gaps / next steps

- **Symbol list**: this doesn't yet know "Nifty 500" — you pass symbols
  explicitly. Once `index_membership` exists (the harder table we
  discussed), point this script at it instead of a hardcoded list.
- **Nightly scheduling**: not wired up yet — this is a script you run
  manually for now. A cron job / scheduled task is a five-minute addition
  once you're happy with the data it's pulling.
- **Rate limiting**: `--sleep` defaults to 1s between symbols to avoid
  hammering NSE. Tune as needed, but don't blast 500 symbols with no delay.
- If NSE blocks the cold `requests` session (some setups need more
  browser-like TLS fingerprinting), the fallback is `jugaad-data`'s
  `NSELive` class, which already handles this for equity quotes — worth
  trying if `fetch_surveillance.py`'s plain `requests` session gets blocked.

## Changelog

- **2026-07-13**: Added `financial_results` table + fetch script
  (`src/fetch_financial_results.py`). Schema bakes in the point-in-time
  rule directly: `disclosure_date` (broadcast timestamp) is the required
  join key, `period_end_date` (fiscal quarter-end) is descriptive-only.
  Field names are an unverified best guess (same caveat as
  `corporate_announcements`) — parsing and idempotent upsert tested with
  synthetic data, live endpoint not yet confirmed. Also fixed a permission
  regression on `run_nightly.sh` (lost its executable bit).
- **2026-07-13**: Added `CLAUDE.md` so Claude Code (VSCode extension) has
  persistent project context — it doesn't share memory with claude.ai
  conversations, so this file is now the bridge between the two.
- **2026-07-13**: Added `corporate_announcements` table + fetch script.
  Field names are a best-effort guess (unverified against a live NSE
  response) — needs a DevTools check the same way ASM/GSM did.
- **2026-07-13**: Added `index_membership` table + fetch script (current
  Nifty 500 snapshot from NSE Indices CSV, untested live). Added
  `backfill_prices.py` (checkpointed historical backfill across the full
  universe) and `run_nightly.sh` (chains surveillance + membership +
  latest-day prices for cron use).
- **2026-07-10**: Initial commit — `daily_prices` and `surveillance_flags`
  tables + fetch scripts. Both confirmed working against live NSE data
  (ASM and GSM field names fixed after checking real DevTools responses).
