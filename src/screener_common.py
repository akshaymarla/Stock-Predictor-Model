"""
Shared helpers for every screener.in-sourced fetch script (fetch_financial_results.py,
fetch_balance_sheet.py, fetch_cash_flow.py, fetch_ratios.py). Centralized here on
purpose: the point-in-time disclosure-date logic must behave identically in every
script that uses it, and a copy-pasted slip in just one file could reintroduce the
"default to today() on no match" bug this project explicitly guards against -- see
the note at the top of fetch_financial_results.py for the full story.
"""
import json
from datetime import datetime, timedelta

DISCLOSURE_WINDOW_DAYS = 65  # SEBI: 45 days (Q1-Q3) / 60 days (Q4, annual) + buffer


def metrics_json(metrics: dict) -> str:
    """Serialize a period's full flattened metrics dict for the
    raw_metrics_json catch-all column (see the note in schema.sql). Every
    screener.in-sourced table has one -- different company types (bank vs
    manufacturer vs NBFC) use genuinely different line items, not just
    different labels for the same concept, so nothing should be silently
    dropped just because it doesn't fit today's named columns."""
    return json.dumps(metrics, default=str)


def flatten_periods(raw: dict, skip_ttm: bool = True) -> dict:
    """screener.in's scraper methods (quarterlyReport, pnlReport, balanceSheet,
    cashFLow, ratios) return {period_label: [{key: val}, ...], ...} -- a dict of
    single-key dicts per metric row, NOT a pandas DataFrame.

    Confirmed live 2026-07-14 against a real RELIANCE quarter: several base-table
    keys carry a trailing non-breaking space (e.g. 'Sales\\xa0', 'Expenses\\xa0',
    'OtherIncome\\xa0', 'NetProfit\\xa0') that .replace(" ", "") in the vendored
    library doesn't strip (it only strips literal ASCII spaces). Normalizing here
    -- once, centrally -- instead of hardcoding the exact broken strings into each
    script's metric map, so this doesn't silently break again if screener.in's
    markup shifts in some other whitespace-y way.
    """
    flattened = {}
    for period, entries in raw.items():
        if skip_ttm and period == "TTM":
            continue
        merged = {}
        for entry in entries:
            for key, value in entry.items():
                merged[key.replace("\xa0", "").strip()] = value
        flattened[period] = merged
    return flattened


def find_disclosure(conn, symbol: str, period_end_date: str):
    """Find the earliest 'financial result' announcement for this symbol within
    DISCLOSURE_WINDOW_DAYS of period_end_date, by joining against our own
    confirmed-live corporate_announcements table. screener.in carries NO
    disclosure/announcement timestamp anywhere in its data -- this is the only
    source of truth for "when did the market actually learn this" used across
    every screener.in-sourced table.

    Returns (disclosure_date, seq_id), or (None, None) if nothing matches --
    callers MUST skip the row in that case, never fall back to any default date.
    """
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


def period_type(period_end_date: str, annual: bool = False) -> str:
    """Indian fiscal quarters: Apr-Jun=Q1, Jul-Sep=Q2, Oct-Dec=Q3, Jan-Mar=Q4.
    `annual` must be passed explicitly by the caller (e.g. True for pnlReport()
    results) -- a March period-end is ambiguous between Q4 and the fiscal
    year-end on its own, since Indian fiscal years also end in March."""
    if annual:
        return "ANNUAL"
    month = int(period_end_date[5:7])
    return {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}.get(month, "ANNUAL")


def add_common_args(parser):
    """--symbols, --consolidated-only/--standalone-only, --sleep -- shared by
    every screener.in-sourced fetch script's CLI. Centralized here after these
    flags drifted out of sync once already (added to fetch_financial_results.py
    but forgotten in fetch_balance_sheet.py/fetch_cash_flow.py/fetch_ratios.py,
    2026-07-15) -- importing this instead of copy-pasting argparse calls means
    that can't happen again."""
    parser.add_argument("--symbols", nargs="+",
                         help="NSE symbols, e.g. RELIANCE TCS INFY. "
                              "Omit to use the full Nifty 500 universe from index_membership.")
    view_group = parser.add_mutually_exclusive_group()
    view_group.add_argument("--consolidated-only", action="store_true",
                             help="skip the standalone view, halves request volume")
    view_group.add_argument("--standalone-only", action="store_true",
                             help="skip the consolidated view, halves request volume")
    parser.add_argument("--sleep", type=float, default=2.0,
                         help="seconds to sleep between requests -- screener.in throttles "
                              "aggressive scraping, raise this if you see rate-limit errors")


def resolve_views(args) -> list:
    """Turn --consolidated-only/--standalone-only into the (is_consolidated,
    result_type) pairs callers loop over. Default (neither flag): both views."""
    if args.consolidated_only:
        return [(True, "CONSOLIDATED")]
    if args.standalone_only:
        return [(False, "STANDALONE")]
    return [(True, "CONSOLIDATED"), (False, "STANDALONE")]
