# Nifty Alpha Pipeline

Data pipeline for the Nifty 500 stock-screening project. Every code change
gets a changelog entry below so this stays an accurate running log of
progress — check the bottom of this file for the latest status.

## Tables implemented so far

| Table | Script | Status |
|---|---|---|
| `daily_prices` | `src/fetch_daily_prices.py` | Working (confirmed against live NSE data) |
| `surveillance_flags` | `src/fetch_surveillance.py` | Working (ASM confirmed + fixed against live NSE data; GSM wrapper shape confirmed, item field names unconfirmed pending a non-empty response) |
| `index_membership` | `src/fetch_index_membership.py` | Working (confirmed against a real niftyindices.com CSV; current-snapshot only, see caveat below) |
| `corporate_announcements` | `src/fetch_corporate_announcements.py` | Working (confirmed against live NSE DevTools response) |
| `financial_results` | `src/fetch_financial_results.py` | Built, **unverified** — field names are a best guess, needs your DevTools check |
| `shareholding_pattern` | `src/fetch_shareholding_pattern.py` | Working (confirmed end-to-end against live NSE data; dynamic universe, quarterly cadence so not in nightly by default) |

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

- **`fetch_surveillance.py`**: fixed 2026-07-13 after a live run showed
  "parsed 0 flagged symbols" for both ASM and GSM. Root cause for ASM: the
  real response is nested by category (`{"longterm": {"data": [...]}}`),
  not the flat `{"data": [...]}` the code assumed — fixed and verified
  against the real payload. GSM's `[]` was never a bug — NSE currently has
  0 GSM-flagged symbols, and the existing raw-list handling already
  parses that correctly as zero rows. GSM's item field names
  (`gsmStage`, `gsmTime`, etc.) are still unconfirmed since we haven't
  seen a real non-empty GSM response yet — the earlier "confirmed
  2026-07-10" claim in this file was inaccurate; corrected. If GSM parses
  to 0 rows while the diagnostic (added this same day) shows a non-empty
  raw payload, that's the field names being wrong, not an empty list.
  Note: this script never took a symbol list
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

- **`fetch_shareholding_pattern.py`**: field names AND values confirmed
  2026-07-13 against a real HDFCBANK row (`recordId`, `isin`,
  `broadcastDate`, `date`, `pr_and_prgrp`, `public_val`, `employeeTrusts`,
  `revisedStatus`, `submissionDate`, `systemDate`, `xbrl`, all present and
  matching the original guesses). Dates use uppercase month abbreviations
  ("03-JUL-2026") — confirmed Python's `strptime %b` is case-insensitive,
  so no parsing change was needed. Percentages are bare numeric strings
  ("0", "100"), `xbrl` is a plain URL string. Schema updated to add
  `record_id` (NSE's own unique row id, now the primary key, same
  reasoning as `seq_id` in `corporate_announcements`) and `isin`.
  `disclosure_date` uses `broadcastDate`, matching the
  `corporate_announcements` convention. Parsing and idempotent upsert
  tested end-to-end against the real row (both plausible wrapper shapes).
  Endpoint URL confirmed via a real curl capture:
  `GET /api/corporate-share-holdings-master?index=equities&symbol=X` —
  the path was right all along, the bug was a missing `index=equities`
  query param (that's why the earlier guess 200'd with a non-JSON body).
  Not yet run live end-to-end — next step is just confirming it actually
  works against the real endpoint now that all the pieces are in place.

## Usage

```bash
# Daily prices — start small to sanity-check before scaling to 500 symbols
python src/fetch_daily_prices.py \
    --symbols RELIANCE TCS INFY HDFCBANK \
    --from-date 01-01-2024 --to-date 31-12-2024
# omit --symbols for the full Nifty 500 universe from index_membership,
# omit --from-date/--to-date for today only (nightly-run friendly),
# or use --years 5 for a one-time historical backfill of a symbol subset
# (for the full universe, prefer backfill_prices.py -- it checkpoints)

# Surveillance flags (ASM/GSM) — no symbol list needed, pulls the whole flagged list
python src/fetch_surveillance.py

# Index membership -- today's Nifty 500 constituent snapshot, confirmed against live data
python src/fetch_index_membership.py

# Backfill full universe's price history (run fetch_index_membership.py first)
python src/backfill_prices.py --years 5
# safe to re-run if interrupted -- it resumes via data/backfill_checkpoint.json

# Corporate announcements -- confirmed against live NSE data
python src/fetch_corporate_announcements.py
# no args -- defaults to today only (nightly-run friendly)
# --years 5 for a one-time historical backfill, or --from-date/--to-date for a custom range

# Financial results -- UNVERIFIED, see status note in the script itself
python src/fetch_financial_results.py --from-date 01-04-2026 --to-date 13-07-2026

# Shareholding pattern -- confirmed against live NSE data
python src/fetch_shareholding_pattern.py --symbols RELIANCE TCS INFY
# omit --symbols to fetch the full Nifty 500 universe from index_membership instead
# (this is a quarterly filing -- not included in run_nightly.sh by default,
# since hitting 500 symbols every night for data that changes ~4x/year is
# wasted load; run it periodically e.g. weekly instead)

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

- **2026-07-13**: Confirmed `fetch_shareholding_pattern.py` working
  end-to-end on a live run (after the `data/nifty_pipeline.db` local
  schema fix — see below). Also made `fetch_daily_prices.py`'s
  `--from-date`/`--to-date` optional (default to today, matching
  `corporate_announcements`), added a `--years` shortcut for one-time
  backfills of a symbol subset, and simplified `run_nightly.sh`
  accordingly (no more explicitly passing today's date). Deliberately did
  *not* add `fetch_shareholding_pattern.py` to `run_nightly.sh` — it's a
  quarterly filing, so hitting 500 symbols nightly for data that rarely
  changes is wasted NSE load; run it periodically (e.g. weekly) instead.
- **2026-07-13**: Migrated the local `data/nifty_pipeline.db`'s
  `shareholding_pattern` table to the current schema (`record_id` primary
  key) by dropping and recreating just that table — it had 0 committed
  rows at the time (a run had crashed mid-upsert against the old schema),
  so nothing was lost. `corporate_announcements` (810k+ rows from a
  `--years 5` backfill) and the other tables were already on the current
  schema and untouched. Note for future schema changes: `CREATE TABLE IF
  NOT EXISTS` never retrofits an existing table — a table with real data
  and a changed schema needs a manual migration, not just a redeploy.
- **2026-07-13**: Fixed `fetch_shareholding_pattern.py`'s `ENDPOINT_URL`
  using a real curl capture — the path (`/api/corporate-share-holdings-master`)
  was correct all along, the bug was a missing `index=equities` query
  param, which is why it previously 200'd with a non-JSON body instead of
  the real data. All pieces (field names, values, endpoint) are now
  confirmed; next step is a live run to verify end-to-end.
- **2026-07-13**: Confirmed `shareholding_pattern`'s field names AND
  values against a real HDFCBANK row (see "What's actually been tested"
  above). Schema changed: primary key is now `record_id` (NSE's own
  unique row id) instead of symbol+disclosure_date+period_end_date, and a
  new `isin` column captures it. Still can't run this end-to-end — the
  real XHR endpoint URL is still missing, and a live run already confirmed
  the guessed one is wrong.
- **2026-07-13**: Fixed `fetch_corporate_announcements.py --years 5`
  timing out (`Read timed out (read timeout=15)`) on a live run — a
  single request for 5 years of market-wide announcements was too much
  for NSE to return in time. Now chunks any date range into <=90-day
  windows, upserting after each chunk (so one slow/failed chunk doesn't
  lose progress on the rest) and using a 30s per-chunk timeout. The
  single-day nightly case is unaffected (still exactly one request).
- **2026-07-13**: `fetch_corporate_announcements.py`'s `--from-date`/
  `--to-date` are now optional (default to today only, matching the
  nightly use case), plus a `--years` shortcut for a one-time historical
  backfill (e.g. `--years 5`) — no more being forced to type explicit
  dates on every nightly run. Added it to `run_nightly.sh` now that it's
  confirmed working live.
- **2026-07-13**: Confirmed live from a terminal: `fetch_daily_prices.py`
  and `fetch_corporate_announcements.py` both work end-to-end against real
  NSE data (6109 announcement rows, 499 price rows for 2 symbols over a
  year). `fetch_shareholding_pattern.py` does not — it 200s but returns a
  non-JSON body, confirming `ENDPOINT_URL` is a wrong guess; added a
  raw-body diagnostic on JSON-decode failure to help pin down the real
  endpoint once we get a DevTools capture.
- **2026-07-13**: Made the symbol list dynamic wherever it was hardcoded.
  Moved `get_universe()` from `backfill_prices.py` into `db.py` (avoids a
  circular import between `backfill_prices.py` and `fetch_daily_prices.py`).
  `fetch_daily_prices.py` and `fetch_shareholding_pattern.py` now treat
  `--symbols` as optional — omit it and they pull the full Nifty 500
  universe from `index_membership` instead. Simplified `run_nightly.sh`
  accordingly (dropped the inline Python snippet that built `--symbols`
  manually — `fetch_daily_prices.py` does that itself now).
- **2026-07-13**: Fixed `fetch_surveillance.py`'s ASM parsing using the
  raw-response diagnostic added earlier today. Real shape is nested by
  category (`{"longterm": {"data": [...]}}`), not flat — `parse_asm()`
  updated and verified against the real payload. GSM's `parsed 0 flagged
  symbols` was not a bug (NSE currently has 0 GSM flags, response is a
  genuinely empty `[]`); corrected an inaccurate "confirmed" claim about
  GSM's item field names that predates this session — they remain
  unconfirmed until a non-empty GSM response is seen. Also clarified: the
  "errors" reported for `fetch_daily_prices.py`, `fetch_corporate_announcements.py`,
  and `fetch_shareholding_pattern.py` were argparse rejecting a run with
  no CLI arguments (from VSCode's Code Runner, which doesn't pass any) —
  not script bugs. Those need to be run from a terminal with real
  `--symbols`/`--from-date`/`--to-date` args, per the Usage section below.
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
