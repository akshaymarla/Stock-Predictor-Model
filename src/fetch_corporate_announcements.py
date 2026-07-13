"""
Fetches corporate announcements (the "ad hoc business decisions" table --
M&A, management changes, board meetings, order wins, litigation, etc.)
from NSE and loads them into `corporate_announcements`.

STATUS: CONFIRMED 2026-07-13 against a live DevTools response, e.g.:
    {
        "an_dt": "13-Jul-2026 12:50:53",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/....pdf",
        "attchmntText": "Dynamic Cables Limited has informed the Exchange...",
        "desc": "Certificate under SEBI (Depositories and Participants) ...",
        "seq_id": "106695428",
        "sm_isin": "INE600Y01019",
        "sm_name": "Dynamic Cables Limited",
        "symbol": "DYCL",
        ...
    }
The original guessed field names (symbol, desc, attchmntText, attchmntFile,
an_dt) all matched the real response. Two extra confirmed fields worth
capturing: seq_id (NSE's own unique announcement id -- a far more reliable
dedupe key than symbol+date+time+subject) and sm_isin (stable identifier
across symbol renames).

Usage:
    # nightly use -- no args needed, defaults to today only
    python src/fetch_corporate_announcements.py

    # explicit date range
    python src/fetch_corporate_announcements.py --from-date 01-07-2026 --to-date 13-07-2026

    # one-time historical backfill shortcut
    python src/fetch_corporate_announcements.py --years 5
"""
import argparse
import sys
from datetime import datetime, timedelta

import requests

from db import get_conn

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements"


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


def _split_datetime(raw) -> tuple:
    """NSE sends combined datetime strings like '13-Jul-2026 12:50:53' in an_dt."""
    if not raw:
        return None, None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S") if " " in raw else None
        except ValueError:
            continue
    return raw, None


def parse_announcements(payload, fetched_at: str) -> list[tuple]:
    rows = []
    for item in _extract_items(payload):
        seq_id = item.get("seq_id")
        symbol = item.get("symbol")
        raw_dt = item.get("an_dt")
        if not seq_id or not symbol or not raw_dt:
            continue
        ann_date, ann_time = _split_datetime(raw_dt)

        rows.append((
            seq_id, symbol, item.get("sm_isin"), ann_date, ann_time,
            item.get("desc"), item.get("attchmntText"), item.get("attchmntFile"),
            None,  # category -- filled in later
            "NSE", fetched_at,
        ))
    return rows


def upsert(conn, rows: list[tuple]):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO corporate_announcements
            (seq_id, symbol, isin, announcement_date, announcement_time,
             subject, details, attachment_url, category, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(seq_id) DO UPDATE SET fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", help="DD-MM-YYYY, defaults to today (nightly-run friendly)")
    parser.add_argument("--to-date", help="DD-MM-YYYY, defaults to today")
    parser.add_argument("--years", type=float,
                         help="shortcut for a one-time historical backfill: fetch this "
                              "many years back from today, overrides --from-date/--to-date")
    args = parser.parse_args()

    today = datetime.now()
    if args.years:
        from_date = (today - timedelta(days=int(args.years * 365.25))).strftime("%d-%m-%Y")
        to_date = today.strftime("%d-%m-%Y")
    else:
        from_date = args.from_date or today.strftime("%d-%m-%Y")
        to_date = args.to_date or today.strftime("%d-%m-%Y")

    fetched_at = datetime.now().isoformat()
    conn = get_conn()
    session = make_session()

    params = {
        "index": "equities",
        "from_date": from_date,
        "to_date": to_date,
    }
    try:
        resp = session.get(ANNOUNCEMENTS_URL, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        print("See the STATUS note at the top of this file for how to fix it.",
              file=sys.stderr)
        sys.exit(1)

    rows = parse_announcements(payload, fetched_at)
    if not rows:
        print("Parsed 0 rows -- field names in parse_announcements() are "
              "probably wrong. Grab a real response via DevTools and send "
              "it over.", file=sys.stderr)
        sys.exit(1)

    upsert(conn, rows)
    print(f"Done. Upserted {len(rows)} announcement rows.")


if __name__ == "__main__":
    main()
