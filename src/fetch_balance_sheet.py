"""
Fetches balance sheet data from screener.in via the vendored
src/screenerScraper.py and loads it into `balance_sheet`.

STATUS: CONFIRMED 2026-07-15 against a real live RELIANCE balance sheet --
all 10 COLUMN_MAP labels matched the original guess exactly on the first
try. COLUMN_MAP covers the BASE summary table only
(balanceSheet(withAddon=False)) -- deliberately not using withAddon=True.
Traced through screenerScraper.py's __addonData(): the addon endpoint dict
keys (Borrowing, TotalAssets, OtherLiabilities, OtherAssets) are only used
to build request URLs, not the actual field names in the response -- those
come from each schedule's own JSON, a sub-line-item breakdown (e.g. "Fixed
Assets" broken into individual asset categories) with still-unknown key
names. Not chased for now since the base table already covers the useful
summary figures; company-specific/unusual line items that don't fit
COLUMN_MAP land in raw_metrics_json instead (see schema.sql).

POINT-IN-TIME NOTE: same as fetch_financial_results.py -- screener.in has no
disclosure timestamp, so disclosure_date is derived via
screener_common.find_disclosure() against corporate_announcements. No
match = skipped and logged, never defaulted to today.

Usage:
    python src/fetch_balance_sheet.py --symbols RELIANCE TCS
    python src/fetch_balance_sheet.py    # full Nifty 500 universe
"""
import argparse
import sys
import time
from datetime import datetime

from db import get_conn, get_universe
from screenerScraper import ScreenerScrape
from screener_common import (flatten_periods, find_disclosure, period_type,
                              add_common_args, resolve_views, metrics_json)

# Confirmed live 2026-07-15 -- see STATUS note above. Base summary table only.
COLUMN_MAP = {
    "EquityCapital": "equity_capital",
    "Reserves": "reserves",
    "Borrowings": "borrowings",
    "OtherLiabilities": "other_liabilities",
    "TotalLiabilities": "total_liabilities",
    "FixedAssets": "fixed_assets",
    "CWIP": "cwip",
    "Investments": "investments",
    "OtherAssets": "other_assets",
    "TotalAssets": "total_assets",
}

INSERT_COLUMNS = list(COLUMN_MAP.values()) + ["raw_metrics_json"]


def build_rows(conn, symbol: str, periods: dict, result_type: str, fetched_at: str) -> list:
    rows = []
    for period_end_date, metrics in periods.items():
        disclosure_date, seq_id = find_disclosure(conn, symbol, period_end_date)
        if not disclosure_date:
            print(f"    SKIP {symbol} {period_end_date} ({result_type}): no matching "
                  f"'financial result' announcement within the disclosure window.",
                  file=sys.stderr)
            continue

        values = {col: metrics.get(label) for label, col in COLUMN_MAP.items()}
        values["raw_metrics_json"] = metrics_json(metrics)
        rows.append((
            symbol, disclosure_date, period_end_date,
            period_type(period_end_date, annual=True), result_type,
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
        INSERT INTO balance_sheet ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(symbol, period_end_date, result_type) DO UPDATE SET
            {update_clause}
        """,
        rows,
    )
    conn.commit()


def fetch_symbol(scraper: ScreenerScrape, conn, symbol: str, views: list, sleep: float, fetched_at: str) -> list:
    token = scraper.getBSEToken(symbol)
    if not token:
        print(f"    FAILED for {symbol}: no BSE token found -- skipping.", file=sys.stderr)
        return []

    all_rows = []
    for i, (consolidated, result_type) in enumerate(views):
        try:
            scraper.loadScraper(token, consolidated=consolidated)
            periods = flatten_periods(scraper.balanceSheet(withAddon=False))
            if periods:
                all_rows.extend(build_rows(conn, symbol, periods, result_type, fetched_at))
        except Exception as e:
            print(f"    FAILED for {symbol} ({result_type}): {e}", file=sys.stderr)
        if i < len(views) - 1:
            time.sleep(sleep)  # pace between consolidated/standalone views, not just between symbols
    return all_rows


def main():
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    args = parser.parse_args()
    views = resolve_views(args)

    fetched_at = datetime.now().isoformat()
    conn = get_conn()

    symbols = args.symbols
    if not symbols:
        symbols = get_universe(conn)
        if not symbols:
            print("No --symbols given and index_membership is empty.", file=sys.stderr)
            sys.exit(1)
        print(f"No --symbols given -- using the full Nifty 500 universe ({len(symbols)} symbols).")

    scraper = ScreenerScrape()

    total_upserted = 0
    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] fetching {symbol} ...")
        rows = fetch_symbol(scraper, conn, symbol, views, args.sleep, fetched_at)
        if rows:
            upsert(conn, rows)
            total_upserted += len(rows)
            print(f"    upserted {len(rows)} rows")
        time.sleep(args.sleep)

    if total_upserted == 0:
        print("Upserted 0 rows total.", file=sys.stderr)
        sys.exit(1)

    print(f"Done. Upserted {total_upserted} balance sheet rows across {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
