# Institutional Attention Feature — Design + Build Spec

Handoff doc for Claude Code. This is the prerequisite work flagged in
`model_build_spec.md` Section 7b: the project's original differentiator
idea ("institutionally neglected stocks with decent liquidity") has never
actually been tested, because `sh_promoter_pct` (promoter/insider
ownership) is not the same thing as institutional (FII/DII) attention, and
no feature testing the real hypothesis exists yet. This doc scopes what
that feature actually needs to be.

## 0. Read this first — reconcile against current real state

My working knowledge of `shareholding_pattern`'s current schema/source may
be stale — it went through at least one sourcing pivot (screener.in) since
I last directly verified it. **Before building anything in this doc,
check `README.md`'s current status table for `shareholding_pattern` and
read the actual current `schema.sql`** — don't assume the version
described in earlier design docs is still accurate. Reconcile any
conflict with the current real repo state, not with this doc.

## 1. Check the source we already have before building a new one

Before scoping a new data source, verify something important: **NSE/BSE's
actual SEBI-format shareholding pattern filing likely already contains the
real institutional breakdown**, more granular than what
`fetch_shareholding_pattern.py` originally parsed. SEBI's LODR Regulation
31 shareholding pattern format ("Table III — Statement showing
shareholding pattern of the Public shareholder") typically breaks the
Public category down into sub-categories such as:
- Mutual Funds
- Foreign Portfolio Investors / FIIs
- Financial Institutions / Banks
- Insurance Companies
- Alternate Investment Funds
- (remaining) Non-institutional public

**This has not been confirmed against a real filing for this project** —
the original `fetch_shareholding_pattern.py` was built and left
unverified before this level of detail was ever checked. This is the
first concrete task: get a real DevTools capture (same discipline used
for ASM/GSM, corporate announcements, and the XBRL financial-results
work) of one real company's shareholding pattern filing, and check
whether this institutional breakdown is actually present in the raw
response. If it is, this is by far the best source — official, already
partially built, same filing we already need for promoter_pct — and the
task becomes "parse more of what we already have" rather than "integrate
a new source." Only pursue Section 2's alternative sources if this
genuinely isn't available at the granularity needed.

## 2. Fallback/supplementary sources, if Section 1 doesn't pan out

- **NSE/BSE daily bulk & block deal reports** — these disclose named
  buyers/sellers and quantities for large trades, published daily,
  officially sourced (same legitimacy tier as everything else in this
  project — not a scraped third-party product). Unlike quarterly
  shareholding pattern (a *stock* of holdings, changes slowly), this is a
  *flow* signal — did an institutional-scale trade happen in this stock
  recently. Worth treating as a **supplementary, optional** feature, not
  a replacement for the quarterly holding-level data — different
  information, different cadence.
- Do NOT reach for a third-party scraped aggregator for this
  specifically. We already went through the screener.in ToS/reliability
  discussion at length for `financial_results` — the same reasoning
  applies here, and this data is more likely to exist natively in the
  official filing anyway (per Section 1) than financial results were.

## 3. Schema

Following the pattern from `financial_results` — name the well-known,
stable SEBI-standard categories as real columns (this format has been
regulatory-stable for years, unlike the XBRL taxonomy that just changed),
but keep a raw fallback for anything unmapped, mirroring the
`raw_metrics_json` pattern already used successfully elsewhere in this
project for exactly this kind of company-template variance.

```sql
CREATE TABLE IF NOT EXISTS shareholding_institutional_breakdown (
    symbol                  TEXT NOT NULL,
    quarter_end_date        TEXT NOT NULL,
    disclosure_date         TEXT,   -- same knowledge-timestamp discipline as shareholding_pattern; NULL if genuinely unknown, never guessed
    mutual_fund_pct         REAL,
    fii_fpi_pct             REAL,
    financial_institution_pct REAL,  -- banks/FIs, per SEBI's category label
    insurance_pct           REAL,
    alternate_investment_fund_pct REAL,
    other_institution_pct   REAL,   -- catch-all for institutional sub-categories not individually modeled above
    total_institutional_pct REAL,   -- sum of the above -- compute at write time, don't leave it to be recomputed inconsistently downstream
    raw_categories_json     TEXT,   -- full raw category:value breakdown as reported, for anything the named columns don't capture -- same resilience pattern as raw_metrics_json elsewhere in this project
    source                  TEXT NOT NULL,  -- 'NSE' or 'BSE'
    fetched_at              TEXT NOT NULL,
    PRIMARY KEY (symbol, quarter_end_date)
);
CREATE INDEX IF NOT EXISTS idx_inst_breakdown_symbol ON shareholding_institutional_breakdown(symbol);
```

If bulk/block deal data (Section 2) gets built too, that's a separate
table (`bulk_block_deals`, one row per deal) — don't conflate a flow
signal with a stock/holding-level snapshot in the same table.

## 4. Point-in-time discipline (same standing rule, restated because this table's whole purpose is a hypothesis test — getting this wrong here would be a particularly bad way to fool ourselves)

- `disclosure_date` must be the actual knowledge-timestamp, same rule as
  `shareholding_pattern`. If the source doesn't cleanly separate
  disclosure date from quarter-end date, fall back to quarter-end
  explicitly and document it — don't silently backfill a fabricated
  disclosure date.
- Any join into the feature-assembly pipeline must use "most recent
  disclosed value on or before date `t`," same pattern as
  `financial_results` and `shareholding_pattern` already use.

## 5. Feature design — don't just add raw levels, add the shape of the actual hypothesis

The original thesis was specifically about *neglect relative to peers*
and *change over time*, not just "low institutional %" in isolation.
Build features that actually reflect that framing, not just the raw
number:

- **Raw levels**: `total_institutional_pct`, `fii_fpi_pct`,
  `mutual_fund_pct` at the most recent disclosed quarter — the baseline,
  needed but not sufficient on its own.
- **Trend, not just level**: QoQ and YoY change in
  `total_institutional_pct` — a stock institutions are *actively
  reducing* attention to is a different situation than one that's always
  been low and stable. Compute at query/feature-assembly time from raw
  quarterly figures (same principle as financial ratios elsewhere in this
  project — don't pre-bake deltas into the raw table).
- **Relative to sector/universe, not absolute**: a cross-sectional
  percentile rank of `total_institutional_pct` within the same sector (or
  within the full Nifty 500 universe if sector data isn't ready) on the
  same date. "Institutionally neglected" is inherently a relative
  concept — 15% institutional holding might be low for a large-cap bank
  and high for a micro-cap — an absolute threshold feature would miss
  this distinction entirely.
- **Combined with the existing liquidity filter**: the original thesis
  was explicitly "decent liquidity AND low institutional attention," not
  low institutional attention alone (which, on its own, correlates with
  illiquid/shady stocks — exactly what `surveillance_flags` and the
  liquidity filter already exist to screen out). Any evaluation of this
  hypothesis should condition on liquidity, not treat institutional
  attention as a stand-alone signal in isolation from it.

## 6. Build order

1. DevTools capture + verification per Section 1 — determine if this
   comes from the source we already have.
2. Build/extend the fetch script accordingly (either deepen the existing
   shareholding_pattern parsing, or build a new fetch script if Section 1
   comes up short).
3. Schema per Section 3, point-in-time discipline per Section 4.
4. Feature-assembly additions per Section 5 — trend and relative-rank
   features, not just raw levels.
5. Re-run the SHAP-based feature-importance check (per
   `model_build_spec.md` Section 7b's now-standing SHAP requirement) with
   these new features included, across the same validated fold/embargo
   harness — this is the actual test of the hypothesis, don't skip
   straight to conclusions before this step.

> ## ✅ RE-VALIDATED ON CLEAN DATA (2026-07-19)
> The `daily_prices` corruption (see `next_phase_plan.md` Section 0b) is
> fully remediated and the SHAP re-check below has been re-run against
> clean data. **"Mixed/partial support" is now a confirmed finding, not a
> provisional one**: `sh_inst_mutual_fund_pct` remains the strongest
> institutional feature at both horizons (rank 8→7 at 14d, 6→6 at 30d,
> sums essentially unchanged), the trend-beats-level result holds, and
> `sh_inst_pctrank` remains the weakest of the six institutional features
> — the same shape of result the corrupted-data run found. This project's
> founding hypothesis test got the scrutiny it deserved rather than being
> grandfathered in, and it held up.
>
> **Second, independent re-validation needed (2026-07-20)**: a separate
> bug (`next_phase_plan.md` Section 0c -- arbitrary standalone/
> consolidated tie-break on 89% of confirmed-disclosure rows, plus stale
> disclosures used with no cutoff) directly affects `fin_eps` and
> `fin_days_since_disclosure` -- features this project's SHAP rankings
> directly compare `sh_inst_*` against (`model_build_spec.md` Section 7b
> cites `fin_eps`'s rank as context). The 0b re-validation above stands on
> its own (separate root cause, already re-confirmed) -- but Section 8's
> SHAP result below was run before the 0c fix, so the *relative* ranking
> of `sh_inst_*` against `fin_eps`/`fin_days_since_disclosure`
> specifically should be treated as pending until re-run against the
> 0c-corrected `model_feature_matrix`. Fixed and rebuilt 2026-07-20 -- see
> README changelog for the fresh re-run.

## 8. RESULT (2026-07-19) — hypothesis tested, confirmed via SHAP on real data

The build in Sections 1-6 is complete and the hypothesis has now actually
been tested — verified directly against the raw per-fold SHAP export, not
taken from a summary.

**Finding: mixed / partially supported.** Institutional attention carries
genuine signal, but does not dominate:
- `sh_inst_mutual_fund_pct` is the strongest institutional feature in
  both horizons (14d: sum |SHAP| 0.2305, rank 9/31; 30d: 0.3495, rank
  6/31) — mutual fund holdings specifically, not FII/FPI or insurance.
- **Trend beats static level, as the design predicted**: both
  `sh_inst_qoq_change_pct` and `sh_inst_yoy_change_pct` outrank the plain
  `sh_inst_total_pct` level in both horizons. The "attention increasing/
  decreasing matters more than the absolute level" framing from Section 5
  held up empirically.
- **Correction to Section 5's relative-rank design, ITSELF corrected
  2026-07-20 after the 0c fix (see the re-validation note above)**: this
  originally read "`sh_inst_pctrank` ranks LOWEST of all six institutional
  features in both horizons (14d rank 24/31, 30d rank 19/31)" — run before
  the `next_phase_plan.md` Section 0c fix (`financial_results` tie-break +
  staleness bug, which touched `fin_eps`/`fin_days_since_disclosure` and
  therefore distorted the overall SHAP allocation these ranks are relative
  to). **Re-run on the 0c-corrected `model_feature_matrix`: `sh_inst_pctrank`
  is no longer the weakest** — `sh_inst_total_pct` (the plain static
  level) is now weakest in both horizons instead, and `sh_inst_pctrank`
  outranks it (14d: pctrank 21/30 vs. total 22/30; 30d: pctrank 12/30 vs.
  total 17/30, a bigger jump). This *reverses* the conclusion below, not
  just adjusts it: the "neglect is relative, not absolute" reasoning
  behind `sh_inst_pctrank`'s design **held up empirically after all** —
  the plain level, not the relative-rank version, is what's actually
  underperforming. (The original caution below -- "don't assume a
  feature is pulling its weight just because the design doc argued for
  it" -- is still good general advice, it just turned out not to apply
  to this specific feature once the data was clean.) Note this is
  currently a full-universe rank, not sector-relative
  (`sector_membership` has no historical snapshots yet, same accepted
  limitation as `avg_sector_*` elsewhere in this project) — the weak
  result may partly reflect that coarseness rather than a clean rejection
  of the relative-attention framing itself.
- `india_vix_close` and `avg_traded_value_20d` (liquidity) remain the top
  2-4 features overall in both horizons — same as every prior model
  iteration this project has run. Institutional attention is a real,
  contributing signal, not the dominant one the original thesis hoped for.

**This is the first time the hypothesis has been tested with real
institutional data** rather than the `sh_promoter_pct` proxy (promoter
ownership, a different concept — see `model_build_spec.md` Section 7b).
Treat the finding above as the actual answer, not the earlier
promoter-based result.

**Known open item**: `fin_opm_pct` reads exactly 0.0 in both horizons'
fold 1 (both share the earliest train_start, 2021-07-16) — likely a
genuine data-coverage gap in the earliest backfill period (LightGBM never
had a non-null value to split on in that window, so SHAP correctly
reports exactly 0.0 rather than something small-but-nonzero) rather than
a per-horizon bug, but not yet confirmed with a direct null-rate check
against that specific window. Worth confirming before relying on
fold-1-specific feature-importance comparisons.

**Calibration note**: this run reproduced the Platt-scaling
ranking-inversion bug in a fourth fold-slot, `30d` fold 1 (0.4906 →
0.5094, exact sum-to-1.000 signature) — and a direct re-check of the
*original* 2026-07-16 report (backed up locally before this run
overwrote it) confirms this exact same fold-slot inverted there too
(0.4936 → 0.5064), a real instance that wasn't caught/documented at the
time (only 3 of the original run's 4 inversions were flagged in the first
pass). The same four fold-slots — `14d` fold 3, `14d` fold 5, `30d` fold
1, `30d` fold 3 — have now inverted identically across two independent
feature sets/model runs. This is no longer just a one-off finding — those
specific calendar windows appear to be structurally weak-signal,
independent of feature set. Isotonic remains the mandatory standing
choice per `model_build_spec.md` Section 7 — do not revisit Platt
scaling.

## 9. Acceptance checklist

- [x] Checked README.md's current real status for `shareholding_pattern`
      before starting (Section 0) — not assumed from prior docs
- [x] Confirmed via real DevTools/API capture whether the institutional
      breakdown already exists in the current shareholding-pattern source
      (Section 1), before building a new source — parsed from XBRL already
      captured via `shareholding_pattern.attachment_url`, three real
      scale/mapping/taxonomy bugs found and fixed (see README changelog
      commit `0d4ce9d`), 14,252/14,252 rows verified in valid range
- [x] `disclosure_date` follows the same real-timestamp-or-explicit-null
      discipline as every other table in this project
- [x] Trend (QoQ/YoY change) and sector/universe-relative rank features
      built, not just raw institutional-holding levels — see Section 8 for
      the important caveat: `sh_inst_pctrank` underperformed the plain
      trend features empirically, despite the design reasoning for it
- [x] Institutional-attention features evaluated jointly with the
      existing liquidity filter, not in isolation (liquidity gap found and
      fixed during this build — the filter wasn't actually wired in before)
- [x] Feature-importance re-check uses SHAP (per the standing requirement
      from `model_build_spec.md` 7b), on the same validated walk-forward
      harness already in use — see Section 8 for the full confirmed result
- [x] README.md status table + changelog updated per CLAUDE.md's
      standing instruction
