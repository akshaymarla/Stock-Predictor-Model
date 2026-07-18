# Nifty Alpha Pipeline

Data pipeline for the Nifty 500 stock-screening project. Every code change
gets a changelog entry below so this stays an accurate running log of
progress — check the bottom of this file for the latest status.

## Tables implemented so far

| Table | Script | Status |
|---|---|---|
| `daily_prices` | `src/fetch_daily_prices.py` | Working (confirmed against live NSE data). ~300 fully-missing trading days found and fixed 2026-07-16 via `src/backfill_price_gaps.py` (NSE's `stock_history` API has a real gap NSE's own bhavcopy archive doesn't) -- see changelog; 2 rare dates remain unfilled by design |
| `surveillance_flags` | `src/fetch_surveillance.py` | Working (ASM confirmed + fixed against live NSE data; GSM wrapper shape confirmed, item field names unconfirmed pending a non-empty response) |
| `index_membership` | `src/fetch_index_membership.py` | Working (confirmed against a real niftyindices.com CSV; current-snapshot only, see caveat below) |
| `corporate_announcements` | `src/fetch_corporate_announcements.py` | Working (confirmed against live NSE DevTools response) |
| `financial_results` | `src/fetch_financial_results.py` | Working, full-universe pull confirmed 2026-07-15 (498/500 symbols, 12,039 rows) — quarterly only; alias-based mapping for company-template variance; disclosure_date confirmed for 48.6%, rest captured with disclosure_date=NULL (see below) |
| `balance_sheet` | `src/fetch_balance_sheet.py` | Working, full-universe pull confirmed 2026-07-15 (498/500 symbols, 5,057 rows); disclosure_date confirmed for 30.7% (mostly annual-cadence reporting, see below) |
| `cash_flow` | `src/fetch_cash_flow.py` | Working, full-universe pull confirmed 2026-07-15 (496/500 symbols, 5,024 rows); disclosure_date confirmed for 30.9% |
| `ratios` | `src/fetch_ratios.py` | Working, full-universe pull confirmed 2026-07-15 (496/500 symbols, 4,982 rows); disclosure_date confirmed for 31.2% |
| `shareholding_pattern` | `src/fetch_shareholding_pattern.py` | Working (confirmed end-to-end against live NSE data; dynamic universe, quarterly cadence so not in nightly by default) |
| `macro_regime_indicators` | `src/fetch_macro_sector.py` | Working (confirmed live 2026-07-16 -- NIFTY 50/India VIX closes + rolling returns spot-checked against raw source data). First table of the macro/sector shock feature set (`macro_sector_shock_features.md`) -- see changelog for a real sourcing pivot away from the original design doc's plan |
| `sector_membership` | `src/fetch_sector_membership.py` | Working (confirmed live 2026-07-16 -- all 15 sector constituent CSVs resolved, 249 rows, spot-checked against real symbols e.g. HDFCBANK correctly in Bank+Financial Services+Private Bank, RELIANCE in Energy+Infrastructure+Oil & Gas). Current-snapshot only, same caveat as `index_membership` |
| `sector_daily_benchmarks` | `src/fetch_macro_sector.py` | Working (confirmed live 2026-07-16 -- sector closes for all 15 sectors spot-checked exactly against raw source data; `sector_relative_alpha_14d` internally consistent across every sector for a given date). Sourced from the same daily snapshot as `macro_regime_indicators`, zero extra requests |
| `model_target_labels` | `src/compute_target_labels.py` | Working (confirmed live 2026-07-16 -- 542,596 rows across 539 symbols after the daily_prices gap fix, up from 398,243 before it). Forward-looking TRAINING LABELS ONLY -- never join into the feature side of a training matrix |
| `model_feature_matrix` | `src/assemble_feature_matrix.py` | Working (confirmed live 2026-07-16 -- 687,372 rows, matches daily_prices exactly; fundamentals join verified zero look-ahead leakage). FEATURES ONLY -- sector_* columns are currently 0/NULL for all historical rows, a known accepted limitation (see changelog), not a bug |

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

- **`fetch_financial_results.py`**: completely re-architected 2026-07-14
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
