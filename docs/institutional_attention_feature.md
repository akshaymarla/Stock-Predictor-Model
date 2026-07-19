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

## 7. Acceptance checklist

- [ ] Checked README.md's current real status for `shareholding_pattern`
      before starting (Section 0) — not assumed from prior docs
- [ ] Confirmed via real DevTools/API capture whether the institutional
      breakdown already exists in the current shareholding-pattern source
      (Section 1), before building a new source
- [ ] `disclosure_date` follows the same real-timestamp-or-explicit-null
      discipline as every other table in this project
- [ ] Trend (QoQ/YoY change) and sector/universe-relative rank features
      built, not just raw institutional-holding levels
- [ ] Institutional-attention features evaluated jointly with the
      existing liquidity filter, not in isolation
- [ ] Feature-importance re-check uses SHAP (per the standing requirement
      from `model_build_spec.md` 7b), on the same validated walk-forward
      harness already in use
- [ ] README.md status table + changelog updated per CLAUDE.md's
      standing instruction
