"""
Fetches granular quarterly financial results (sales, expenses, operating
profit, OPM%, etc.) from screener.in via the vendored src/screenerScraper.py
(github.com/BuildAlgos/screener-scraper) and loads them into
`financial_results`.

POINT-IN-TIME NOTE -- read before touching this file:
screener.in's data has NO disclosure/announcement timestamp anywhere --
quarterlyReport() only returns the quarter-END date (parsed from the UI's
column headers). Confirmed by reading screenerScraper.py directly: none of
its methods except the separate corporateAnnouncements() (a different BSE
API, not wired into the financial data) carry a broadcast/filing time.

So disclosure_date is NOT read from screener.in. It's derived by joining
against our own `corporate_announcements` table (confirmed live NSE data)
for the earliest "financial result"-type announcement dated between
period_end_date and period_end_date+65 days -- SEBI mandates disclosure
within 45 days (Q1-Q3) or 60 days (Q4/annual) of quarter-end, so this window
is a real regulatory bound, not a guess. If nothing matches in that window,
the quarter is SKIPPED and logged -- never defaulted to today's date. That
was a real bug in an earlier draft of this integration: silently stamping
unmatched historical quarters with today() would have corrupted years of
data with a fabricated "disclosed today" timestamp. A gap in the data is
honest; a wrong date is a silent landmine.

DATA SHAPE NOTE: screener.in's scraper methods (quarterlyReport(), etc.)
return a dict, NOT a pandas DataFrame -- {"2025-06-30": [{"Sales": 100.0},
{"Expenses": 80.0}, ...], "TTM": [...]}, one single-key dict per metric row
per quarter. _flatten_quarters() below merges each quarter's list into a
single flat dict. "TTM" (trailing twelve months) is not a specific quarter
and is skipped. Metric key names (e.g. "OperatingProfit", "OPM%") come from
screener.in's literal UI row labels with spaces stripped -- these are NOT
independently confirmed against a live scrape (no network access to
screener.in from the sandbox this was built in); if a live run parses 0
metrics for a matched quarter, that's the first thing to check.

Usage:
    # explicit symbols
    python src/fetch_financial_results.py --symbols RELIANCE TCS

    # omit --symbols for the full Nifty 500 universe from index_membership
    python src/fetch_financial_results.py

Not included in run_nightly.sh: like shareholding_pattern, this is a
quarterly-cadence dataset -- polling 500 symbols against screener.in every
night for data that changes ~4x/year would be wasted load. Run periodically
(e.g. monthly) instead.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta

from db import get_conn, get_universe
from screenerScraper import ScreenerScrape

DISCLOSURE_WINDOW_DAYS = 65  # SEBI: 45 days (Q1-Q3) / 60 days (Q4, annual) + buffer

# screener.in row-label -> our column name. Keys are the literal UI labels
# with spaces/"+' stripped by screenerScraper.py's __pullData(), UNCONFIRMED
# against a live scrape -- see the DATA SHAPE NOTE above.
METRIC_MAP = {
    "Sales": "sales",
    "Expenses": "expenses",
    "OperatingProfit": "operating_profit",
    "OPM%": "opm_pct",
    "OtherIncome": "other_income",
    "Interest": "interest",
    "Depreciation": "depreciation",
    "ProfitbeforeTax": "profit_before_tax",
    "Tax%": "tax_pct",
    "NetProfit": "net_profit",
    "EPSinRs": "eps",
}


def _flatten_quarters(raw: dict) -> dict:
    """raw: {period_label: [{"Sales": 100.0}, {"Expenses": 80.0}, ...], ...}
    (screener.in's actual return shape -- a dict, not a DataFrame).
    Returns {period_label: {"Sales": 100.0, "Expenses": 80.0, ...}},
    skipping "TTM" since it isn't a specific quarter."""
    flattened = {}
    for period, entries in raw.items():
        if period == "TTM":
            continue
        merged = {}
        for entry in entries:
            merged.update(entry)
        flattened[period] = merged
    return flattened


def _period_type(period_end_date: str) -> str:
    """Indian fiscal quarters: Apr-Jun=Q1, Jul-Sep=Q2, Oct-Dec=Q3, Jan-Mar=Q4."""
    month = int(period_end_date[5:7])
    return {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}.get(month, "ANNUAL")


def find_disclosure(conn, symbol: str, period_end_date: str):
    """Find the earliest 'financial result' announcement for this symbol
    within DISCLOSURE_WINDOW_DAYS of period_end_date. Returns
    (disclosure_date, seq_id) or (None, None) if nothing matches -- callers
    must skip the row in that case, not fall back to any default date."""
    window_end = (datetime.strptime(period_end_date, "%Y-%m-%d")
                  + timedelta(days=DISCLOSURE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    row = conn.execute(
        """
        SELECT announcement_date, seq_id FROM corporate_announcements
        WHERE symbol = ?
          AND announcement_date >= ?
          AND announcement_date <= ?
          AND (subject LIKE '%financial result%' OR details LIKE '%financial result%')
        ORDER BY announcement_date ASC
        LIMIT 1
        """,
        (symbol, period_end_date, window_end),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def build_rows(conn, symbol: str, quarters: dict, result_type: str, fetched_at: str) -> list:
    rows = []
    for period_end_date, metrics in quarters.items():
        disclosure_date, seq_id = find_disclosure(conn, symbol, period_end_date)
        if not disclosure_date:
            print(f"    SKIP {symbol} {period_end_date} ({result_type}): no matching "
                  f"'financial result' announcement in corporate_announcements within "
                  f"{DISCLOSURE_WINDOW_DAYS} days -- not guessing a disclosure date.",
                  file=sys.stderr)
            continue

        values = {col: metrics.get(label) for label, col in METRIC_MAP.items()}
        rows.append((
            symbol, disclosure_date, period_end_date, _period_type(period_end_date), result_type,
            values["sales"], values["expenses"], values["operating_profit"], values["opm_pct"],
            values["other_income"], values["interest"], values["depreciation"],
            values["profit_before_tax"], values["tax_pct"], values["net_profit"], values["eps"],
            seq_id, "SCREENER", fetched_at,
        ))
    return rows


def upsert(conn, rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO financial_results
            (symbol, disclosure_date, period_end_date, period_type, result_type,
             sales, expenses, operating_profit, opm_pct, other_income, interest,
             depreciation, profit_before_tax, tax_pct, net_profit, eps,
             disclosure_seq_id, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, period_end_date, result_type) DO UPDATE SET
            disclosure_date=excluded.disclosure_date,
            period_type=excluded.period_type,
            sales=excluded.sales,
            expenses=excluded.expenses,
            operating_profit=excluded.operating_profit,
            opm_pct=excluded.opm_pct,
            other_income=excluded.other_income,
            interest=excluded.interest,
            depreciation=excluded.depreciation,
            profit_before_tax=excluded.profit_before_tax,
            tax_pct=excluded.tax_pct,
            net_profit=excluded.net_profit,
            eps=excluded.eps,
            disclosure_seq_id=excluded.disclosure_seq_id,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def fetch_symbol(scraper: ScreenerScrape, conn, symbol: str, fetched_at: str) -> list:
    token = scraper.getBSEToken(symbol)
    if not token:
        print(f"    FAILED for {symbol}: no BSE token found (NSE and BSE symbols "
              f"can differ) -- skipping.", file=sys.stderr)
        return []

    all_rows = []
    for consolidated, result_type in ((True, "CONSOLIDATED"), (False, "STANDALONE")):
        try:
            scraper.loadScraper(token, consolidated=consolidated)
            raw = scraper.quarterlyReport(withAddon=True)
            quarters = _flatten_quarters(raw)
            if not quarters:
                continue
            all_rows.extend(build_rows(conn, symbol, quarters, result_type, fetched_at))
        except Exception as e:
            print(f"    FAILED for {symbol} ({result_type}): {e}", file=sys.stderr)
    return all_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                         help="NSE symbols, e.g. RELIANCE TCS INFY. "
                              "Omit to use the full Nifty 500 universe from index_membership.")
    parser.add_argument("--sleep", type=float, default=1.0,
                         help="seconds to sleep between symbols, be polite to screener.in/BSE")
    args = parser.parse_args()

    fetched_at = datetime.now().isoformat()
    conn = get_conn()

    symbols = args.symbols
    if not symbols:
        symbols = get_universe(conn)
        if not symbols:
            print("No --symbols given and index_membership is empty -- run "
                  "fetch_index_membership.py first, or pass --symbols explicitly.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"No --symbols given -- using the full Nifty 500 universe "
              f"from index_membership ({len(symbols)} symbols).")

    scraper = ScreenerScrape()

    total_upserted = 0
    total_skipped_no_token = 0
    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] fetching {symbol} ...")
        rows = fetch_symbol(scraper, conn, symbol, fetched_at)
        if rows:
            upsert(conn, rows)
            total_upserted += len(rows)
            print(f"    upserted {len(rows)} quarter rows")
        time.sleep(args.sleep)

    if total_upserted == 0:
        print("Upserted 0 rows total -- see stderr above for per-symbol failures "
              "(missing BSE tokens, no disclosure match within the window, etc.).",
              file=sys.stderr)
        sys.exit(1)

    print(f"Done. Upserted {total_upserted} financial result rows across {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
