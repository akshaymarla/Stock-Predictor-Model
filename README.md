# Nifty Alpha Pipeline

Data pipeline for the Nifty 500 stock-screening project. Every code change
gets a changelog entry below so this stays an accurate running log of
progress — check the bottom of this file for the latest status.

## Tables implemented so far

| Table | Script | Status |
|---|---|---|
| `daily_prices` | `src/fetch_daily_prices.py` | Working (confirmed against live NSE data) |
| `surveillance_flags` | `src/fetch_surveillance.py` | Working (ASM + GSM both confirmed against live NSE data) |
| `index_membership` | `src/fetch_index_membership.py` | Working (confirmed against a real niftyindices.com CSV; current-snapshot only, see caveat below) |
| `corporate_announcements` | `src/fetch_corporate_announcements.py` | Working (confirmed against live NSE DevTools response) |
| `financial_results` | `src/fetch_financial_results.py` | Built, **unverified** — field names are a best guess, needs your DevTools check |
| `shareholding_pattern` | `src/fetch_shareholding_pattern.py` | Built, **partially confirmed** — field names are real (from a live DevTools column-config response), endpoint URL and exact value formats still a best guess |

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

- **`fetch_corporate_announcements.py`**: confirmed 2026-07-13 against a
  live DevTools response — the original guessed field names (`symbol`,
  `desc`, `attchmntText`, `attchmntFile`, `an_dt`) all matched exactly.
  Schema/parsing updated to also capture `seq_id` (NSE's own unique
  announcement id, now the primary key — more reliable than
  symbol+date+time+subject) and `sm_isin` (stable identifier across symbol
  renames). Tested end-to-end (including idempotent re-upsert) against the
  real payload.

- **`fetch_surveillance.py`**: the ASM/GSM endpoint paths
  (`/api/reportASM`, `/api/reportGSM1`) are the commonly documented ones,
  but I could not verify them live for the same reason. The parsing,
  date-normalization, and upsert logic are tested with synthetic
  NSE-shaped data. If the live endpoint 401s, 403s, or returns a
  different JSON shape than expected, that's expected — see the
  "HONESTY NOTE" at the top of the file for how to fix it (open NSE's
  ASM/GSM page in a browser, check DevTools → Network for the real
  request, send it my way). Note: this script never took a symbol list
  (no `--symbols` arg) — it pulls NSE's whole current ASM/GSM flagged list
  directly, so `index_membership` becoming live doesn't change anything
  here. The script that *did* need a hardcoded symbol list
  (`fetch_daily_prices.py`) already gets its universe dynamically from
  `index_membership` via `backfill_prices.get_universe()`, wired up in
  `run_nightly.sh`.

- **`fetch_index_membership.py`**: confirmed 2026-07-13 against a real
  `ind_nifty500list.csv` from niftyindices.com — the header aliases
  (`Company Name`, `Industry`, `Symbol`, `Series`, `ISIN Code`) all matched
  exactly, no code changes needed. `parse_csv()` and the idempotent upsert
  were tested end-to-end against the real file contents.

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

- **`fetch_shareholding_pattern.py`**: partially confirmed 2026-07-13.
  NSE's own column-config response (for the shareholding pattern page,
  `?symbol=HDFCBANK`) confirmed the real field names (`pr_and_prgrp`,
  `public_val`, `employeeTrusts`, `revisedStatus`, `date`,
  `submissionDate`, `revisionDate`, `xbrl`, `broadcastDate`, `systemDate`)
  — but that response only described the columns, not an actual data row,
  so exact value formats (percentage as bare number vs. string, `xbrl` as
  a plain URL vs. object) are still a best guess. The actual XHR endpoint
  URL is also unconfirmed — `ENDPOINT_URL` in the script is a guess
  following this repo's other `/api/corporate-*` pattern, not something
  seen in DevTools yet. `disclosure_date` uses `broadcastDate` ("Exchange
  Received Time"), matching the convention already used in
  `corporate_announcements`, not `date` (NSE's "AS ON DATE" snapshot
  period) or `submissionDate` (can precede public dissemination). Parsing
  and idempotent upsert tested end-to-end with synthetic data shaped like
  the confirmed field names.

## Usage

```bash
# Daily prices — start small to sanity-check before scaling to 500 symbols
python src/fetch_daily_prices.py \
    --symbols RELIANCE TCS INFY HDFCBANK \
    --from-date 01-01-2024 --to-date 31-12-2024

# Surveillance flags (ASM/GSM) — no symbol list needed, pulls the whole flagged list
python src/fetch_surveillance.py

# Index membership -- today's Nifty 500 constituent snapshot, confirmed against live data
python src/fetch_index_membership.py

# Backfill full universe's price history (run fetch_index_membership.py first)
python src/backfill_prices.py --years 5
# safe to re-run if interrupted -- it resumes via data/backfill_checkpoint.json

# Corporate announcements -- confirmed against live NSE data
python src/fetch_corporate_announcements.py --from-date 01-07-2026 --to-date 13-07-2026

# Financial results -- UNVERIFIED, see status note in the script itself
python src/fetch_financial_results.py --from-date 01-04-2026 --to-date 13-07-2026

# Shareholding pattern -- field names confirmed, endpoint URL still UNVERIFIED, see status note
python src/fetch_shareholding_pattern.py --symbols RELIANCE TCS INFY

# Nightly job (surveillance + membership snapshot + latest day's prices for whole universe)
./run_nightly.sh
```

## Inspecting the data

```bash
sqlite3 data/nifty_pipeline.db "SELECT * FROM daily_prices LIMIT 5;"
sqlite3 data/nifty_pipeline.db "SELECT * FROM surveillance_flags LIMIT 5;"
```

## Known gaps / next steps

- **Symbol list**: resolved for `daily_prices` — `backfill_prices.py` and
  `run_nightly.sh` both pull the universe from `index_membership` now that
  it's confirmed working, instead of a hardcoded list.
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

- **2026-07-13**: Added `shareholding_pattern` table + fetch script
  (`src/fetch_shareholding_pattern.py`). Field names confirmed from a real
  NSE column-config response (`pr_and_prgrp`, `public_val`,
  `employeeTrusts`, `broadcastDate`, `systemDate`, etc.), but the endpoint
  URL and exact value formats are still a guess since we only had the
  column config, not a sample data row — see "What's actually been
  tested" above. `disclosure_date` uses `broadcastDate`, matching the
  `corporate_announcements` convention.
- **2026-07-13**: Confirmed `index_membership` against a real
  `ind_nifty500list.csv` — header aliases matched exactly, no code
  changes needed. `backfill_prices.py` and `run_nightly.sh` already pulled
  their symbol universe from `index_membership` (no change needed there
  either) — that dependency chain now runs on confirmed-live data instead
  of an untested table. Also confirmed `fetch_surveillance.py` needs no
  change — it was never hardcoded to a symbol list, it pulls NSE's whole
  ASM/GSM flagged list directly.
- **2026-07-13**: Confirmed `corporate_announcements` against a live NSE
  DevTools response (see "What's actually been tested" above) — original
  guessed field names all matched. Schema changed: primary key is now
  `seq_id` (NSE's own unique announcement id) instead of
  symbol+date+time+subject, and a new `isin` column captures `sm_isin`.
  If you have a local `data/nifty_pipeline.db` from before this change,
  delete it and re-run the fetch scripts — `CREATE TABLE IF NOT EXISTS`
  won't retrofit the new column/key onto an existing table.
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
