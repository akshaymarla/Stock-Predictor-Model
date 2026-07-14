"""
Thin SQLite helper. Keeps fetch scripts free of boilerplate.

Also patches a default HTTP timeout onto every requests.Session call
process-wide -- see the note below. Every fetch script already does
`from db import get_conn`, so this is the one place that reaches all of
them (including jugaad_data and screenerScraper.py's own sessions)
without editing each script individually.
"""
import sqlite3
from pathlib import Path

import requests

# Confirmed live 2026-07-14: backfill_prices.py hung indefinitely (90+
# minutes, no progress) mid-overnight-run. Root cause traced to
# jugaad_data.nse.history.NSEHistory._get(), which calls
# self.s.get(url, params=params, verify=...) with NO timeout at all --
# right after a couple of transient DNS blips, some connection apparently
# got established but never received a response, and plain requests waits
# forever with no timeout set. This isn't fixable by editing jugaad_data
# (it's a pip-installed dependency, not vendored), so we patch a default
# timeout onto requests.Session globally instead. setdefault() means any
# call that already passes its own timeout= (most of our own fetch
# scripts do, 10-15s) is unaffected -- this only fills the gap for calls
# (like jugaad_data's) that never set one.
_ORIGINAL_SESSION_REQUEST = requests.Session.request


def _request_with_default_timeout(self, method, url, **kwargs):
    kwargs.setdefault("timeout", 20)
    return _ORIGINAL_SESSION_REQUEST(self, method, url, **kwargs)


requests.Session.request = _request_with_default_timeout

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nifty_pipeline.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def get_universe(conn, index_name="NIFTY500") -> list:
    """Latest snapshot's symbol list from index_membership."""
    row = conn.execute(
        "SELECT MAX(snapshot_date) FROM index_membership WHERE index_name = ?",
        (index_name,),
    ).fetchone()
    latest_snapshot = row[0]
    if not latest_snapshot:
        return []
    symbols = conn.execute(
        "SELECT symbol FROM index_membership WHERE index_name = ? AND snapshot_date = ? "
        "ORDER BY symbol",
        (index_name, latest_snapshot),
    ).fetchall()
    return [s[0] for s in symbols]
