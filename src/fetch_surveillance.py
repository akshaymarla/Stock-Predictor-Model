"""
Fetches the ASM/GSM surveillance lists from NSE and loads them into
`surveillance_flags`.

STATUS: CONFIRMED 2026-07-13 against a live run. ASM's real shape is
nested by ASM category, NOT a flat {"data": [...]} like GSM:
    {"longterm": {"data": [{
        "symbol": "21STCENMGM", "companyName": "...", "isin": "...",
        "series": null, "survCode": "LTASM - I (13)",
        "survDesc": "Long Term Additional Surveillance Measure (LTASM) - Stage I",
        "asmSurvIndicator": "Stage I", "asmTime": "13-Jul-2026", "srno": 1
    }, ...]}}
"shortterm" (short-term ASM, as opposed to "longterm"/LTASM above) is
assumed to be a sibling key with the same {"data": [...]} shape -- that's
an educated guess by naming symmetry, not yet seen in a live response
(the live run that confirmed "longterm" happened to have 0 short-term
flags). If it turns out to be named differently, short-term ASM flags will
silently be skipped -- symbol/date extraction itself won't break.

GSM's real response was `[]` -- a bare JSON list, empty because NSE
currently has 0 GSM-flagged symbols. That's a legitimate result, not a
bug: _extract_gsm_items() already handles a raw list correctly. We don't
yet have a confirmed non-empty GSM sample, so the field names in
parse_gsm() (gsmStage, gsmTime, etc.) remain an unconfirmed guess until
GSM has flags again.

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


def _extract_asm_items(payload) -> list:
    """ASM is nested by category: {"longterm": {"data": [...]}, "shortterm": {"data": [...]}}.
    "shortterm" is an unconfirmed guess by naming symmetry -- see STATUS note."""
    if not isinstance(payload, dict):
        return []
    items = []
    for key in ("shortterm", "longterm"):
        section = payload.get(key)
        if isinstance(section, dict):
            items.extend(section.get("data", []))
    return items


def _extract_gsm_items(payload) -> list:
    """Confirmed live: a bare JSON list (currently empty). Handle a {"data": [...]}
    wrapper too, defensively, in case that changes."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("data", [])
    return []


def parse_asm(payload, fetched_at: str) -> list[tuple]:
    rows = []
    for item in _extract_asm_items(payload):
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
    UNCONFIRMED item shape -- the wrapper shape (bare list) IS confirmed
    live (2026-07-13, see STATUS note at top of file), but the live
    response was an empty list, so the field names below (gsmStage,
    gsmTime, etc.) are still the original best guess, not yet checked
    against a real GSM-flagged entry:
    [{
        "symbol": "BALKRISHNA", "companyName": "...", "isin": "...",
        "gsmStage": "0", "gsmTime": "10-Jul-2026 08:08:02",
        "survCode": "GSM - 0 (99)",
        "survDesc": "Shortlisted under Graded Surveillance Measure",
        "srno": 7
    }, ...]
    If this ever parses to 0 rows while the raw payload (printed to stderr
    by main()) shows a non-empty list, these field names are wrong --
    send me a real entry and I'll fix it.
    """
    rows = []
    for item in _extract_gsm_items(payload):
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
            if not rows:
                print(f"  -> 0 rows but the request succeeded (HTTP {resp.status_code}) -- "
                      f"the response shape didn't match what parse_{name.lower()}() expects. "
                      f"Raw payload (first 500 chars): {resp.text[:500]!r}", file=sys.stderr)
            all_rows.extend(rows)
        except Exception as e:
            print(f"{name} fetch FAILED: {e}", file=sys.stderr)
            print(f"  -> see the HONESTY NOTE at the top of this file", file=sys.stderr)

    upsert(conn, all_rows)
    print(f"Done. Upserted {len(all_rows)} surveillance flag rows.")


if __name__ == "__main__":
    main()
