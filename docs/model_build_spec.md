# Model Build Spec — Outperformance Probability Models (14d / 30d)

Handoff doc for Claude Code. Consolidates the model-design discussion from
claude.ai into a concrete build spec. Read this alongside
`macro_sector_shock_features.md` and the current `README.md`/`schema.sql`
— this assumes `model_target_labels` (via `compute_target_labels.py`) and
the feature-assembly pipeline exist and are point-in-time correct before
any of this starts.

## 1. Scope: two fully separate models

Build `model_14d` and `model_30d` as two independent models, not one model
serving both horizons. Different label horizons likely have different
dominant signal sources (shorter horizon leans more on momentum/catalysts,
longer horizon leans more on fundamentals) — don't force a shared
architecture or shared hyperparameters between them. Separate training
scripts, separate saved model artifacts, separate evaluation reports.

## 2. Model family: gradient-boosted trees (LightGBM, primary choice)

Reasons this is the right family for this specific problem, not just a
default choice:
- **Native missing-value handling.** This dataset has real, structural
  missingness (48.6% disclosure-match rate on financials, sparse
  announcements, NULL sector/macro joins for early history) — trees learn
  which branch to send missing values down during training rather than
  needing an imputation step that could itself inject bias.
- **Captures conditional/interaction logic without hand-engineering it** —
  e.g. "an order-win catalyst matters, unless VIX is currently spiking"
  is exactly the kind of split-based logic trees learn on their own from
  data, rather than needing an explicit interaction feature built by hand.
- **Feature importance / SHAP values** — needed for interpretability,
  since testing the "institutionally neglected" hypothesis empirically
  (does low-FII/DII-holding actually carry predictive weight, or not) is
  a stated project goal, not just raw prediction accuracy.

XGBoost is an acceptable substitute if there's a concrete reason to prefer
it (e.g. tooling familiarity) — the validation scheme and everything else
in this doc applies identically either way.

## 2b. Rolling vs. expanding window — decided empirically, not by default

**Do not default to expanding window by inertia.** The baseline run (see
`README.md`/changelog for the corrected naive-baseline results) found
direct empirical evidence that matters here: with an expanding window,
every fold's **training** base rate sits within ~2.5pp of 0.5
(0.492–0.525), while **test-period** base rates swing 42.3%–58.3% across
the same folds. This means the expanding window is averaging away real,
substantial regime variation that clearly exists in the data — not a
hypothetical concern, a measured one.

**Required**: train both `model_14d` and `model_30d` twice — once with an
expanding window, once with a rolling (fixed-length lookback) window —
using the identical fold/embargo boundaries already validated in the
baseline run. Compare AUC, calibration, and per-fold metrics side by side
between the two window strategies, same as baselines are compared against
the real model. Pick whichever generalizes better across folds, and state
the choice with the comparison numbers in the report — don't pick one
silently.

If a rolling window is used, pick the lookback length deliberately (e.g.
2 years, 3 years) and state the reasoning — a lookback too short
sacrifices training data for limited regime-responsiveness gain, too long
re-introduces the smoothing problem this comparison exists to catch.

## 3. Data shape: pooled cross-sectional panel

One row = one `(symbol, date)` pair. Train **one model across the full
~500-stock universe pooled together** — not one model per stock. A few
years of daily data per individual stock is too little to learn from
reliably in isolation; pooling assumes similar situations behave similarly
across companies, which is both more data-efficient and the actual
modeling bet being made (that a given combination of catalyst + sector
context + macro regime + fundamentals tends to produce similar
forward-looking behavior regardless of which specific company it is).

## 4. THE critical leakage risk: overlapping label windows

This is the single most important thing to get right in this spec, and it
is NOT the same failure mode as the point-in-time feature leakage already
handled at the data layer — it's a second, separate leakage risk specific
to model validation.

**The problem**: the label for date `t` is "did the stock beat Nifty over
the *next* N days" (N = 14 or 30). The label for `t` and the label for
`t+1` share N-1 of their N days — adjacent rows' labels are highly
correlated, not independent. A naive chronological train/test split (e.g.
"train before June 1, test after") looks clean by date but isn't clean by
information content: rows just before the split date have label windows
that extend *past* the split, meaning test-period information has
effectively leaked backward into training labels.

**The fix — embargo/purge at every split boundary**: leave a gap of at
least N days (the label horizon) between the end of any training slice and
the start of the following test/validation slice. No row's label window
should overlap into a period whose data was excluded from that row's
training slice by intent (e.g. future data belonging to the test period).

```
[-------- train --------][ embargo gap = N days ][-------- test --------]
```

This is a well-established technique in financial ML (often called
purged/embargoed cross-validation) specifically because of this
overlapping-label problem. Do not skip it or treat it as optional —
skipping it will produce backtest metrics that look good and are wrong.

## 5. Validation scheme: walk-forward, not a single static split

Rather than one train/test split, retrain periodically to simulate actual
usage:

```
Fold 1: [====train====][gap][==test==]
Fold 2:      [====train====][gap][==test==]
Fold 3:           [====train====][gap][==test==]
```

- Use either an expanding window (train grows each fold, always starts
  from the earliest available data) or a rolling window (fixed-size
  training window that slides forward) — expanding is the simpler default
  unless there's a specific reason to believe older data actively hurts
  (e.g. a genuine regime change you don't want the model learning from
  anymore).
- Each fold's `[gap]` is the embargo from Section 4.
- Given the backfilled history spans 2022–present (multiple genuinely
  different regimes: rate-hike cycles, elections, the screener.in data
  transition itself), aim for enough folds to see performance across more
  than one regime — a single fold covering only one calm period will look
  falsely reassuring.
- Report metrics **per fold**, not just averaged across folds — a model
  that does great in 4 folds and terribly in 1 is a different, more
  concerning result than uniformly mediocre performance across all 5, even
  if the average looks similar.

## 6. Baselines — build and report these BEFORE the real model, not after

Non-negotiable given this project's own stated principle (a model that
can't beat a naive baseline isn't adding value, regardless of how
sophisticated it looks). Two baselines, both evaluated with the exact same
walk-forward/embargo scheme as the real model (an unfair comparison is
worse than no comparison):

1. **Naive baseline**: predict the historical base rate of "beats Nifty"
   from the training fold (check empirically whether this is actually
   close to 50/50 — a cap-weighted index vs. an equal-weighted stock
   universe means it plausibly is NOT exactly 50/50, and that alone is
   worth knowing before interpreting anything else).
2. **Simple baseline**: logistic regression on price/volume momentum
   features only (no fundamentals, no announcements, no macro/sector) —
   isolates how much the "extra" data actually contributes once it's
   added in the full model.

Report both baselines' metrics side by side with the full LightGBM model's
metrics in the same table, every fold. If the full model doesn't clearly
beat these, that is important information to surface directly, not a
result to bury or re-run until it looks better.

## 7. Evaluation metrics: calibration matters as much as discrimination

The actual deliverable is a probability percentage someone acts on, not
just a ranking — so a well-discriminating-but-badly-calibrated model is a
real problem, not a minor caveat.

- **Discrimination**: AUC-ROC, precision/recall at a few threshold
  points — standard, still worth reporting.
- **Calibration**: bucket predictions (e.g. 0-10%, 10-20%, ... 90-100%)
  and check actual realized outperformance rate within each bucket against
  the bucket's midpoint. Plot this (predicted vs. actual) per fold.
  Gradient-boosted trees are often overconfident out of the box — if
  calibration is poor, apply a calibration step fit on a held-out slice,
  not on the same data used to fit the underlying model.

  **Use isotonic regression, not Platt scaling — confirmed requirement,
  not a preference.** Verified directly (2026-07-18, see README
  changelog): when the raw model's AUC on the calibration slice dips below
  0.5 by sampling noise alone, Platt's logistic fit learns a negative
  coefficient and **inverts the ranking** on test data. Confirmed three
  separate times across the fold/horizon grid — 14d fold 3
  (0.532→0.468), 30d fold 3 (0.539→0.461), 14d fold 5 (0.516→0.484) — in
  all three cases the pre/post AUC pair sums to exactly 1.000, the
  mathematical signature of a fully inverted ranking. Isotonic regression
  is structurally immune to this (monotonic by construction — it can
  flatten a ranking toward uninformative, but cannot invert it). Do not
  use Platt scaling anywhere in this pipeline.

  **Calibration reshapes existing signal, it does not create signal that
  isn't there.** Confirmed directly: isotonic correctly improved
  calibration where the calibration slice had real signal (14d fold 2:
  diff at predicted=0.65 went from -0.106 to +0.012). Where the
  calibration slice itself had near-random or below-random AUC (14d fold
  5, calibration-slice AUC ~0.46), isotonic correctly collapsed ~99.8% of
  predictions into a single bucket rather than manufacturing a false
  improvement. **This is the correct, desired behavior of a
  well-implemented calibrator, not a bug** — a calibrator that "fixes" a
  fold with no real underlying signal would be hiding a problem, not
  solving one. Don't chase further calibration tuning on folds where the
  raw model itself has no signal; fix the features/model for that regime
  instead — calibration cannot substitute for it.
- Report calibration alongside AUC in every evaluation output — a model
  selected purely on AUC without checking calibration is a real risk given
  the "probability %" framing of the whole product.

## 7b. Feature importance: use SHAP, not default split-count/gain importance

**Confirmed directly (2026-07-18) that LightGBM's default feature
importance is misleading for this dataset, not just theoretically
biased.** An earlier report found `sh_promoter_pct` as the top feature for
the 30d model using default (split-count) importance. Re-checked with
proper SHAP values on the same folds: `sh_promoter_pct` actually ranks
**7th** (mean |SHAP| 0.346, summed across folds), well behind
`india_vix_close` (0.989, clear top feature for both horizons),
`fin_days_since_disclosure` (0.689), and `fin_eps` (0.620). Use SHAP for
any feature-importance claim in this project going forward — default
importance measures how often/how much a feature is used to split, not
how much it actually moves the prediction, and the two disagreed
substantially here.

**Correction to the institutional-neglect hypothesis test**: the
SHAP-corrected result does NOT mean the institutional-neglect hypothesis
(the project's original differentiator idea) is false — it means
`sh_promoter_pct` specifically isn't a strong predictor. `sh_promoter_pct`
measures promoter/insider ownership, not institutional (FII/DII)
attention — a different concept. This project has never actually had a
clean feature testing the real hypothesis: `shareholding_pattern`'s build
spec flagged from the start that NSE's raw filing doesn't natively split
Public into FII/DII (see README/CLAUDE.md), so `fii_pct`/`dii_pct` have
sat NULL since that table was built. **The institutional-neglect
hypothesis has not yet been properly tested — the feature needed to test
it doesn't exist yet.** Building a genuine FII/DII holding-change feature
(or a reasonable proxy, e.g. analyst coverage count) is a real prerequisite
before drawing any conclusion about this hypothesis, not a nice-to-have.

## 8. Edge case: labels for stocks that exit the universe mid-window

If a stock is delisted, suspended, or drops out of the Nifty 500 partway
through its forward 14d/30d window, "did it beat Nifty" is not well-defined
for that row.

- These rows must be **excluded from training and evaluation**, not
  computed on a truncated/stale window.
- This should already be handled by `compute_target_labels.py` per its
  spec in `macro_sector_shock_features.md` Section 6 (rows without a full
  forward window are NULL/excluded) — but re-verify this specifically for
  the delisting/dropout case, not just the "near the end of available
  history" case, since a mid-history delisting can create the same
  truncated-window problem anywhere in the timeline, not just at the very
  end.
- Silently computing a truncated window here would quietly reintroduce a
  version of the survivorship-bias problem `index_membership` was built to
  solve at the data layer — don't let it back in at the label layer.

## 9. Feature set — pull from what's already built, exclude what's not ready

Use the tables/features confirmed working per `README.md`'s status table
at time of training — do not include a feature source still marked
unverified. Concretely, as of this doc:

- **Include**: `daily_prices`-derived momentum/liquidity features,
  `surveillance_flags` (as an exclusion filter, not a training feature —
  ASM/GSM-flagged stocks should likely be excluded from the universe
  entirely, consistent with the original "remove junk stocks" principle),
  confirmed financial figures, macro/sector context once
  `macro_sector_shock_features.md`'s build is confirmed working.
- **Flag explicitly in the training report which feature sources were
  available for which time periods** — e.g. if macro/sector features only
  exist from a certain backfill date onward, earlier folds trained without
  them are not directly comparable to later folds that had them. Don't let
  this silently vary the feature set across folds without it being visible
  in the report.

## 10. Build order

1. Confirm `model_target_labels` is correctly built and delisting/dropout
   edge cases (Section 8) are verified, not just assumed from spec.
2. Build the walk-forward/embargo splitting logic (Sections 4-5) as a
   reusable utility — both baselines and the real model must use the
   exact same splitting code, not separately re-implemented logic that
   could subtly differ.
3. Run and report both baselines (Section 6) across all folds.
4. Train `model_14d` and `model_30d` (LightGBM) using the same splitting
   utility, full available feature set per Section 9 — **twice each**,
   once expanding-window and once rolling-window, per Section 2b. Compare
   before picking one.
5. Evaluate: discrimination + calibration (Section 7), per fold, alongside
   the baselines in the same report.
6. Only after this full loop runs cleanly end-to-end: consider
   hyperparameter tuning, feature selection, or SHAP-based feature-value
   analysis — not before the basic walk-forward loop with baselines is
   trustworthy.

## 11. Acceptance checklist

- [ ] Rolling-window and expanding-window variants both trained and
      compared side by side (AUC + calibration + per-fold), with the
      choice between them stated explicitly along with the comparison
      numbers — not defaulted silently
- [ ] Embargo/purge gap implemented and applied identically to baselines
      and the real model
- [ ] Walk-forward folds span more than one visibly different market
      regime, not just one calm period
- [ ] Per-fold metrics reported, not just an average across folds
- [ ] Both baselines reported side by side with the full model in every
      evaluation output
- [ ] Calibration curve checked and reported alongside AUC, per fold
- [ ] Calibration uses isotonic regression, NOT Platt scaling — confirmed
      requirement given Platt's demonstrated ranking-inversion failure
      mode on this data
- [ ] Any feature-importance claim uses SHAP values, not default
      split-count/gain importance — confirmed to disagree substantially
      on this dataset
- [ ] Institutional-neglect hypothesis is not treated as tested/settled
      until a genuine FII/DII (or equivalent institutional-attention)
      feature exists — `sh_promoter_pct` is not a valid stand-in for it
- [ ] Delisting/mid-history dropout rows confirmed excluded, spot-checked
      against a couple of real historical delisting/exclusion events
- [ ] Feature availability by time period is visible in the report, not
      silently inconsistent across folds
- [ ] `model_14d` and `model_30d` are fully separate artifacts with
      separate evaluation reports
- [ ] Update `README.md`'s status table + changelog per `CLAUDE.md`'s
      standing instruction
