"""
Fetches granular QUARTERLY financial results (sales, expenses, operating
profit, OPM%, etc.) from screener.in via the vendored src/screenerScraper.py
(github.com/BuildAlgos/screener-scraper) and loads them into
`financial_results`.

QUARTERLY ONLY -- no annual/PnL data (2026-07-15, was previously wired in
via pnlReport()). Removed because it was a real bug, not just clutter: a
Q4 quarter (ending March) and the annual/FY result share the IDENTICAL
period-end date ('2015-03-01' either way), and this table's primary key is
(symbol, period_end_date, result_type) -- so the annual upsert was silently
OVERWRITING the real Q4 quarterly row whenever both existed for the same
symbol. If annual P&L data is wanted later, it needs its own table with its
own PK, not to share this one.

POINT-IN-TIME NOTE -- read before touching this file:
screener.in's data has NO disclosure/announcement timestamp anywhere --
quarterlyReport() only returns the quarter-END date (parsed from the UI's
column headers). So disclosure_date is NOT read from screener.in. It's
derived in screener_common.find_disclosure() by joining against our own
`corporate_announcements` table (confirmed live NSE data) for the earliest
"financial result"-type announcement within 65 days of quarter-end -- a real
SEBI-mandated disclosure window, not a guess. If nothing matches, the
quarter is SKIPPED and logged -- never defaulted to today's date. Note this
means quarters older than our corporate_announcements backfill range will
always be skipped (that's correct, not a bug -- we genuinely don't know
when the market learned about a 2015 result unless we've backfilled that
far back).

FIELD NAME VARIANCE ACROSS COMPANIES: confirmed 2026-07-15 -- different
company types use different screener.in row labels for the same underlying
concept (e.g. a bank like HDFCBANK doesn't necessarily use "Sales" the way
a manufacturer like RELIANCE does). METRIC_ALIASES below maps each of our
columns to a LIST of possible screener.in labels, tried in order, instead
of a single fixed string. When none of a column's aliases are found for a
given quarter, build_rows() logs the full set of unmapped keys seen for
that quarter to stderr -- use that to extend METRIC_ALIASES with new
aliases as they turn up, rather than trying to guess every company
template's vocabulary up front.

RATE LIMITING: screener.in throttles aggressive scraping (their own
screener-scraper README says so). Each symbol does up to 2 base page loads
(consolidated + standalone) x up to 5 addon calls each -- fetch_symbol()
sleeps between the two views, not just between symbols, to reduce burst
load. If you still see "rate-limited" errors, increase --sleep or use
--consolidated-only / --standalone-only to halve the request volume.

Usage:
    # explicit symbols
    python src/fetch_financial_results.py --symbols RELIANCE TCS

    # omit --symbols for the full Nifty 500 universe from index_membership
    python src/fetch_financial_results.py

    # fetch only one view (halves request volume, helps with rate limiting)
    python src/fetch_financial_results.py --symbols RELIANCE --consolidated-only

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
from screener_common import flatten_periods, find_disclosure, period_type, add_common_args, resolve_views

# screener.in row-label (after screener_common's whitespace normalization) ->
# our column name, as a list of aliases tried in order. Core fields confirmed
# live 2026-07-15 against a real RELIANCE quarter; "Revenue" added as a
# sales alias per a live report that some companies (e.g. banks) use it
# instead of "Sales" -- not yet independently confirmed against a live bank
# capture. Extend this as new aliases turn up in the unmapped-keys log.
METRIC_ALIASES = {
    "sales": ["Sales", "Revenue"],
    "expenses": ["Expenses"],
    "operating_profit": ["OperatingProfit"],
    "opm_pct": ["OPM%"],
    "other_income": ["OtherIncome"],
    "interest": ["Interest"],
    "depreciation": ["Depreciation"],
    "profit_before_tax": ["Profitbeforetax"],
    "tax_pct": ["Tax%"],
    "net_profit": ["NetProfit"],
    "eps": ["EPSinRs"],
    "yoy_sales_growth_pct": ["YOYSalesGrowth%"],
    "material_cost_pct": ["MaterialCost%"],
    "employee_cost_pct": ["EmployeeCost%"],
    "exceptional_items": ["Exceptionalitems"],
    "other_income_normal": ["Otherincomenormal"],
    "profit_from_associates": ["ProfitfromAssociates"],
    "minority_share": ["Minorityshare"],
    "exceptional_items_at": ["ExceptionalitemsAT"],
    "profit_excl_exceptional": ["ProfitexclExcep"],
    "profit_for_pe": ["ProfitforPE"],
    "profit_for_eps": ["ProfitforEPS"],
    "yoy_profit_growth_pct": ["YOYProfitGrowth%"],
}
# RawPDF isn't a numeric metric -- handled separately as raw_pdf_url below.

INSERT_COLUMNS = list(METRIC_ALIASES.keys()) + ["raw_pdf_url"]

ALL_KNOWN_ALIASES = {alias for aliases in METRIC_ALIASES.values() for alias in aliases} | {"RawPDF"}


def _lookup(metrics: dict, aliases: list):
    for alias in aliases:
        if alias in metrics:
            return metrics[alias]
    return None


def build_rows(conn, symbol: str, periods: dict, result_type: str, fetched_at: str) -> list:
    rows = []
    for period_end_date, metrics in periods.items():
        disclosure_date, seq_id = find_disclosure(conn, symbol, period_end_date)
        if not disclosure_date:
            print(f"    SKIP {symbol} {period_end_date} ({result_type}): no matching "
                  f"'financial result' announcement in corporate_announcements within "
                  f"the disclosure window -- not guessing a disclosure date.",
                  file=sys.stderr)
            continue

        values = {col: _lookup(metrics, aliases) for col, aliases in METRIC_ALIASES.items()}
        values["raw_pdf_url"] = metrics.get("RawPDF")

        if values["sales"] is None:
            unmapped = set(metrics.keys()) - ALL_KNOWN_ALIASES
            if unmapped:
                print(f"    NOTE {symbol} {period_end_date} ({result_type}): 'sales' "
                      f"didn't match any known alias. Unmapped keys seen: "
                      f"{sorted(unmapped)} -- may need a new alias in METRIC_ALIASES.",
                      file=sys.stderr)

        rows.append((
            symbol, disclosure_date, period_end_date,
            period_type(period_end_date), result_type,
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


def fetch_symbol(scraper: ScreenerScrape, conn, symbol: str, views: list, sleep: float, fetched_at: str) -> list:
    token = scraper.getBSEToken(symbol)
    if not token:
        print(f"    FAILED for {symbol}: no BSE token found (NSE and BSE symbols "
              f"can differ) -- skipping.", file=sys.stderr)
        return []

    all_rows = []
    for i, (consolidated, result_type) in enumerate(views):
        try:
            scraper.loadScraper(token, consolidated=consolidated)
            quarters = flatten_periods(scraper.quarterlyReport(withAddon=True))
            if quarters:
                all_rows.extend(build_rows(conn, symbol, quarters, result_type, fetched_at))
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
        rows = fetch_symbol(scraper, conn, symbol, views, args.sleep, fetched_at)
        if rows:
            upsert(conn, rows)
            total_upserted += len(rows)
            print(f"    upserted {len(rows)} rows")
        time.sleep(args.sleep)

    if total_upserted == 0:
        print("Upserted 0 rows total -- see stderr above for per-symbol failures "
              "(missing BSE tokens, rate limiting, no disclosure match within the "
              "window, etc.).", file=sys.stderr)
        sys.exit(1)

    print(f"Done. Upserted {total_upserted} financial result rows across {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
