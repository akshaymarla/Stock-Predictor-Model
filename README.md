# Nifty Alpha Pipeline — Step 1: `daily_prices` + `surveillance_flags`

The first two tables from the schema: the ones with clean, reliable free
sources. Everything here uses SQLite (`data/nifty_pipeline.db`) so there's
zero setup — swap for Postgres later without touching the logic.

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

## Usage

```bash
# Daily prices — start small to sanity-check before scaling to 500 symbols
python src/fetch_daily_prices.py \
    --symbols RELIANCE TCS INFY HDFCBANK \
    --from-date 01-01-2024 --to-date 31-12-2024

# Surveillance flags (ASM/GSM) — no symbol list needed, pulls the whole flagged list
python src/fetch_surveillance.py
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
