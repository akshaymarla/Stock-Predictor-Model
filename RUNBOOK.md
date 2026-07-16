# RUNBOOK — how the files run

`README.md` tracks *what's been built and confirmed*. This file tracks
*when things are supposed to run* — the operational side. If you're asking
"do I need to pass `--symbols`?" or "how often should this fire?", it's
answered here.

## The short version

Every script below already defaults to the full Nifty 500 universe when you
omit `--symbols` — that part of "going dynamic" is done at the code level.
What's still manual is *scheduling*: nothing currently runs itself. This
doc is the plan for closing that gap.

## Per-script dynamic status

| Script | Symbols | Dates | Notes |
|---|---|---|---|
| `fetch_index_membership.py` | N/A — whole market | N/A | No args needed at all |
| `fetch_surveillance.py` | N/A — whole flagged list | N/A | No args needed at all |
| `fetch_daily_prices.py` | Optional, defaults to full universe | Optional, defaults to today | `--years N` available for a manual backfill of a symbol subset |
| `fetch_corporate_announcements.py` | N/A — whole market | Optional, defaults to today | `--years N` available for backfill (chunked into 90-day windows) |
| `fetch_shareholding_pattern.py` | Optional, defaults to full universe | N/A — NSE always returns the latest filing | |
| `fetch_financial_results.py` | Optional, defaults to full universe | N/A — screener.in returns all available history every call | `--consolidated-only` / `--standalone-only` to halve request volume |
| `fetch_balance_sheet.py` | Optional, defaults to full universe | N/A | same flags as above |
| `fetch_cash_flow.py` | Optional, defaults to full universe | N/A | same flags as above |
| `fetch_ratios.py` | Optional, defaults to full universe | N/A | same flags as above |
| `backfill_prices.py` | Dynamic via `index_membership` | **Requires `--years` or `--from-date`** | Intentionally manual — this IS the one-time seeding tool, not meant to run unattended |

## Staged plan

### Stage 1 — One-time seeding (manual, run once per fresh DB)

Order matters: `index_membership` has to populate the universe before
anything else can default to it.

```bash
cd src
python3 fetch_index_membership.py
python3 backfill_prices.py --years 5
python3 fetch_corporate_announcements.py --years 5
python3 fetch_shareholding_pattern.py
python3 fetch_financial_results.py
python3 fetch_balance_sheet.py
python3 fetch_cash_flow.py
python3 fetch_ratios.py
```

Not recurring — this seeds history. Re-run only if you tear down and
rebuild the DB, or want to backfill further back than the original `--years`.

### Stage 2 — Nightly automation (daily-cadence data)

`run_nightly.sh` already chains the right scripts: `index_membership`
refresh, `surveillance_flags`, latest day's `daily_prices`, today's
`corporate_announcements`. Needs an actual cron/launchd entry — as of
2026-07-15 this is still run by hand.

Open question: exact fire time. A 9pm IST attempt already showed NSE's
historical price data isn't reliably live that early (see `fetch_daily_prices.py`'s
known-gotcha note) — needs one more live test to find a time that
consistently works, or a retry-with-backoff wrapper.

### Stage 3 — Periodic automation (quarterly-cadence data) — built 2026-07-16

`financial_results`, `balance_sheet`, `cash_flow`, `ratios` change ~4x/year,
but different companies report on different days spread across a ~6-week
results season each quarter. Two-part design, both pieces built and tested
live against real June 2026 quarter-end results:

1. **Reactive, nightly** (`src/trigger_quarterly_refetch.py`, wired into
   `run_nightly.sh`): each night, scans that day's `corporate_announcements`
   for a results-disclosure using the same two patterns
   `screener_common.find_disclosure()` already validated (`Outcome of
   Board Meeting` + details mentions results, or `Financial Result(s)
   Updates` directly), and re-fetches just the matched symbols across all
   4 tables. Considered using the earlier "Board Meeting Intimation"
   (advance notice, ~1-3 week lead time) instead, but confirmed live it's
   too sparse to rely on (376 rows vs. 51,048 "Outcome of Board Meeting"
   rows in the 5-year corpus) — most companies don't file a matching
   intimation subject. Tested live 2026-07-16 against the real June'26
   quarter catch-up window (01-06-2026 to 16-07-2026): correctly
   identified 13 symbols with a genuine results disclosure, 12 of which
   we'd already captured via the full-universe pull and 1 (`ITI`)
   genuinely new. Two symbols already in our data (`LTTS`, `TATAELXSI`)
   for the same period were NOT triggered — checked and confirmed
   consistent, not a bug: both have `disclosure_date = NULL` for that
   period (the NULL-capture fallback), meaning we never had a confirmed
   announcement match for them in the first place, so the trigger
   correctly can't find one either.
2. **Full-universe safety net** (`run_periodic.sh`, standalone, NOT called
   from `run_nightly.sh`): sweeps all 4 scripts across the entire universe.
   Catches whatever the reactive trigger misses — a disclosure subject
   pattern not yet discovered, a transient fetch failure, a
   `corporate_announcements` gap that day. Idempotent upsert makes
   re-running against companies with nothing new cheap (just re-confirms).
   Suggested cadence once scheduled: weekly during results season
   (~6 weeks after each quarter-end), monthly otherwise — not yet
   scheduled, same as everything else in this doc.

Resolved 2026-07-16 (per Akshay's own knowledge of screener.in's typical
behavior, not an independently-verified live test): screener.in usually
updates the same day a company announces results, so the nightly trigger
isn't racing a stale-data window in the way originally worried about. Even
if capture lags the actual announcement by up to about a week in some
edge case, that's an accepted tolerance, not a problem to design around —
the nightly trigger (worst case: next-day capture) plus the periodic
safety-net sweep (`run_periodic.sh`) already comfortably sit inside that
bound. No design change needed as a result of this.

### Stage 4 — Infrastructure, deferred

Cron only fires if the machine is awake at the scheduled time — a missed
nightly run is a real gap, since `fetch_daily_prices.py`'s default is
today-only, not a catch-up range (recovery: re-run with an explicit
`--from-date` covering the missed days). A "runs itself" version of this
pipeline eventually wants: (1) the Postgres migration already on the radar
(see `README.md`'s "Known gaps"), and (2) an always-on execution
environment instead of a personal laptop's cron. See discussion in
`README.md` changelog / conversation history for the tradeoffs considered
(small VPS vs. scheduled GitHub Actions vs. managed cron services).

## Rate-limit discipline (screener.in-sourced scripts)

`financial_results`/`balance_sheet`/`cash_flow`/`ratios` all hit
screener.in, which throttles aggressive scraping (confirmed live — see
`README.md` changelog 2026-07-15). Every symbol does up to 2 page loads
(consolidated + standalone) plus addon calls each. Rules of thumb:
- Default `--sleep` is 2s between requests; raise it for large/unattended runs.
- `--consolidated-only` / `--standalone-only` halves request volume when
  you don't need both views.
- A full-universe run across all four scripts is realistically a multi-hour
  job, not a quick check — plan for that when scheduling Stage 3.
