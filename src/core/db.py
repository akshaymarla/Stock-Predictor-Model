"""
DB helper — now backed by Supabase (Postgres) instead of local SQLite.

Every fetch script still does `from core.db import get_conn` and calls
conn.execute(...) / conn.executemany(...) with '?' placeholders exactly
like it did against sqlite3. That interface is preserved here via
_PGConnection, so NONE of the fetch/derived scripts needed to change --
only this file (and schema.sql) did.

Also patches a default HTTP timeout onto every requests.Session call
process-wide -- see the note below. Unchanged from the SQLite version.
"""
import os
import sqlite3  # noqa: F401  (kept only as a reference for the interface being mirrored)
from pathlib import Path

import psycopg2
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # loads .env if present; no-op in production (Render sets real env vars)
except ImportError:
    pass

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

# core/db.py -> parent=core, parent.parent=src, parent.parent.parent=repo root
SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema.sql"

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")


class _PGConnection:
    """
    Thin wrapper so scripts written against sqlite3's Connection.execute()/
    executemany() (which return a cursor you can call .fetchone()/.fetchall()
    on directly) keep working unmodified against psycopg2, which normally
    requires an explicit cursor object for every call.

    Also translates '?' placeholders (SQLite style, used throughout this
    project) to psycopg2's '%s' style. Safe here because no SQL string in
    this codebase uses a literal '?' character (verified against every
    fetch/derived script) -- if you add a query with a literal '?' in a
    string value, use %% or pass it as a bound parameter instead.
    """

    def __init__(self, raw_conn):
        self._conn = raw_conn

    @staticmethod
    def _translate(query: str) -> str:
        return query.replace("?", "%s")

    def execute(self, query, params=()):
        cur = self._conn.cursor()
        cur.execute(self._translate(query), params)
        return cur

    def executemany(self, query, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(self._translate(query), seq_of_params)
        return cur

    def executescript(self, script: str):
        # psycopg2 cursors execute multi-statement scripts fine as-is;
        # schema.sql has no '?' in it so no translation needed.
        cur = self._conn.cursor()
        cur.execute(script)
        cur.close()
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self):
        return self._conn.cursor()


def get_conn() -> _PGConnection:
    if not SUPABASE_DB_URL:
        raise RuntimeError(
            "SUPABASE_DB_URL is not set. Set it to your Supabase Postgres "
            "connection string (Session pooler URI, port 5432 or 6543) "
            "as an environment variable -- see .env.example."
        )
    raw = psycopg2.connect(SUPABASE_DB_URL)
    conn = _PGConnection(raw)
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
