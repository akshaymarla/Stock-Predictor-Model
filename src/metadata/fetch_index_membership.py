"""
Fetches the CURRENT Nifty 500 constituent list from NSE Indices and records
it as a dated snapshot in `index_membership`.

IMPORTANT CAVEAT (the one we discussed at length -- read before relying on
this for backtesting):
This gives you TODAY's constituents, not who was in the index a year ago.
Every time you run this, it adds one more dated snapshot -- so run it
periodically (e.g. as part of the nightly job) and over time you'll build
your own point-in-time record going forward. It does NOT retroactively
solve the "who was in the index in 2022" problem; that's the separate
historical-reconstruction task (via NSE Indices' monthly archive reports)
we talked about doing later.

Source: https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv
(confirmed to exist via web search; NOT fetched live from this sandbox --
niftyindices.com isn't in the reachable domain list here. Column names
below are the standard ones this file has used historically -- verify
after your first real run and tell me if they've changed.)

Usage:
    python src/fetch_index_membership.py
"""
import csv
import io
import sys
from datetime import date

import requests

from core.db import get_conn

CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
INDEX_NAME = "NIFTY500"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

# Different possible header spellings seen across versions of this file,
# mapped to our schema's column names. Matched case-insensitively.
HEADER_ALIASES = {
    "company name": "company_name",
    "industry": "industry",
    "symbol": "symbol",
    "series": "series",
    "isin code": "isin",
    "isin": "isin",
}


def fetch_csv_text() -> str:
    resp = requests.get(CSV_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_csv(csv_text: str, snapshot_date: str) -> list[tuple]:
    reader = csv.DictReader(io.StringIO(csv_text))
    # normalize headers so we're resilient to case/spacing differences
    field_map = {}
    for raw_field in reader.fieldnames or []:
        key = raw_field.strip().lower()
        if key in HEADER_ALIASES:
            field_map[raw_field] = HEADER_ALIASES[key]

    rows = []
    for row in reader:
        normalized = {field_map[k]: v for k, v in row.items() if k in field_map}
        symbol = normalized.get("symbol", "").strip()
        if not symbol:
            continue
        rows.append((
            symbol,
            INDEX_NAME,
            normalized.get("company_name", "").strip(),
            normalized.get("industry", "").strip(),
            normalized.get("isin", "").strip(),
            snapshot_date,
        ))
    return rows


def upsert(conn, rows: list[tuple]):
    conn.executemany(
        """
        INSERT INTO index_membership
            (symbol, index_name, company_name, industry, isin, snapshot_date)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, index_name, snapshot_date) DO UPDATE SET
            company_name=excluded.company_name,
            industry=excluded.industry,
            isin=excluded.isin
        """,
        rows,
    )
    conn.commit()


def main():
    snapshot_date = date.today().isoformat()
    try:
        csv_text = fetch_csv_text()
    except Exception as e:
        print(f"FAILED to fetch constituent CSV: {e}", file=sys.stderr)
        print("If this 403s, niftyindices.com may need the same cookie "
              "handling NSE's main API needs -- try requests.Session() "
              "with a prior GET to https://www.niftyindices.com first.",
              file=sys.stderr)
        sys.exit(1)

    rows = parse_csv(csv_text, snapshot_date)
    if not rows:
        print("Parsed 0 rows -- the CSV headers probably changed. "
              "Paste me the first couple of lines of the raw CSV and "
              "I'll fix HEADER_ALIASES.", file=sys.stderr)
        sys.exit(1)

    conn = get_conn()
    upsert(conn, rows)
    print(f"Done. Recorded {len(rows)} Nifty 500 constituents "
          f"as of snapshot_date={snapshot_date}.")


if __name__ == "__main__":
    main()
