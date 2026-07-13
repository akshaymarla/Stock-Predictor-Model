"""
Fetches corporate announcements (the "ad hoc business decisions" table --
M&A, management changes, board meetings, order wins, litigation, etc.)
from NSE and loads them into `corporate_announcements`.

STATUS: UNVERIFIED -- same situation we started ASM/GSM in.
Rendered from https://www.nseindia.com/companies-listing/corporate-filings-announcements
The endpoint URL and param below (`/api/corporate-announcements?index=equities`)
are the pattern used by several working open-source NSE scrapers, but I have
NOT confirmed the exact field names against a live response the way we did
for ASM/GSM. Expect this to need one round of fixing:

  1. Run it.
  2. If it errors or the parsed row count is 0, open the page above in a
     browser, DevTools -> Network -> XHR, find the real request, and send
     me the URL + a sample response the same way you did for ASM/GSM --
     I'll fix parse_announcements() to match.

Likely field names based on common NSE announcement API conventions
(UNCONFIRMED, just my best starting guess):
    symbol, desc (subject line), attchmntText (details),
    attchmntFile (PDF url), an_dt or sm_dt or dt (announcement datetime,
    usually as one combined string like "10-Jul-2026 18:32:11")

Usage:
    python src/fetch_corporate_announcements.py --from-date 01-07-2026 --to-date 13-07-2026
"""
import argparse
import sys
from datetime import datetime

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
    """
    Best guess: NSE tends to send combined datetime strings like
    '10-Jul-2026 18:32:11'. Split into (date, time) matching our schema.
    Falls back gracefully if the format doesn't match -- returns
    (raw_string, None) rather than crashing, so a format surprise doesn't
    kill the whole run; you'll see it in the data and can flag it to me.
    """
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
        symbol = item.get("symbol")
        # try a few plausible key names for the datetime field
        raw_dt = (item.get("an_dt") or item.get("sm_dt") or item.get("dt")
                  or item.get("broadcastDate") or item.get("date"))
        if not symbol or not raw_dt:
            continue
        ann_date, ann_time = _split_datetime(raw_dt)

        subject = item.get("desc") or item.get("subject") or item.get("attchmntText", "")
        details = item.get("attchmntText") if item.get("desc") else None
        attachment = item.get("attchmntFile") or item.get("attachmentUrl")

        rows.append((
            symbol, ann_date, ann_time, subject, details, attachment,
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
            (symbol, announcement_date, announcement_time, subject, details,
             attachment_url, category, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, announcement_date, announcement_time, subject)
        DO UPDATE SET fetched_at=excluded.fetched_at
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
