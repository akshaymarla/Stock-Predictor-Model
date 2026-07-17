# Nifty Alpha Pipeline — Project Context for Claude Code

## What this is
A data pipeline for a quantitative Nifty 500 stock-screening project. The
end goal (not yet started) is a model that outputs a probability of a
stock beating the Nifty index over a monthly or 2-week holding period.
This repo is currently just the **data layer** — getting clean, trustworthy
data in before any modeling starts.

## Before making changes
1. Read `README.md` — it has a status table (what's confirmed working vs.
   untested vs. not started) and a changelog. Don't trust your own
   assumptions about what's done; check there first.
2. `schema.sql` is authoritative for table structure. If you change a
   table, update `schema.sql` first, then the code that writes to it.
3. **After any code change, update `README.md`**: add a changelog entry
   (date + what changed) and update the status table if a table's status
   changed (e.g. "untested" -> "confirmed working"). This is a hard
   requirement, not optional — the person working on this relies on the
   README as the running log of progress, especially since work happens
   across both this tool and a separate claude.ai conversation that
   doesn't share context with you.

## Non-negotiable architectural principle: point-in-time correctness
Every table must be queryable "as of" a date without leaking future
information. Concretely:
- Fundamentals/announcements/shareholding data must key off the date the
  market actually learned the information (`announcement_date`,
  `disclosure_date`, `fetched_at`), never the period-end date
  (`quarter_end_date`). A join for "what did we know as of date D" must
  filter `knowledge_timestamp <= D`, using the most recent record.
- `index_membership` is snapshot-based (records what the index looked
  like on each fetch date) — it does NOT yet solve historical
  reconstruction (pre-today membership). Don't assume it does.
- Never build a feature or label using information that wouldn't have
  been known at prediction time. If you're unsure whether something
  leaks, flag it rather than guessing.

## Data sources — status and known gaps
See the status table in `README.md` for the current source of truth. In
general:
- `daily_prices`, `surveillance_flags` (ASM/GSM): confirmed working
  against live NSE data. Field names were verified via real DevTools
  responses, not guessed.
- `index_membership`, `corporate_announcements`: built but the live NSE
  endpoint/field names are not yet fully confirmed. If a fetch script
  returns 0 rows or errors, the likely cause is NSE's response shape not
  matching what's hardcoded — check the "STATUS"/"HONESTY NOTE" comment
  at the top of the relevant script for how to fix it (grab a real
  response via browser DevTools rather than guessing).
- `financial_results`, `shareholding_pattern`: not started.
- NSE's site requires session cookies before `/api/*` endpoints respond
  (cold requests get 401/403) — see `make_session()` in the existing
  fetch scripts for the pattern to reuse.

## Conventions used so far
- SQLite for now (`data/nifty_pipeline.db`), swappable for Postgres later.
- Every fetch script: parse -> normalize -> **idempotent upsert**
  (`ON CONFLICT ... DO UPDATE`), so nightly re-runs never duplicate rows.
- Fetch scripts are standalone (`python src/fetch_x.py`), not a package —
  imports are flat (`from db import get_conn`), run from inside `src/`.
- Long-running/batch operations (see `backfill_prices.py`) checkpoint
  progress to `data/*.json` so an interrupted run resumes instead of
  restarting.
- `data/*.db` and checkpoint files are gitignored — don't commit them.

## What NOT to do without asking
- Don't invent NSE/BSE API field names and present them as confirmed —
  be explicit when something is a best guess pending live verification
  (follow the existing pattern in `fetch_corporate_announcements.py`).
- Don't remove the point-in-time discipline for convenience (e.g. joining
  on `quarter_end_date` instead of `announcement_date` because it's
  simpler) — this is the one thing that silently corrupts everything
  downstream.
