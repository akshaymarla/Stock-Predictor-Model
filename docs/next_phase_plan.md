# Next Phase — Gap Closures + Economic Backtest

Handoff doc for Claude Code. Combines the remaining known gaps with the
next real milestone (does any of this actually make money after costs).
Sections are numbered in the order they should be done — later sections
(especially the backtest) are more valuable with the gaps closed first,
but not blocked by all of them if time is tight; see Section 6 for what's
truly sequential vs. parallel-safe.

## 0. Read this first

Reconcile against the current real `README.md`/`schema.sql`/`CLAUDE.md`
before starting anything below — same standing instruction as every prior
doc. In particular, confirm whether sector features (Section 1) were
actually built, since I can't tell from here whether they're missing or
just unwired.

---

## 0b. CRITICAL BLOCKER (2026-07-19) — `daily_prices` corruption, must be resolved before Part A or Part B results are trusted further

**Do not proceed to Part B (or trust any prior Part A / model result) until
this is fully remediated.** Discovered while building the Part B backtest
(an absurd 867% single-fold average return led to this), but the root
cause predates Part B entirely and affects everything downstream of
`daily_prices`.

**The bug**: `jugaad-data==0.33.1`'s own `_stock()` method has an inverted
condition (`jugaad_data/nse/history.py:80`) that silently sends
`series="ALL"` to NSE's API even when `fetch_daily_prices.py` correctly
requests `series="EQ"`. This mixed non-equity instruments (mostly
corporate bonds/NCDs sharing a symbol string with an equity, explaining
why frequent bond issuers like `IFCI`, `PFC`, `NHPC`, `NTPC`, `RECLTD`
were worst-hit) into what should have been pure equity price data —
different price scale, much lower volume, silently blended in.

**Scope, confirmed by direct scan**: 91 of 539 symbols (~17% of the
tracked universe) affected, 4,508 anomalous price points, spanning
2021-07-14 to 2026-06-23 (essentially the entire backfill history).
Includes large, liquid, heavily-relied-on names: `HDFCBANK`, `WIPRO`,
`KOTAKBANK`, `NESTLEIND`, `BAJFINANCE`, `DRREDDY`, `BRITANNIA` (25.8% of
its own row count affected) among others.

**Fix applied (forward-only)**: `fetch_symbol()` now filters to
`series == "EQ"` using NSE's own per-row `CH_SERIES` field before
returning — verified against `IFCI` (29 contaminated rows correctly
dropped). This fixes all *future* fetches. It does NOT fix what's already
sitting in the database.

**Why every prior model result is now suspect, not just Part B**:
`daily_prices` feeds momentum features (`return_5d/10d/20d`,
`volatility_20d`, `volume_ratio_20d`) and `model_target_labels`
(`stock_return_14d/30d`, `alpha_14d/30d`, `outperform_flag`) directly.
Every baseline, LightGBM, SHAP, and calibration result produced anywhere
in this project so far — including the institutional-neglect hypothesis
test's "mixed/partial support" conclusion in
`institutional_attention_feature.md` Section 8 — was computed on data
where ~17% of the universe had corrupted momentum inputs and corrupted
labels. **The methodology (walk-forward/embargo, isotonic-over-Platt,
SHAP-over-default-importance) does not need to be rebuilt** — only
re-run. But treat every specific number produced so far as "pending
re-validation," not settled, until re-run on clean data — including
results that seem intuitively plausible; a subtle version of this
corruption (a bad row landing near the real price) would not have been
visually obvious the way `BRITANNIA`'s 5000-vs-30 split was.

**Remediation, in order — do not skip steps or reorder**:
1. Add `fetched_at` and `source` audit columns to `daily_prices` (this
   table was the one table in the project without them — exactly why
   this bug's origin couldn't be traced by run. Close this gap
   permanently while already doing a full re-backfill, not just for this
   incident).
2. Full re-backfill of `daily_prices` for the **entire ~539-symbol
   universe**, not just the 91 flagged symbols — the `>50%` single-day
   jump heuristic is a detection floor, not a completeness guarantee; a
   contaminated row landing close to the real price wouldn't produce a
   visible jump but would still be wrong. Reuse `backfill_prices.py`'s
   existing checkpointed/resumable design — this is exactly the
   long-running, interruptible job that infrastructure exists for.
3. Recompute `avg_traded_value_20d` (depends on `daily_prices`) and
   rebuild `model_target_labels` (depends on clean forward-looking
   returns) from scratch — do not patch either incrementally.
4. Re-run baselines → LightGBM → SHAP → calibration fresh. Compare
   against the old numbers to see what actually changed, rather than
   assuming the conclusions hold — the institutional-neglect result
   specifically deserves this scrutiny since it's the project's founding
   hypothesis test.
5. Add an automated sanity check (the same `>50%` single-day-move
   heuristic used to find this, logged/flagged on every nightly fetch) so
   the next instance of this class of bug is caught in hours, not
   discovered months later by a downstream symptom the way this one was.
6. Only after 1-5 are complete: resume Part B.

## PART A — Close known gaps (status below predates the 0b discovery — treat
as informative history, not current ground truth, until re-run per 0b)

## 1. Wire in sector features (or confirm they're already built and just not feeding the model)

The last SHAP export had zero sector-level features
(`sector_relative_alpha`, sector return/volume-ratio) — only the macro
half of `macro_sector_shock_features.md`'s design (VIX, Nifty momentum)
appears in the model. Two possibilities, check which is real:
- `sector_membership`/`sector_daily_benchmarks` were never actually
  built — go build them per that doc.
- They exist but `assemble_feature_matrix.py` doesn't join them in — fix
  the join (remember the point-in-time-safe "most recent snapshot on or
  before date t" pattern from that doc, not an exact-date match).

Either way, re-run SHAP once fixed to confirm sector features are
actually contributing something — don't just confirm they're present in
the matrix, confirm they show up with non-trivial importance, the same
rigor already applied to the institutional-attention features.

## 2. Fix catalyst detection — zero-cost path preferred (no LLM API budget available right now)

`recent_order_dispute_flag_30d` is the lowest-ranked feature in both
horizons (0.023/0.028) — confirms the brittleness flagged when it was
first built (one narrow regex misses most real phrasings). An
LLM-classification upgrade was scoped and built
(`src/classify_announcements.py`, logic-verified with `--mock`) but is
blocked on API budget the project doesn't want to spend right now. Try
the free options below first, in order — the bar to clear is low, since
the existing feature contributes almost nothing.

**2a. Check for a free win first: does NSE's raw announcement filing
already carry a structured category field?** Under SEBI Regulation 30,
material-event disclosures are supposed to be tagged by the filer under a
predefined list of event categories (e.g. "Award/Receipt of Order,"
"Litigation," etc.) — this may already exist in the raw
`corporate-announcements` API response and just never got captured, since
that fetch script's fields were a best-effort guess from the start and
were only verified for `subject`/`details`, not necessarily every field
present. **Check this via a real DevTools capture before building
anything** — if it's there, this becomes "parse an existing field," not a
classification problem at all: free, instant, and more reliable than any
inferred classification since it's the filer's own disclosure tag.

**2b. If 2a doesn't pan out — a properly expanded keyword/phrase
classifier.** The original regex had essentially one narrow pattern. A
real attempt at this approach hasn't actually been tried: build a curated
list of many real phrasings per category (order wins: "bags order," "wins
contract," "emerges as L1 bidder," "receives letter of award," "secures
purchase order," etc.; equivalent lists for litigation/regulatory
categories), checked against `details` as well as `subject` (the
subject-only lesson from `financial_results`'s disclosure-matching work
applies here too). Free, deterministic, fully inspectable.

**2c. If 2b's ceiling isn't high enough — a locally-trained classifier.**
TF-IDF or bag-of-words features + logistic regression or naive Bayes,
trained on a modest hand-labeled sample (a few hundred announcements,
labeled by hand — a one-time time cost, not a money cost). Runs entirely
locally, no API involved, and tends to generalize better than exact
keyword matching while staying inspectable (you can see which words drove
any given classification, unlike an LLM's reasoning).

**2d. Local LLM (e.g. via Ollama)** — if closer-to-Claude classification
quality is wanted without API spend, this trades API cost for local
compute instead. Worth it only if 2a-2c don't clear a reasonable bar.

**Only fall back to the already-built `classify_announcements.py` (real
Claude API calls) if 2a-2d don't produce a meaningfully better feature
than the near-zero original** — at that point it's a deliberate,
budgeted decision to spend on API usage for a specific, demonstrated gap,
not a default first choice.

**Retire, don't leave hanging**: whichever path is taken, if the result
still doesn't clear a real bar above the old near-zero feature, remove it
from the feature set entirely rather than let a contributing-nothing
feature sit in the matrix unexamined — a feature at ~0 importance isn't
neutral, it's one more thing that could be silently wrong without anyone
noticing.

### RESULT (2026-07-19)

2a found a real free win: `desc` in NSE's raw response — already captured
as `subject`, just never used this way — turns out to BE the SEBI
Regulation 30 structured category tag (262 distinct values, 100%
populated). No classifier needed at all; 2b-2d were skipped as
unnecessary. Built `src/classify_announcements_by_subject.py` (a
deterministic category→sentiment mapping), ran it for real on the
training universe (269,056 rows: 2.0% positive, 1.0% negative, 97.0%
neutral) at zero cost, and reassembled `model_feature_matrix` with real
flags.

**SHAP re-check: honest negative result.** Neither
`recent_negative_catalyst_flag_30d` (rank 32/32 both horizons, worse than
the old regex) nor `recent_positive_catalyst_flag_30d` (rank 31/32 [14d],
30/32 [30d], only marginally better) clears a meaningfully higher bar
than the old near-zero regex flag. Per the retirement criterion above,
**both flags are retired from `models/data_loader.py`'s
`ALL_FEATURE_COLUMNS`** — the classification itself
(`corporate_announcements.category`/`sentiment`) is real and kept for
potential future use, just not fed to the model as a boolean flag.
`classify_announcements.py` (the LLM path) remains unused — the free
approach already answered the question. Full detail in README changelog.

## 3. Close the `fin_opm_pct` null-rate question

Still open from the institutional-attention round: confirm whether
`fin_opm_pct` being exactly 0.0 SHAP in both horizons' fold 1 is a genuine
data-coverage gap in the earliest backfill window (2021-07-16 onward) or
something else. Direct null-rate check, cheap to close out.

---

## PART B — The real milestone: does this make money after costs

Everything evaluated so far (AUC, calibration, SHAP) describes the
*model*. None of it answers whether the model is *useful*. This part
does.

## 4. Portfolio-level economic backtest

**Design**:
- For each of the 5 existing walk-forward test folds (reuse the exact
  same fold boundaries already validated — don't redefine new ones),
  simulate a portfolio over that fold's test window:
  - On each rebalance date, use **that fold's own trained model** to
    score all eligible stocks (universe = `index_membership` snapshot
    valid as of that date, filtered by `surveillance_flags` per the
    project's standing junk-stock exclusion, same as everywhere else).
  - Rank by predicted probability, hold the top-N (test a few N values —
    e.g. 10, 20, 30 — report all of them, don't pick one arbitrarily).
  - Hold for exactly one label horizon (14d or 30d, matching the model),
    then rebalance — start with non-overlapping holding periods for
    simplicity; a staggered/overlapping-tranche design is more realistic
    but more complex, treat as a future refinement once the simple
    version works.
- **Apply real transaction costs at every rebalance** — brokerage + STT +
  slippage. Use a clearly-labeled, adjustable cost parameter rather than
  a hardcoded silent assumption — current Indian equity delivery cost
  structures (STT, brokerage rates) should be looked up fresh rather than
  assumed from training data, since these are the kind of rates that
  change and neither of us should guess at current figures. Report
  results at a couple of different cost assumptions (e.g. optimistic vs.
  conservative) so the sensitivity to costs is visible, not hidden behind
  one number.
- **Compare against, in every report**:
  - Buy-and-hold Nifty over the identical period (the actual benchmark
    the whole project is defined against)
  - An equal-weight random-N-stock portfolio from the same universe (the
    same "beat the naive baseline" discipline used throughout this
    project — a stock-picking model that can't beat random picks from
    the same universe isn't adding value, no matter what its AUC says)
- **Report per fold, not just aggregated** — same standing discipline as
  every evaluation in this project. A portfolio backtest that looks good
  on average but blows up in one fold is a materially different, more
  concerning result than uniform modest performance.
- **Metrics**: cumulative return, max drawdown, hit rate (fraction of
  rebalance periods where the portfolio beat Nifty) — be cautious about
  reporting a Sharpe-style ratio given the limited number of independent
  rebalance periods per fold; note the sample-size caveat explicitly
  rather than presenting it as precise.

**Point-in-time / survivorship traps to avoid — these are easy to get
wrong even with a working model already in hand**:
- Each rebalance date must use the model trained on data available *as
  of* that date — i.e. the correct walk-forward fold's model, not a
  single "final" model applied backwards across the whole test period.
- The universe at each rebalance date must reflect what
  `index_membership` actually looked like then, not today's constituent
  list — the same survivorship-bias trap `index_membership` was built to
  solve at the data layer could quietly reappear here at the backtest
  layer if this isn't explicit.
- **Handle mid-hold delisting/dropout explicitly**: if a held stock exits
  the universe before the holding period ends, define exactly what
  happens (e.g. mark at last available price, exclude and redistribute
  weight, etc.) — don't let this silently produce a wrong return number.
  This is the same edge case flagged in `model_build_spec.md` Section 8
  for labels; it needs an equally explicit answer here for portfolio
  returns.

## 5. Decision layer design (only after Section 4 has real numbers to react to)

Once the backtest shows what's actually achievable, design the practical
layer on top:
- **Position sizing**: equal-weight vs. probability-weighted (higher
  predicted probability gets more capital) — the backtest in Section 4
  should test both if feasible, since this is a real, cheap-to-test
  design choice, not just a detail to decide by default.
- **Minimum probability threshold**: should there be a floor below which
  no position is taken even if it would otherwise make the top-N (i.e.
  don't force capital into a full N names on periods where confidence is
  broadly low)?
- **Rebalance cadence**: matches the label horizon by default; note
  whether a different cadence (e.g. rebalancing more/less frequently than
  the label horizon) was considered and why or why not.

This section is explicitly downstream of Section 4 — don't design the
decision layer in a vacuum before seeing what the backtest actually shows
about achievable, cost-adjusted performance.

## 6. Build order

0. **Section 0b (`daily_prices` remediation) — supersedes everything
   below.** Do not start or continue Part A/B work until 0b's steps 1-5
   are complete. Any Part A work done before 0b's fix (sector features,
   catalyst detection, `fin_opm_pct`) should be re-checked once clean
   data is in place — those findings predate the corruption discovery.
1. Section 1 (sector features) and Section 3 (`fin_opm_pct`) can be done
   in parallel — independent of each other.
2. Section 2 (catalyst detection) — independent, can also run in
   parallel with 1 and 3.
3. Re-run SHAP once Section 1 is resolved, to confirm sector features
   actually contribute (not just present).
4. Section 4 (portfolio backtest) — start this once Section 1-3 are
   resolved, since the backtest should use the most complete, correct
   feature set available, not a stale one with known gaps still open.
5. Section 5 (decision layer) — only after Section 4 has real results.

## 7. Acceptance checklist

- [ ] `daily_prices` has `fetched_at`/`source` audit columns added
- [ ] Full ~539-symbol universe re-backfilled with the `series == "EQ"`
      filter fix in place — not just the 91 originally-flagged symbols
- [ ] `avg_traded_value_20d` recomputed and `model_target_labels` rebuilt
      from scratch on clean data
- [ ] Baselines, LightGBM, SHAP, and calibration re-run on clean data,
      old vs. new numbers compared explicitly rather than assumed stable
- [ ] Automated single-day-jump sanity check added to the nightly
      pipeline, so this class of bug is caught going forward, not just
      fixed retroactively
- [x] Confirmed whether sector features exist and are wired into the
      feature matrix — CLOSED as a known data-availability limitation,
      not a bug: `sector_membership` has exactly 1 snapshot,
      `assemble_feature_matrix.py`'s join is already point-in-time-correct,
      not fixable by code. No SHAP re-run applicable (columns aren't in
      `ALL_FEATURE_COLUMNS`). See README changelog for full detail.
- [x] Zero-cost catalyst-detection options tried before any LLM API
      spend — 2a found `subject` already IS the SEBI structured category
      (no LLM/keyword-classifier needed at all, 2b-2d skipped as
      unnecessary). Real classification run at zero cost (269,056 rows).
      SHAP re-check: honest negative result, neither flag clears a
      meaningfully higher bar than the old regex — both flags retired
      from the model's feature set per the doc's own criterion, with
      user confirmation. `src/classify_announcements.py` (LLM path)
      remains built but unused — not needed. See Section 2 RESULT and
      README changelog for full detail.
- [x] Whichever approach is used, checks the real source of truth, not
      just an inferred subset — `subject` is the filer's own disclosure
      tag (not free text needing `details` inspection at all, since the
      category classification problem turned out not to exist)
- [x] `fin_opm_pct` null-rate confirmed for the earliest backfill window —
      genuine sourcing gap (2021: 0%, 2022: 25%, 2023+: ~80-85%), not a
      bug, no code fix applicable.
- [ ] Portfolio backtest uses each fold's own trained model at each
      rebalance date — not one model applied across the whole test period
- [ ] Universe at each rebalance date reflects the correct historical
      `index_membership` snapshot, not today's constituent list
- [ ] Mid-hold delisting/dropout has an explicit, documented handling rule
- [ ] Transaction costs are parameterized and looked up fresh, not
      silently assumed or hardcoded from possibly-stale figures
- [ ] Backtest compared against BOTH buy-and-hold Nifty AND a
      random-N-stock baseline from the same universe, every fold
      individually reported
- [ ] Multiple top-N values tested and reported, not a single arbitrary
      choice
- [x] README.md status table + changelog updated per CLAUDE.md's standing
      instruction
