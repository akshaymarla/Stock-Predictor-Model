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

CREATE TABLE IF NOT EXISTS corporate_announcements (
    symbol            TEXT NOT NULL,
    announcement_date TEXT NOT NULL,   -- the knowledge-timestamp: when the market learned this
    announcement_time TEXT,            -- HH:MM:SS if available, separate from date for clarity
    subject           TEXT,            -- short headline/subject of the filing
    details           TEXT,            -- longer description text, if provided
    attachment_url    TEXT,            -- link to the underlying PDF filing, if any
    category          TEXT,            -- controlled vocabulary, filled in later (NLP/manual pass)
    source            TEXT NOT NULL,   -- 'NSE' or 'BSE'
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (symbol, announcement_date, announcement_time, subject)
);

CREATE INDEX IF NOT EXISTS idx_corp_announcements_symbol ON corporate_announcements(symbol);
CREATE INDEX IF NOT EXISTS idx_corp_announcements_date ON corporate_announcements(announcement_date);
