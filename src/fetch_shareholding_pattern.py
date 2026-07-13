"""
Fetches shareholding pattern disclosures (promoter/public/employee-trust
split, filed quarterly per SEBI regulations) from NSE and loads them into
`shareholding_pattern`.

STATUS: PARTIALLY CONFIRMED, 2026-07-13. Field NAMES below come from a real
NSE DevTools response -- the "columns" config NSE returns for
https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern?symbol=HDFCBANK
which lists the real keys each data row uses: name, pr_and_prgrp,
public_val, employeeTrusts, revisedStatus, date, submissionDate,
revisionDate, xbrl, broadcastDate, systemDate, timeDifference.

What's NOT yet confirmed:
  - The actual XHR API endpoint URL (the URL above is the page, not the
    API call the page makes under the hood -- same situation
    corporate-announcements started in). ENDPOINT_URL below is a guess
    following this repo's other /api/corporate-* endpoints.
  - The exact VALUE formats (e.g. is pr_and_prgrp a bare number, a string
    with "%", does xbrl come back as a plain URL string or a nested
    object) -- we only got the column config, not a sample data row.

Expect one round of fixing, same as the other scripts:
  1. Run it.
  2. If it errors or the parsed row count is 0, open the page above in a
     browser, DevTools -> Network -> XHR, find the real request, and send
     me the URL + a sample response -- I'll fix parse_shareholding() and
     ENDPOINT_URL to match.

POINT-IN-TIME NOTE: disclosure_date uses broadcastDate ("Exchange Received
Time" per NSE's own hover-table label), not `date` (which is NSE's "AS ON
DATE" -- the shareholding snapshot period, not when the market learned it).
See the comment in schema.sql for the full reasoning.

Usage:
    python src/fetch_shareholding_pattern.py --symbols RELIANCE TCS INFY
"""
import argparse
import sys
import time
from datetime import datetime
from typing import Optional

import requests

from db import get_conn, get_universe

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

ENDPOINT_URL = "https://www.nseindia.com/api/corporate-share-holdings-master"


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
    if not raw:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _to_float(raw):
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _attachment_url(raw):
    """xbrl's "type": "attachment" in the columns config suggests either a
    plain URL string or a small object -- handle both defensively."""
    if isinstance(raw, dict):
        return raw.get("url") or raw.get("link") or raw.get("fileName")
    return raw


def parse_shareholding(payload, symbol: str, fetched_at: str) -> list[tuple]:
    rows = []
    for item in _extract_items(payload):
        raw_disclosure = item.get("broadcastDate")
        period_end = _to_iso_date(item.get("date"))
        if not raw_disclosure or not period_end:
            continue
        disclosure_date = _to_iso_date(raw_disclosure)

        rows.append((
            symbol, disclosure_date, period_end,
            _to_float(item.get("pr_and_prgrp")),
            _to_float(item.get("public_val")),
            _to_float(item.get("employeeTrusts")),
            item.get("revisedStatus"),
            _to_iso_date(item.get("submissionDate")),
            _to_iso_date(item.get("revisionDate")),
            _to_iso_date(item.get("systemDate")),
            _attachment_url(item.get("xbrl")),
            "NSE", fetched_at,
        ))
    return rows


def upsert(conn, rows: list[tuple]):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO shareholding_pattern
            (symbol, disclosure_date, period_end_date, promoter_pct, public_pct,
             employee_trust_pct, status, submission_date, revision_date,
             dissemination_time, attachment_url, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, disclosure_date, period_end_date) DO UPDATE SET
            promoter_pct=excluded.promoter_pct,
            public_pct=excluded.public_pct,
            employee_trust_pct=excluded.employee_trust_pct,
            status=excluded.status,
            revision_date=excluded.revision_date,
            dissemination_time=excluded.dissemination_time,
            attachment_url=excluded.attachment_url,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                         help="NSE symbols, e.g. RELIANCE TCS INFY. "
                              "Omit to use the full Nifty 500 universe from index_membership.")
    parser.add_argument("--sleep", type=float, default=1.0,
                         help="seconds to sleep between symbols, be polite to NSE")
    args = parser.parse_args()

    fetched_at = datetime.now().isoformat()
    conn = get_conn()
    session = make_session()

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

    all_rows = []
    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] fetching {symbol} ...")
        try:
            resp = session.get(ENDPOINT_URL, params={"symbol": symbol}, timeout=15)
            resp.raise_for_status()
            try:
                payload = resp.json()
            except ValueError:
                print(f"    FAILED for {symbol}: response wasn't valid JSON "
                      f"(HTTP {resp.status_code}) -- ENDPOINT_URL is probably wrong. "
                      f"Raw response (first 500 chars): {resp.text[:500]!r}", file=sys.stderr)
                continue
            rows = parse_shareholding(payload, symbol, fetched_at)
            print(f"    got {len(rows)} rows")
            all_rows.extend(rows)
        except Exception as e:
            print(f"    FAILED for {symbol}: {e}", file=sys.stderr)
        time.sleep(args.sleep)

    if not all_rows:
        print("Parsed 0 rows total -- see the STATUS note at the top of "
              "this file for how to fix it.", file=sys.stderr)
        sys.exit(1)

    upsert(conn, all_rows)
    print(f"Done. Upserted {len(all_rows)} shareholding pattern rows.")


if __name__ == "__main__":
    main()
