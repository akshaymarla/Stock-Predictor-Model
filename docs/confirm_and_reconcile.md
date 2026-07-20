# Confirm & Reconcile — Two Quick Checks

Handoff doc for Claude Code. Two small, independent items — not new
feature work, just closing loose ends before anything else gets added on
top of an uncertain base.

## Part A — Confirm 0b is actually fully closed

`next_phase_plan.md` Section 0b listed 5 remediation steps. Steps 3-4
(rebuild `model_target_labels`, re-run baselines/LightGBM/SHAP/
calibration) are confirmed done — they're what produced the SHAP reversal
and decision-layer results already verified in this conversation. Steps
1, 2, and 5 were not explicitly re-confirmed after 0c became the focus.
Check each directly rather than assume:

1. **Does `daily_prices` actually have `fetched_at`/`source` audit
   columns now?** This was 0b's step 1 — check `schema.sql` and confirm
   `fetch_daily_prices.py`/`backfill_prices.py` actually populate them,
   not just that the columns exist.
2. **Was the full ~539-symbol universe re-backfilled** with the
   `series == "EQ"` filter fix in place (0b step 2), or did work move to
   0c before this fully completed? Confirm row counts/date coverage look
   complete, not partial.
3. **Was the automated single-day-price-jump sanity check added to the
   nightly pipeline** (0b step 5)? This is the check that would catch the
   *next* instance of this class of bug in hours instead of months — it's
   easy for this kind of "add monitoring" step to get deprioritized once
   the immediate fire is out, and it's the single highest-leverage item
   in 0b if it hasn't been done yet.

Also confirm: **has `working_predictor_base` been merged into `master`**
yet? Given how much has landed on the branch since that was last
discussed (0b, 0c, decision layer, weekly shortlist), worth an explicit
status check rather than assuming it happened.

Update `README.md`'s status table/changelog to reflect the real state of
each item above — if something's still open, say so plainly rather than
letting 0b read as fully closed when a piece of it isn't.

## Part B — Reconcile stale legacy-`financial_results` guidance

Early in this project (before the screener.in pivot), a task was scoped
for `fetch_financial_results_legacy.py` — parsing pre-2025 XBRL filings
directly from NSE, with a detailed spec (PDF cross-referencing for the
One/Four context ambiguity, etc.) written up in `README.md`'s
`financial_results` design section. That guidance predates the later
pivot to screener.in as the actual financial-data source, and it's not
clear whether it was ever explicitly superseded or is just stale,
contradictory documentation still sitting in the repo.

**Task**: read `README.md`'s `financial_results` section in full and
determine:
1. Is the legacy-XBRL-parsing task still relevant, or fully superseded by
   the screener.in-sourced `financial_results` pipeline (the one that
   went through the 0c tie-break/staleness fix)?
2. If superseded: mark the old legacy-XBRL guidance clearly as historical/
   abandoned in the README — don't delete it (it has real information in
   it, e.g. the confirmed XBRL taxonomy/One-Four findings, which could be
   useful again someday), but make it unambiguous that it's not the
   current path, so nobody picks it up thinking it's an open task.
3. If NOT fully superseded (e.g. if there's a real reason to still want
   direct-from-NSE historical data for pre-2025 quarters that
   screener.in can't provide as far back) — say so explicitly and scope
   what's actually still needed, rather than leaving it ambiguous.

Either outcome is fine — the point is an explicit, current answer in the
docs, not stale guidance that contradicts what the pipeline actually does
now.

## Acceptance checklist

- [x] `daily_prices` audit columns confirmed present AND populated —
      found and fixed a real gap (`backfill_price_gaps.py` never set
      them), closed 373/464 NULL rows, 91 (0.016%) remain as a
      documented, small accepted residual
- [x] Full-universe re-backfill confirmed complete, not partial —
      539/539 symbols, low-row-count symbols all trace to real IPOs/
      delistings, not gaps
- [x] Automated single-day-jump sanity check confirmed present in the
      nightly pipeline (already built, confirmed wired into
      `run_nightly.sh` via `fetch_daily_prices.py`)
- [x] `working_predictor_base` → `master` merge status confirmed — NOT
      merged, 24 commits ahead, reported not actioned
- [x] `README.md`'s `financial_results` section reconciled — the
      referenced legacy-XBRL guidance does not exist anywhere in this
      repo (confirmed via full git history, not just current files);
      flagged as a genuine discrepancy rather than fabricated, provenance
      note added to README instead
- [x] README status table + changelog updated to reflect real current
      state of all of the above
