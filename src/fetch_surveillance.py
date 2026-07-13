"""
Fetches the ASM/GSM surveillance lists from NSE and loads them into
`surveillance_flags`.

HONESTY NOTE (read this before running):
Unlike fetch_daily_prices.py, there's no maintained library wrapping this
endpoint, so this hits NSE's API directly. NSE's site requires you to first
load nseindia.com in a browser-like session to get valid cookies before its
/api/* endpoints will respond -- hitting the API cold usually gets a 401/403.
The endpoint paths below (reportASM / reportGSM1) are the commonly
documented ones as of my knowledge, but NSE changes these without notice
and I could NOT verify them live (nseindia.com isn't reachable from the
sandbox this was built in). Treat this file as a solid *starting point*,
not a guaranteed-working script:
  1. Run it.
  2. If it 401s/403s or the JSON shape doesn't match, open nseindia.com's
     ASM/GSM page in a browser, open DevTools -> Network tab, find the
     actual XHR request, and paste me the real URL + response shape --
     I'll fix the parsing in a minute.

Usage:
    python src/fetch_surveillance.py
"""
import sys
from datetime import date, datetime

import requests

from db import get_conn

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

ASM_URL = "https://www.nseindia.com/api/reportASM"
GSM_URL = "https://www.nseindia.com/api/reportGSM"


def make_session() -> requests.Session:
    """NSE requires cookies from a real page load before /api/* will respond."""
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    s.get("https://www.nseindia.com", timeout=10)  # sets cookies
    return s


def _extract_items(payload) -> list:
    """
    Handle both possible shapes defensively: {"data": [...]} or a raw list.
    Confirmed for ASM (2026-07-10, real response): wrapped under "data".
    Assuming GSM matches the same wrapper convention until confirmed --
    flag me if reportGSM1 turns out to be a raw list instead.
    """
    if isinstance(payload, dict):
        return payload.get("data", [])
    if isinstance(payload, list):
        return payload
    return []


def parse_asm(payload, fetched_at: str) -> list[tuple]:
    """
    Confirmed real response shape (checked against a live NSE response,
    2026-07-10):
    {"data": [{
        "symbol": "ASTRAMICRO", "companyName": "...", "isin": "...",
        "series": null, "survCode": "LTASM - I (13)",
        "survDesc": "Long Term Additional Surveillance Measure (LTASM) - Stage I",
        "asmSurvIndicator": "Stage I", "asmTime": "10-Jul-2026", "srno": 19
    }, ...]}
    """
    rows = []
    for item in _extract_items(payload):
        symbol = item.get("symbol")
        stage_raw = item.get("asmSurvIndicator", "")
        stage = stage_raw.replace(" ", "_").upper() or "UNKNOWN"
        start_date_raw = item.get("asmTime")
        if not symbol or not start_date_raw:
            continue
        start_date = _normalize_date(start_date_raw)
        rows.append((symbol, f"ASM_{stage}", start_date, None, "NSE", fetched_at))
    return rows


def parse_gsm(payload, fetched_at: str) -> list[tuple]:
    """
    Confirmed real response shape (checked against a live NSE response,
    2026-07-10). Note this differs from ASM's shape in two ways:
      - "gsmStage" is a bare number string ("0", "1", "2"...), not text
        like ASM's "Stage I"
      - "gsmTime" includes a timestamp ("10-Jul-2026 08:08:02"), not just
        a date -- _normalize_date() below handles both formats.
    {"data": [{
        "symbol": "BALKRISHNA", "companyName": "...", "isin": "...",
        "gsmStage": "0", "gsmTime": "10-Jul-2026 08:08:02",
        "survCode": "GSM - 0 (99)",
        "survDesc": "Shortlisted under Graded Surveillance Measure",
        "srno": 7
    }, ...]}
    """
    rows = []
    for item in _extract_items(payload):
        symbol = item.get("symbol")
        stage = item.get("gsmStage", "UNKNOWN")
        start_date_raw = item.get("gsmTime")
        if not symbol or not start_date_raw:
            continue
        start_date = _normalize_date(start_date_raw)
        rows.append((symbol, f"GSM_STAGE_{stage}", start_date, None, "NSE", fetched_at))
    return rows


def _normalize_date(raw: str) -> str:
    """
    NSE sends dates in a couple of different formats depending on endpoint:
    ASM uses '05-Jul-2026' (date only), GSM uses '10-Jul-2026 08:08:02'
    (date + time). Try all known formats, normalize to YYYY-MM-DD.
    """
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw  # fall back to raw string rather than crashing the whole run


def upsert(conn, rows: list[tuple]):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO surveillance_flags
            (symbol, flag_type, start_date, end_date, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, flag_type, start_date) DO UPDATE SET
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    fetched_at = datetime.now().isoformat()
    conn = get_conn()
    session = make_session()

    all_rows = []
    for name, url, parser in [("ASM", ASM_URL, parse_asm), ("GSM", GSM_URL, parse_gsm)]:
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            rows = parser(payload, fetched_at)
            print(f"{name}: parsed {len(rows)} flagged symbols")
            all_rows.extend(rows)
        except Exception as e:
            print(f"{name} fetch FAILED: {e}", file=sys.stderr)
            print(f"  -> see the HONESTY NOTE at the top of this file", file=sys.stderr)

    upsert(conn, all_rows)
    print(f"Done. Upserted {len(all_rows)} surveillance flag rows.")


if __name__ == "__main__":
    main()
