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

-- disclosure_date is the knowledge-timestamp (when NSE broadcast the result
-- to the market) -- joins for "what did we know as of date D" MUST filter on
-- this, never on period_end_date. period_end_date (the fiscal quarter/year
-- being reported on) is descriptive only; results for a quarter ending
-- months ago can be disclosed today, and using period_end_date to join would
-- leak that lag into a backtest.
CREATE TABLE IF NOT EXISTS financial_results (
    symbol           TEXT NOT NULL,
    disclosure_date  TEXT NOT NULL,   -- when the result was filed/broadcast -- join key
    period_end_date  TEXT,            -- fiscal period being reported on -- NOT a join key
    period_type      TEXT,            -- e.g. 'Q1', 'Q2', 'Q3', 'Q4', 'ANNUAL'
    result_type      TEXT NOT NULL,   -- 'STANDALONE' or 'CONSOLIDATED'
    revenue          REAL,
    net_profit       REAL,
    eps              REAL,
    attachment_url   TEXT,            -- link to the underlying PDF filing, if any
    source           TEXT NOT NULL,   -- 'NSE' or 'BSE'
    fetched_at       TEXT NOT NULL,
    PRIMARY KEY (symbol, disclosure_date, period_end_date, result_type)
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
