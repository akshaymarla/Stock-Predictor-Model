"""
Fetches quarterly/annual financial results (revenue, net profit, EPS) from
NSE and loads them into `financial_results`.

STATUS: UNVERIFIED -- same situation corporate_announcements started in.
Rendered from https://www.nseindia.com/companies-listing/corporate-filings-financial-results
The endpoint URL and params below (`/api/corporates-financial-results?index=equities`)
are the pattern used by several working open-source NSE scrapers, but I have
NOT confirmed the exact field names against a live response. Expect this to
need one round of fixing:

  1. Run it.
  2. If it errors or the parsed row count is 0, open the page above in a
     browser, DevTools -> Network -> XHR, find the real request, and send
     me the URL + a sample response the same way you did for ASM/GSM --
     I'll fix parse_results() to match.

Likely field names based on common NSE financial-results API conventions
(UNCONFIRMED, just my best starting guess):
    symbol, re_broadcast_timestamp or bc_dt (broadcast/disclosure datetime --
    THE point-in-time field, see schema.sql comment), re_end_date or toDate
    (period-end date -- descriptive only, never join on this), re_cons or
    consolidated ("Consolidated"/"Non-Consolidated"), re_revenue or
    reNetSales, re_net_profit or reProfitLoss, re_eps or reBasicEPS,
    reAttachment or attachmntFile (PDF url).

Usage:
    python src/fetch_financial_results.py --from-date 01-04-2026 --to-date 13-07-2026
"""
import argparse
import sys
from datetime import datetime
from typing import Optional

import requests

from db import get_conn

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

RESULTS_URL = "https://www.nseindia.com/api/corporates-financial-results"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    s.get("https://www.nseindia.com", timeout=10)  # sets cookies, same as ASM/GSM
    return s


def _extract_items(payload) -> list:
    if isinstance(payload, dict):
        return payload.get("data", payload.get("rows", []))
    if isinstance(payload, list):
        return payload
    return []


def _to_iso_date(raw) -> Optional[str]:
    """Best guess: NSE tends to send dates like '10-Jul-2026' or with a time
    component like '10-Jul-2026 18:32:11'. Falls back to the raw string
    rather than crashing, so a format surprise doesn't kill the whole run."""
    if not raw:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _to_float(raw):
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_results(payload, fetched_at: str) -> list[tuple]:
    rows = []
    for item in _extract_items(payload):
        symbol = item.get("symbol")
        # try a few plausible key names for the broadcast/disclosure datetime --
        # this is the point-in-time field, NOT the period-end date
        raw_disclosure = (item.get("re_broadcast_timestamp") or item.get("bc_dt")
                           or item.get("broadcastDate") or item.get("an_dt"))
        if not symbol or not raw_disclosure:
            continue
        disclosure_date = _to_iso_date(raw_disclosure)

        raw_period_end = item.get("re_end_date") or item.get("toDate") or item.get("period_end")
        period_end_date = _to_iso_date(raw_period_end)

        consolidated_flag = (item.get("re_cons") or item.get("consolidated")
                              or item.get("audited", ""))
        result_type = ("CONSOLIDATED" if "consol" in str(consolidated_flag).lower()
                        else "STANDALONE")

        revenue = _to_float(item.get("re_revenue") or item.get("reNetSales"))
        net_profit = _to_float(item.get("re_net_profit") or item.get("reProfitLoss"))
        eps = _to_float(item.get("re_eps") or item.get("reBasicEPS"))
        attachment = item.get("reAttachment") or item.get("attchmntFile")
        period_type = item.get("re_qtr") or item.get("period") or None

        rows.append((
            symbol, disclosure_date, period_end_date, period_type, result_type,
            revenue, net_profit, eps, attachment, "NSE", fetched_at,
        ))
    return rows


def upsert(conn, rows: list[tuple]):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO financial_results
            (symbol, disclosure_date, period_end_date, period_type, result_type,
             revenue, net_profit, eps, attachment_url, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, disclosure_date, period_end_date, result_type)
        DO UPDATE SET
            period_type=excluded.period_type,
            revenue=excluded.revenue,
            net_profit=excluded.net_profit,
            eps=excluded.eps,
            attachment_url=excluded.attachment_url,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="DD-MM-YYYY")
    parser.add_argument("--to-date", required=True, help="DD-MM-YYYY")
    args = parser.parse_args()

    fetched_at = datetime.now().isoformat()
    conn = get_conn()
    session = make_session()

    params = {
        "index": "equities",
        "from_date": args.from_date,
        "to_date": args.to_date,
    }
    try:
        resp = session.get(RESULTS_URL, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        print("See the STATUS note at the top of this file for how to fix it.",
              file=sys.stderr)
        sys.exit(1)

    rows = parse_results(payload, fetched_at)
    if not rows:
        print("Parsed 0 rows -- field names in parse_results() are "
              "probably wrong. Grab a real response via DevTools and send "
              "it over.", file=sys.stderr)
        sys.exit(1)

    upsert(conn, rows)
    print(f"Done. Upserted {len(rows)} financial result rows.")


if __name__ == "__main__":
    main()
