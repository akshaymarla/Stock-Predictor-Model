"""
Thin SQLite helper. Keeps fetch scripts free of boilerplate.
"""
import sqlite3
from pathlib import Path

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
