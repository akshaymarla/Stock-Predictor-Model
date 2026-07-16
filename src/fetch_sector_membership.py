"""
Fetches CURRENT sectoral index constituent lists from NSE Indices and
records them as a dated snapshot in `sector_membership`. Same
current-snapshot-only caveat as fetch_index_membership.py: this gives
today's constituents, not historical membership -- it does NOT
retroactively solve "who was in Nifty Bank in 2022".

THE ACTUAL FIX for the sector-mapping bug documented in
macro_sector_shock_features.md: real official per-sector constituent CSVs,
not fuzzy-matching index_membership's generic `industry` field (that
field's real values are granular BSE/NSE classifications like
"FERTILISERS & PESTICIDES", confirmed from a real sample earlier in this
project -- nothing like a broad sector name a naive CASE-WHEN could match).

URL NAMING, confirmed live 2026-07-16 (do NOT assume analogy -- two of the
15 didn't follow the obvious pattern, see SECTOR_CSV_FILES below):
- Most sectors: ind_nifty{sector}list.csv (e.g. ind_niftybanklist.csv)
- 'Financial Services' is ind_niftyfinancelist.csv, NOT
  ind_niftyfinservicelist.csv or ind_niftyfinancialserviceslist.csv (both
  404-equivalent -- niftyindices.com returns HTTP 200 with an HTML error
  page for a bad path, not a clean 404, so a wrong guess doesn't fail loudly)
- 'Private Bank' is ind_nifty_privatebanklist.csv (note the underscore
  after "nifty", unlike every other sector here) -- ind_niftyprivatebanklist.csv
  and ind_niftypvtbanklist.csv both silently 200 with HTML instead of CSV.

sector_name values match the real NSE index names exactly as they appear
in fetch_macro_sector.py's daily snapshot (e.g. 'Nifty Bank', NOT
'NIFTY BANK') -- required so sector_membership joins cleanly against
sector_daily_benchmarks once that table exists.

Usage:
    python src/fetch_sector_membership.py
"""
import csv
import io
import sys
from datetime import date

import requests

from db import get_conn

BASE_URL = "https://www.niftyindices.com/IndexConstituent/"

# sector_name -> CSV filename. Confirmed live 2026-07-16 against the real
# site -- see the module docstring for the two exceptions to the obvious
# naming pattern.
SECTOR_CSV_FILES = {
    "Nifty Bank": "ind_niftybanklist.csv",
    "Nifty IT": "ind_niftyitlist.csv",
    "Nifty FMCG": "ind_niftyfmcglist.csv",
    "Nifty Pharma": "ind_niftypharmalist.csv",
    "Nifty Auto": "ind_niftyautolist.csv",
    "Nifty Metal": "ind_niftymetallist.csv",
    "Nifty Realty": "ind_niftyrealtylist.csv",
    "Nifty Energy": "ind_niftyenergylist.csv",
    "Nifty Media": "ind_niftymedialist.csv",
    "Nifty Financial Services": "ind_niftyfinancelist.csv",
    "Nifty PSU Bank": "ind_niftypsubanklist.csv",
    "Nifty Private Bank": "ind_nifty_privatebanklist.csv",
    "Nifty Consumer Durables": "ind_niftyconsumerdurableslist.csv",
    "Nifty Oil & Gas": "ind_niftyoilgaslist.csv",
    "Nifty Infrastructure": "ind_niftyinfralist.csv",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0 Safari/537.36"
    ),
}

# Same header-alias resilience pattern as fetch_index_membership.py.
HEADER_ALIASES = {
    "company name": "company_name",
    "symbol": "symbol",
    "isin code": "isin",
    "isin": "isin",
}


def fetch_sector_csv(sector_name: str, filename: str) -> str:
    resp = requests.get(BASE_URL + filename, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    text = resp.text
    # niftyindices.com returns HTTP 200 with an HTML error page for a bad
    # path instead of a clean 404 (confirmed live) -- never trust status
    # code alone, check the response actually looks like our CSV.
    if not text.lstrip().startswith("Company Name,"):
        raise ValueError(f"response for {sector_name} doesn't look like a real "
                          f"constituent CSV (got: {text[:80]!r})")
    return text


def parse_csv(csv_text: str, sector_name: str, snapshot_date: str) -> list[tuple]:
    reader = csv.DictReader(io.StringIO(csv_text))
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
            symbol, sector_name,
            normalized.get("company_name", "").strip(),
            normalized.get("isin", "").strip(),
            snapshot_date, "NSE_INDICES",
        ))
    return rows


def upsert(conn, rows: list[tuple], fetched_at: str):
    conn.executemany(
        """
        INSERT INTO sector_membership
            (symbol, sector_name, company_name, isin, snapshot_date, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, sector_name, snapshot_date) DO UPDATE SET
            company_name=excluded.company_name,
            isin=excluded.isin,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        [r + (fetched_at,) for r in rows],
    )
    conn.commit()


def main():
    from datetime import datetime
    snapshot_date = date.today().isoformat()
    fetched_at = datetime.now().isoformat()
    conn = get_conn()

    total = 0
    for sector_name, filename in SECTOR_CSV_FILES.items():
        try:
            csv_text = fetch_sector_csv(sector_name, filename)
        except Exception as e:
            print(f"    FAILED for {sector_name} ({filename}): {e}", file=sys.stderr)
            continue

        rows = parse_csv(csv_text, sector_name, snapshot_date)
        if not rows:
            print(f"    WARNING {sector_name}: parsed 0 rows, headers may have "
                  f"changed", file=sys.stderr)
            continue

        upsert(conn, rows, fetched_at)
        total += len(rows)
        print(f"    {sector_name}: {len(rows)} constituents")

    if total == 0:
        print("Upserted 0 rows total.", file=sys.stderr)
        sys.exit(1)

    print(f"Done. Recorded {total} sector_membership rows across "
          f"{len(SECTOR_CSV_FILES)} sectors as of snapshot_date={snapshot_date}.")


if __name__ == "__main__":
    main()
