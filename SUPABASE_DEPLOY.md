# Supabase + Render — deployment config

What changed from the original SQLite version, and the exact steps to stand
this up. Table names, columns, and every fetch script's CLI flags are
**unchanged** — only the connection layer and file locations moved.

---

## 1. What changed in the code

| File | Change |
|---|---|
| `src/core/db.py` | `sqlite3.connect()` → `psycopg2.connect(SUPABASE_DB_URL)`, wrapped in `_PGConnection` so `conn.execute(...)`/`conn.executemany(...)` still work exactly like sqlite3's did (translates `?` → `%s` under the hood). No other script needed to change. |
| `schema.sql` | Unchanged structurally — verified Postgres-compatible as-is (no `PRAGMA`/`AUTOINCREMENT`/SQLite-only functions anywhere in it). Only the header comment was updated. |
| `requirements.txt` | Added `psycopg2-binary`, `python-dotenv`. |
| `run_nightly.sh` / `run_periodic.sh` / `run_historical_seed.sh` | Updated script paths for the new `src/<stage>/` layout, added `export PYTHONPATH="$(pwd)/src"`, and now run from repo root instead of `cd src` first. `run_periodic.sh` now also sweeps `fetch_shareholding_pattern.py` (previously only ran at seed time — see README §8 note). |
| `data/nifty_pipeline.db` | Deleted — no longer used, all storage is in Supabase now. `data/backfill_checkpoint.json` still exists locally (it's a resume-pointer for `backfill_prices.py`, not DB data). |

One behavior note: `screenerScraper.py` caches auth tokens to a relative `./tokens/` folder. Previously this resolved to `src/tokens/` (scripts ran from inside `src/`); now scripts run from the repo root, so it'll create `tokens/` at the repo root instead. Harmless, just flagging it so it's not a surprise.

---

## 2. Supabase setup

1. Create a project at supabase.com.
2. Go to **SQL Editor** → paste the full contents of `schema.sql` → run once. This creates all 14 tables.
3. Go to **Project Settings → Database → Connection string → URI**. Copy the **Session pooler** string (host contains `pooler.supabase.com`, port `5432`), not the direct connection — cron jobs open short, one-off connections per run, which the pooler handles better than a direct connection limit.
4. That string is your `SUPABASE_DB_URL`.

---

## 3. Render setup

1. Push this repo to GitHub (the `.gitignore` already excludes `data/`, `logs/`, `.env`).
2. **New → Cron Job** (first one):
   - Build command: `pip install -r requirements.txt`
   - Command: `./run_nightly.sh`
   - Schedule: `0 21 * * 1-5`
   - Environment variable: `SUPABASE_DB_URL` = (from step 2.3 above)
3. **New → Cron Job** (second one):
   - Same build command.
   - Command: `./run_periodic.sh`
   - Schedule: `0 22 * * 0`
   - Same `SUPABASE_DB_URL` env var.
4. **One-time seed**: open the Shell tab on either service (or run locally with `SUPABASE_DB_URL` exported) and run `./run_historical_seed.sh`. This takes hours (≈500 symbols × several scripts with rate-limit sleeps) — let it finish, then check the row-count summary it prints at the end.
5. If you want the seed's row-count summary to work, make sure `postgresql-client` (`psql`) is installed in the Render environment — add `apt-get install -y postgresql-client` to the build command, or skip it and just check row counts in Supabase's Table Editor instead.

---

## 4. Local testing before you deploy

```bash
cp .env.example .env        # fill in your real SUPABASE_DB_URL
pip install -r requirements.txt --break-system-packages
export PYTHONPATH="$(pwd)/src"
python3 src/metadata/fetch_index_membership.py   # should populate index_membership and exit 0
```

If that runs clean, `run_nightly.sh` and `run_periodic.sh` will too.
