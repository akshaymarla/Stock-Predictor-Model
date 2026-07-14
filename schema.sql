-- Schema for the first two tables of the Nifty alpha-model pipeline.
-- SQLite for now (zero setup). Swap for Postgres later without changing the logic much.

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
-- see src/screenerScraper.py), which only exposes the quarter-END date --
-- it has NO disclosure/announcement timestamp anywhere in its data. So
-- disclosure_date here is NOT taken from screener.in directly -- it's derived
-- by joining against our own corporate_announcements table (confirmed live
-- NSE data) for the earliest "financial result"-type announcement dated
-- between period_end_date and period_end_date+65 days (SEBI mandates
-- disclosure within 45-60 days of quarter-end, so this window is a real
-- regulatory bound, not a guess). disclosure_seq_id records which
-- corporate_announcements row was matched, for audit. If no announcement is
-- found in that window, the row is skipped entirely at fetch time rather
-- than defaulting to today's date -- see fetch_financial_results.py.
-- period_end_date is descriptive only, never a join key: results for a
-- quarter ending months ago can be disclosed today, and joining on
-- period_end_date would leak that lag into a backtest. Note: period_end_date
-- is the 1st of the quarter-ending month (screener.in's header format only
-- has month+year, not the exact last day) -- fine since it's descriptive-only.
CREATE TABLE IF NOT EXISTS financial_results (
    symbol            TEXT NOT NULL,
    disclosure_date   TEXT NOT NULL,   -- derived from corporate_announcements -- join key
    period_end_date   TEXT NOT NULL,   -- quarter-ending month from screener.in -- NOT a join key
    period_type       TEXT,            -- 'Q1', 'Q2', 'Q3', 'Q4', or 'ANNUAL', derived from period_end_date
    result_type       TEXT NOT NULL,   -- 'STANDALONE' or 'CONSOLIDATED'
    sales             REAL,
    expenses          REAL,
    operating_profit  REAL,
    opm_pct           REAL,
    other_income      REAL,
    interest          REAL,
    depreciation      REAL,
    profit_before_tax REAL,
    tax_pct           REAL,
    net_profit        REAL,
    eps               REAL,
    disclosure_seq_id TEXT,            -- corporate_announcements.seq_id used to derive disclosure_date
    source            TEXT NOT NULL,   -- 'SCREENER'
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (symbol, period_end_date, result_type)
);

CREATE INDEX IF NOT EXISTS idx_financial_results_symbol ON financial_results(symbol);
CREATE INDEX IF NOT EXISTS idx_financial_results_disclosure_date ON financial_results(disclosure_date);

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
