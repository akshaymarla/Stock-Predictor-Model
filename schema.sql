-- Schema for the Nifty alpha-model pipeline.
-- Runs against Supabase (Postgres). Originally written for SQLite; verified
-- Postgres-compatible as-is (no PRAGMA/AUTOINCREMENT/SQLite-only functions
-- anywhere in this file) -- see core/db.py for the connection layer that changed.

CREATE TABLE IF NOT EXISTS daily_prices (
    symbol              TEXT NOT NULL,
    date                TEXT NOT NULL,   -- YYYY-MM-DD
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    prev_close          REAL,
    volume              INTEGER,
    delivery_qty        INTEGER,
    delivery_pct        REAL,
    avg_traded_value_20d REAL,           -- rolling 20-day avg (close * volume), filled in post-load
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(date);
CREATE INDEX IF NOT EXISTS idx_daily_prices_symbol ON daily_prices(symbol);

CREATE TABLE IF NOT EXISTS surveillance_flags (
    symbol      TEXT NOT NULL,
    flag_type   TEXT NOT NULL,   -- e.g. 'ASM_STAGE_1', 'GSM_STAGE_2', 'TRADE_TO_TRADE'
    start_date  TEXT NOT NULL,
    end_date    TEXT,            -- NULL = still active as of last fetch
    source      TEXT,            -- 'NSE' or 'BSE'
    fetched_at  TEXT NOT NULL,   -- when we recorded this, for audit trail
    PRIMARY KEY (symbol, flag_type, start_date)
);

CREATE INDEX IF NOT EXISTS idx_surveillance_symbol ON surveillance_flags(symbol);

-- Snapshot-based for now: every fetch records what NSE Indices says the
-- constituents are AS OF THAT FETCH DATE. This is NOT the same as true
-- historical point-in-time membership (see README caveat) -- it just lets
-- us start accumulating our own point-in-time record going forward, while
-- historical reconstruction (pre-today) is tackled separately later.
CREATE TABLE IF NOT EXISTS index_membership (
    symbol       TEXT NOT NULL,
    index_name   TEXT NOT NULL,   -- e.g. 'NIFTY500'
    company_name TEXT,
    industry     TEXT,
    isin         TEXT,
    snapshot_date TEXT NOT NULL,  -- the date this constituent list was fetched
    PRIMARY KEY (symbol, index_name, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_index_membership_snapshot ON index_membership(snapshot_date);

-- Confirmed 2026-07-13 against a live NSE DevTools response. seq_id is
-- NSE's own unique announcement id -- a much more reliable dedupe key than
-- (symbol, date, time, subject), which could theoretically collide.
CREATE TABLE IF NOT EXISTS corporate_announcements (
    seq_id            TEXT NOT NULL,    -- NSE's own unique id for this announcement
    symbol            TEXT NOT NULL,
    isin              TEXT,             -- from sm_isin, stable identifier across symbol renames
    announcement_date TEXT NOT NULL,    -- the knowledge-timestamp: when the market learned this
    announcement_time TEXT,             -- HH:MM:SS if available, separate from date for clarity
    subject           TEXT,             -- short headline/subject of the filing
    details           TEXT,             -- longer description text, if provided
    attachment_url    TEXT,             -- link to the underlying PDF filing, if any
    category          TEXT,             -- controlled vocabulary, filled in later (NLP/manual pass)
    source            TEXT NOT NULL,    -- 'NSE' or 'BSE'
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (seq_id)
);

CREATE INDEX IF NOT EXISTS idx_corp_announcements_symbol ON corporate_announcements(symbol);
CREATE INDEX IF NOT EXISTS idx_corp_announcements_date ON corporate_announcements(announcement_date);

-- Financial metrics come from screener.in (via the vendored screenerScraper.py,
-- see src/screenerScraper.py and src/screener_common.py), which only exposes
-- the quarter/year-END date -- it has NO disclosure/announcement timestamp
-- anywhere in its data. So disclosure_date here is NOT taken from screener.in
-- directly -- it's derived by joining against our own corporate_announcements
-- table (confirmed live NSE data) for the earliest "financial result"-type
-- announcement dated between period_end_date and period_end_date+65 days
-- (SEBI mandates disclosure within 45-60 days of quarter-end, so this window
-- is a real regulatory bound, not a guess). disclosure_seq_id records which
-- corporate_announcements row was matched, for audit. If no announcement is
-- found in that window, disclosure_date/disclosure_seq_id are stored as NULL
-- rather than defaulting to today's date -- see screener_common.find_disclosure().
-- The row is still captured (metrics aren't lost), but a NULL disclosure_date
-- is deliberately unusable as a point-in-time join key: any "what did we know
-- as of date D" query filters disclosure_date <= D, and SQL's NULL comparison
-- semantics exclude these rows automatically -- no special-casing needed to
-- keep them out of point-in-time-sensitive features. (2026-07-15: previously
-- these rows were dropped entirely via `continue` in build_rows() -- changed
-- because that silently threw away real financial data just because the
-- disclosure date couldn't be confirmed yet.)
-- period_end_date is descriptive only, never a join key: results for a
-- quarter ending months ago can be disclosed today, and joining on
-- period_end_date would leak that lag into a backtest. Note: period_end_date
-- is the 1st of the quarter/year-ending month (screener.in's header format
-- only has month+year, not the exact last day) -- fine since it's
-- descriptive-only. This table holds BOTH quarterly (fetch_financial_results.py
-- quarterlyReport()) and annual (pnlReport(), period_type='ANNUAL') results --
-- same P&L shape, just different reporting cadence.
--
-- Core metric columns confirmed live 2026-07-14 against a real RELIANCE
-- quarter (field names matched after fixing a whitespace-normalization bug --
-- see screener_common.flatten_periods()). The metrics below RawPDF are
-- screener.in's own "addon" schedule-derived fields, also confirmed live in
-- that same capture (yoy_sales_growth_pct, material_cost_pct, etc.) -- these
-- come along "for free" whenever quarterlyReport(withAddon=True) is called
-- and are kept since they're useful modeling features, not because they were
-- specifically requested field-by-field.
--
-- raw_metrics_json (2026-07-15): different company types (bank vs
-- manufacturer vs NBFC) use genuinely different screener.in line items, not
-- just different labels for the same concept (that's what METRIC_ALIASES in
-- fetch_financial_results.py handles) -- a bank has no CWIP/Investments in
-- the manufacturing sense, but has things like Interest Earned/Expended
-- instead. Rather than an ever-growing column list or a lossy fixed schema,
-- this column stores the FULL flattened per-period metrics dict as JSON, so
-- nothing is ever silently dropped regardless of company template. The
-- named columns above remain the convenient, clean path for metrics common
-- across companies; company-specific fields live in this JSON blob until/
-- unless they're common enough to promote to a real column.
CREATE TABLE IF NOT EXISTS financial_results (
    symbol                  TEXT NOT NULL,
    disclosure_date         TEXT,            -- derived from corporate_announcements -- join key; NULL if unconfirmed, see note above
    period_end_date         TEXT NOT NULL,   -- quarter/year-ending month from screener.in -- NOT a join key
    period_type             TEXT,            -- 'Q1'/'Q2'/'Q3'/'Q4' (quarterlyReport) or 'ANNUAL' (pnlReport)
    result_type             TEXT NOT NULL,   -- 'STANDALONE' or 'CONSOLIDATED'
    sales                   REAL,
    expenses                REAL,
    operating_profit        REAL,
    opm_pct                 REAL,
    other_income            REAL,
    interest                REAL,
    depreciation            REAL,
    profit_before_tax       REAL,
    tax_pct                 REAL,
    net_profit              REAL,
    eps                     REAL,
    raw_pdf_url             TEXT,            -- screener.in's link to the underlying source filing
    yoy_sales_growth_pct    REAL,
    material_cost_pct       REAL,
    employee_cost_pct       REAL,
    exceptional_items       REAL,
    other_income_normal     REAL,            -- other income excluding exceptional items, per screener.in
    profit_from_associates  REAL,
    minority_share          REAL,
    exceptional_items_at    REAL,            -- exceptional items, after tax
    profit_excl_exceptional REAL,
    profit_for_pe           REAL,
    profit_for_eps          REAL,
    yoy_profit_growth_pct   REAL,
    raw_metrics_json        TEXT,            -- full flattened metrics dict for this period, see note above
    disclosure_seq_id       TEXT,            -- corporate_announcements.seq_id used to derive disclosure_date
    source                  TEXT NOT NULL,   -- 'SCREENER'
    fetched_at              TEXT NOT NULL,
    PRIMARY KEY (symbol, period_end_date, result_type)
);

CREATE INDEX IF NOT EXISTS idx_financial_results_symbol ON financial_results(symbol);
CREATE INDEX IF NOT EXISTS idx_financial_results_disclosure_date ON financial_results(disclosure_date);

-- Same point-in-time reasoning and disclosure-matching as financial_results
-- above -- see screener_common.find_disclosure(). Column names confirmed
-- 2026-07-15 against a real live RELIANCE balance sheet -- all 10 matched
-- the original guess exactly. raw_metrics_json: same reasoning as
-- financial_results above -- catches any company-specific line item (e.g.
-- a bank's Deposits/Advances) that doesn't fit these common columns.
-- disclosure_date is nullable -- same reasoning as financial_results: no
-- match within the window means we capture the row anyway (metrics aren't
-- lost) with disclosure_date=NULL rather than guessing.
CREATE TABLE IF NOT EXISTS balance_sheet (
    symbol             TEXT NOT NULL,
    disclosure_date    TEXT,            -- derived from corporate_announcements -- join key; NULL if unconfirmed
    period_end_date    TEXT NOT NULL,   -- NOT a join key
    period_type        TEXT,
    result_type        TEXT NOT NULL,
    equity_capital     REAL,
    reserves           REAL,
    borrowings         REAL,
    other_liabilities  REAL,
    total_liabilities  REAL,
    fixed_assets       REAL,
    cwip               REAL,
    investments        REAL,
    other_assets       REAL,
    total_assets       REAL,
    raw_metrics_json   TEXT,
    disclosure_seq_id  TEXT,
    source             TEXT NOT NULL,
    fetched_at         TEXT NOT NULL,
    PRIMARY KEY (symbol, period_end_date, result_type)
);

CREATE INDEX IF NOT EXISTS idx_balance_sheet_symbol ON balance_sheet(symbol);
CREATE INDEX IF NOT EXISTS idx_balance_sheet_disclosure_date ON balance_sheet(disclosure_date);

-- Same point-in-time reasoning as financial_results. Column names confirmed
-- 2026-07-15 against a real live RELIANCE cash flow statement -- all 4
-- matched the original guess exactly. raw_metrics_json: same reasoning as
-- financial_results above.
CREATE TABLE IF NOT EXISTS cash_flow (
    symbol               TEXT NOT NULL,
    disclosure_date      TEXT,            -- derived from corporate_announcements -- join key; NULL if unconfirmed
    period_end_date      TEXT NOT NULL,   -- NOT a join key
    period_type          TEXT,
    result_type          TEXT NOT NULL,
    cash_from_operating  REAL,
    cash_from_investing  REAL,
    cash_from_financing  REAL,
    net_cash_flow        REAL,
    raw_metrics_json     TEXT,
    disclosure_seq_id    TEXT,
    source                TEXT NOT NULL,
    fetched_at            TEXT NOT NULL,
    PRIMARY KEY (symbol, period_end_date, result_type)
);

CREATE INDEX IF NOT EXISTS idx_cash_flow_symbol ON cash_flow(symbol);
CREATE INDEX IF NOT EXISTS idx_cash_flow_disclosure_date ON cash_flow(disclosure_date);

-- Same point-in-time reasoning as financial_results. Column names confirmed
-- 2026-07-15 against a real live RELIANCE ratios pull -- all 6 matched the
-- original guess exactly, despite ratios() having no addon fetch to hint
-- at field names from beforehand. raw_metrics_json: same reasoning as
-- financial_results above.
CREATE TABLE IF NOT EXISTS ratios (
    symbol                   TEXT NOT NULL,
    disclosure_date          TEXT,            -- derived from corporate_announcements -- join key; NULL if unconfirmed
    period_end_date          TEXT NOT NULL,   -- NOT a join key
    period_type              TEXT,
    result_type              TEXT NOT NULL,
    debtor_days              REAL,
    inventory_days           REAL,
    days_payable             REAL,
    cash_conversion_cycle    REAL,
    working_capital_days     REAL,
    roce_pct                 REAL,
    raw_metrics_json         TEXT,
    disclosure_seq_id        TEXT,
    source                   TEXT NOT NULL,
    fetched_at                TEXT NOT NULL,
    PRIMARY KEY (symbol, period_end_date, result_type)
);

CREATE INDEX IF NOT EXISTS idx_ratios_symbol ON ratios(symbol);
CREATE INDEX IF NOT EXISTS idx_ratios_disclosure_date ON ratios(disclosure_date);

-- Confirmed 2026-07-13 against a real HDFCBANK row. recordId is NSE's own
-- unique row id -- same reasoning as seq_id in corporate_announcements,
-- a more reliable dedupe key than symbol+disclosure_date+period_end_date.
-- disclosure_date is broadcastDate ("Exchange Received Time" per NSE's own
-- hover-table labels) -- the knowledge-timestamp, and the join key. Mirrors
-- the choice already made in corporate_announcements (an_dt, the earlier of
-- two near-identical timestamps). period_end_date (NSE's "AS ON DATE") is
-- the shareholding snapshot date being reported on -- descriptive only,
-- never a join key. submission_date is when the company filed with the
-- exchange, which can precede public dissemination -- also not join-safe.
-- dissemination_time (systemDate) is kept as an audit column for anyone who
-- later wants the maximally conservative timestamp instead of broadcastDate.
CREATE TABLE IF NOT EXISTS shareholding_pattern (
    record_id           TEXT NOT NULL,   -- NSE's own unique id for this filing
    symbol              TEXT NOT NULL,
    isin                TEXT,            -- stable identifier across symbol renames
    disclosure_date     TEXT NOT NULL,   -- broadcastDate -- join key
    period_end_date     TEXT,            -- "AS ON DATE" -- NOT a join key
    promoter_pct        REAL,            -- pr_and_prgrp
    public_pct          REAL,            -- public_val
    employee_trust_pct  REAL,            -- employeeTrusts
    status              TEXT,            -- revisedStatus
    submission_date     TEXT,            -- when filed -- descriptive only, NOT a join key
    revision_date       TEXT,            -- set if this filing was later revised
    dissemination_time  TEXT,            -- systemDate -- audit column, see note above
    attachment_url      TEXT,            -- xbrl filing link
    source              TEXT NOT NULL,   -- 'NSE' or 'BSE'
    fetched_at          TEXT NOT NULL,
    PRIMARY KEY (record_id)
);

CREATE INDEX IF NOT EXISTS idx_shareholding_symbol ON shareholding_pattern(symbol);
CREATE INDEX IF NOT EXISTS idx_shareholding_disclosure_date ON shareholding_pattern(disclosure_date);

-- Market-wide risk/regime context, not stock-specific -- one row per
-- trading day, joins to the training matrix on date alone. No
-- point-in-time derivation needed here (unlike disclosure-based tables):
-- an index close on date D is, by construction, known on date D.
--
-- SOURCING NOTE (2026-07-16): the original plan (macro_sector_shock_features.md
-- Section 4a) was jugaad_data.nse.index_df() per-symbol against
-- niftyindices.com's Backpage.aspx AJAX endpoint. Confirmed live that this
-- endpoint no longer works -- niftyindices.com has been redesigned (now on
-- Sitefinity CMS) and the endpoint returns the site's homepage HTML instead
-- of JSON, for every symbol, regardless of session/cookies/headers. Not a
-- symbol-name problem, not fixable from our side. Switched to
-- jugaad_data.nse.NSEIndicesArchives.bhavcopy_index_raw(date) instead -- a
-- static daily CSV snapshot at niftyindices.com/Daily_Snapshot/ind_close_all_DDMMYYYY.csv
-- covering ALL ~161 NSE indices (confirmed live, including 'Nifty 50' and
-- 'India VIX' by exact name) in one request per day, rather than one
-- request per index per date range. Confirmed working back to at least
-- 2021-01-04. Non-trading days (weekends/holidays) return HTTP 200 with
-- the same homepage HTML rather than a clean 404 -- fetch_macro_sector.py
-- must detect a real CSV header before trusting a response, not just check
-- the status code.
CREATE TABLE IF NOT EXISTS macro_regime_indicators (
    date                    TEXT NOT NULL PRIMARY KEY,  -- YYYY-MM-DD
    nifty50_close           REAL,
    nifty50_return_5d       REAL,   -- 5-trading-day pct return
    nifty50_return_10d      REAL,
    nifty50_dist_50dma_pct  REAL,   -- % distance of close from 50-day moving average
    india_vix_close         REAL,
    vix_change_5d_pts       REAL,   -- ABSOLUTE point change over 5 trading days (not %)
    vix_change_5d_pct       REAL,   -- percentage change over 5 trading days
    source                  TEXT NOT NULL,   -- 'NSE_INDICES_ARCHIVE'
    fetched_at              TEXT NOT NULL
);

-- THE FIX for sector-mapping: real official NSE Indices sectoral
-- constituent lists, not fuzzy string-matching against index_membership's
-- generic `industry` field (that field holds granular BSE/NSE
-- classifications like "FERTILISERS & PESTICIDES", not broad sector names).
-- Snapshot-based, same pattern as index_membership -- gives today's
-- constituents each run, does NOT retroactively reconstruct historical
-- sector membership. A stock can legitimately appear under multiple
-- sector_name rows in the same snapshot (NSE's sectoral indices overlap,
-- e.g. a large private bank is in both 'Nifty Bank' and
-- 'Nifty Private Bank') -- this is intentional, not a bug; feature
-- assembly must pick an explicit policy for handling that (prefer most
-- specific index, or join all and average) rather than silently taking
-- whichever row a query happens to return first.
CREATE TABLE IF NOT EXISTS sector_membership (
    symbol         TEXT NOT NULL,
    sector_name    TEXT NOT NULL,   -- e.g. 'Nifty Bank' -- must match macro_regime_indicators/sector_daily_benchmarks naming exactly (real NSE index names, confirmed 2026-07-16, NOT all-caps)
    company_name   TEXT,
    isin           TEXT,
    snapshot_date  TEXT NOT NULL,
    source         TEXT NOT NULL,   -- 'NSE_INDICES'
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (symbol, sector_name, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_sector_membership_snapshot ON sector_membership(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_sector_membership_symbol ON sector_membership(symbol);

-- One row per (sector, day). Sourced from the SAME daily index snapshot
-- as macro_regime_indicators (see fetch_macro_sector.py's sourcing note)
-- -- every sector index's close is already in that one file, so this adds
-- zero extra HTTP requests, just more parsed rows per day. sector_name
-- matches sector_membership.sector_name exactly (real NSE index names,
-- e.g. 'Nifty Bank'). No point-in-time derivation needed here either --
-- same reasoning as macro_regime_indicators.
CREATE TABLE IF NOT EXISTS sector_daily_benchmarks (
    sector_name               TEXT NOT NULL,
    date                      TEXT NOT NULL,
    sector_close              REAL,
    sector_return_3d          REAL,
    sector_return_5d          REAL,
    sector_return_14d         REAL,
    sector_relative_alpha_14d REAL,  -- sector_return_14d minus nifty50's 14d return over the identical window
    source                    TEXT NOT NULL,   -- 'NSE_INDICES_ARCHIVE'
    fetched_at                TEXT NOT NULL,
    PRIMARY KEY (sector_name, date)
);
CREATE INDEX IF NOT EXISTS idx_sector_benchmarks_date ON sector_daily_benchmarks(date);

-- TRAINING LABELS ONLY -- this table intentionally uses FUTURE data
-- relative to `date` (forward-looking returns), the opposite of every
-- other table in this repo. NEVER join this into the feature side of a
-- training matrix -- it's the label side only. Kept in its own clearly-
-- named table specifically so there's no risk of it accidentally ending
-- up on the feature side. See macro_sector_shock_features.md Section 6.
--
-- Forward windows are in TRADING days (via macro_regime_indicators.date
-- as the market calendar), not calendar days. Rows near the end of
-- available price history that don't have a full 14d/30d forward window
-- yet are simply absent from this table -- computed on a truncated
-- window would silently understate or overstate real alpha, so
-- compute_target_labels.py excludes them rather than computing a biased
-- partial value. alpha_Nd = stock_return_Nd - nifty_return_Nd, matching
-- the project's original label definition (absolute outperformance, no
-- cost/tax adjustment).
CREATE TABLE IF NOT EXISTS model_target_labels (
    symbol               TEXT NOT NULL,
    date                 TEXT NOT NULL,   -- the "as of" date the label is computed FROM
    stock_return_14d     REAL,
    nifty_return_14d     REAL,   -- NIFTY 50's return over the identical window
    alpha_14d            REAL,
    outperform_14d_flag  INTEGER,   -- 1 if alpha_14d > 0 else 0
    stock_return_30d     REAL,
    nifty_return_30d     REAL,
    alpha_30d            REAL,
    outperform_30d_flag  INTEGER,
    fetched_at           TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_model_target_labels_date ON model_target_labels(date);

-- FEATURES ONLY -- deliberately does NOT include any column from
-- model_target_labels. Keeping labels in their own separate table (joined
-- in later, at training time, by whoever trains the model) means there's
-- no risk of future-looking data accidentally ending up on the feature
-- side of a join. See macro_sector_shock_features.md Section 6.
--
-- Built by src/assemble_feature_matrix.py, which fixes 3 real bugs found
-- in an earlier draft (macro_sector_shock_features.md Section 5):
--   1. Sector membership is joined via "most recent snapshot_date <= date"
--      (sector_membership snapshots are sparse -- an exact-date join would
--      null out sector features for the vast majority of rows).
--   2. "Last known financials as of date" uses a proper ranking (most
--      recent disclosure_date <= date), not a bare-column MAX() -- a bare
--      MAX() only happens to work in SQLite specifically and silently
--      breaks on Postgres, this project's planned future migration target.
--   3. Announcement-derived features check BOTH `subject` AND `details`
--      (learned building financial_results' disclosure-matching: NSE often
--      files substantive news under a generic subject like "Outcome of
--      Board Meeting", with the real content only in `details`).
--
-- Fundamentals/shareholding features only use rows with disclosure_date
-- IS NOT NULL (financial_results/balance_sheet/cash_flow/ratios/
-- shareholding_pattern can have NULL disclosure_date for periods where no
-- matching announcement was found -- those are correctly unusable as a
-- point-in-time join key, see the note on financial_results above).
--
-- sector_* columns are AVERAGED across every sector_name a symbol belongs
-- to as of `date` (NSE's sectoral indices legitimately overlap, e.g. a
-- large private bank is in both 'Nifty Bank' and 'Nifty Private Bank') --
-- an explicit, documented policy per the schema note on sector_membership,
-- not an arbitrary pick of whichever row a query happens to return first.
--
-- KNOWN LIMITATION, confirmed 2026-07-16: sector_membership is
-- snapshot-based, same as index_membership -- it does NOT retroactively
-- reconstruct historical sector membership. Since the only snapshot taken
-- so far is from today, sector_count/avg_sector_* are correctly NULL/0 for
-- every historical row in the 5-year backfill (verified: 100% of an
-- initial 3,076-row test batch). This is accepted for now, same stance
-- already taken on index_membership's identical limitation -- these
-- columns will start populating naturally for real going forward as more
-- snapshots accumulate from running fetch_sector_membership.py
-- periodically. Historical reconstruction (if NSE Indices publishes past
-- sector reconstitution data) is a separate, not-yet-started task.
CREATE TABLE IF NOT EXISTS model_feature_matrix (
    symbol                        TEXT NOT NULL,
    date                          TEXT NOT NULL,
    -- price/liquidity
    close                         REAL,
    volume                        REAL,
    avg_traded_value_20d          REAL,
    -- macro regime (same-day values -- known on `date` by construction)
    nifty50_close                 REAL,
    nifty50_return_5d             REAL,
    nifty50_return_10d            REAL,
    nifty50_dist_50dma_pct        REAL,
    india_vix_close               REAL,
    vix_change_5d_pts             REAL,
    vix_change_5d_pct             REAL,
    -- sector context (averaged across all sector_membership rows as-of `date`)
    sector_count                  INTEGER,   -- how many sectors this symbol belongs to as of `date`
    avg_sector_return_3d          REAL,
    avg_sector_return_5d          REAL,
    avg_sector_return_14d         REAL,
    avg_sector_relative_alpha_14d REAL,
    -- last-known fundamentals as of `date` (point-in-time via disclosure_date)
    fin_disclosure_date           TEXT,   -- audit column: which disclosure this snapshot came from
    fin_result_type               TEXT,   -- 'CONSOLIDATED' or 'STANDALONE', whichever was most recent
    fin_days_since_disclosure     INTEGER,
    fin_sales                     REAL,
    fin_net_profit                REAL,
    fin_opm_pct                   REAL,
    fin_eps                       REAL,
    bs_disclosure_date            TEXT,
    bs_total_assets                REAL,
    bs_borrowings                  REAL,
    cf_disclosure_date            TEXT,
    cf_net_cash_flow               REAL,
    ratio_disclosure_date         TEXT,
    ratio_roce_pct                 REAL,
    -- last-known shareholding as of `date`
    sh_disclosure_date            TEXT,
    sh_promoter_pct               REAL,
    sh_public_pct                 REAL,
    -- announcement-derived signal (both subject AND details checked, see
    -- note above) -- crude keyword flag for regulatory/legal/order-related
    -- news in the trailing 30 days as of `date`, not NLP -- a real
    -- sentiment/topic model is a separate, lower-priority tier
    recent_order_dispute_flag_30d INTEGER,
    fetched_at                    TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_feature_matrix_date ON model_feature_matrix(date);
