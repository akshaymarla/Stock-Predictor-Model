# Reports Archive + Weekly Shortlist — Design + Build Spec

Handoff doc for Claude Code. Two related, independent pieces: a small
process fix (stop losing analytical history between runs) and a real
tool (make the weekly manual-review workflow actually useful, given how
this pipeline is genuinely being used right now — a screening aid feeding
human judgment, not an unattended allocator).

## PART A — Reports archive convention

## 1. The problem

`models/reports/` is fully gitignored. Full reports are large and
verbose, so that's reasonable — but it means every prior run's numbers
become permanently unrecoverable the moment a new run overwrites them or
a rebuild happens. This has already cost real information at least once
(a claimed connection between an early baseline finding and a later
fold's characteristics couldn't be checked because the underlying report
was gone and only a lossy summary of it survived in conversation memory).

## 2. The fix — a small, deliberate exception, not a reversal of the gitignore

Keep full reports gitignored (they're genuinely too large/verbose to
track). Add a **compact JSON summary** per major run, written to
`models/reports/archive/`, and git-track that specific subfolder as an
exception:

```
# in .gitignore
models/reports/*
!models/reports/archive/
!models/reports/archive/**
```

Naming convention: `<run_type>_<YYYYMMDD>.json`, e.g.
`lightgbm_20260718.json`, `backtest_20260719.json`,
`shap_calibration_20260718.json`. One file per run, never overwritten —
if the same run type happens twice in a day, append a suffix rather than
clobber the earlier file.

## 3. What the compact summary should contain

Small and fixed-schema, not a dump of the full report — just enough to
answer "what happened in this run" without needing the full artifact:

```json
{
  "run_type": "lightgbm | baseline | backtest | shap_calibration | ...",
  "run_date": "YYYY-MM-DD",
  "git_commit": "<short hash at time of run>",
  "horizons": {
    "14d": {
      "folds": [
        {
          "fold": 1,
          "train_start": "...", "train_end": "...",
          "calib_start": "...", "calib_end": "...",
          "test_start": "...", "test_end": "...",
          "n_test": 0,
          "actual_rate": 0.0,
          "auc_raw": 0.0, "auc_platt": 0.0, "auc_isotonic": 0.0,
          "top_5_shap_features": [["feature_name", 0.0], ...]
        }
      ]
    }
  },
  "notes": "any one-line caveat worth attaching to this specific run"
}
```

Adjust fields per run type (a backtest run's summary should carry
per-strategy Calmar/return/drawdown instead of AUC, for example) — the
point is a small, consistent, always-populated schema per run type, not
a rigid one-size-fits-all format.

## 4. Standing instruction (add to `CLAUDE.md`)

Every major run (baseline, LightGBM, SHAP/calibration, backtest) writes
its compact summary to `models/reports/archive/` as part of that run,
not as an afterthought — same discipline as the README changelog update
already required after every code change. Do this going forward, and
optionally backfill compact summaries for the last few significant runs
from whatever's still recoverable now, before more rebuilds erase them
too.

---

## PART B — Weekly shortlist with per-stock explanations

## 5. Why this, not more backtest/decision-layer work

Confirmed actual usage: this pipeline feeds a weekly manual-review step
— top-ranked names get cross-checked against news/fundamentals by hand
before anything is acted on. It is not running unattended and not sized
as a mechanical allocator. Given that, the regime-scaling/position-sizing
decision-layer work (valuable, already built) is more relevant to a
*future* less-supervised phase — it's not what's actually in the critical
path for how this gets used today. What *is* in that critical path: making
the weekly ranking itself more useful to a human who's about to go verify
it by hand.

## 6. What to build: `src/weekly_shortlist.py`

**Not a backtest** — a live scoring run against the most current data
available, producing a ranked shortlist with an explanation attached to
each name.

- **Model**: use a model trained on ALL available history through the
  most recent data point — not one of the walk-forward evaluation folds
  (those hold out data deliberately for honest evaluation; a live
  production run has no future to leak from, so use everything available).
  If a "production" training script doesn't exist yet as distinct from
  the walk-forward evaluation training code, this is the point to build
  one — same model family/hyperparameters, just trained on the full
  history rather than a held-out-respecting fold.
- **Universe**: today's `index_membership` snapshot, filtered by
  `surveillance_flags` (exclude ASM/GSM-flagged names, same standing
  junk-stock rule as everywhere else) and the liquidity filter. Report
  how many names got excluded and why, so it's visible that filtering
  happened, not silent.
- **Ranking**: score the full filtered universe, take the top-N
  (configurable, e.g. default 20).
- **Per-stock explanation — the actual point of this tool**: for each
  shortlisted stock, compute real per-stock SHAP values (not aggregate
  feature importance) and surface the top-5 contributing features with:
  - the feature's SHAP contribution for this specific stock
  - the stock's actual current value for that feature
  - in human-readable form — e.g. "Ranked highly primarily due to: rising
    mutual fund holdings (+2.3pp QoQ), low India VIX (11.2, calm regime),
    strong 20-day momentum" rather than raw feature names and numbers.
- **Calibrated probability, shown alongside raw**: report both the raw
  model output and the isotonic-calibrated probability — the calibrated
  one is the more honest number to actually look at, but showing both
  keeps this consistent with how every other evaluation in this project
  has treated calibration as a first-class concern, not an afterthought.
- **Regime context flag**: include the current values of the macro
  features that have consistently mattered across every evaluation this
  project has run (`india_vix_close`, `nifty50_dist_50dma_pct`, etc.) and
  a simple flag if current conditions resemble a historically
  weak-signal/high-uncertainty regime (e.g. VIX elevated relative to its
  own recent range) — not to block the shortlist, just to hand the human
  reviewer the same context the model itself is conditioning on.
- **Output**: both a machine-readable format (CSV or JSON, for keeping a
  personal history of past weeks' shortlists) and a human-readable
  summary (markdown is fine, or an HTML report matching the style of
  prior artifacts in this project) — the human-readable version is what
  actually gets read during the weekly review, the machine-readable one
  is for your own future reference/tracking of shortlist history over
  time.

## 7. Build order

1. Confirm/build the production-training approach (full-history model,
   distinct from walk-forward evaluation training) — Section 6.
2. Universe filtering + ranking (reuses existing
   `index_membership`/`surveillance_flags`/liquidity logic, no new design
   needed here).
3. Per-stock SHAP explanation generation — the core new piece.
4. Calibrated-probability + regime-context additions.
5. Output formatting (both machine- and human-readable).
6. Part A's archive convention should be live before this starts
   generating regular output, so each week's shortlist run also
   contributes to the historical archive rather than being another thing
   that's lost after the fact.

## 8. Acceptance checklist

- [x] `models/reports/archive/` git-tracked exception added to
      `.gitignore`, compact-summary schema defined and used
- [x] `CLAUDE.md` updated with the standing "write archive summary every
      major run" instruction
- [x] Weekly shortlist uses a full-history production model, not a
      walk-forward evaluation fold's model
- [x] Universe filtering (index membership + surveillance flags +
      liquidity) applied and exclusion counts reported visibly
- [x] Per-stock explanations use real SHAP values for that specific
      stock, not aggregate/global feature importance
- [x] Both raw and isotonic-calibrated probability shown
- [x] Regime-context flag included, informational only — doesn't filter
      or block the shortlist
- [x] Both machine-readable and human-readable output formats produced
- [x] README.md status table + changelog updated per CLAUDE.md's standing
      instruction
