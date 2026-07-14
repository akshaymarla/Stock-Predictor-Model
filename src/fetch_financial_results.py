"""
Fetches granular quarterly + annual financial results (sales, expenses,
operating profit, OPM%, etc.) from screener.in via the vendored
src/screenerScraper.py (github.com/BuildAlgos/screener-scraper) and loads
them into `financial_results`.

POINT-IN-TIME NOTE -- read before touching this file:
screener.in's data has NO disclosure/announcement timestamp anywhere --
quarterlyReport()/pnlReport() only return the period-END date (parsed from
the UI's column headers). So disclosure_date is NOT read from screener.in.
It's derived in screener_common.find_disclosure() by joining against our own
`corporate_announcements` table (confirmed live NSE data) for the earliest
"financial result"-type announcement within 65 days of period-end -- a real
SEBI-mandated disclosure window, not a guess. If nothing matches, the period
is SKIPPED and logged -- never defaulted to today's date. See
src/screener_common.py for why this logic is centralized rather than
duplicated per script.

DATA SHAPE + FIELD NAME NOTES:
screener.in's scraper methods return a dict, NOT a pandas DataFrame --
{"2025-06-30": [{"Sales": 100.0}, {"Expenses": 80.0}, ...], "TTM": [...]}.
screener_common.flatten_periods() merges each period's list into one flat
dict and strips a confirmed whitespace bug: several base-table keys carry a
trailing non-breaking space (e.g. 'Sales\\xa0') that the vendored library's
own key-cleaning (`.replace(" ", "")`) doesn't catch, since \\xa0 isn't an
ASCII space. METRIC_MAP below is confirmed live 2026-07-14 against a real
RELIANCE quarter -- both the core P&L fields and the "addon" schedule-derived
bonus fields (YoY growth, cost %, exceptional items, minority share, etc.)
that come along for free with quarterlyReport(withAddon=True).

Usage:
    # explicit symbols
    python src/fetch_financial_results.py --symbols RELIANCE TCS

    # omit --symbols for the full Nifty 500 universe from index_membership
    python src/fetch_financial_results.py

    # skip the annual P&L pull, quarterly only
    python src/fetch_financial_results.py --symbols RELIANCE --no-annual

Not included in run_nightly.sh: like shareholding_pattern, this is a
quarterly-cadence dataset -- polling 500 symbols against screener.in every
night for data that changes ~4x/year would be wasted load. Run periodically
(e.g. monthly) instead.
"""
import argparse
import sys
import time
from datetime import datetime

from db import get_conn, get_universe
from screenerScraper import ScreenerScrape
from screener_common import flatten_periods, find_disclosure, period_type

# screener.in row-label (after screener_common's whitespace normalization) ->
# our column name. Confirmed live 2026-07-14 against a real RELIANCE quarter.
METRIC_MAP = {
    "Sales": "sales",
    "Expenses": "expenses",
    "OperatingProfit": "operating_profit",
    "OPM%": "opm_pct",
    "OtherIncome": "other_income",
    "Interest": "interest",
    "Depreciation": "depreciation",
    "Profitbeforetax": "profit_before_tax",
    "Tax%": "tax_pct",
    "NetProfit": "net_profit",
    "EPSinRs": "eps",
    "YOYSalesGrowth%": "yoy_sales_growth_pct",
    "MaterialCost%": "material_cost_pct",
    "EmployeeCost%": "employee_cost_pct",
    "Exceptionalitems": "exceptional_items",
    "Otherincomenormal": "other_income_normal",
    "ProfitfromAssociates": "profit_from_associates",
    "Minorityshare": "minority_share",
    "ExceptionalitemsAT": "exceptional_items_at",
    "ProfitexclExcep": "profit_excl_exceptional",
    "ProfitforPE": "profit_for_pe",
    "ProfitforEPS": "profit_for_eps",
    "YOYProfitGrowth%": "yoy_profit_growth_pct",
}
# RawPDF isn't a numeric metric -- handled separately as raw_pdf_url below.

INSERT_COLUMNS = list(METRIC_MAP.values()) + ["raw_pdf_url"]


def build_rows(conn, symbol: str, periods: dict, result_type: str, annual: bool, fetched_at: str) -> list:
    rows = []
    for period_end_date, metrics in periods.items():
        disclosure_date, seq_id = find_disclosure(conn, symbol, period_end_date)
        if not disclosure_date:
            print(f"    SKIP {symbol} {period_end_date} ({result_type}): no matching "
                  f"'financial result' announcement in corporate_announcements within "
                  f"the disclosure window -- not guessing a disclosure date.",
                  file=sys.stderr)
            continue

        values = {col: metrics.get(label) for label, col in METRIC_MAP.items()}
        values["raw_pdf_url"] = metrics.get("RawPDF")

        rows.append((
            symbol, disclosure_date, period_end_date,
            period_type(period_end_date, annual=annual), result_type,
            *[values[col] for col in INSERT_COLUMNS],
            seq_id, "SCREENER", fetched_at,
        ))
    return rows


def upsert(conn, rows: list):
    if not rows:
        return
    columns = ["symbol", "disclosure_date", "period_end_date", "period_type", "result_type"] + \
              INSERT_COLUMNS + ["disclosure_seq_id", "source", "fetched_at"]
    placeholders = ", ".join(["?"] * len(columns))
    update_cols = [c for c in columns if c not in
                   ("symbol", "period_end_date", "result_type", "source")]
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
    conn.executemany(
        f"""
        INSERT INTO financial_results ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(symbol, period_end_date, result_type) DO UPDATE SET
            {update_clause}
        """,
        rows,
    )
    conn.commit()


def fetch_symbol(scraper: ScreenerScrape, conn, symbol: str, fetch_annual: bool, fetched_at: str) -> list:
    token = scraper.getBSEToken(symbol)
    if not token:
        print(f"    FAILED for {symbol}: no BSE token found (NSE and BSE symbols "
              f"can differ) -- skipping.", file=sys.stderr)
        return []

    all_rows = []
    for consolidated, result_type in ((True, "CONSOLIDATED"), (False, "STANDALONE")):
        try:
            scraper.loadScraper(token, consolidated=consolidated)

            quarters = flatten_periods(scraper.quarterlyReport(withAddon=True))
            if quarters:
                all_rows.extend(build_rows(conn, symbol, quarters, result_type, False, fetched_at))

            if fetch_annual:
                annual_periods = flatten_periods(scraper.pnlReport(withAddon=True))
                if annual_periods:
                    all_rows.extend(build_rows(conn, symbol, annual_periods, result_type, True, fetched_at))
        except Exception as e:
            print(f"    FAILED for {symbol} ({result_type}): {e}", file=sys.stderr)
    return all_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                         help="NSE symbols, e.g. RELIANCE TCS INFY. "
                              "Omit to use the full Nifty 500 universe from index_membership.")
    parser.add_argument("--no-annual", action="store_true",
                         help="skip the annual P&L pull (pnlReport), quarterly only")
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
    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] fetching {symbol} ...")
        rows = fetch_symbol(scraper, conn, symbol, not args.no_annual, fetched_at)
        if rows:
            upsert(conn, rows)
            total_upserted += len(rows)
            print(f"    upserted {len(rows)} rows")
        time.sleep(args.sleep)

    if total_upserted == 0:
        print("Upserted 0 rows total -- see stderr above for per-symbol failures "
              "(missing BSE tokens, no disclosure match within the window, etc.).",
              file=sys.stderr)
        sys.exit(1)

    print(f"Done. Upserted {total_upserted} financial result rows across {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
