# Nifty Alpha Pipeline

Data pipeline for the Nifty 500 stock-screening project. Every code change
gets a changelog entry below so this stays an accurate running log of
progress — check the bottom of this file for the latest status.

## Tables implemented so far

| Table | Script | Status |
|---|---|---|
| `daily_prices` | `src/fetch_daily_prices.py` (nightly), `src/backfill_prices_via_bhavcopy.py` (bulk) | **Corruption fully remediated 2026-07-19** -- 556,800 clean rows (539/539 symbols, 2021-07-13 to 2026-07-16), verified via independent full-table scan (zero unexplained anomalies remaining, 74 residual jumps all confirmed genuine corporate actions) plus a fresh coverage re-check 2026-07-21 (docs/confirm_and_reconcile.md Part A) -- low-row-count symbols all trace to real 2024/2025 IPOs or delistings/mergers, not gaps. See changelog for the full investigation: root cause, the switch to a bhavcopy-based bulk backfill, and two further bugs found and fixed along the way (bogus non-trading-day rows, an over-strict EQ-only series filter). Automated single-day-jump sanity check (`check_price_jump_anomalies()`) confirmed wired into `run_nightly.sh`'s execution path via `fetch_daily_prices.py`'s `main()` -- `run_nightly.sh` itself is still run by hand, not on a cron schedule yet (a separate, deliberate decision, see the script's own header comment). Audit columns (`source`/`fetched_at`) confirmed 99.98% populated after a 2026-07-21 fix closed a gap in `backfill_price_gaps.py`'s upsert (it never set them) -- 91/556,800 rows (0.016%) still show NULL, isolated single symbol/date pairs not yet individually diagnosed, data itself unaffected (already covered by the anomaly scan). Also: ~300 fully-missing trading days found and fixed 2026-07-16 via `src/backfill_price_gaps.py`; 2 rare dates remain unfilled by design |
| `surveillance_flags` | `src/fetch_surveillance.py` | Working (ASM confirmed + fixed against live NSE data; GSM wrapper shape confirmed, item field names unconfirmed pending a non-empty response) |
| `index_membership` | `src/fetch_index_membership.py` | Working (confirmed against a real niftyindices.com CSV; current-snapshot only, see caveat below) |
| `corporate_announcements` | `src/fetch_corporate_announcements.py` | Working (confirmed against live NSE DevTools response). `category`/`sentiment` columns added 2026-07-19 and populated for real via `src/classify_announcements_by_subject.py` -- `subject` turned out to already be NSE's own SEBI Reg. 30 structured category tag, so this is a free deterministic mapping, not a classifier. 269,056 training-universe rows classified (2.0% positive, 1.0% negative, 97.0% neutral). Feature-level use retired from the model after SHAP re-check (see changelog) -- data itself remains real and available. `src/classify_announcements.py` (LLM path) built but unused, blocked on API credentials, not needed given the free result |
| `financial_results` | `src/fetch_financial_results.py` | Working, full-universe pull confirmed 2026-07-15 (498/500 symbols, 12,039 rows) — quarterly only; alias-based mapping for company-template variance; disclosure_date confirmed for 48.6%, rest captured with disclosure_date=NULL (see below). **Two real data-quality issues found and FIXED 2026-07-20** (`next_phase_plan.md` Section 0c) while verifying `weekly_shortlist.py` output against real financials: (1) 127/498 symbols had a confirmed disclosure_date >180 days stale (46/498 had none at all), sometimes stuck years in the past (FORTIS/BHARATFORG last confirmed 2023) while real, more recent quarters sat unmatched — fixed with a 240-day staleness cutoff in `assemble_feature_matrix.py`, past which the value is treated as NULL rather than current; (2) STANDALONE vs CONSOLIDATED selection was an undocumented SQL-tie-order artifact (89% of confirmed-disclosure rows have both scopes filed on the same date) — fixed to deterministically prefer CONSOLIDATED. Same two fixes also apply to `balance_sheet`/`cash_flow`/`ratios` (identical `result_type` column, same join pattern). `model_feature_matrix` rebuilt from scratch on the fix; every downstream model/backtest re-run — see changelog for the full diff |
| `balance_sheet` | `src/fetch_balance_sheet.py` | Working, full-universe pull confirmed 2026-07-15 (498/500 symbols, 5,057 rows); disclosure_date confirmed for 30.7% (mostly annual-cadence reporting, see below) |
| `cash_flow` | `src/fetch_cash_flow.py` | Working, full-universe pull confirmed 2026-07-15 (496/500 symbols, 5,024 rows); disclosure_date confirmed for 30.9% |
| `ratios` | `src/fetch_ratios.py` | Working, full-universe pull confirmed 2026-07-15 (496/500 symbols, 4,982 rows); disclosure_date confirmed for 31.2% |
| `shareholding_pattern` | `src/fetch_shareholding_pattern.py` | Working (confirmed end-to-end against live NSE data; dynamic universe, quarterly cadence so not in nightly by default) |
| `macro_regime_indicators` | `src/fetch_macro_sector.py` | Working (confirmed live 2026-07-16 -- NIFTY 50/India VIX closes + rolling returns spot-checked against raw source data). First table of the macro/sector shock feature set (`macro_sector_shock_features.md`) -- see changelog for a real sourcing pivot away from the original design doc's plan |
| `sector_membership` | `src/fetch_sector_membership.py` | Working (confirmed live 2026-07-16 -- all 15 sector constituent CSVs resolved, 249 rows, spot-checked against real symbols e.g. HDFCBANK correctly in Bank+Financial Services+Private Bank, RELIANCE in Energy+Infrastructure+Oil & Gas). Current-snapshot only, same caveat as `index_membership` |
| `sector_daily_benchmarks` | `src/fetch_macro_sector.py` | Working (confirmed live 2026-07-16 -- sector closes for all 15 sectors spot-checked exactly against raw source data; `sector_relative_alpha_14d` internally consistent across every sector for a given date). Sourced from the same daily snapshot as `macro_regime_indicators`, zero extra requests |
| `model_target_labels` | `src/compute_target_labels.py` | Working (rebuilt from scratch 2026-07-19 on clean `daily_prices` -- 547,965 rows across 539 symbols). Forward-looking TRAINING LABELS ONLY -- never join into the feature side of a training matrix |
| `model_feature_matrix` | `src/assemble_feature_matrix.py` | Working (rebuilt from scratch 2026-07-20 on clean `daily_prices` + the 0c tie-break/staleness fixes -- 556,778 rows, matches daily_prices exactly; fundamentals join verified zero look-ahead leakage). FEATURES ONLY -- sector_* columns are currently 0/NULL for all historical rows, a known accepted limitation (see changelog), not a bug. Includes `sh_inst_*` institutional-attention block (level + QoQ/YoY trend, ~95% coverage). `recent_order_dispute_flag_30d` retired 2026-07-19, replaced by `recent_negative_catalyst_flag_30d`/`recent_positive_catalyst_flag_30d` (LLM-sourced) -- both currently all-zero pending the real classification run, see changelog. `fin_net_profit` non-null count dropped 289,677 → 254,432 after the staleness-cutoff fix -- expected and correct (previously-wrong stale values now correctly NULL rather than silently trusted) |
| `shareholding_institutional_breakdown` | `src/fetch_institutional_breakdown.py` | Working, full universe fetched and verified (confirmed 2026-07-19: 14,252/14,252 rows have `total_institutional_pct` populated, 0 rows outside the valid [0,1] range, 0 unclassified category names). Parses the XBRL filing `shareholding_pattern.attachment_url` already points to -- not a new data source. Went through 3 real bugs (scale normalization, category-mapping coverage across XBRL eras, BSE's taxonomy-specific total anchor) -- see changelog for all three |
| `models/shortlists/*` (not a DB table) | `src/weekly_shortlist.py` | Working, ran end-to-end 2026-07-20 -- production model (trained on all history minus a reserved calibration tail) scores today's eligible universe, ranks top-N, attaches real per-stock SHAP explanations. Machine + human-readable output, gitignored (per-run, not meant to survive rebuilds; compact archive summary in `models/reports/archive/` is what persists). See changelog for two bugs found and fixed during first real run |
| `tracked_picks` | `src/log_shortlist_picks.py` (write, wired into `weekly_shortlist.py`), `src/resolve_tracked_picks.py` (resolve, wired into `run_nightly.sh`) | **New 2026-07-21** (`tracking_dashboard_spec.md`) -- turns the weekly shortlist into a genuine out-of-sample track record. Confirmed working end-to-end: a real `weekly_shortlist.py` run (both horizons, top-20) logged 40 real rows (pick_date 2026-07-15, entry prices spot-checked exactly against `daily_prices`); resolution logic spot-checked separately against two synthetic picks (RELIANCE @ 2026-06-01/14d resolved with `actual_alpha`/`actual_stock_return`/`actual_nifty_return` matching `model_target_labels`' `alpha_14d`/etc. exactly, confirming it reuses `compute_target_labels.py`'s math rather than reimplementing it; CADILAHC @ 2022-02-22/14d correctly produced `delisted_during_hold` since its 14-trading-day window runs past its real 2022-03-04 delisting) -- both test rows deleted afterward, not left in the real table. All 40 real rows are currently `open` (too soon to resolve). `calibrated_prob_at_pick`/`top_factors_json` are frozen at insert time, never rewritten. `target_close_date` stored at insert time is a weekend-adjusted calendar-day *estimate* (the real trading calendar doesn't extend into the future yet) -- `resolve_tracked_picks.py` overwrites it with the exact trading-day date at resolution |
| `models/reports/tracking_dashboard.html` (not a DB table) | `src/generate_tracking_dashboard.py` | **New 2026-07-21** -- single self-contained HTML file (Chart.js via CDN), regenerated on demand. Confirmed rendering correctly against both an empty table and the real 40-row state (open-position day-by-day trend charts, hit-rate-by-calibration-bucket section reusing `evaluate.calibration_curve()`, sortable resolved-picks table, delisted section) -- hit-rate/resolved/delisted sections have no real data to show yet since nothing has resolved. Gitignored under the existing `models/reports/*` rule; the DB table is the persistent source of truth |
| `frontend/` (not a DB table) | `src/export_screener_data.py` (data), `frontend/index.html`+`styles.css`+`app.js` (UI) | **New 2026-07-22** (`frontend-screener-spec.md`) -- plain HTML/CSS/JS screener UI (ticker header + 14D/30D model picker + ranked card deck), per the spec's own stack-agnostic reference implementation since this repo has no existing frontend stack. **Candidate scope corrected same day**: first shipped as the full ~440-name eligible universe (the spec's own Section 9 Q4 was initially resolved that way), but confirmed by real use to be unscannable -- reverted to each horizon's actual top-20 selection (matching `weekly_shortlist.py`'s own established top-N convention), computed as the union of both horizons' top-20 (35 unique names in the real run, 5 overlap) so a stock can still legitimately appear on only one horizon's tab. Both horizon models still score the full eligible universe internally (`export_screener_data.py` reuses `weekly_shortlist.py`'s `train_production_model()`/`load_universe()` directly, skips its SHAP pass since the card UI doesn't show per-stock factors) -- exporting/ranking is what's now scoped down, not the underlying scoring, so rankings stay exact. The frontend re-slices to this horizon's own top-20 client-side (`app.js`) rather than showing the full union on every tab -- provably correct since a stock genuinely in a horizon's true top-20 is always in the union by construction, so sorting the union by that horizon's probability and taking the top 20 reproduces the true top-20 exactly. Real re-run's top-5-by-horizon matched the earlier standalone `weekly_shortlist.py` run's output exactly (ICICIAMC/SAILIFE/SAMMAANCAP/INDUSTOWER at 68.8% for 14d; HINDPETRO/RKFORGE at 77.8% for 30d) -- `data.js` shrank from 130KB/440 candidates to 11KB/35. `model_meta.status`/notes are derived for real from `tracked_picks`' live hit rate (reuses `generate_tracking_dashboard.py`'s calculation) -- currently `"provisional"` since 0 picks have resolved yet, not hardcoded. `is_stale` correctly showed `true` in the real output since the pipeline's confirmed scoring date (2026-07-15) lags real "today" (2026-07-22, nightly hasn't run since). Data file (`frontend/data.js`) is a plain `<script src>` include, not a `fetch()`'d JSON file -- deliberately, so the page still opens directly with zero server (file:// pages can't `fetch()` JSON in most browsers) -- and is gitignored/regenerated on demand, same status as `models/shortlists/*`. HTML/CSS/JS structurally validated (balanced tags/braces/parens) and visually confirmed loading correctly in Safari (page title + DOM confirmed via URL query) -- full interactive click-through not automated, user asked not to proceed with the JS-injection approach tried for that; visual/interaction spot-check by eye is still recommended before treating this as fully confirmed |
| `frontend/` v2 restructure | `src/export_screener_data.py` (+tracking/universe_snapshot), `frontend/*` (sidebar shell) | **New 2026-07-22** (`frontend-spec-v2-sidebar-nav.md`) -- renamed to "Nifty Alpha Predictor," restructured the single-screen build behind a left sidebar into 4 sections (Home, Overview, Statistical View, Other Stocks — To Compare), reusing the existing Overview render logic unchanged. **Key interpretation call**: the Statistical View's day-by-day tracking table is built from the REAL `tracked_picks` table (the actual picks `weekly_shortlist.py` already logged, currently pick_date 2026-07-15/20 names per horizon), not a fresh top-N recomputed by `export_screener_data.py` each run — the spec's own text ("matches the out-of-sample tracking just started note already in `data.model_meta.notes`") points directly at `tracked_picks`, and a fresh-every-run set would just reset to all-nulls on every export instead of accumulating real history. `trading_days` columns use real trading-calendar dates for whatever's actually elapsed and the same weekend-adjusted estimate convention as `log_shortlist_picks.py` for future columns, so the table shape never changes as real days fill in. Confirmed in the real export: 14 columns, only D+0 has real data (`close`/`day_change_pct` populated), D+1 onward correctly `null` — expected, since `scoring_date` is by definition the newest confirmed day. The Compare section's full-Nifty-500 lookup (`universe_snapshot`, `frontend/universe.js`) reuses `backtest.py`'s `price_at_or_before()`/`load_price_lookup()` directly for every lookback (trading-day-based for d3/d7/d14, calendar-month/year with prior-trading-day fallback for m1/m6/y1, per the spec's own stated convention) — real output's `change_pct_vs_last` formula verified against the spec's own worked RELIANCE example (both give 1.24%/5.81% for the same inputs). `universe.js` (500 stocks) is lazy-loaded only when Compare is first opened, not bundled into the always-loaded `data.js`, per the spec's own build-time-decision note. All open questions in the spec's Section 8 resolved using the spec's own stated defaults (calendar lookback w/ fallback for 1M/6M/1Y, 2-stock-max compare, all top-N rows shown in Statistical View, same export script/cadence for the universe snapshot, "Go to Overview" CTA included) — none required asking the user. Structurally validated (balanced HTML/CSS/JS) and opened successfully in the browser; full interactive click-through across all 4 sections not automated this round (see the note on the v1 row about declined browser-automation) |
| `models/lots/*` (not a DB table) | `src/freeze_lot.py` (freeze), `src/export_screener_data.py` (publish latest 3 to site) | **New 2026-07-22** — introduces "lots": each `weekly_shortlist.py` run's picks, frozen permanently and shown as a distinct, selectable batch (per direct instruction: make a real first cut tonight, freeze it, never silently overwrite). `freeze_lot.py` reads the latest `tracked_picks.pick_date`, and if `models/lots/lot_<N>_<pick_date>.json` doesn't already exist, builds it — candidates pulled straight from `tracked_picks.calibrated_prob_at_pick` (never re-scored) enriched with company/price/52w-range metadata *as of pick_date* (not "today"), plus a day-by-day tracking table via the same logic the v2 Statistical View already used. Idempotent by pick_date — re-running `weekly_shortlist.py` same-day (itself a no-op) can never double-freeze or overwrite a live lot. `export_screener_data.py` was correspondingly stripped of ALL live model scoring (that only ever happens inside `weekly_shortlist.py` now, on the user's own explicit cadence) — it just reads back whatever's in `models/lots/` and embeds the most recent `MAX_SITE_LOTS=3`; real re-run dropped from ~2 minutes to ~14s CPU with the training removed. `models/lots/` itself is never pruned (gitignored, kept forever locally); only the site's exposure is capped at 3. Frontend: both Overview and Statistical View now open on a lot-picker screen (labeled by real incrementing lot number + `pick_date`, e.g. "LOT 1 — MODEL RUN: 15 Jul 2026", not repositioned "1st/2nd/3rd" text, so labels stay stable once an old lot rolls off) before the existing horizon picker; the selected lot's number/model-run-date is shown on the banner, deck header, and stat table header. Retroactively froze the existing `tracked_picks` data (pick_date 2026-07-15) as **Lot 1**; verified `export_screener_data.py`'s rebuilt `data.js` matches it exactly (rank 1 ICICIAMC @ 68.8% for 14D, matching the original `weekly_shortlist.py` run). Structurally validated; not yet click-through tested in a live browser session for the lot-picker flow specifically |

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
  response. Confirmed working live 2026-07-13 (RELIANCE/TCS, full-year
  range). **Known gotcha, also confirmed live**: a same-day-only request
  (the nightly default) fails for ~all symbols with a cryptic pandas error
  if run before NSE has published that day's data. Root cause is inside
  `jugaad_data` itself (crashes on an empty API response instead of
  handling it) — not fixable from our side, but `fetch_symbol()` now
  pre-checks and raises a clear, readable error instead of the confusing
  internal one. If `run_nightly.sh`'s 9pm IST slot still hits this
  regularly, NSE's data may not be live by then — try a later cron time.

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

- **`fetch_financial_results.py`, provenance note (added 2026-07-21,
  `docs/confirm_and_reconcile.md` Part B)**: screener.in (via vendored
  `src/screenerScraper.py`) has been the **only** `financial_results`
  data source this pipeline has ever actually built or run, confirmed by
  reading the full git history — there is no `fetch_financial_results_legacy.py`,
  no direct-from-NSE XBRL parsing path, and no prior README section
  describing one, at any commit. A legacy-XBRL-financials task was
  referenced as existing documentation to reconcile against, but no trace
  of it was found anywhere in this repo — flagged directly rather than
  fabricated a "historical" section for something that was never built
  here. (Real, separate XBRL parsing *does* exist in this project —
  `src/fetch_institutional_breakdown.py` parses `shareholding_pattern`'s
  XBRL filing, a different table entirely, see its own changelog entries
  for the taxonomy-era bugs found there — don't confuse the two.)
  **Below is the actual history**: completely re-architected 2026-07-14
  around the vendored `src/screenerScraper.py` (screener.in scraper,
  github.com/BuildAlgos/screener-scraper) instead of a guessed NSE endpoint.
  **Key finding from reading the real library**: screener.in's data has no
  disclosure/announcement timestamp at all — only the quarter-end date. An
  earlier draft of this integration defaulted unmatched quarters to
  `datetime.now()`, which would have silently stamped years of historical
  results as "disclosed today" — a real violation of this project's
  point-in-time rule, caught before it was ever run. The fix:
  `disclosure_date` is derived (in `src/screener_common.py`, shared by every
  screener.in-sourced script) by joining against our own confirmed-live
  `corporate_announcements` table for the earliest "financial result"
  announcement within 65 days of quarter-end (a SEBI-mandated disclosure
  window, not an arbitrary guess); if nothing matches, the quarter is
  **skipped and logged**, never defaulted. Also caught before running live:
  `quarterlyReport()` returns a plain `dict` (`{"2025-06-30": [{"Sales":
  100.0}, ...], ...}`), not a `pandas.DataFrame` as the first draft assumed.
  **Confirmed 2026-07-14 against a real live RELIANCE quarter**: most base
  metric keys carry a trailing non-breaking space the vendored library's own
  `.replace(" ", "")` doesn't strip (e.g. `'Sales\xa0'`), which was silently
  producing `NULL` values for `sales`/`expenses`/`other_income`/`net_profit`
  — fixed by normalizing keys centrally in
  `screener_common.flatten_periods()` instead of hardcoding the broken
  strings. Also fixed a casing mismatch (`Profitbeforetax`, not
  `ProfitbeforeTax`). The same real capture revealed a set of "addon" bonus
  fields that come free with `quarterlyReport(withAddon=True)` — YoY
  growth %, material/employee cost %, exceptional items, minority share,
  and the underlying filing PDF link — all now captured as extra columns.
  `pnlReport()` (annual P&L, same shape) is now also pulled into the same
  table with `period_type='ANNUAL'`. Tested end-to-end against the exact
  real payload plus synthetic data for the skip/no-match path,
  missing-BSE-token handling, and idempotent upsert.

  **2026-07-15 update — annual data removed, alias-based field mapping,
  rate-limit fixes.** A live multi-symbol run surfaced a real bug in the
  "pull annual too" design above: a Q4 quarter (ending March) and the
  annual/FY result share the *identical* period-end date, and this table's
  primary key is `(symbol, period_end_date, result_type)` — so
  `pnlReport()`'s annual row was silently overwriting the real Q4 quarterly
  row on upsert. Confirmed on the live DB: HDFCBANK and TCS had **zero**
  proper Q4 rows as a result. Fix: `pnlReport()`/annual data removed
  entirely from this script (quarterly only, no `period_type='ANNUAL'`
  possible anymore); local DB's 18 corrupted annual rows deleted, the
  underlying quarterly data is intact and will repopulate on the next run.
  Also confirmed live: different company templates use different row
  labels for the same concept (e.g. a bank may use "Revenue" where a
  manufacturer uses "Sales") — `METRIC_ALIASES` now maps each column to a
  list of candidate labels instead of one fixed string, and logs any
  unmapped keys to stderr when the primary revenue field doesn't match, so
  new aliases can be added from real data instead of guessed upfront. Also
  fixed the `TypeError: 'NoneType' object is not iterable` /
  `object of type 'NoneType' has no len()` crashes seen on a live run —
  root cause: `screenerScraper.py`'s `requestAPI()` returns `None` on a
  non-200 response (screener.in rate-limiting, confirmed by request-volume
  correlation — each symbol was firing up to 10 rapid requests with zero
  internal pacing), and the vendored library didn't guard against that.
  Patched two narrow `None` guards directly into `screenerScraper.py`
  (marked inline as deviations from upstream, documented in its header) so
  a rate-limit now raises a clear, catchable message instead of crashing.
  Also added real pacing (sleep between the consolidated/standalone views,
  not just between symbols, default raised to 2s) and
  `--consolidated-only`/`--standalone-only` flags to halve request volume
  when needed.

- **`fetch_balance_sheet.py`**, **`fetch_cash_flow.py`**, **`fetch_ratios.py`**
  (2026-07-14): same architecture and shared point-in-time logic as
  `fetch_financial_results.py` (via `src/screener_common.py`), but their
  `COLUMN_MAP`s are **unverified guesses**, not yet checked against a live
  scrape. Deliberately use `withAddon=False` for balance sheet and cash
  flow — traced through `screenerScraper.py`'s `__addonData()` and found
  that the addon endpoint dict keys (`Borrowing`, `TotalAssets`, etc.) only
  name request URLs, not actual returned fields (those are unconfirmed
  sub-line-item breakdowns) — so these two rely only on the base summary
  table, a smaller and more defensible guess. `ratios()` has no addon
  endpoint at all, making it the lowest-confidence of the four. Plumbing
  (build/upsert/skip-on-no-match) tested with synthetic data; field names
  need the same live-capture-then-fix round `fetch_financial_results.py`
  already went through — each script's docstring has the exact diagnostic
  command to run.

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

# Financial results (screener.in) -- quarterly only, confirmed working
python src/fetch_financial_results.py --symbols RELIANCE TCS
# omit --symbols for the full Nifty 500 universe from index_membership
# --consolidated-only / --standalone-only halves request volume if you hit rate limits
# --sleep N to widen pacing further (default 2s between requests)
# (quarterly cadence -- not in run_nightly.sh, same reasoning as shareholding_pattern)

# Balance sheet / cash flow / ratios (screener.in) -- UNVERIFIED, see status note in each script
python src/fetch_balance_sheet.py --symbols RELIANCE TCS
python src/fetch_cash_flow.py --symbols RELIANCE TCS
python src/fetch_ratios.py --symbols RELIANCE TCS
# omit --symbols for the full Nifty 500 universe from index_membership

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

- **2026-07-22 (real bug: `stock_df` dates were shifted -1 day; NSE's bhavcopy format changed; Lot 2 redone)**:
  User reported stock prices on the site looked like next-day values
  labeled under the wrong date. Investigated by cross-referencing against
  NSE's bhavcopy (the more authoritative source) and found two separate,
  compounding issues:
  1. **`fetch_daily_prices.py`'s `stock_df`/`NSE`-sourced fetches were
     uniformly dating every row one calendar day EARLIER than the true
     trade date.** Proven with hard evidence, not inference: pulled true
     bhavcopy closes for RELIANCE across 07-16→07-22 and matched them
     exactly against what was stored under 07-15→07-21 (identical
     values, one day offset, for every single date in the chain). This
     is a different, more systematic bug than the earlier-suspected
     "one Sunday-mislabeled row" — it affected every row from every
     `stock_df` fetch this session (both the plain nightly single-day
     fetch and the explicit `--from-date`/`--to-date` backfill used to
     patch that day's gap). The earlier same-night fix (renaming one
     row from a Sunday to what looked like the right weekday via chain
     continuity) turned out to have landed on the wrong date too — a
     symptom of not yet knowing the shift was uniform across the whole
     batch, not an isolated row.
  2. **NSE switched its CM bhavcopy file to a new unified/UDiFF-style
     schema starting 2026-07-21** (confirmed live: 07-20's file uses
     `SYMBOL/SERIES/DATE1/CLOSE_PRICE/...`, 07-21 onward uses
     `TckrSymb/SctySrs/TradDt/ClsPric/...` — completely different
     columns, same underlying settlement data). This silently broke
     `fetch_bhavcopy_rows()` (shared by `backfill_price_gaps.py` and
     `backfill_prices_via_bhavcopy.py`) for every date from 07-21
     onward — not a "no data yet" case, a parser that no longer
     recognized any column in the new file at all, so every row was
     silently dropped. Fixed: `fetch_bhavcopy_rows()` now detects which
     format a response is (presence of `TradDt` vs. `DATE1`) and uses
     the matching column map. **The new format has no delivery-quantity
     data at all** (checked the full 34-column header) — NSE now
     appears to publish that separately; `delivery_qty`/`delivery_pct`
     are left `NULL` for new-format rows rather than guessed, a real
     open gap, not a bug in this parser.
  - **Remediation**: deleted the 2,496 mislabeled `NSE`-sourced rows
    (dates 07-15 through 07-21) and re-fetched all 6 real trading days
    (07-15 through 07-22) via the now-fixed bhavcopy path — the more
    reliable source to begin with, per this project's own established
    preference. Recomputed `model_target_labels`/`model_feature_matrix`
    on the corrected prices. `scoring_date` now correctly reaches
    **2026-07-22 (today)**, not stuck behind a phantom gap.
  - **Lot 2 was contaminated** (frozen using the shifted data — its
    entry prices were confirmed off, e.g. BAJAJHLDNG's frozen entry
    price was really the *next* day's close, and the model's own
    ranking inputs were built on the same shifted feature data). Per
    direct confirmation: deleted its `tracked_picks` rows and frozen
    file, and remade it for real on the corrected data — new Lot 2 is
    pick_date **2026-07-22** (today, now that the gap is genuinely
    closed), with a completely different top-20 for each horizon (as
    expected, given how much actually changed). Lot 1 was unaffected
    throughout (predates any of this session's fetches).
  - Ticker header's `is_stale` now correctly reads `false` for the first
    time this session — the pipeline is finally caught up to the actual
    present.

- **2026-07-22 (Lot tracking wasn't refreshing; `run_nightly.sh` made permanent/cron-robust)**:
  - **Real bug**: `freeze_lot.py` computed each lot's day-by-day
    `tracking` table once, at freeze time, and baked it into the frozen
    lot file forever -- so Lot 1's tracking stayed stuck showing only
    its freeze-day snapshot even after a week of real trading days had
    actually elapsed. `candidates` (the picks/probabilities) are
    correctly meant to freeze forever; `tracking` never should have --
    it's just reporting real market closes that keep happening. Fixed:
    `export_screener_data.py`'s `load_site_lots()` now recomputes
    `tracking` fresh from the live DB on every export (using each lot's
    frozen `candidates` for the symbol list + `pick_date`), for every lot
    currently shown on the site, not just the newest. The on-disk
    `models/lots/*.json` files are untouched by this -- only what gets
    embedded into `data.js` changes. Verified: Lot 1 now correctly shows
    real closes for D+0 through D+4 (07-15 through 07-21) and `null`
    beyond, matching how far the confirmed data actually extends.
  - **`run_nightly.sh` now cron-robust**: explicitly exports the correct
    `PATH` (anaconda's `python3`, which has requests/lightgbm/shap/pandas
    -- the bare system `python3` doesn't) at the top of the script,
    rather than relying on the invoking environment already having this
    set up. This was a real, confirmed gap -- every manual run this
    session needed the same PATH prepended by hand, and cron/launchd
    invoke scripts with a minimal environment that does NOT source
    `.zshrc`/`.bashrc`, so the crontab below would have silently failed
    on `ModuleNotFoundError` without this. Verified by simulating a
    stripped `env -i` environment before installing the real crontab.
  - **Direct instruction: made the 9pm run permanent**, not one-off --
    installed the exact crontab line `run_nightly.sh`'s own header
    comment had been documenting as the intended eventual schedule since
    2026-07-16: `0 21 * * 1-5` (9pm IST, Mon-Fri). `weekly_shortlist.py`
    (the only thing that creates a NEW lot) is deliberately still NOT
    part of this script -- the nightly run keeps prices/tracking/Compare
    fresh every night regardless, but a new lot only gets made on an
    explicit trigger, unchanged from the earlier "freeze until told"
    instruction. **Known caveat, not yet verified**: modern macOS often
    requires Full Disk Access be granted (System Settings → Privacy &
    Security) to whatever runs `cron` before scheduled jobs actually
    fire -- not something checkable or grantable from here. If
    `logs/nightly.log` doesn't show a run the next morning, check that
    first.

- **2026-07-22 (first real production run — hit 3 real bugs, all fixed, Lot 2 made for real)**:
  The scheduled 9pm run never fired (Mac rebooted ~20:41, killing the
  background `sleep`/`caffeinate` job — a real limitation of that
  approach, no crontab/launchd involved so it couldn't survive a
  restart). Ran the chain by hand afterward instead, which surfaced a
  chain of three real, connected bugs:
  1. **`scoring_date` never advanced.** `daily_prices` landed on 07-21,
     `macro_regime_indicators` landed on 07-22, with no overlapping date
     past 07-15 — both tables had gaps from the ~week nightly wasn't
     running, and neither fetch script backfills a range by default
     (single-latest-day only, by design). `weekly_shortlist.py` silently
     scored against the same `pick_date` as Lot 1.
  2. **That caused real data corruption**: since `weekly_shortlist.py`
     produced a genuinely different top-20 on the same `pick_date`,
     `log_shortlist_picks.py`'s per-symbol-only conflict check let the
     new, different symbols get appended alongside Lot 1's original 20
     rather than being rejected as a duplicate batch — `tracked_picks`
     grew to 31/28 rows instead of 20/20. **Fixed in two parts**: (a)
     deleted the 11+8 extra rows, using the already-frozen
     `models/lots/lot_1_2026-07-15.json` as ground truth for exactly
     which symbols legitimately belonged (Lot 1's on-disk/on-site file
     was never touched — `freeze_lot.py`'s idempotent-by-pick_date check
     correctly did nothing both times); (b) `log_shortlist_picks.py` now
     checks for ANY existing row at a `(horizon, pick_date)` pair up
     front and skips the whole batch, instead of only guarding
     per-symbol — a `pick_date` is meant to be one atomic, already-logged
     unit, never partially appended to.
  3. **A genuine upstream date-mislabeling bug**, found while backfilling
     the gap: fetching `daily_prices` for 16-07-2026→21-07-2026 returned
     a row correctly chained by `prev_close` to its neighbors (so the
     underlying price data was real) but tagged `date='2026-07-19'` — a
     **Sunday**, not a real trading day — consistently across every one
     of 499 symbols. The actual Friday (07-17) was simultaneously
     missing. This traces to the underlying `jugaad_data`/NSE archive
     source (the DATE field jugaad_data returns), not this project's own
     date-range logic (which just passes a fixed from/to boundary
     through). Verified no legitimate 07-17 or 07-19 rows existed before
     acting, then renamed the 499 mislabeled rows from 07-19 → 07-17 by
     hand. Backfilled the same 07-16→07-21 gap in
     `macro_regime_indicators` (no mislabeling there — different source,
     `NSE_INDICES_ARCHIVE`), then recomputed `model_target_labels` and
     `model_feature_matrix` on the corrected data.
  - With the gap actually closed, re-ran `weekly_shortlist.py` for real
    — genuinely new top-20s on `pick_date=2026-07-21` (BAJAJHLDNG/
    KOTAKBANK/... at 61.8% for 14D; ICICIAMC/UTIAMC/... at 77.6%/76.3%
    for 30D), froze it as **Lot 2**, republished the site. Both lots now
    correctly distinct and visible.
  - Also fixed two frontend bugs found by inspection right after Lot 1
    was frozen (before any of the above): `app.js` called
    `renderLotTiles()`/`formatDate()` at load time before the `MONTHS`
    array (a `var` declared further down the file) was ever assigned —
    throwing and silently halting every statement after it in the same
    top-level script, which is why the ticker, Overview, and Statistical
    View all appeared simultaneously broken from one root cause. Moved
    all helpers before first use. Separately, `.ticker-header`'s and
    `.compare-search-row`'s own `display` property was overriding the
    `[hidden]` attribute's `display: none` (equal CSS specificity, author
    styles win), so the ticker header showed on the Home page regardless
    of what `app.js` set `.hidden` to. Both fixed and visually confirmed
    in Safari.

- **2026-07-22 (lots: freeze each real batch of picks, stop live-scoring on every export)**:
  Direct instruction: run the pipeline tonight for a real first "final
  cut," then freeze that batch until told otherwise — future runs happen
  only on a condition to be given later, never automatically. This
  required a real design decision, not just a UI tweak: Overview's
  candidates had been a fresh top-N re-scored by `export_screener_data.py`
  on every run (the v1/v2 design) — that's fundamentally incompatible
  with "freeze until told," since it would silently change every time the
  script ran again for any reason (e.g. a nightly Compare refresh).
  - `src/freeze_lot.py`: freezes the latest `tracked_picks.pick_date`
    into a permanent `models/lots/lot_<N>_<pick_date>.json` — candidates
    come straight from `calibrated_prob_at_pick` (never re-scored),
    enriched with metadata as of `pick_date`. Idempotent by pick_date, so
    it's safe to run nightly as a no-op on days without a new
    `weekly_shortlist.py` run.
  - `export_screener_data.py` had its entire model-training/scoring path
    removed — it now only reads back whatever lots already exist on disk
    and republishes the most recent `MAX_SITE_LOTS=3` to `data.js`, plus
    refreshes the live nifty ticker/model_meta/Compare snapshot around
    them. Real re-run: ~14s CPU, down from ~2 minutes, now that LightGBM
    training isn't happening on every export.
  - `models/lots/` is never pruned — every lot ever frozen stays on disk
    permanently, only the site's exposure is capped at 3, per direct
    instruction ("we still store them in our folders").
  - Retroactively froze the existing `tracked_picks` data (pick_date
    2026-07-15, logged 2026-07-21) as **Lot 1** so it's not lost under
    the new model.
  - `run_nightly.sh` now also runs `freeze_lot.py` + `export_screener_data.py`
    at the tail — safe/cheap, keeps the site's live pieces (ticker,
    Compare) fresh nightly without ever touching the frozen lots
    themselves. `weekly_shortlist.py` (the only thing that actually
    creates a new lot) is deliberately NOT part of this script, matching
    its pre-existing "run by hand" status.
  - Frontend: Overview and Statistical View both gained a lot-picker
    screen before the existing horizon picker. Lots are labeled by their
    real, permanently-incrementing number + `pick_date` (e.g. "LOT 1 —
    MODEL RUN: 15 Jul 2026") rather than a repositioned "1st/2nd/3rd" —
    chose this so a lot's label never changes even after an older one
    rolls off the site, which a purely positional "1st/2nd/3rd" labeling
    would not have given.
  - Verified: rebuilt `data.js` from the frozen Lot 1 exactly matches the
    original `weekly_shortlist.py` run (rank 1 ICICIAMC @ 68.8% for 14D).
    HTML/CSS/JS structurally validated; the lot-picker's actual
    click-through wasn't separately browser-tested this round.

- **2026-07-22 (`frontend-spec-v2-sidebar-nav.md` — sidebar restructure + Statistical View + Compare)**:
  Renamed "Nifty 500 Neglect Screener" → **"Nifty Alpha Predictor"** and
  restructured the single-screen build into a 4-section app (Home,
  Overview, Statistical View, Other Stocks — To Compare) behind a
  persistent left sidebar, per spec. Overview's picker/deck behavior is
  unchanged — just moved under its own nav item.
  - **Statistical View**: a day-by-day tracking table, deliberately built
    from the REAL `tracked_picks` table rather than a fresh top-N
    recomputed on every export run. The spec's own text ties this view to
    `data.model_meta.notes`' "out-of-sample tracking just started" line,
    which only makes sense if it's the same real tracked picks — a
    fresh-every-run set would just show an all-null table every time
    instead of accumulating real history as days actually elapse.
    `trading_days` columns mix real trading-calendar dates (for whatever
    has actually happened) with the same weekend-adjusted estimate
    `log_shortlist_picks.py` already uses for future dates, so the table
    never changes shape as real closes fill in. Verified in the real
    export: 14/30 columns generated correctly, only D+0 populated (real
    data), rest correctly `null` — expected, since the pipeline's
    confirmed scoring date is always the newest day by definition.
  - **Other Stocks — To Compare**: full Nifty 500 lookup + up to 2-stock
    side-by-side comparison across 7 lookback periods. Reuses
    `backtest.py`'s `price_at_or_before()`/`load_price_lookup()` directly
    for every single lookback rather than a new date-math convention —
    trading-day-based for 3D/7D/14D, calendar month/year with
    prior-trading-day fallback for 1M/6M/1Y, matching the spec's own
    stated preference. Verified the `change_pct_vs_last` formula against
    the spec's own worked RELIANCE example before trusting it in the real
    export (both hand-calc and real output agree). Exported to a separate
    `frontend/universe.js` (500 stocks), lazy-loaded only when the
    Compare section is first opened rather than bundled into `data.js`
    which loads on every page view, per the spec's own build-time-decision
    note directed at this exact choice.
  - **All 5 of Section 8's open questions resolved using the spec's own
    stated defaults** (calendar lookback w/ prior-trading-day fallback for
    1M/6M/1Y; hardcoded 2-stock compare cap, not generic N; Statistical
    View shows all top-N rows, no checkbox narrowing; universe snapshot
    shares `export_screener_data.py`'s cadence; Home keeps the "Go to
    Overview" CTA) — none of these required stopping to ask, since the
    spec itself already stated a preference for each.
  - Verified: HTML/CSS/JS structurally balanced, export script re-run
    produced both `data.js` (with the new `tracking` key) and
    `frontend/universe.js` correctly, page opened successfully in the
    browser. Full interactive click-through across all 4 sections was
    **not** automated this round (same browser-automation constraint
    noted on the v1 frontend row above) — worth a manual pass before
    fully trusting the Compare search/keyboard-nav and sidebar mobile
    collapse.

- **2026-07-22 (`frontend-screener-spec.md` — build the screener frontend)**:
  First frontend in the repo (previously data/model layer only) — a
  plain HTML/CSS/JS UI (per the spec's own stack-agnostic reference
  implementation, since no existing frontend stack was found to adapt
  to) presenting the model's ranked output as a ticker header + 14D/30D
  picker + card deck, replacing whatever earlier frontend draft existed
  in a separate claude.ai conversation this tool doesn't share context
  with.
  - **Resolved the spec's own open Section 9 Q4** (full universe vs.
    top-N cutoff) with the user: full eligible universe, ranked — not a
    pre-filtered subset. This meant `src/export_screener_data.py`
    couldn't just reuse `weekly_shortlist.py`'s existing top-20 output;
    it scores both horizon models across the entire ~440-name eligible
    universe (reusing `train_production_model()`/`load_universe()`
    directly, skipping the SHAP explanation pass since the card UI
    never shows per-stock factors — real, measured time saved: the full
    export ran in ~55s without it).
  - **Reverted same day**: the full-440 deck turned out unscannable in
    real use, confirming the spec's own doubt about that choice. Kept
    the full-universe *scoring* (needed for correct rankings) but scoped
    the *export/display* to each horizon's actual top-20 — the union of
    both horizons' top-20 sets (35 unique names, 5 overlap in the real
    re-run), with `app.js` re-slicing to each horizon's own top-20
    client-side when its tab is selected. See the status table entry
    for why that client-side re-slice is provably equivalent to the true
    full-universe top-20, not an approximation.
  - `frontend/index.html` + `styles.css` + `app.js` — ticker header
    (sticky, skeleton-loading state, honest `is_stale`/"AS OF" framing
    rather than implying a same-day live value when the pipeline's
    confirmed data lags), Screen A model picker (data-driven footnote,
    never hardcoded), Screen B card deck (client-side sort per horizon,
    tiebreak alphabetical, empty-state and data-missing-state copy per
    spec rather than a silent blank render). Design tokens, breakpoints,
    keyboard focus states, and `prefers-reduced-motion` handling taken
    directly from spec Sections 7–8.
  - `model_meta.status`/`notes` are derived for real from `tracked_picks`'
    live hit rate (reuses `generate_tracking_dashboard.py`'s hit-rate
    calculation from yesterday's tracking-dashboard work) — currently
    `"provisional"` since 0 real picks have resolved yet, not a
    hardcoded placeholder string.
  - `frontend/data.js` is a plain `<script src>` include, not a
    `fetch()`'d JSON file — deliberate, so the page keeps this project's
    "opens directly, zero server" convention (file:// pages can't
    `fetch()` JSON in most browsers, but a plain script include works
    fine). Gitignored/regenerated on demand, same status as
    `models/shortlists/*`.
  - Verified end-to-end against real data: full export run produced 440
    candidates; entry price / 52-week range / sector spot-checked
    against `daily_prices`/`index_membership` directly; `is_stale`
    correctly read `true` in the real output (pipeline's confirmed
    scoring date, 2026-07-15, lags real today, 2026-07-22 — nightly
    hasn't run since). HTML/CSS/JS structurally validated (balanced
    tags/braces/parens) and the page confirmed actually loading in
    Safari (title + URL queried via AppleScript). Full interactive
    click-through (picker → deck → back, card content) was **not**
    automated this round — the user stopped a JS-injection-based
    automated check partway through, so this is verified by structural/
    code review and a load check, not a full driven click-test. Worth a
    manual eyeball before trusting the interactive states fully.

- **2026-07-21 (`tracking_dashboard_spec.md` — build the pick-tracking dashboard)**:
  Turns `weekly_shortlist.py`'s one-shot output into a genuine live track
  record — the out-of-sample validation the backtest can't give, since a
  backtest only says how the model *would have* done on historical folds,
  not how it's *actually* doing on picks made after the fact. Pure
  observability, not a trading system.
  - Added `tracked_picks` (schema.sql) — one row per (symbol, horizon,
    pick_date), written once and never rewritten. `calibrated_prob_at_pick`/
    `top_factors_json` are frozen at insert time on purpose — a future
    retrain must never silently overwrite what the model actually said at
    pick time, or the whole point of tracking is defeated.
  - `src/log_shortlist_picks.py`, wired directly into `weekly_shortlist.py`
    (not a separate manual step) — logs all 20 shortlisted names per
    horizon every run, using `backtest.py`'s existing `price_at_or_before()`
    for entry price rather than a second "what does buying on this date
    mean" convention. `target_close_date` stored here is a weekend-adjusted
    calendar-day *estimate* only, since the real trading calendar
    (`macro_regime_indicators`) doesn't extend into the future — it's just
    a "check back around here" trigger for the resolver, always a safe
    upper bound (ignores the handful of yearly NSE holidays, so it never
    undershoots).
  - `src/resolve_tracked_picks.py`, added to `run_nightly.sh` — resolves
    `open` picks once the real trading calendar (not the estimate) has
    actually advanced far enough, reusing `compute_target_labels.py`'s
    exact forward-window/`_pct_return` math (never a second
    implementation), and overwrites `target_close_date` with the true
    trading-day date at resolution. A symbol missing a close on the exact
    target day is marked `delisted_during_hold`, never given a fabricated
    return — same mid-hold-dropout stance already used in `backtest.py`.
  - `src/generate_tracking_dashboard.py` — single self-contained HTML file
    (Chart.js via CDN, opens with no server), written to
    `models/reports/tracking_dashboard.html` (already covered by the
    existing `models/reports/*` gitignore rule — the DB table is the
    persistent record, not this file). Sections: live hit rate (overall +
    by horizon + by calibrated-probability bucket, reusing
    `evaluate.calibration_curve()` rather than a new bucketing
    implementation), open positions (day-by-day stock-vs-Nifty cumulative
    return chart per pick, computed fresh from `daily_prices`/
    `macro_regime_indicators` at generation time — no second stored copy
    of already-current data), sortable resolved-picks log, and an
    excluded/delisted table so dropouts are shown, not silently dropped.
  - **Verified end-to-end against the real pipeline, not just unit-level**:
    ran `weekly_shortlist.py` for real (both horizons, top-20,
    scoring_date 2026-07-15) — 40 rows logged, entry prices spot-checked
    exactly against `daily_prices`. Resolution logic separately verified
    with two synthetic picks (deleted afterward, never left in the real
    table): RELIANCE @ 2026-06-01/14d resolved with
    `actual_alpha`/`actual_stock_return`/`actual_nifty_return` matching
    `model_target_labels`'s `alpha_14d`/`stock_return_14d`/
    `nifty_return_14d` for that exact symbol/date exactly, confirming the
    resolver genuinely reuses `compute_target_labels.py`'s math; CADILAHC
    @ 2022-02-22/14d correctly produced `delisted_during_hold` (its
    14-trading-day window runs past its real 2022-03-04 delisting date).
    Dashboard regenerated against the real 40-row state and rendered
    correctly (open-position charts have one data point so far — as
    expected, since `macro_regime_indicators` hasn't advanced past the
    2026-07-15 scoring date yet; will extend automatically as future
    nightly runs add days).
  - Real production data now accumulating going forward — the model's
    calibrated probabilities can finally be checked against what actually
    happened, not just against historical folds.

- **2026-07-21 (`docs/confirm_and_reconcile.md` — confirm 0b's remaining steps, reconcile stale legacy-financials guidance)**:
  Not new feature work — closing loose ends before building further on
  top of an uncertain base, per the doc's own framing. Checked each item
  directly rather than assumed carried-over status from 0c's focus:

  **Part A**:
  - `daily_prices` audit columns (`source`/`fetched_at`): present in
    schema, but NOT fully populated — found `backfill_price_gaps.py`'s
    `upsert_gap_rows()` never set them at all (only the main
    `backfill_prices_via_bhavcopy.py` path did). This left 464 rows NULL,
    373 of them the entirety of 2021-07-16 (the earliest date in
    `macro_regime_indicators`' own covered range — should have been
    covered by the main backfill, wasn't). Fixed `fetch_bhavcopy_rows()`
    (shared by both scripts) to set `source`/`fetched_at` itself, updated
    both callers' upserts accordingly (this required also fixing
    `backfill_prices_via_bhavcopy.py`'s `upsert_rows()`, which would have
    silently double-appended audit columns and broken the INSERT's fixed
    arity on its next run — caught before it caused damage). Re-ran the
    fix against the real bhavcopy source (not a fake backfilled
    timestamp) for the 82 affected dates: 373-row 2021-07-16 gap fully
    closed, plus 22 previously-fully-missing rows filled as a side
    effect. 91 rows (0.016%) remain NULL — scattered single symbol/date
    pairs, not yet individually diagnosed, data itself already verified
    correct by the earlier anomaly scan. Documented as a small accepted
    residual rather than chased further.
  - Full-universe backfill: confirmed complete — 539/539 symbols,
    2021-07-13 to 2026-07-16, low-row-count symbols all trace to real
    2024/2025 IPOs (MEESHO, GROWW, PINELABS, EMMVEE) or delistings/mergers
    (CADILAHC, ANGELBRKG), not gaps.
  - Automated single-day-jump sanity check: confirmed wired in —
    `check_price_jump_anomalies()` runs inside `fetch_daily_prices.py`'s
    `main()`, which `run_nightly.sh` calls directly. `run_nightly.sh`
    itself is still run by hand, not cron-scheduled — a separate,
    already-documented deliberate choice, not part of what this check
    needed.
  - `working_predictor_base` → `master`: **not merged**. Branch is 24
    commits ahead of `master`/`origin/master` (which are in sync with each
    other). Status reported, not acted on — merging is a call for the
    user to make, not something to do unprompted.

  **Part B**: the "legacy XBRL financial-results parsing" guidance
  referenced as existing README content to reconcile **does not exist
  anywhere in this repo** — checked the full git history, not just
  current file state. `financial_results` went directly from "guessed
  NSE endpoint, unverified" (2026-07-13) to "re-architected around
  screener.in" (2026-07-14) with no intermediate XBRL-direct-from-NSE
  implementation ever built. Rather than fabricate a "mark as historical"
  section for something that never existed here, flagged the discrepancy
  directly and added a provenance note to the `fetch_financial_results.py`
  section clarifying screener.in has been the only source, ever — see
  that section above.

- **2026-07-20 (Section 0c fully remediated: tie-break + staleness fixed, `model_feature_matrix` rebuilt, every downstream model/backtest/decision-layer re-run and diffed against the pre-fix numbers)**:
  Closes out the bug flagged in the entry directly below (real-financials
  verification). Fix applied in `src/assemble_feature_matrix.py`:

  1. `load_disclosure_series()` now orders CONSOLIDATED after STANDALONE
     on tied disclosure dates for every table with a `result_type` column
     (`financial_results`, `balance_sheet`, `cash_flow`, `ratios` -- all
     four, not just financials), so `most_recent_as_of()`'s tie-break is
     deterministic instead of an SQL row-order accident. Verified
     directly: RELIANCE now resolves to its real CONSOLIDATED Q4 FY26
     figures (sales 294,059 / net profit 20,589) instead of an arbitrary
     pick between that and the STANDALONE figures.
  2. `most_recent_as_of()` and `institutional_trend_as_of()` (which has
     its own separate bisect) now take `MAX_DISCLOSURE_STALENESS_DAYS =
     240` and return `None` past that cutoff rather than a stale row --
     applied to every disclosure-based join (fin/bs/cf/ratio/sh/
     institutional), not just `financial_results`. Verified directly:
     FORTIS and BHARATFORG (both stuck >1000 days on a 2023 disclosure)
     now correctly resolve to `None` instead of the wrong figure.
  3. `model_feature_matrix` rebuilt from scratch (556,778 rows, unchanged
     count as expected -- same symbol/date keys, different values).
     `fin_net_profit` non-null count dropped 289,677 → 254,432 (~12%
     fewer) -- the correct, expected consequence of no longer trusting
     stale disclosures. Result-type mix across all confirmed-disclosure
     rows flipped from a near-arbitrary split to 230,197 CONSOLIDATED vs.
     24,235 STANDALONE, i.e. CONSOLIDATED is now what's actually used
     whenever both exist, as intended.

  **Re-ran the full chain, diffed old vs. new rather than assumed
  stable** (git commit `2bf50a1`'s archive summaries preserved as
  `*_pre_0c.json` in `models/reports/archive/` for exactly this kind of
  comparison):

  - **Baselines**: unaffected, as expected -- the simple baseline only
    uses price/volume momentum, no `fin_*` features.
  - **LightGBM**: small, bounded AUC changes per fold (±0.001–0.011),
    no systemic collapse or suspicious jump -- e.g. 14d fold 3 expanding
    0.5376→0.5480, 30d fold 3 rolling 0.5476→0.5589. Consistent with a
    real but non-catastrophic correction, same shape as the 0b re-run.
  - **Platt-inversion fold-slots reproduced a FOURTH time**, same four
    calendar windows as every prior run (14d fold 3, 14d fold 5, 30d
    fold 1, 30d fold 3), all four AUC pairs summing to exactly 1.0000 --
    continues to look like a structural property of those specific
    calendar windows, not an artifact of any of the bugs fixed so far.
  - **SHAP institutional-attention ranking -- one real reversal, not just
    noise**: `sh_inst_mutual_fund_pct` remains the strongest institutional
    feature in both horizons (unchanged). But `institutional_attention_feature.md`
    Section 8's claim that `sh_inst_pctrank` is the *weakest* of the six
    institutional features **no longer holds** -- `sh_inst_total_pct`
    (the plain raw level) is now weakest in both horizons instead, and
    `sh_inst_pctrank` outranks it (14d: pctrank 21/30 vs. total 22/30;
    30d: pctrank 12/30 vs. total 17/30, a bigger jump). This actually
    *strengthens* the original Section 5 design reasoning ("relative rank
    should beat absolute level") rather than weakening it -- the earlier
    "correction to Section 5" was itself apparently distorted by 0c's
    noise leaking into the overall SHAP allocation via adjacent `fin_*`
    features. Updated `institutional_attention_feature.md` to flag this
    precisely rather than leave the old claim standing uncorrected.
  - **Backtest -- real, fold-level shifts, not just noise, headline
    framing roughly holds**: `beats Nifty` counts now 3-4/5 across N
    (was 2-4/5), `beats random` now 2-3/5 (was 3-4/5) -- still a "mixed,
    modest edge" story, not a reversal of the headline finding. But
    individual folds moved meaningfully: 14d fold 3 (the fold with the
    worst drawdown) *improved* -- model return went from clearly negative
    (-0.077 to -0.076 across N) to roughly flat/slightly positive (-0.018
    to +0.043), and max drawdown improved from ~-20% to ~-15%, still the
    worst fold but less severe. 14d fold 4 got *worse* at N=10 specifically
    -- flipped from positive (+0.039) to negative (-0.081), drawdown
    widened from -8.3% to -11.7%. Report this precisely rather than only
    the aggregate, per this project's standing per-fold discipline --
    a fold-level reversal is exactly the kind of thing an average could
    hide.
  - **Decision layer -- the most consequential change**: recomputed the
    compounded/Calmar numbers (same method as the 2026-07-20 Section 5
    correction) on the rebuilt data:

    | Horizon | Variant | Old Calmar | New Calmar |
    |---|---|---|---|
    | 14d | baseline | 4.61 | 3.95 |
    | 14d | half exposure | 7.06 | **5.18** |
    | 14d | zero exposure | 14.81 | 4.72 |
    | 30d | baseline | 3.91 | 2.34 |
    | 30d | half exposure | 6.20 | 3.93 |
    | 30d | zero exposure | 11.97 | 5.93 |

    **14d's finding reversed, not just shrank**: previously zero-exposure
    strictly dominated (monotonically increasing Calmar with more
    de-risking). On corrected data, **half-exposure now has the best
    Calmar (5.18), beating zero-exposure (4.72)** -- going all the way to
    cash is no longer optimal at 14d, there's an interior optimum instead.
    30d's finding held directionally (still monotonically improving with
    more de-risking, zero-exposure still best) but the magnitude of the
    advantage roughly halved. **The earlier 14.81/11.97 Calmar figures
    were partly an artifact of the 0c bug's noise, not a clean signal** --
    this is exactly why the raw-JSON-before-treating-as-confirmed
    discipline from the Section 5 correction exists. Updated
    `next_phase_plan.md` Section 5's RESULT block accordingly. **Which
    variant to deploy remains explicitly undecided** (same as before --
    this is a use-case-dependent choice, not a default), but the
    corrected numbers materially change what that choice is actually
    weighing.
  - **`weekly_shortlist.py`**: re-ran on the corrected data. FORTIS and
    BHARATFORG no longer appear in either horizon's top-5 (their earlier
    high rank was partly driven by the wrong stale `fin_days_since_disclosure`
    value being a strong supporting SHAP feature) -- confirms the fix
    changed real output, not just internal numbers. Every `fin_*`
    explanation line now genuinely shows `[consolidated]` in practice
    (previously an arbitrary mix), and no `[STALE]` caveat fired in this
    week's top rankings.

  **Not changed**: the methodology itself (walk-forward/embargo,
  isotonic-over-Platt, SHAP-over-default-importance, the archive
  convention) -- same conclusion as every prior "re-run after a bug fix"
  entry in this project. What changed is specific numbers, several
  materially (the decision-layer Calmar reversal being the most
  consequential), which is exactly why this project treats every result
  as provisional until independently re-verified rather than
  grandfathered in.

- **2026-07-20 (real-financials verification on the first live `weekly_shortlist.py` output -- found a systemic bug, not fixed yet, flagging for a scope decision)**:
  User independently spot-checked shortlist output against real, current
  financials (SBIN, NTPC, Tata Steel, Hero MotoCorp, Swiggy, ABFRL, Urban
  Company all checked out) and found FORTIS's `fin_net_profit` (Rs 13 Cr)
  didn't match Fortis Healthcare's real, well-documented Q4 FY26
  consolidated net profit (Rs 271 Cr) -- a ~20x gap. Investigated directly
  rather than assuming standalone-vs-consolidated scope explained it:

  **What FORTIS's Rs 13 Cr actually is**: neither the real Q4 FY26
  standalone (Rs 25 Cr) nor consolidated (Rs 271 Cr) figure -- both exist
  in `financial_results` with `disclosure_date=NULL`. The ONLY row for
  FORTIS with a confirmed `disclosure_date` is Q1 FY24 (period ending
  2023-06-01, disclosed 2023-08-04), STANDALONE variant (sales 289,
  net_profit 13) -- `assemble_feature_matrix.py`'s point-in-time lookup
  correctly only trusts confirmed-disclosure rows, so it's frozen on this
  one, 3-year-old quarter. Not a rounding/scope issue -- a real staleness
  bug. Confirmed the same pattern independently on BHARATFORG (last
  confirmed disclosure also 2023).

  **How widespread**: checked the whole universe directly, not just these
  two names. 46/498 symbols have zero confirmed-disclosure rows ever;
  127/498 have their latest confirmed disclosure >180 days stale (some
  strings of the same "1004-1096 days, stuck in 2023" pattern:
  BALRAMCHIN, CONCORDBIO, HBLENGINE, HEROMOTOCO, KIRLOSENG, MMTC, SCI,
  JUBLFOOD among the worst). 325/498 are reasonably fresh (<=180 days).
  This is a real, standing gap for the affected third of the universe,
  not noise that resolves itself next week -- traces back to
  `corporate_announcements`-to-`financial_results` disclosure matching
  failing for these symbols' recent quarters specifically, a deeper issue
  than the existing "~52% coverage" framing conveyed.

  **A second, separate bug found while investigating**: for FORTIS and
  BHARATFORG, the one confirmed-disclosure row available has BOTH a
  STANDALONE and CONSOLIDATED variant filed on the same `disclosure_date`
  (real filings always report both together) -- `most_recent_as_of()` in
  `assemble_feature_matrix.py` (`bisect_right` on disclosure_date, no
  secondary sort key) picks whichever sorts last among the tie, which is
  an artifact of SQL row-fetch order, not a deliberate "most recent"
  choice despite `schema.sql`'s comment wording it that way. Checked how
  common this collision is: **89% (2762/3102) of all confirmed-disclosure
  (symbol, date) groups have both scopes present** -- meaning
  `fin_sales`/`fin_net_profit`/`fin_opm_pct`/`fin_eps` have been an
  effectively arbitrary, undocumented mix of standalone/consolidated
  figures across most of the training history the model was built on,
  not just this week's scoring row. This predates today's session --
  found while verifying output, not introduced by it.

  **What was fixed today (`src/weekly_shortlist.py` only, presentation
  layer, no training data touched)**: every `fin_sales`/`fin_net_profit`/
  `fin_opm_pct`/`fin_eps` explanation line now shows `[standalone]` or
  `[consolidated]` explicitly (previously silently ambiguous). Any
  `fin_days_since_disclosure` over 400 days now carries a strengthened
  caveat naming the specific risk (fundamentals may be frozen years in
  the past, not just "slightly stale") rather than the earlier, softer
  "disclosure-matching gap" wording.

  **What was NOT fixed, deliberately**: the underlying STANDALONE/
  CONSOLIDATED tie-break non-determinism and the disclosure-matching
  staleness for the affected third of the universe. Both live in
  `assemble_feature_matrix.py`/`fetch_financial_results.py`, feed
  `model_feature_matrix` (and therefore every trained model, backtest,
  and decision-layer result already treated as findings this session),
  and fixing either means reassembling `model_feature_matrix` and
  probably retraining/re-evaluating everything downstream -- too large a
  scope change to make unilaterally on the way to shipping a shortlist
  script. Flagging for a decision on how to proceed (e.g. prefer
  CONSOLIDATED when both exist, since that's what's typically quoted in
  news coverage the user would cross-check against) rather than guessing.

- **2026-07-20 (Part B of `docs/reports_archive_and_shortlist_spec.md`: `src/weekly_shortlist.py`)**:
  Built the actual day-to-day deliverable this project currently exists
  for -- confirmed with the user this pipeline feeds a weekly manual-
  review screening step, not an unattended allocator, so a ranked
  shortlist with real per-stock explanations is more directly useful
  right now than further backtest/decision-layer refinement.

  **Production model**: trained on ALL available labeled history, not a
  walk-forward evaluation fold (a live run has no future to leak from) --
  except the tail, reserved for isotonic calibration via
  `splitting.add_calibration_split()` applied to one synthetic
  all-history "fold", same function every other evaluation in this
  project uses, same "never calibrate on data the model trained on" rule.

  **Universe**: today's `index_membership` snapshot (2026-07-14, 500
  symbols -- valid to use directly since, unlike the backtest's historical-
  reconstruction problem, a live run only needs the current snapshot),
  filtered by active `surveillance_flags` and a newly-added liquidity
  floor (bottom decile of `avg_traded_value_20d` on the scoring date,
  relative/percentile-based since no absolute threshold existed anywhere
  in this pipeline before now -- confirmed directly while building the
  institutional-attention feature 2026-07-19). Exclusion counts reported,
  not silent.

  **Explanations**: real per-stock `shap.TreeExplainer` values (not
  aggregate importance) -- top-5 contributing features per stock rendered
  as human-readable text with SHAP sign/magnitude attached.

  **Regime flag**: reuses the exact `nifty50_dist_50dma_pct < 0` signature
  found (Section 5, below) to precede the worst Part B backtest drawdown,
  plus a VIX-percentile-vs-trailing-year check. Informational only --
  doesn't filter the shortlist.

  Dual output per horizon: `models/shortlists/shortlist_<horizon>_<date>.json`
  (machine-readable) + `.md` (human-readable), both gitignored (per-run,
  not meant to survive rebuilds -- the compact summary written to
  `models/reports/archive/` via the standing `write_archive_summary()`
  convention is what persists long-term).

  **Two real bugs found and fixed during the first live run, not
  assumed away**:
  1. The scoring date was initially `MAX(date) FROM daily_prices` --
     picked up a stray/partial fetch on 2026-07-16 (81 of 500 symbols,
     source='NSE' not 'NSE_BHAVCOPY', NOT present in
     `macro_regime_indicators`, this project's canonical trading
     calendar) and silently dropped 410 symbols from the universe as
     "no data". Fixed to require the scoring date be a confirmed trading
     day (`date IN (SELECT date FROM macro_regime_indicators)`); the
     script now also prints a NOTE if a later stray date exists in
     `daily_prices`, flagged for investigation rather than silently
     ignored every run. Real scoring date: 2026-07-15 (500/500 symbols).
     **2026-07-16's stray 81-row fetch is still sitting in `daily_prices`
     and hasn't been root-caused yet** -- worth investigating before the
     next `weekly_shortlist.py` run if it recurs or grows.
  2. `fin_sales`/`fin_net_profit`/`bs_total_assets`/`bs_borrowings`/
     `cf_net_cash_flow` (sourced from screener.in, `source='SCREENER'`)
     are natively in Rs Crores, not raw rupees -- the first explanation
     draft divided by 1e7 a second time (correct for `avg_traded_value_20d`,
     which genuinely is raw rupees, computed in this pipeline from
     `daily_prices`), producing absurd values like "quarterly net profit:
     Rs 32" for a real ~Rs 32 Cr figure. Verified against a known real
     figure (BHARATFORG's actual quarterly net profit range) before
     fixing, not just assumed. Separate `CRORE_FEATURES` vs
     `RUPEE_FEATURES` formatting sets now used.

  Also added a caveat string to any `fin_days_since_disclosure`
  explanation over 400 days: this reflects `financial_results`'
  known disclosure_date-matching gap (~52% coverage system-wide, per
  `data_loader.py`'s docstring), not the company actually going silent --
  confirmed by checking BHARATFORG directly (all its recent
  `financial_results` rows have `disclosure_date IS NULL`, so
  `assemble_feature_matrix.py`'s point-in-time lookup correctly falls
  back to the last row with a *confirmed* disclosure date, which happens
  to be old). Not a bug to fix here, just something that would otherwise
  mislead the person doing the manual review this tool feeds.

  Acceptance checklist in `docs/reports_archive_and_shortlist_spec.md`
  Part B not yet formally checked off item-by-item -- worth a pass before
  calling this fully done.

- **2026-07-20 (Section 5: decision layer -- tested, not assumed)**:
  Before building this, investigated two open questions from the Part B
  backtest result rather than designing in a vacuum:

  **Was fold 3 (the down-market fold with the bad drawdown) identifiable
  in advance?** Yes, confirmed directly: it's the only fold (both
  horizons) where `nifty50_dist_50dma_pct` reads negative at test_start
  (every other fold starts with the market above its 50-day average), and
  its `nifty50_return_10d` is the most negative of any fold. These are
  features the model already sees and weights heavily for individual
  stock ranking (`nifty50_dist_50dma_pct` is a top-2 SHAP feature at both
  horizons) but the Section 4 strategy never used at the portfolio-
  exposure level -- it's always fully invested in top-N regardless of
  regime.

  **Was the 30d fold 2 "random beats model" result one lucky draw?** No --
  reconstructed the exact 20 random draws (same RNG seed) for that fold's
  first rebalance period: all 20 were positive, tightly clustered
  (+4.9% to +13.65%, mean +9.16%). A genuine broad-market-rally /
  low-dispersion effect, not noise from an unlucky sample -- when nearly
  every stock moves together, which ones you pick matters less.

  **Built `models/decision_layer.py`**, reusing Section 4's exact
  fold/model/cost infrastructure for a fair comparison, testing all three
  spec questions plus a regime-exposure variant motivated by the finding
  above:
  - **Regime-based exposure scaling** (reduce total exposure when
    `nifty50_dist_50dma_pct` < 0 at the rebalance date): works exactly as
    designed, with a real cost -- but the cost is smaller than a first,
    per-fold-simple-average look suggested, and the benefit is bigger.
    **Correction after independent review (2026-07-20): simple
    per-fold arithmetic averaging understates drawdown protection,
    because a large loss doesn't average away cleanly -- it compounds.**
    Chaining all 5 folds into one sequential, compounded equity curve
    (the way capital would actually experience these folds back to back)
    and computing Calmar ratio (compounded return / max drawdown) tells
    a meaningfully different story than the per-fold averages did:
    | metric | baseline | half-exposure | zero-exposure |
    |---|---|---|---|
    | 14d compounded return | +97.2% | +87.1% | +75.8% |
    | 14d max drawdown | -21.1% | -12.4% | -5.1% |
    | 14d Calmar (return/\|dd\|) | 4.61 | 7.06 | **14.81** |
    | 30d compounded return | +69.4% | +58.9% | +47.0% |
    | 30d max drawdown | -17.8% | -9.5% | -3.9% |
    | 30d Calmar (return/\|dd\|) | 3.91 | 6.20 | **11.97** |

    On a risk-adjusted basis, regime scaling is not a marginal trade-off
    -- it's a 3x+ improvement in Calmar ratio, even though absolute
    compounded return is lower. Whether that trade (lower total return,
    much better return-per-unit-of-drawdown-risk) is the right call still
    depends on the use case (see "which strategy to deploy" below) -- but
    it is a materially more favorable trade than the original per-fold
    simple-average framing suggested. In fold 3 specifically, drawdown
    goes from -20.0% (14d) / -16.9% (30d) at baseline to -10.6% / -8.6%
    at half exposure to -0.25% / 0% at zero exposure.
  - **Probability-weighted position sizing**: mixed, mostly small
    effects either direction. One standout: 30d fold 5 improved on BOTH
    return (+15.3% -> +19.8%) and drawdown (-3.0% -> -0.4%)
    simultaneously. Not a dominant strategy, but not nothing.
  - **Minimum probability threshold (0.5)**: essentially a null result.
    Verified directly against the raw (uncalibrated) prediction
    distribution: 43-69% of raw predictions across all 5 folds/both
    horizons fall in [0.4, 0.6) (vs. ~20% under a uniform distribution) --
    a real concentration, but a threshold placed at 0.5 sits inside that
    dense band rather than cutting into it, so it rarely excludes a
    stock that already made the top-N cut. Note: this is NOT the same
    finding as isotonic's ~99% single-bucket collapse documented
    elsewhere for fold 5 specifically (a calibration-slice-driven
    near-random-AUC artifact, collapsed into [0.3,0.4) on ISOTONIC
    output) -- that's a different, narrower phenomenon on a different
    (calibrated, not raw) set of numbers; conflating the two would
    overstate how extreme the raw clustering actually is. Same underlying
    character either way though: weak discrimination shows up again,
    consistently, in a third independent evaluation. Not chased further
    with a higher threshold value given time constraints.

  **Open item, honestly flagged rather than guessed at**: whether fold 2
  (the "genuine market breadth" fold, confirmed via reconstructed random
  draws above) corresponds to the same calendar period as the 58.3%
  base-rate fold from the very first naive-baseline round early in this
  project could not be verified -- `models/reports/` is gitignored and
  has been overwritten by many re-runs since (including the full
  daily_prices remediation), and even this conversation's own memory of
  that early finding only preserved the range (42.3%-58.3% across folds),
  not a fold-by-fold date mapping. Today's clean-data fold 2 shows
  actual_rate 0.522 (14d) / 0.540 (30d) -- real but not as extreme as
  58.3%. Not treated as confirmed either way.

  Full per-fold, per-variant numbers in
  `models/reports/decision_layer_report.json`. `next_phase_plan.md`
  Section 5 updated with the regime-scaling addition and rationale before
  this was built, not after.

- **2026-07-20 (Part B: portfolio backtest run -- the actual "does this
  make money" test)**: `models/backtest.py` run for real on clean data
  (the corruption bug surfaced it before it could produce trustworthy
  results, see 2026-07-19 entries -- this is the completion). Per
  `next_phase_plan.md` Section 4: each of the 5 rolling-window folds
  trains its own model, scores the eligible universe (`daily_prices`
  presence + `surveillance_flags` exclusion -- see the `index_membership`
  proxy caveat below) at each non-overlapping rebalance date, holds the
  top-N, and compares against buy-and-hold Nifty and an equal-weight
  random-N-stock baseline (averaged over 20 draws), at two real cost
  scenarios (optimistic: zero brokerage, the modern discount-broker norm;
  conservative: adds a modest brokerage + slippage allowance) using rates
  sourced live from Zerodha/NSE.

  **Result: genuinely mixed, same shape as every other check in this
  project.** Small sample sizes throughout (10 rebalances/fold for 14d =
  50 total across all folds, only 4/fold for 30d = 20 total) -- no
  Sharpe-style ratio computed, per the spec's own explicit caution about
  over-precision on a sample this small.
  - **Model beats Nifty buy-and-hold in 2-4 of 5 folds** depending on N
    (worse at N=10, better at N=30 -- more diversification helps).
    Simple average cumulative return across folds: 14d model +15.9% vs.
    Nifty +5.9%; 30d model +12.8% vs. Nifty +1.8%.
  - **Model beats the random-N-stock baseline in 3-4 of 5 folds** -- real
    but modest evidence of an edge beyond pure random picking. At the
    30d horizon specifically, the AVERAGE-magnitude edge over random
    nearly vanishes (+12.8% model vs. +12.5% random) even though the
    model wins more folds by count -- a large single-fold outlier in
    random's favor (fold 2: random +25.9-30.6% vs. model +7-13%) offsets
    the model's wins elsewhere. Don't read "wins more folds" and
    "matches on average magnitude" as contradictory -- both are true and
    both matter.
  - **Fold 3 (test window ~Oct 2024-Apr 2025) is a genuine down-market
    period where model, random, AND Nifty all lose money** -- not a
    model-specific failure. But within that fold, the **model's max
    drawdown is notably worse than Nifty's** (-20.0% vs. -11.4% at 14d
    N=20; -16.9% vs. -8.25% at 30d) -- a real, honest finding: this is a
    pure ranking/stock-picking strategy with no defensive hedge, so it
    can concentrate losses beyond the benchmark during a broad downturn
    rather than being protected against one.
  - **Costs matter but don't flip any fold's outcome** -- conservative
    vs. optimistic costs cost a few percentage points per fold
    (reasonable, expected degradation), never enough to change which
    strategy wins.
  - Zero mid-hold delistings/dropouts occurred across any tested period
    (both horizons) -- reassuring for this specific window, though the
    `index_membership` universe-proxy limitation (see below) still
    applies more broadly.

  **Known limitation, stated in every report**: the universe at each
  rebalance date uses `daily_prices` presence as a proxy for
  `index_membership` (which has no historical snapshots -- see the
  2026-07-19 entry below), so it cannot detect a stock's removal from the
  Nifty 500 for reasons other than it no longer trading at all. True
  historical index reconstruction remains a separate, not-yet-started
  task.

  **Bottom line**: this does not read as "clearly makes money" or
  "clearly doesn't" -- it reads as a real but modest edge, concentrated
  more at higher N (more diversification) and more visible against Nifty
  than against random stock-picking, with a real risk of amplified
  drawdown in a genuine down market. Consistent with every other signal
  check in this project (AUC ~0.48-0.58, SHAP showing real-but-weak
  contribution from most features). Full per-fold, per-N, per-cost-
  scenario numbers in `models/reports/backtest_report.json`.

- **2026-07-20 (small consistency correction found resuming Part B)**:
  Before starting the portfolio backtest, re-verified `model_feature_matrix`
  and `model_target_labels` actually matched `daily_prices` as claimed
  below -- they didn't. `assemble_feature_matrix.py` is pure upsert; it
  never prunes rows whose `(symbol, date)` no longer exists in
  `daily_prices`, so 135,776 rows orphaned by the 2026-07-19 cleanup
  (mostly the bogus non-trading-day deletion) were still sitting in
  `model_feature_matrix`, inflating it to 692,554 rows instead of the
  556,778 actually reported. The "matches daily_prices exactly" claim
  below was written from the "rows upserted" log line, not a fresh
  `COUNT(*)` -- an inference, not a re-verified fact. Also found 450
  similarly-orphaned `model_target_labels` rows (`IREDA`, `TATACAP`,
  `LTF`, `SAMMAANCAP` -- the labels rebuild ran before their pre-IPO/
  pre-rename price cleanup was finalized). Both deleted; both tables now
  genuinely match `daily_prices` (556,778 rows each, confirmed via direct
  count). Not worth re-running the model suite over -- the orphaned label
  rows were 0.08% of the ~548K rows those runs trained on, and produced
  NULL features (computed fresh from `daily_prices` at training time),
  not wrong ones.

- **2026-07-19 (CRITICAL remediation completed: daily_prices fully clean,
  all models re-validated)**: Closes out the corruption investigation
  below -- full `next_phase_plan.md` Section 0b remediation done.

  **The per-symbol re-backfill approach turned out to be unusable.**
  Repeated attempts against NSE's `stock_history` API within a couple of
  hours drove what looks like IP-level throttling: `jugaad-data`'s
  session cookie-refresh call (already known to have no timeout, see the
  entry below) started hanging on nearly every symbol, even with
  progressively more conservative pacing (1.0s -> 0.6s -> 0.3s sleep, a
  30s hard per-symbol timeout added via `signal.alarm` in
  `backfill_prices.py`). The hard-timeout made the hang non-fatal but the
  failure rate reached ~100%, making forward progress impractical.

  **Switched to NSE's bhavcopy settlement archive instead**
  (`src/backfill_prices_via_bhavcopy.py`, new) -- a structurally
  different fix, not just a workaround: `NSEArchives` is a completely
  separate class from `NSEHistory` with its own session and an explicit
  `timeout=4` on every call, so the exact hang mechanism above cannot
  occur there. It's also a different NSE endpoint entirely (unaffected by
  today's per-endpoint throttling) and far more efficient -- one file per
  trading day covers every symbol, ~1,250 requests total instead of 539
  symbols x several chunked requests each. Confirmed reliable: the full
  5-year backfill completed in ~12 minutes once switched.

  **Two further bugs found and fixed during this pass, beyond the
  original series="ALL" bug**:
  1. **~135,290 rows sat on dates that were never real trading days at
     all** (weekends, and recognizable Indian market holidays -- Republic
     Day, Independence Day, Gandhi Jayanti, Christmas, etc. -- confirmed
     by cross-referencing every "anomalous" date against
     `macro_regime_indicators`' authoritative trading calendar and
     manually checking the weekday ones against known holidays). Pure
     artifacts of the original corruption bug, safe to delete outright
     (`DELETE ... WHERE date NOT IN (SELECT date FROM
     macro_regime_indicators)`, scoped to the calendar's own covered
     range to correctly exclude a few real trading days that predate/
     postdate its coverage).
  2. **The `series == "EQ"` filter was too strict.** Confirmed live via a
     direct bhavcopy inspection (2021-08-09, POONAWALLA): the file has
     TWO rows for the same symbol/date, one `series=BE` (close=172.50,
     volume=338,685 -- the real trade) and one `series=N3` (close=1099,
     volume=9 -- a genuine bond). `BE` (Book Entry / Trade-to-Trade
     settlement) is real equity trading, used for newly-listed stocks and
     stocks under surveillance (the same ASM/GSM concept this project
     already tracks in `surveillance_flags`) -- not a bond series. The
     old EQ-only filter correctly dropped the bond row but also wrongly
     dropped the real BE-series trade, leaving old corrupted rows
     untouched wherever a stock was trading in BE mode. Fixed
     `fetch_bhavcopy_rows()` (shared by `backfill_price_gaps.py` and the
     new bulk script) to accept `EQ` and `BE`, preferring `EQ` if both
     somehow appear for the same symbol/date.

  **Two genuine pre-IPO data issues found via the same investigation**
  (not corruption, but the original bug's "series=ALL" request pulled in
  bond-era history for companies that didn't have public equity yet):
  `IREDA` (real IPO late Nov 2023) and `TATACAP`/Tata Capital (real IPO
  Oct 2025) both had bond-series data misattributed to their equity
  symbol for the entire period before their actual listings. Deleted 425
  and 57 pre-IPO rows respectively -- these companies correctly have no
  `daily_prices` history before their real listing dates now.

  **Final verification**: independent full-table anomaly scan (same
  >50% single-day-jump heuristic used throughout) found **zero**
  remaining unexplained anomalies -- the 74 that remain were individually
  checked and are all genuine corporate actions/stock splits (e.g. 360ONE
  1773.3 -> 441.05, a clean ~4:1 split that persists afterward, confirmed
  via normal volume throughout, not the tiny-volume signature of bond
  contamination).

  **Downstream rebuild, in order**: `avg_traded_value_20d` recomputed for
  all 539 symbols, `model_target_labels` rebuilt from scratch (547,965
  rows), `model_feature_matrix` reassembled (556,778 rows), then
  baselines -> LightGBM (both window strategies) -> SHAP -> calibration
  all re-run fresh and compared explicitly against the pre-cleanup
  numbers rather than assumed stable:
  - **AUC changes were small and bounded** (-0.03 to +0.04 per fold, no
    systemic collapse or suspicious jump) -- consistent with removing
    noise from ~17% of the universe, not a wholesale re-derivation.
  - **The institutional-neglect hypothesis result is confirmed, not just
    provisional**: `sh_inst_mutual_fund_pct` remains the strongest
    institutional feature at both horizons (rank 8->7 at 14d, 6->6 at
    30d, sums essentially unchanged), trend still beats level, `sh_inst_
    pctrank` still ranks weakest. "Mixed/partial support" was not an
    artifact of the corruption.
  - **The Platt-scaling inversion pattern reproduced a THIRD independent
    time**, at the exact same four fold-slots (14d fold 3, 14d fold 5,
    30d fold 1, 30d fold 3) -- now confirmed across three separate runs
    with different feature sets AND different underlying data quality.
    Strong evidence these are structurally weak-signal calendar windows,
    not an artifact of anything upstream. Isotonic mandate further
    cemented.

  Pending-re-validation warnings removed from `docs/model_build_spec.md`
  and `docs/institutional_attention_feature.md`, replaced with
  confirmation notes. `docs/next_phase_plan.md` Section 0b's acceptance
  checklist fully checked off. Part B (portfolio backtest) work,
  paused since the corruption was found, can now resume on trustworthy
  data.

- **2026-07-19 (CRITICAL: daily_prices corruption found, root-caused,
  forward-fixed -- full remediation pending)**: Found while building the
  Part B portfolio backtest (`models/backtest.py`) -- an absurd single-fold
  average return (867% for a 20-stock basket over one rebalance period)
  traced to `BRITANNIA`'s price series alternating day-to-day between two
  unrelated value clusters (~Rs 5,000-5,300, the real price, and ~Rs 29-30
  with an order of magnitude lower volume, clearly a different
  instrument).

  **Scope, confirmed by a full-table scan** (any single-day jump >50%,
  generous given NSE circuit limits are typically 5-20%): **91 of 539
  symbols (~17% of the tracked universe) affected, 4,508 anomalous price
  points, spanning 2021-07-14 to 2026-06-23** -- essentially the entire
  backfill history, not a rare glitch. Includes large, liquid,
  heavily-relied-on names: `HDFCBANK`, `WIPRO`, `KOTAKBANK`, `NESTLEIND`,
  `BAJFINANCE`, `DRREDDY`, `BRITANNIA` (25.8% of its own 1,538 rows
  affected). Worst-hit symbols were overwhelmingly PSU/financial
  companies (`M&MFIN` 686 jumps, `IFCI` 478, `PFC` 410, `NHPC` 383, `NTPC`
  379, `RECLTD` 293, `HUDCO` 243) -- a real pattern, not noise: these are
  frequent corporate-bond/NCD issuers.

  **Root cause, confirmed by direct inspection of the installed
  `jugaad-data==0.33.1` library's source** (not assumed): `_stock()`
  (`jugaad_data/nse/history.py` line 80) has an inverted condition --
  `'series': series if series != "EQ" else "ALL"` -- meaning when
  `fetch_daily_prices.py` correctly requests `series="EQ"`, the library
  silently sends `series="ALL"` to NSE's actual API. Non-equity
  instruments sharing a symbol string with the equity (bonds/NCDs, mostly)
  get silently mixed in. `stock_df()`'s output DataFrame still labels each
  row's real series correctly via NSE's own `CH_SERIES` field, even though
  the request asked for everything -- so this was fixable entirely on our
  side by filtering the response, without needing to patch the library.

  **Fix applied (forward-only)**: `fetch_symbol()` in
  `src/fetch_daily_prices.py` now filters to `series == "EQ"` before
  returning, with a loud stderr log of how many rows got dropped per
  symbol. Verified against `IFCI` (a known-bad case): 29 contaminated rows
  correctly dropped, remaining data now internally consistent (~Rs 29-38
  range throughout, no more spurious jumps to ~Rs 1000-2300).

  **This fixes only future fetches.** The 91 symbols' existing corrupted
  rows are still sitting in `daily_prices` as of this entry -- full
  remediation (re-backfill, `model_target_labels` rebuild, re-run
  baselines/LightGBM/SHAP/calibration) is scoped in
  `docs/next_phase_plan.md` Section 0b and has NOT started yet.
  **Every prior model result in this project (baselines, LightGBM, SHAP,
  calibration -- including the institutional-neglect hypothesis test's
  "mixed/partial support" conclusion) should be treated as provisional**
  until re-run on clean data -- `daily_prices` feeds momentum features
  (`return_5d/10d/20d`, `volatility_20d`, `volume_ratio_20d`) and
  `model_target_labels` directly for all 91 affected symbols. Added
  pending-re-validation warnings to `docs/model_build_spec.md` and
  `docs/institutional_attention_feature.md` accordingly. Part B backtest
  work is paused until Section 0b's remediation completes.

- **2026-07-19 (catalyst detection: free path found, tried, retired)**:
  Revised `docs/next_phase_plan.md` Section 2 -- rather than defaulting
  to the already-built LLM classification path (blocked on missing API
  credentials, see the entry below), tried free options first, in order.

  **2a found a genuine free win, and it changed the whole approach**:
  a live capture of NSE's raw `corporate-announcements` response
  confirmed `desc` -- already captured as `subject` in this table since
  the fetch script was first built, just never used this way -- IS the
  SEBI Regulation 30 structured event-category tag the doc hoped might
  exist. 262 distinct controlled-vocabulary values across the full
  813,037-row history (e.g. "Bagging/Receiving of orders/contracts",
  "Pendency of Litigation(s)/dispute(s)...", "Awarding of order(s)/
  contract(s)"), 100% populated. This made 2b-2d (expanded keyword list,
  local classifier, local LLM) unnecessary -- `subject` already IS the
  category, so this became "map a known finite vocabulary to sentiment"
  rather than "infer a category from free text."

  Built `src/classify_announcements_by_subject.py`: a deterministic
  category->sentiment mapping (`POSITIVE_SUBJECTS`/`NEGATIVE_SUBJECTS`,
  only clearly one-directional categories mapped, everything else --
  administrative filings, genuinely mixed-direction categories like
  "Credit Rating- Revision" -- defaults to neutral rather than guessed,
  same discipline as every other classification in this project). Ran
  for real on the 269,056-row training universe (free, instant, zero API
  calls): 2.0% positive, 1.0% negative, 97.0% neutral. Reassembled
  `model_feature_matrix` with the real (non-placeholder) flags:
  `recent_positive_catalyst_flag_30d` true for 92,165 rows (13.4%),
  `recent_negative_catalyst_flag_30d` true for 41,442 rows (6.0%).

  **SHAP re-check: honest negative result.** Neither flag clears a
  meaningfully higher bar than the old regex-based
  `recent_order_dispute_flag_30d` (0.023/0.028, rank ~31/31). New sums:
  `recent_negative_catalyst_flag_30d` 0.0094 (14d)/0.0085 (30d), rank
  32/32 both horizons -- actually WORSE than the old combined flag.
  `recent_positive_catalyst_flag_30d` 0.0292 (14d)/0.0510 (30d), rank
  31/32 and 30/32 -- only marginally better, same bottom-of-the-list
  position. Working theory: these events are likely already reflected in
  price/volume momentum (`return_5d`/`return_10d`) by the time the model
  sees them, so a categorical flag is largely redundant. Per the doc's
  own explicit retirement criterion ("if the result still doesn't clear
  a real bar... remove it from the feature set entirely"), and per
  user confirmation, **both flags are now retired from
  `models/data_loader.py`'s `ALL_FEATURE_COLUMNS`** (excluded, same
  pattern as `sector_*`) -- the underlying classification
  (`corporate_announcements.category`/`sentiment`) is kept, real, and
  free to re-derive other features from later (e.g. category counts, not
  just a boolean flag); it's the specific flag construction that didn't
  earn its place, not the classification mechanism itself.
  `src/classify_announcements.py` (the LLM path) remains unused --
  this free approach already answered the question an LLM pass would
  have, at zero cost.

- **2026-07-19 (next-phase gap closures, Part A)**: Started
  `docs/next_phase_plan.md` -- saved to `docs/`, Section 0 reconciled
  against current real state before building anything.

  **Section 1 (sector features) -- closed as a known limitation, not a
  bug.** Checked directly: `sector_membership` has exactly 1 snapshot
  (2026-07-16, 249 rows) despite `sector_daily_benchmarks` having full
  history back to 2021-07-16 -- membership itself is snapshot-only. The
  join in `assemble_feature_matrix.py`
  (`most_recent_sector_snapshot()`) is already point-in-time-correct; the
  blocker is pure data availability (`sector_count > 0` for 0 of 687,372
  `model_feature_matrix` rows), already correctly reflected in
  `data_loader.py`'s existing exclusion of `sector_*`. Not fixable by
  code -- needs either the nightly cron running for a long stretch
  (forward-looking only; 2021-present backfill history stays permanently
  unsectored) or a historical sector-reconstitution data source, which
  `schema.sql` already flags as a separate, not-yet-started task. No SHAP
  re-run needed -- the columns aren't in `ALL_FEATURE_COLUMNS` at all.

  **Section 3 (`fin_opm_pct`) -- confirmed genuine, closed.** Direct
  null-rate check by disclosure year in `financial_results`: 2021 0%
  populated, 2022 25%, 2023 onward ~80-85%. `sales`/`eps` are populated
  consistently even in 2021-2022 -- this is an `opm_pct`-specific
  sourcing gap in the earliest scraped period, not general missingness.
  Fully explains the exact-0.0 SHAP value found in both horizons' fold 1
  (train window starts 2021-07-16). No code fix -- exactly the kind of
  structural missingness LightGBM's native NULL-handling exists for.

  **Section 2 (catalyst detection) -- upgraded to LLM classification,
  built but not yet run.** `recent_order_dispute_flag_30d`'s
  keyword-regex (SHAP-confirmed lowest-ranked feature in both horizons,
  0.023/0.028) is replaced by `src/classify_announcements.py`, which
  classifies `corporate_announcements.subject`+`details` (both, not just
  subject) into a category + sentiment via LLM, added as new
  `category`/`sentiment`/`classification_model`/`classified_at` columns
  on `corporate_announcements` (`category` already existed, documented
  from the start as "filled in later"). Script is idempotent/resumable
  (`WHERE category IS NULL`, same pattern as
  `fetch_institutional_breakdown.py`), scoped to the training-universe
  ~539 symbols by default (`--all-symbols` lifts this), and its
  batching/parsing/upsert logic is verified end-to-end via a `--mock`
  mode (canned fake responses, zero network calls) -- confirmed correct
  parsing, out-of-vocabulary rejection, and DB writes on a real 47-row
  test batch before reverting the fake data.

  `assemble_feature_matrix.py`'s `has_recent_order_dispute()` /
  `ORDER_DISPUTE_KEYWORDS` are removed, replaced by
  `recent_catalyst_flags()` deriving `recent_negative_catalyst_flag_30d`/
  `recent_positive_catalyst_flag_30d` from `sentiment` directly (not a
  hardcoded category->sentiment mapping -- a buybacks and a rights issue
  are both "corporate_action" but pull sentiment in opposite directions).
  Full universe reassembled cleanly (687,372 rows, 2:30 runtime): both
  new columns are correctly 0/0 for every row (not NULL), matching the
  documented "built but not yet populated" state.

  **BLOCKED, not silently skipped**: the real classification run hasn't
  happened -- no `ANTHROPIC_API_KEY` or `anthropic` package found in this
  environment, and this is a standalone pipeline script that needs its
  own real credentials (can't borrow this session's access). Flagged
  directly rather than mocking a fake result or quietly leaving the
  feature dead. Once credentials exist: `pip install anthropic`,
  `export ANTHROPIC_API_KEY=...`, `python src/classify_announcements.py`,
  then re-run `assemble_feature_matrix.py` and re-check SHAP -- an
  all-zero feature contributes nothing by construction, so this isn't a
  real test of the upgrade yet, just confirmation the plumbing works.

- **2026-07-19 (independent verification round)**: Cross-checked the SHAP/
  calibration re-run below against an independent read of the raw JSON
  export (`models/reports/shap_calibration_report.json`) — same discipline
  applied to every prior model result in this project. Every headline
  claim held up exactly (feature sums, ranks, the trend-beats-level and
  mutual-fund findings), with two real corrections found in the process:

  1. **A fourth Platt-inversion fold-slot**: `30d` fold 1 also inverts
     (0.4906→0.5094, sum-to-1.000 signature) -- missed in the original
     summary, which only called out fold 3 (both horizons) and `14d` fold
     5. Checking back against the *original* 2026-07-16 report (backed up
     locally as `shap_calibration_report_prev_2026-07-16.json` before this
     round's run overwrote it) confirms this same fold-slot inverted there
     too (0.4936→0.5064) -- also not caught/documented at the time. All
     four fold-slots (`14d` fold 3, `14d` fold 5, `30d` fold 1, `30d` fold
     3) have now inverted identically across two independent feature sets
     -- stronger evidence these specific calendar windows are
     structurally weak-signal, not a fluke of one run. Doesn't change the
     isotonic-mandate decision, just strengthens the evidence for it.
  2. **`sh_inst_pctrank` underperforms**: the percentile-rank feature
     Section 5 specifically argued for (institutional neglect is
     relative, not absolute) actually ranks LOWEST of all six
     institutional features in both horizons (14d rank 24/31, 30d rank
     19/31) -- weaker than the plain static level it was meant to
     improve on. Likely confounded by only having full-universe rank
     available (no sector-relative rank yet, same `sector_membership`
     limitation noted elsewhere), so not a clean rejection of the
     "relative attention" framing -- but a real empirical result, not
     swept under the rug just because the design reasoning for it was
     sound.

  Folded both corrections into `docs/institutional_attention_feature.md`
  (new Section 8 with the full confirmed result) and
  `docs/model_build_spec.md` (Section 7 updated to 4 confirmed Platt
  instances with the cross-run-consistency framing; Section 7b's
  institutional-neglect hypothesis marked RESOLVED with the mixed/partial
  finding, replacing the "not yet tested" language).

- **2026-07-19 (later)**: Built the institutional-attention feature-assembly
  additions (`docs/institutional_attention_feature.md` Section 5) and
  re-ran the SHAP check (Section 6) -- this is the actual test of the
  project's original institutional-neglect hypothesis, now that
  `shareholding_institutional_breakdown` is fully verified clean.

  **Section 0 reconciliation caught a real gap in the doc's own
  assumption**: Section 5 says to condition institutional-attention
  features jointly with "the existing liquidity filter" -- checked
  directly, no such filter exists anywhere in this pipeline (only
  `surveillance_flags` is an active row-exclusion filter in
  `models/data_loader.py`; `avg_traded_value_20d` was captured in
  `model_feature_matrix` since 2026-07-16 but never exposed to the model).
  Rather than inventing an arbitrary liquidity cutoff, added
  `avg_traded_value_20d` to `ALL_FEATURE_COLUMNS` so LightGBM/SHAP can
  surface the real interaction -- more rigorous than a fixed threshold,
  and consistent with how every other conditioning relationship in this
  project's model is handled (as a feature, not a hard filter).

  **New features added**, per Section 5's "trend and relative rank, not
  just raw levels" framing: `model_feature_matrix` gained
  `sh_inst_total_pct`/`fii_fpi_pct`/`mutual_fund_pct` (raw levels,
  point-in-time via `disclosure_date`), `sh_inst_qoq_change_pct`/
  `yoy_change_pct` (computed at assembly time in
  `assemble_feature_matrix.py` from the raw quarterly series, not
  pre-baked into `shareholding_institutional_breakdown` -- YoY only
  computed when a disclosed quarter is found 300-400 days prior, guarding
  against irregular filing gaps). `sh_inst_pctrank` (cross-sectional
  percentile rank of `sh_inst_total_pct` on the same date) is computed
  fresh in `data_loader.py` instead, against the FULL universe --
  sector-relative ranking isn't buildable yet, same accepted
  `sector_membership` snapshot-only limitation already noted for
  `avg_sector_*` columns. Verified the QoQ/YoY calculation by hand against
  two real symbols before running at full scale: BSE (irregular filing
  gaps, e.g. an extra 2019-09-26 filing) correctly produced QoQ=+0.0731/
  YoY=+0.1431 for 2024-04-01 matching the raw quarterly series exactly;
  HDFCBANK showed a plausible +22.6pp YoY jump around its 2023 merger with
  HDFC Ltd. Full universe reassembled cleanly: 687,372 rows (matches
  `daily_prices` exactly, same as before), ~95% coverage on the new level/
  QoQ columns (matching `sh_promoter_pct`'s existing ~95% coverage, as
  expected since both trace back to the same `shareholding_pattern`
  disclosure chain), 85.5% on YoY (expected -- needs an even earlier
  quarter to exist).

  **SHAP re-check result -- the actual hypothesis test**: mean |SHAP value|
  averaged across all 5 rolling-window folds, both horizons. Headline:
  institutional attention carries real, non-trivial signal but is **not**
  the dominant feature the original thesis hoped for -- `india_vix_close`
  and (newly exposed) `avg_traded_value_20d` remain the top 2-4 features
  for both horizons, consistent with every SHAP check run so far in this
  project. Among the 6 new institutional features specifically:
    - **Trend beats level, exactly as Section 5's design predicted**:
      `sh_inst_qoq_change_pct`/`yoy_change_pct` both outrank the static
      `sh_inst_total_pct` level in both horizons (e.g. 30d: YoY rank 9,
      QoQ rank 10, vs. the raw level at rank 17) -- a genuinely confirmed
      finding, not an assumption.
    - `sh_inst_mutual_fund_pct` is the strongest single institutional
      feature in both horizons (14d rank 9, 30d rank 6) -- ahead of the
      aggregate `sh_inst_total_pct` and of `sh_inst_fii_fpi_pct`
      specifically, suggesting mutual fund positioning carries more
      signal than the FII/FPI split or the combined total.
    - `sh_inst_pctrank` (the relative-rank feature) is the WEAKEST of the
      6 new features in both horizons -- but this is confounded by only
      having full-universe rank available rather than sector-relative
      rank (see the `sector_membership` limitation above), so it
      shouldn't be read as a clean rejection of the "neglect is relative"
      framing, just a note that the current rank is coarser than the
      original design called for.
    - `sh_promoter_pct` stays low-ranked (14d rank 23, 30d rank 18),
      consistent with the 2026-07-16 finding -- promoter ownership still
      isn't a strong signal. But the real institutional-attention features
      sit meaningfully above the noise floor (`recent_order_dispute_flag_30d`,
      `volume_ratio_20d`), in the same lower-middle tier as
      `cf_net_cash_flow`/`fin_net_profit`/`bs_borrowings`.
  **Verdict: mixed / partially supported.** The institutional-neglect
  hypothesis, tested for the first time with a genuine FII/DII feature
  instead of `sh_promoter_pct`, shows real predictive signal -- especially
  its trend over time and the mutual-fund-specific split -- but doesn't
  displace `india_vix_close`/liquidity as the dominant features. Full
  per-fold numbers in `models/reports/shap_calibration_report.json`
  (previous report backed up to
  `shap_calibration_report_prev_2026-07-16.json` for comparison).

  **Calibration**: re-ran isotonic vs. Platt on the same folds -- the same
  Platt AUC-inversion pattern recurred at fold 3 for both horizons (14d:
  raw 0.5396 -> platt 0.4604; 30d: raw 0.5482 -> platt 0.4518, both
  near-complementary pairs, same mechanism as 2026-07-16), a second
  independent confirmation of the standing isotonic-mandate/Platt-ban
  decision in `docs/model_build_spec.md` Section 7 -- not a new finding,
  but good to see it reproduce rather than have been a one-off.

- **2026-07-18**: Added `shareholding_institutional_breakdown` +
  `src/fetch_institutional_breakdown.py` per
  `docs/institutional_attention_feature.md` -- building the feature needed
  to actually test the project's original institutional-neglect hypothesis
  (flagged as untested in `model_build_spec.md` Section 7b, since
  `sh_promoter_pct` measures promoter ownership, not institutional
  attention). Per the doc's Section 0, reconciled against current real
  state before starting rather than trusting the doc's own possibly-stale
  assumptions -- confirmed `shareholding_pattern` never went through the
  screener.in sourcing pivot (that only affected `financial_results` and
  friends); it's been NSE-sourced via `corporate-share-holdings-master`
  since 2026-07-13.

  **Section 1's hypothesis confirmed conclusively, live**: the real
  institutional breakdown was NOT a new data source to integrate --
  `shareholding_pattern`'s summary API only exposes the coarse promoter/
  public split, but the XBRL filing URL it already captures
  (`attachment_url`, present on all 17,670 existing rows) contains the
  full SEBI Table III breakdown: ~40 leaf categories via a
  `CategoryOfShareholdersAxis` XBRL dimension (Mutual Funds, FPI Category
  I/II, Banks, Insurance, AIFs, sovereign wealth funds, pension funds,
  and more) -- confirmed by downloading and directly parsing a real
  TRENT filing. `InstitutionsDomesticMember`/`InstitutionsForeignMember`
  are NSE-computed rollup aggregates, not leaf categories -- confirmed
  their values exactly equal the sum of their own children (e.g.
  domestic institutions 0.2327 = Mutual Funds 0.1464 + Banks 0.0014 +
  Insurance 0.0609 + AIF 0.0051 + Provident Funds 0.0169 + Sovereign
  Wealth 0.0019), so `total_institutional_pct` uses these official
  rollups directly rather than re-deriving a sum that could silently
  drift from NSE's own total if a category is missed.

  Tested on 3 symbols before the full run (135 quarters): 102 parsed
  successfully, 33 failed cleanly on a real, explained gap -- NSE's own
  API returns a literal placeholder URL
  (`.../corporate/xbrl/-`) for filings with no XBRL attached (confirmed
  live: 3,354 of 17,670 total rows have this exact placeholder, mostly
  pre-2020 filings before/during SEBI's XBRL rollout) -- not a bug in our
  original capture or the new parser.

  **Real scale bug found and fixed after the first full run completed**
  (14,253 rows parsed, 99.6% success against the ~14,316 fetchable
  documents) -- a distribution sanity check (not just the earlier spot
  check, which happened to land on a correctly-scaled row) showed
  `total_institutional_pct` ranging up to 92.05, with ~5,524 of 14,253
  rows (~39%) outside a plausible 0-1 fraction range. Root cause,
  confirmed by downloading and directly comparing two real filings: NSE
  revised the shareholding-pattern XBRL taxonomy at least once (namespace
  is date-stamped, e.g. `.../shp/2025-05-31/...` vs `.../shp/2025-10-31/...`),
  and `ShareholdingAsAPercentageOfTotalNumberOfShares` means something
  different across versions -- newer schema uses a decimal fraction
  (0.2582 = 25.82%), an older one uses a raw percentage number (25.82
  directly). Confirmed exactly via `ShareholdingPatternMember` (the grand
  total, which must always equal 100% of the company by definition): it
  reads `1` in the newer schema, `100.00` in the older one. Fixed with a
  self-calibrating per-filing check (if the filing's own total reads
  `>10`, normalize every value in that filing by dividing by 100) rather
  than hardcoding namespace version strings, since there could be more
  than just these two versions across an 11-year filing history and this
  approach is robust to all of them without cataloging each one. Verified
  the fix against both real filings before re-running: HDFCBANK's
  previously-wrong 84.65 now correctly normalizes to 0.8465, and the
  already-correct newer-schema value (0.1464) is unchanged. Full table
  cleared and the corrected fetch re-run from scratch (not a
  partial/targeted fix) to guarantee every row uses the same, verified
  scale -- final corrected row counts to follow in a later entry.

- **2026-07-19**: Two more real bugs found and fixed in
  `shareholding_institutional_breakdown`, on top of the 2026-07-18 scale
  bug -- final verified state for the full table.

  **Bug 2 -- category-mapping coverage**: after the scale fix's corrected
  full re-fetch (14,252 rows), a follow-up distribution check found
  `total_institutional_pct` was NULL for 6,993 of 14,252 rows (49%).
  Root cause: a third distinct XBRL taxonomy era (member names like
  lowercase `MutualFundsOrUtiMember`, a single combined `InstitutionsMember`
  rollup instead of split domestic/foreign, `FinancialInstitutionOrBanksMember`/
  `IndianFinancialInstitutionsOrBanksMember` instead of `BanksMember`) that
  the original `CATEGORY_TO_COLUMN`/`NON_INSTITUTIONAL_MEMBERS` mapping
  didn't recognize. Worse, the old code's `else: other_pct += val` default
  silently swept genuinely non-institutional categories under unrecognized
  older-era names (e.g. `NonInstitutionsMember`, `PublicShareholdingMember`)
  into `other_institution_pct`, inflating it for those rows. Fixed by:
  surveying all 81 distinct member names actually present across the full
  dataset (via `raw_categories_json`, which already captured everything
  unconditionally regardless of naming), building a comprehensive explicit
  classification of all 81 into `CATEGORY_TO_COLUMN` / a new
  `OTHER_INSTITUTIONAL_MEMBERS` allowlist / expanded `ROLLUP_MEMBERS` /
  expanded `NON_INSTITUTIONAL_MEMBERS` (surfaced real NSE typos in the
  process -- "Catergory", "Goverments", "Isis", lowercase "Coporatewhere"),
  and flipping the unsafe "unrecognized defaults to included" behavior to a
  safe "unrecognized defaults to excluded, with a loud `UNCLASSIFIED`
  stderr warning" behavior. Added `--reprocess` mode
  (`fetch_institutional_breakdown.py --reprocess`) that re-derives every
  computed column from already-captured `raw_categories_json` with **zero
  network calls** -- used here to fix the bug across all 14,252 rows
  without re-fetching ~14,000 XBRL documents. Verified against the specific
  known-broken row (360ONE, 2019-09-18) before running at full scale.

  **Bug 3 -- BSE's taxonomy doesn't use a reliable total anchor**: even
  after both fixes, 3 rows (symbol `BSE`, quarters 2023-12-31/2024-03-31/
  2024-09-30) still had `total_institutional_pct` outside [0,1] (23.3,
  25.73, 24.69). Root cause, confirmed by downloading and directly
  inspecting BSE's real filing
  (`SHP_187478_1099821_19042024105056_WEB.xml`): BSE Ltd (the exchange
  itself) files under the `in-bse-shp` taxonomy, where
  `ShareholdingPatternMember` -- normally a reliable 100%-total anchor for
  the scale check -- instead holds an unrelated value (7.94) for this
  filer, so the existing scale-normalization check never triggered and
  BSE's institutional values stayed at percentage-number scale (e.g.
  domestic 12.69 + foreign 13.04 = 25.73) instead of being divided by 100.
  Confirmed the real total via the regulatory-guaranteed 3-way split
  instead: `ShareholdingOfPromoterAndPromoterGroupMember` (0) +
  `PublicShareholdingMember` (77.64) +
  `SharesHeldByNonPromoterNonPublicShareholdersMember` (22.36) = exactly
  100.00 (BSE, as a demutualized exchange, genuinely has no promoter).
  Fixed by extracting the scale logic into `normalize_scale()` with this
  3-way split as a fallback anchor whenever `ShareholdingPatternMember`
  doesn't look like a plausible total (not near 1, not near 100) --
  taxonomy-version-agnostic, doesn't depend on any single named member
  being mapped correctly. `reprocess()` now also re-applies
  `normalize_scale()` to the stored `raw_categories_json` (needed because
  BSE's raw values were captured pre-fix, still at the wrong scale;
  idempotent for the other 14,249 already-correct rows since their stored
  total already reads ~1).

  **Final verified state**: 14,252/14,252 rows have
  `total_institutional_pct` populated (was 7,259/14,252 before bug 2's
  fix), 0 rows outside [0,1] (was 5,524 before bug 1's fix, 3 after --
  now 0), 0 `UNCLASSIFIED` warnings on a full reprocess run. Range
  0.0-0.9263, mean 0.271.

- **2026-07-16**: Ran SHAP feature attribution + Platt/isotonic calibration
  correction for `model_14d`/`model_30d` (rolling-window strategy only,
  the one picked earlier), per `docs/model_build_spec.md` Sections 7 and
  10. Added `models/shap_and_calibration.py` and
  `splitting.add_calibration_split()` -- a proper 3-way fold split
  (model-fit train / calibration hold-out / test, each separated by its
  own embargo) so the calibrator is fit on data the underlying model never
  trained on, per spec Section 7's explicit requirement.

  **Real correction, not a confirmation**: SHAP does NOT support the
  earlier finding that `sh_promoter_pct` is 30d's top feature. Under mean
  |SHAP value| (summed across folds), it ranks 7th (0.346) -- `india_vix_close`
  (0.989), `fin_days_since_disclosure` (0.689), and `fin_eps` (0.620) are
  all well ahead of it. `india_vix_close` is the clear top feature for
  BOTH horizons under SHAP, more dominant than the split-count ranking
  suggested. The institutional-neglect hypothesis (does low promoter/
  institutional-holding carry real predictive weight?) is **not currently
  supported** by this more rigorous check -- the earlier split-count-based
  finding should not be treated as confirmed.

  **A genuine, unexpected finding about Platt scaling**: verified at full
  scale (not assumed) that calibration is supposed to be AUC-invariant
  (any monotonic transform preserves ranking) -- but Platt scaling's AUC
  changed substantially in several folds (14d fold 3: 0.5317 -> 0.4683,
  30d fold 3: 0.5386 -> 0.4614). Root cause, confirmed by checking the
  fitted Platt coefficients directly: whenever the raw model's AUC ON THE
  CALIBRATION SLICE ITSELF happened to dip below 0.5 (pure sampling noise
  -- the true signal is weak enough that a modest 60-trading-day subsample
  can land on the wrong side of random by chance), the Platt-fitted
  logistic regression learned a NEGATIVE coefficient, inverting the
  ranking when applied to the test set (0.5317 -> 0.4683 is exactly
  `1 - 0.5317`, a near-total rank inversion). Isotonic regression is
  structurally immune to this failure mode (constrained non-decreasing by
  construction, so it can flatten but never invert) -- its AUC only
  drifted slightly (tie-related, 0.001-0.03) in every fold, including the
  same ones where Platt inverted disastrously. **Recommendation: use
  isotonic, not Platt scaling, for this model** -- a specific, evidenced
  choice, not a generic preference.

  **Calibration correction is fold-dependent, confirmed by direct
  comparison, not assumed to universally help**: fold 2 (14d and 30d both
  -- calibration-slice AUC meaningfully above 0.5, 0.540 and 0.556
  respectively) shows isotonic correction clearly tightening
  miscalibration (e.g. 14d fold 2 at predicted=0.65: raw diff -0.106 ->
  isotonic diff +0.012). The fold-5-specific pattern flagged in the
  earlier LightGBM report (stayed under-confident even at high predicted
  probabilities) did NOT get fixed by isotonic correction -- its
  calibration slice had an AUC of 0.463, itself below random, so isotonic
  had no real signal to work with and instead collapsed 99.8% of test
  predictions into a single narrow bucket rather than correcting the
  pattern. Same root story for 30d fold 3 (calibration-slice AUC 0.464).
  Honest conclusion: calibration correction can only reshape signal that
  exists in the calibration slice, not manufacture signal that isn't
  there -- its reliability depends on having a large-enough, stable-enough
  calibration slice, which isn't guaranteed fold to fold when the
  underlying model's edge is this thin everywhere.

  Full report + interactive comparison charts (AUC raw/Platt/isotonic per
  fold, SHAP rankings, before/after calibration curves for a working vs.
  non-working fold): `models/reports/shap_calibration_report.json`
  (gitignored, regenerate via `python models/shap_and_calibration.py`).

- **2026-07-16**: Trained `model_14d` and `model_30d` (LightGBM) per
  `docs/model_build_spec.md` build order step 4, saved to the repo at
  `docs/model_build_spec.md` (updated with a new Section 2b requiring an
  expanding-vs-rolling-window comparison, motivated directly by the
  naive-baseline finding below). Each horizon trained TWICE — expanding
  window and rolling window (~1.9 years, fixed at fold 1's expanding-window
  training size so fold 1 is identical between strategies, isolating the
  window-strategy effect to folds 2-5) — using the same walk-forward/
  embargo splitting utility as the baselines, with test/embargo boundaries
  verified programmatically identical between strategies before training
  started.

  **Honest result, not smoothed over**: LightGBM does NOT clearly beat the
  momentum-only baseline in every fold. It's worse than `simple_logreg` in
  14d fold 4 and 30d folds 2 and 4 (both window strategies). Mean AUC
  across folds 2-5 (fold 1 excluded since it's identical either way): 14d
  — simple_logreg 0.5208, lightgbm_expanding 0.5200, lightgbm_rolling
  0.5297; 30d — simple_logreg 0.5246, lightgbm_expanding 0.5202,
  lightgbm_rolling 0.5243. **Window strategy pick: rolling** — equal-or-
  better mean AUC in both horizons and a meaningfully better result in the
  most recent fold (14d: 0.570 vs 0.535; 30d: 0.591 vs 0.580) for both
  horizons, though the margin is modest given how weak the overall signal
  is everywhere — not a decisive win, stated as such rather than oversold.

  **Calibration is fold-dependent, not a simple "overconfident" story**
  (the spec assumed gradient-boosted trees are typically overconfident —
  checked directly rather than assumed): folds 1-4 show the classic shape
  (under-confident at low predicted probabilities, over-confident at
  high ones), but fold 5 stays under-confident even at high predictions —
  tracking the same base-rate regime shift the naive-baseline bug
  investigation surfaced. Not yet corrected with a calibration step (Platt/
  isotonic, per spec Section 7) — flagged as a real next step, not done.

  **Feature importance confirms the spec's own horizon hypothesis**
  (Section 1: "shorter horizon leans more on momentum/catalysts, longer
  horizon leans more on fundamentals"): 14d's top features are macro/
  momentum-leaning (`nifty50_dist_50dma_pct`, `india_vix_close`,
  `volatility_20d`), while 30d's top feature is `sh_promoter_pct`
  (shareholding) with fundamentals (`fin_eps`, `cf_net_cash_flow`,
  `bs_total_assets`, `fin_sales`) more prominent throughout — a real,
  measured signal directly relevant to the project's "institutionally
  neglected" hypothesis, not yet confirmed via proper SHAP analysis
  (explicitly deferred per spec Section 10 until this basic loop was
  trustworthy — it now is, SHAP is the natural next step).

  Full comparison report (baselines + both window strategies, AUC/
  calibration/feature-importance charts, toggle between horizons):
  `models/reports/lightgbm_report.json` (gitignored, regenerate via
  `python models/train_lightgbm.py`).

  **Two corrections after independent review** of the numbers above (both
  checked directly against the raw report JSON and the DB, not assumed):
  (1) `sh_promoter_pct` is 30d's top feature only when averaged across all
  5 folds (699.4, clearly #1) -- in fold 5 specifically `fin_eps` edges it
  out (800 vs. 527, `sh_promoter_pct` 8th that fold). Averaging is the more
  trustworthy read (a single fold's ranking is noisier), so the conclusion
  stands, but stated now as "top on average across folds," not an
  unqualified "top feature." The "not yet confirmed via SHAP" caveat
  matters more than usual here specifically because the institutional-
  neglect hypothesis is a real project thesis, not just a nice-to-have --
  LightGBM's default split-count importance has known biases (can
  overweight frequently-splitting continuous features relative to actual
  predictive contribution), so this finding should be confirmed with real
  SHAP values before anyone leans on it.
  (2) The rolling window's `train_rows` isn't constant across folds despite
  a genuinely fixed 486-trading-day window (verified: exactly 486 days
  every fold, no boundary bug) -- it climbs steadily (200,218 -> 224,513
  for 14d, ~12% growth fold 1 to fold 5). Investigated directly rather than
  assumed: confirmed via each symbol's first `daily_prices` date that this
  is real new-listing/IPO growth entering the tracked universe over
  calendar time (407 symbols predate the backfill window entirely, then a
  steady +44/+19/+16/+21/+25 new symbols across the five fold windows in
  order) -- not a window-boundary artifact, and it affects both window
  strategies equally, so it doesn't undermine the expanding-vs-rolling
  comparison.

- **2026-07-16**: Fixed a real reporting bug in `models/train_baselines.py`
  the user caught by reading the baseline report closely: the naive
  baseline's precision/recall looked internally inconsistent (recall
  flipping to exactly 0 or exactly 1 in a pattern that didn't track the
  displayed "base_rate"). Root cause, confirmed by direct computation: the
  naive baseline's classification decision comes from the TRAIN fold's
  base rate compared to a 0.5 threshold (correct -- using test-fold
  information to build the prediction would itself be a leak), but
  `evaluate.py`'s reported `"base_rate"` field was `np.mean(y_true)` on
  the TEST fold -- two different numbers sharing one ambiguous label.
  Confirmed empirically across all 5 folds x 2 horizons: 8 of 10 land on
  opposite sides of 0.5 for train vs. test, explaining the "flip" exactly.
  Also surfaced a deeper, genuinely useful finding along the way: every
  fold's TRAIN base rate sits within ~2.5pp of 0.5 (0.492-0.525) even
  though TEST base rates swing 42.3%-58.3% -- expanding windows average
  out the regime swings, so the naive baseline's predicted class is
  effectively decided by ~1pp of noise, and precision/recall at a fixed
  threshold amplify that into a full 0-to-1 swing. AUC (mathematically
  exactly 0.5 for any constant predictor) is the metric that actually
  reflects this baseline's zero-information nature; precision/recall here
  are diagnostic, not comparable to the real model. Fixed by renaming the
  ambiguous field to `actual_rate` and having `run_naive_baseline()`
  return `train_base_rate` explicitly so it can be reported alongside it
  as `predicted_prob`, with an inline note whenever the two disagree in
  sign. Baseline report regenerated and re-published with both numbers
  shown side by side plus an explanation of the mechanism.

- **2026-07-16**: Started the model build (`model_build_spec.md`) in a new
  `models/` directory, kept separate from `src/`'s data-pipeline scripts.
  Re-verified Section 8's delisting/mid-history-dropout edge case against
  2 real events rather than trusting the spec (`IIFLWAM`→`360ONE` and
  `LTI`→`LTIMINDTREE`, both symbol changes during the 5-year backfill
  window) -- confirmed `compute_target_labels.py` already handles this
  correctly by construction (a row gets a 14d label but no 30d label right
  at the cutoff, then no rows at all afterward), not by any special-cased
  delisting logic. Flagged a deeper, separate survivorship-bias risk this
  doesn't cover: `daily_prices`' ~539-symbol universe came from a single
  `index_membership` snapshot, so any stock that left the Nifty 500
  *before* that snapshot was never backfilled at all -- a data-layer
  limitation, not fixable at the label layer.

  Built `models/splitting.py` (walk-forward/embargo cross-validation,
  Sections 4-5), `models/data_loader.py` (joins momentum features computed
  fresh from `daily_prices` + context features from `model_feature_matrix`,
  deliberately excluding `sector_*` columns since they're 0%/NULL for all
  current training history -- see `model_feature_matrix`'s schema note),
  `models/evaluate.py` (discrimination + calibration, Section 7), and
  `models/train_baselines.py` (Section 6, run BEFORE any real model per
  the project's own principle).

  Baselines run across 5 walk-forward folds per horizon (542,596 total
  rows, 537 symbols). Two real findings: (1) the "beats Nifty" base rate
  is NOT a stable ~50/50 -- it swings from 42.3% to 58.3% across folds,
  confirming the spec's suspicion that a cap-weighted index vs. an
  equal-weighted universe comparison isn't symmetric; (2) price/volume
  momentum alone has very weak discriminative power at this horizon (AUC
  ~0.48-0.55 across folds, barely above random) and mild overconfidence
  where it does predict away from the base rate (one fold: 0.55 predicted
  vs. 0.45 actual) -- sets a low, honest bar for the full LightGBM model
  to clear, not a strong one. Full per-fold results in
  `models/reports/baselines_report.json` (gitignored, regenerate via
  `python models/train_baselines.py`).

  Not yet built: the LightGBM models themselves (Sections 2-3, using the
  same splitting/evaluation utilities), SHAP-based feature analysis
  (explicitly deferred per Section 10 until the basic walk-forward loop
  with baselines is trustworthy -- which it now is).

- **2026-07-16**: Closed the open "does screener.in lag the NSE
  disclosure" question from the nightly-trigger design (`RUNBOOK.md`
  Stage 3) — screener.in typically updates same-day, so the nightly
  trigger isn't racing stale data. No design change needed; the nightly
  trigger + `run_periodic.sh` safety net already comfortably tolerate the
  "up to a week" delay bar even in an edge case.

- **2026-07-16**: Designed and built the nightly/periodic scheduling
  strategy (`RUNBOOK.md` Stages 2-3), ahead of actually scheduling cron --
  the job stays run-by-hand until the model itself is built. Added
  `src/trigger_quarterly_refetch.py`: reacts same-day to a results-
  disclosure announcement in `corporate_announcements` (reusing
  `screener_common.find_disclosure()`'s two validated patterns) and
  re-fetches just that symbol across all 4 screener.in tables, instead of
  sweeping the full ~500-symbol universe on a blind schedule. Considered
  using the earlier "Board Meeting Intimation" (advance notice) as the
  trigger instead, but confirmed live it's too sparse (376 rows vs. 51,048
  "Outcome of Board Meeting" rows in the 5-year corpus) to rely on. Added
  `run_periodic.sh` as the full-universe safety-net sweep (standalone, not
  called from `run_nightly.sh`) to catch whatever the nightly trigger
  misses. `run_nightly.sh` extended to also run `fetch_macro_sector.py`,
  `fetch_sector_membership.py`, `compute_target_labels.py`, and
  `assemble_feature_matrix.py` nightly, keeping the whole macro/sector
  pipeline current as new data lands.

  Tested live against the real June 2026 quarter-end catch-up window
  (01-06-2026 to 16-07-2026): correctly identified 13 symbols with a
  genuine results-disclosure announcement. 12 already had confirmed
  disclosure dates from earlier today's full-universe pull (re-confirmed,
  not newly discovered). The 13th, `ITI`, surfaced a real edge case: its
  June-window announcement was actually a *delayed March'26 filing* (SEBI
  LODR Q4/annual results, submitted late), not a June'26 result at all --
  correctly left with `disclosure_date = NULL` for that period since it
  falls outside the 65-day window, exactly the same late-filer pattern
  already seen with `BBTC`. Confirms the trigger can't assume "any
  disclosure in the window is for the expected quarter" -- it just reacts
  to a real disclosure and lets the existing window logic sort out which
  period it actually belongs to.

- **2026-07-16**: Added `model_feature_matrix` + `src/assemble_feature_matrix.py`
  -- Step 5 (final step) of the macro/sector shock feature build. Features
  only -- deliberately does NOT join in `model_target_labels`, so labels
  stay in their own table and can't accidentally end up on the feature
  side. Fixes all 3 bugs flagged in `macro_sector_shock_features.md`
  Section 5: sector membership joined via "most recent snapshot_date <=
  date" (not an exact-date match), last-known fundamentals via proper
  per-symbol ranking (not a bare-column `MAX()`, which only happens to
  work in SQLite and breaks on Postgres), and the announcement-derived
  `recent_order_dispute_flag_30d` checks both `subject` AND `details`
  (screener.in disclosure-matching already taught us NSE often files real
  content under a generic subject with the substance only in `details`).
  Multi-sector membership (a stock can legitimately be in >1 sectoral
  index) is handled by averaging `sector_daily_benchmarks` across every
  `sector_name` a symbol belongs to as of `date` -- an explicit policy,
  documented in schema.sql, not an arbitrary first-row pick.

  Ran against the full universe: 687,372 rows (matches `daily_prices`'
  total row count exactly -- every price row got a feature row). Coverage:
  80.1% macro (the ~20% gap traces to known daily_prices/macro_regime_indicators
  calendar mismatches, not a new issue), 52.1% fundamentals (recently-listed
  companies have less disclosure history, dragging down the average -- not
  a bug), 95.4% shareholding, 101,267 rows with a real order/dispute
  announcement flag. Zero rows with negative `fin_days_since_disclosure`
  -- confirmed no look-ahead leakage in the fundamentals join.

  **Real limitation found and accepted, not fixed**: `sector_count` is 0
  for 100% of rows right now. `sector_membership` is snapshot-based, same
  as `index_membership` -- it does NOT retroactively reconstruct historical
  sector membership, and the only snapshot taken so far is from today
  (2026-07-16), which is after every date currently in `daily_prices`.
  Accepted for now, same stance already taken on `index_membership`'s
  identical limitation -- `sector_count`/`avg_sector_*` will start
  populating for real going forward as more snapshots accumulate from
  running `fetch_sector_membership.py` periodically. Historical
  reconstruction (if NSE Indices publishes past sector reconstitution
  data) is a separate, not-yet-started task.

- **2026-07-16**: Fixed a real, pre-existing gap in `daily_prices` --
  ~300 trading days (out of ~1,250 in the 5-year history) had ZERO rows
  for every one of the 539 tracked symbols, 79% of them Fridays, often in
  consecutive-week chains. Discovered as a side effect of building
  `compute_target_labels.py` (it correctly returned NULL labels around
  these dates instead of guessing, which is what surfaced the gap).
  Confirmed live this is a real limitation of `jugaad_data`'s
  `stock_history` AJAX API (used by `fetch_daily_prices.py`) -- re-querying
  that exact API live, today, for multiple symbols (RELIANCE, TCS) on
  multiple known-missing dates still returns nothing; not fixable on our
  end since it's NSE's own API behavior. Added `src/backfill_price_gaps.py`,
  which routes around it via NSE's official bhavcopy settlement archive
  (a different, authoritative NSE endpoint, confirmed live to have full
  data for both a post- and pre- July 2024 UDiff-format-cutover date).
  Filled 301 of 302 fully-missing days (134,046 new rows across 538
  symbols), then recomputed `avg_traded_value_20d` across every affected
  symbol's complete history (a rolling 20-day calc that a single isolated
  inserted day can't compute correctly in isolation). Two rare dates
  intentionally left unfilled, both isolated NSE archive quirks rather
  than bugs: 2021-11-04 (Diwali Muhurat trading -- bhavcopy_raw() returns
  content, but its own DATE1 field is the prior day, 2021-11-03; caught by
  a date-match check added to `fetch_bhavcopy_rows()` rather than silently
  writing rows under the wrong date) and 2022-08-08 (the pre-UDiff CSV
  endpoint serves a raw ZIP file instead of plain text for this one date,
  which `jugaad_data`'s fallback doesn't unzip). Only FULLY-missing days
  were touched -- "partial coverage" days (some but not all symbols have a
  row) were deliberately left alone, since those are frequently legitimate
  (IPOs, delistings, individual halts) and need per-symbol judgment, not a
  blanket backfill.

- **2026-07-16**: Added `model_target_labels` + `src/compute_target_labels.py`
  -- Step 4 of the macro/sector shock feature build. Forward-looking
  training labels only (14d/30d stock return vs. NIFTY 50 return over the
  identical window, using `macro_regime_indicators.date` as the trading
  calendar so windows are in TRADING days, not calendar days) -- kept in
  its own clearly-named table specifically so it can't accidentally end up
  on the feature side of a training matrix. Rows near the end of available
  price history without a full forward window are excluded, not computed
  on a truncated window. This is the script that surfaced the
  `daily_prices` gap fixed above -- see that entry for the full story.

- **2026-07-16**: Added `sector_daily_benchmarks`, extending
  `src/fetch_macro_sector.py` (Step 3 of the macro/sector shock feature
  build). Reuses the exact same daily snapshot fetch as
  `macro_regime_indicators` -- every sector index's close is already in
  that one file, so this adds zero extra HTTP requests, just more parsed
  rows per day. `sector_relative_alpha_14d` (sector's 14d return minus
  NIFTY 50's 14d return over the identical window) computed at write time
  per the design doc. Tested live for 2026-07-01 to 2026-07-14 (150 rows,
  15 sectors x 10 trading days): every `sector_close` spot-checked exactly
  against the raw source CSV, and `sector_relative_alpha_14d` confirmed
  internally consistent across all 15 sectors for a given date (e.g.
  `Nifty Realty` 14d return 13.97% vs the implied ~0.96% NIFTY 50 baseline
  gives +13.01% alpha, and every other sector's alpha backs out to the
  same ~0.96% baseline). `SECTOR_NAMES` is imported from
  `fetch_sector_membership.SECTOR_CSV_FILES` as a single source of truth
  so the two scripts' sector name lists can't silently drift apart.

- **2026-07-16**: Added `sector_membership` + `src/fetch_sector_membership.py`
  -- Step 2 of the macro/sector shock feature build, and the actual fix for
  the sector-mapping bug flagged in `macro_sector_shock_features.md`: real
  official NSE Indices per-sector constituent CSVs instead of fuzzy-matching
  `index_membership`'s generic `industry` field. Confirmed all 15 target
  sectors live rather than assuming the URL naming pattern by analogy --
  13 followed the obvious `ind_nifty{sector}list.csv` pattern, but 2 didn't:
  `Financial Services` is `ind_niftyfinancelist.csv` (not
  `ind_niftyfinservicelist.csv`, which silently 200s with an HTML error page
  instead of a clean 404 -- niftyindices.com doesn't 404 on bad paths), and
  `Private Bank` is `ind_nifty_privatebanklist.csv` (note the underscore,
  unlike every other sector). 249 total constituent rows across 15 sectors,
  spot-checked against real symbols: `HDFCBANK` correctly lands in `Nifty
  Bank` + `Nifty Financial Services` + `Nifty Private Bank` simultaneously
  (the schema's multi-sector-membership design working as intended), `SBIN`
  in `Nifty Bank` + `Nifty Financial Services` + `Nifty PSU Bank`,
  `RELIANCE` in `Nifty Energy` + `Nifty Infrastructure` + `Nifty Oil & Gas`.
  `sector_name` values match the real NSE index names used in
  `fetch_macro_sector.py`'s daily snapshot exactly (e.g. `Nifty Bank`, not
  `NIFTY BANK`), so this joins cleanly once `sector_daily_benchmarks` exists.
  Same current-snapshot-only caveat as `index_membership` -- today's
  constituents only, doesn't retroactively reconstruct historical membership.

- **2026-07-16**: Added `macro_regime_indicators` + `src/fetch_macro_sector.py`
  -- Step 1 of the macro/sector shock feature build (`macro_sector_shock_features.md`).
  The design doc's planned approach (`jugaad_data.nse.index_df()` per-symbol
  against niftyindices.com's `Backpage.aspx` AJAX endpoint) is confirmed
  broken live: niftyindices.com has been redesigned onto a newer CMS
  (Sitefinity) and that endpoint now returns the site's homepage HTML
  instead of JSON, for every symbol, regardless of session/cookies/headers
  -- not a symbol-string problem, not fixable from our side. Found and
  switched to `jugaad_data.nse.NSEIndicesArchives.bhavcopy_index_raw(date)`
  instead -- a static daily CSV snapshot
  (`niftyindices.com/Daily_Snapshot/ind_close_all_DDMMYYYY.csv`) covering
  ALL ~161 NSE indices (confirmed live, including `Nifty 50` and
  `India VIX` by exact name -- note: NOT the all-caps `NIFTY 50`/`INDIA VIX`
  the design doc assumed) in one request per day instead of one request per
  index per date range, confirmed working back to at least 2021-01-04. Also
  confirmed a real gotcha: non-trading days (weekends/holidays) return
  HTTP 200 with the same homepage HTML as a broken request, not a clean
  404 -- `_parse_snapshot()` detects a real CSV via the header row, never
  trusts the status code alone. This also sets up the future
  `sector_daily_benchmarks` companion table cheaply, since the same daily
  file already contains every sector index's close too.

  Tested live for 2026-07-01 to 2026-07-14 (10 real trading days, both
  weekends correctly excluded) -- `nifty50_close`/`india_vix_close` values
  spot-checked exactly against the raw source CSV. Rolling features
  (`nifty50_return_5d/10d`, `nifty50_dist_50dma_pct`,
  `vix_change_5d_pts/pct`) computed over TRADING days (not calendar days),
  seeded with a 90-day fetch buffer before the requested range so the
  first requested row isn't NULL for lack of lookback history.

  Next steps per the design doc's build order: `fetch_sector_membership.py`
  (real NSE Indices per-sector constituent CSVs -- the actual fix for
  mapping symbols to sectors, not fuzzy-matching the generic `industry`
  field), then extending this script's same daily-snapshot mechanism to
  populate `sector_daily_benchmarks`, then `compute_target_labels.py`, then
  `assemble_feature_matrix.py`. Not yet built.

- **2026-07-15**: Fixed a real disclosure-matching bug in
  `screener_common.find_disclosure()` that was silently causing >99% of
  `financial_results` periods from 2023-2026 (well inside our
  `corporate_announcements` coverage) to be skipped. Root cause: NSE doesn't
  consistently title the results disclosure "Financial Results" — confirmed
  two real filing patterns via live data (subject `Outcome of Board
  Meeting` with the substance only in `details`, and subject filed directly
  as `Financial Result Updates`/`Financial Results Updates`), neither of
  which the original `subject LIKE '%financial result%'` filter caught
  well. `find_disclosure()` now matches both patterns.

  Also changed the "no match" behavior across all four screener.in-sourced
  tables (`financial_results`, `balance_sheet`, `cash_flow`, `ratios`):
  previously the row was dropped entirely (`continue` in `build_rows()`),
  losing the actual financial metrics whenever a disclosure date couldn't
  be confirmed. Now the row is still captured with `disclosure_date = NULL`
  (schema changed from `NOT NULL` to nullable on all four tables, migrated
  non-destructively on the local DB using explicit named-column
  `INSERT...SELECT`, verifying row counts matched before dropping the old
  table). A NULL `disclosure_date` is unusable as a point-in-time join key
  by construction — SQL's `NULL <= D` is never true, so any "what did we
  know as of date D" query excludes these rows automatically with no
  special-casing needed. This surfaces a real, common pattern instead of
  hiding it: some companies (e.g. BBTC/Bombay Burmah Trading Corporation)
  file quarterly results consistently *outside* the 65-day SEBI window,
  every quarter — the conservative window correctly refuses to guess which
  announcement is theirs, but now the metrics aren't lost either.

  Ran all four scripts against the full Nifty 500 universe in batches of 50
  symbols with a 3-minute cooldown between batches (mitigating a screener.in
  connection-refused incident mid-run — see below). Final counts:
  `financial_results` 12,039 rows/498 symbols (48.6% confirmed
  disclosure_date), `balance_sheet` 5,057/498 (30.7%), `cash_flow`
  5,024/496 (30.9%), `ratios` 4,982/496 (31.2%) — the lower match rate on
  the latter three vs. `financial_results` reflects that screener.in mostly
  reports balance sheet/cash flow/ratios on an annual cadence, so most
  individual quarters genuinely have no same-quarter disclosure to match
  against. Zero point-in-time window violations across all four tables
  (verified: no `disclosure_date` before `period_end_date` or beyond the
  65-day window).

  Mid-run, screener.in started refusing all connections
  (`Connection refused`, not a 429/empty-response) partway through a
  continuous full-universe pull — plausibly an IP-level response to the
  prior day's sustained load rather than downtime, though not conclusively
  distinguishable from the outside. Recovered by switching to a mobile
  hotspot (a personal secondary connection, not a proxy/VPN pool) and
  batching requests (50 symbols, 3 min cooldown between batches) instead of
  one continuous run — a more conservative load pattern generally, not just
  a way around the block. No further failures across the full run. Looked
  into scraping tickertape.in as a fallback source but declined — its
  Terms & Conditions explicitly prohibit copying/reproducing/reverse-
  engineering platform content, unlike screener.in where an established
  open-source scraper already existed. Also evaluated `indianapi.in`'s paid
  Indian Stock Market API as a longer-term, ToS-clean alternative (API-key
  based, not scraped) — covers the right data categories but pricing/field
  names/data quality unverified (docs are a JS-rendered SPA this session
  couldn't inspect); worth a real trial-account capture before building
  against it.

- **2026-07-15**: Ran the first full historical seed across the entire
  Nifty 500 universe (`./run_historical_seed.sh`, ~9 hours end-to-end,
  13:59–23:11) to establish a baseline and surface anything that breaks at
  real scale before model-building starts. Added `RUNBOOK.md` (operational
  companion to this file — *when* things run, not *what's* been built) and
  the orchestration script itself. Final row counts: `daily_prices` 553,712,
  `corporate_announcements` 813,037, `shareholding_pattern` 17,670,
  `financial_results` 6,459, `balance_sheet`/`cash_flow`/`ratios` ~1,550
  each, `surveillance_flags` 447, `index_membership` 1,000.

  Two real issues found and fixed along the way:
  - `backfill_prices.py` hung indefinitely (90+ min, no progress) after a
    transient DNS blip mid-run. Root cause: `jugaad_data`'s
    `NSEHistory._get()` calls `requests.Session.get()` with no `timeout=`
    at all — not fixable by editing the dependency directly, so `src/db.py`
    now monkeypatches a 20s default timeout onto `requests.Session.request`
    process-wide at import time (every fetch script already does
    `from db import get_conn`, so this covers `jugaad_data`,
    `screenerScraper.py`, and our own scripts without touching each one).
    The hung process was killed and the step re-run to completion
    (500/500 symbols). Confirmed the fix works as intended later in the
    same run: a second DNS blip during `fetch_cash_flow.py` (AAVAS, ABB,
    BLUEDART) failed fast instead of hanging, and a targeted re-run for
    those 3 symbols closed the gap cleanly.
  - `run_historical_seed.sh`'s log paths were relative and broke the
    instant the script did `cd src` partway through — every step failed
    silently (redirected into a nonexistent directory) before any Python
    ran. Fixed by computing `$PROJECT_ROOT`/`$LOGS_DIR` as absolute paths
    once at the top.

  One known, pre-existing gap confirmed (not introduced by this run): BSE
  and CDSL fail on all four screener.in-sourced scripts with "no BSE token
  found" — their screener.in company-page token doesn't match the NSE
  symbol. Not yet resolved; affects 2 of ~500 symbols.

- **2026-07-15**: Confirmed `cash_flow` (all 4 columns) and `ratios` (all 6
  columns) live against real RELIANCE data — both matched their original
  guesses exactly on the first try, same as `balance_sheet` earlier today.
  All four screener.in-sourced tables are now confirmed working with real
  data, none needed a `COLUMN_MAP`/`METRIC_ALIASES` fix.

  Added a `raw_metrics_json` column to all four tables (via new
  `screener_common.metrics_json()`) to address a real gap: different
  company types (bank vs manufacturer vs NBFC) use genuinely different line
  items, not just different labels for the same concept — a bank has no
  `CWIP`/`Investments` in the manufacturing sense, but has things like
  Interest Earned/Expended instead. `METRIC_ALIASES` (in
  `fetch_financial_results.py`) already handles "same concept, different
  label" (e.g. Sales vs Revenue); `raw_metrics_json` handles "different
  concept entirely" by storing the *complete* flattened per-period metrics
  dict alongside the named columns, so nothing is ever silently dropped
  regardless of company template — verified with a synthetic bank-shaped
  quarter (`InterestEarned`/`InterestExpended`/`NetInterestIncome`, none of
  which have named columns) landing intact in the JSON blob while `Revenue`
  still correctly populated `sales` via the alias. Local DB migrated
  non-destructively via `ALTER TABLE ADD COLUMN` across all four tables —
  all existing rows (74 financial_results, 5 each of balance_sheet/
  cash_flow/ratios) preserved.
- **2026-07-15**: Confirmed `balance_sheet` live against a real RELIANCE
  balance sheet — all 10 guessed column names (`EquityCapital`, `Reserves`,
  `Borrowings`, `OtherLiabilities`, `TotalLiabilities`, `FixedAssets`,
  `CWIP`, `Investments`, `OtherAssets`, `TotalAssets`) matched exactly, no
  code changes needed (the `\xa0` variants some keys carried were already
  handled by the shared normalization in `screener_common.flatten_periods()`).
- **2026-07-14**: Confirmed `financial_results` live against a real
  RELIANCE quarter and fixed what it found: several base metric keys carry
  a trailing non-breaking space (`'Sales\xa0'` etc.) that the vendored
  library's own key-cleaning misses, silently nulling out `sales`,
  `expenses`, `other_income`, and `net_profit` — fixed by normalizing keys
  centrally in new `src/screener_common.py` (shared by every screener.in
  script) rather than hardcoding the broken strings. Also fixed a casing
  mismatch (`Profitbeforetax`). Added 12 new columns for "addon" bonus
  fields confirmed in that same capture (YoY growth %, cost breakdowns,
  exceptional items, minority share, source PDF link) and wired in
  `pnlReport()` (annual P&L) into the same table via `--no-annual` to
  opt out. Local `data/nifty_pipeline.db`'s `financial_results` table (50
  rows from the prior buggy run) migrated non-destructively via `ALTER
  TABLE ADD COLUMN` — existing rows preserved, their null metric columns
  will self-correct on the next fetch run via the idempotent upsert.
  Added three new tables + scripts on the same architecture:
  `balance_sheet`, `cash_flow`, `ratios` (`fetch_balance_sheet.py`,
  `fetch_cash_flow.py`, `fetch_ratios.py`) — all share
  `src/screener_common.py`'s disclosure-date derivation, all **unverified**
  pending a live capture the same way `financial_results` just went
  through (each script's docstring has the exact diagnostic command).
- **2026-07-14**: Re-architected `financial_results` around screener.in
  (vendored `src/screenerScraper.py` from github.com/BuildAlgos/screener-scraper,
  added `beautifulsoup4` to `requirements.txt`, `src/tokens/` gitignored —
  its downloaded BSE ticker cache, not secrets). Schema upgraded to the
  granular metrics screener.in actually provides (`sales`, `expenses`,
  `operating_profit`, `opm_pct`, `other_income`, `interest`, `depreciation`,
  `profit_before_tax`, `tax_pct`, `net_profit`, `eps`), primary key changed
  to `(symbol, period_end_date, result_type)` since `disclosure_date` is now
  a derived value, not a natural key. Caught and fixed two issues in an
  earlier integration draft before it ever ran: (1) it defaulted
  `disclosure_date` to `datetime.now()` for any quarter it couldn't match
  via crude keyword text — since screener.in has no announcement timestamp
  at all, this would have silently mislabeled most historical quarters as
  "disclosed today"; fixed by deriving `disclosure_date` from our own
  confirmed `corporate_announcements` table (earliest "financial result"
  announcement within a 65-day SEBI-mandated disclosure window; no match =
  skip and log, never a fabricated date). (2) It assumed `quarterlyReport()`
  returns a `pandas.DataFrame`; it actually returns a `dict` of
  `{quarter_label: [{metric: value}, ...]}` — would have crashed
  immediately with `AttributeError` on the very first call. Tested
  end-to-end with synthetic data shaped like the vendored library's real
  return type, including both the disclosure-match and skip-on-no-match
  paths. Not yet run live (screener.in/BSE unreachable from this sandbox).
  Local `data/nifty_pipeline.db`'s `financial_results` table (0 rows, old
  schema) migrated the same way `shareholding_pattern` was — dropped and
  recreated, nothing lost.
- **2026-07-13**: Fixed a cryptic crash in `fetch_daily_prices.py` —
  `FAILED for RHIM: "None of [Index(['CH_TIMESTAMP', ...])] are in the
  [columns]"` — hit on a live same-day-only run across ~all symbols. Root
  cause (confirmed by reading `jugaad_data`'s source): its `stock_df()`
  crashes with that pandas error when NSE's API returns an empty list for
  a date, which happens when today's data isn't published yet (usually
  a few hours after market close, not immediately). `fetch_symbol()` now
  pre-checks with `stock_raw()` (no extra network cost — it's what
  `stock_df()` calls internally, and results are cached) and raises a
  clear message instead. This doesn't make the data appear sooner — if
  `run_nightly.sh` hits this regularly at 9pm IST, NSE's historical API
  may need more time after close than assumed.
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
