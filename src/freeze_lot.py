"""
Freezes the latest weekly_shortlist.py run into a permanent "lot" file
under models/lots/ -- tracked_picks' own numbers are already frozen at
pick time (never rewritten), but nothing previously captured "this was
the Nth batch we ever made, here is its complete display bundle" as one
immutable artifact the frontend can show alongside older ones.

WORKFLOW (2026-07-22, per direct instruction): weekly_shortlist.py is no
longer run on any automatic schedule -- it's run only when explicitly
triggered (a future condition to be given later). This script is meant
to run immediately after each such run:

    python src/weekly_shortlist.py       # trains + logs the new pick_date
    python src/freeze_lot.py             # freezes it as the next lot
    python src/export_screener_data.py   # rebuilds the site's data.js
                                          # from the most recent MAX_SITE_LOTS

FREEZE SEMANTICS: idempotent by pick_date -- if a lot for the latest
tracked_picks pick_date already exists on disk, this does nothing. A lot
is written once and never touched again after that, matching the
explicit "freeze the list until told otherwise" instruction. Re-running
weekly_shortlist.py the same day is itself a no-op too (log_shortlist_picks.py's
ON CONFLICT DO NOTHING), so there's no path to accidentally double-freezing
or silently overwriting a lot that's already live on the site.

models/lots/ is NEVER pruned by this script or export_screener_data.py --
every lot ever made stays on disk permanently ("we still store them in
our folders"). Only export_screener_data.py's SITE-FACING output
(frontend/data.js) is limited to the most recent few.

Usage:
    python src/freeze_lot.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_conn  # noqa: E402
from export_screener_data import build_lot_candidates, build_horizon_tracking  # noqa: E402

LOTS_DIR = Path(__file__).resolve().parent.parent / "models" / "lots"
HORIZON_DAYS = (("14d", 14), ("30d", 30))


def latest_pick_date(conn):
    row = conn.execute("SELECT MAX(pick_date) FROM tracked_picks").fetchone()
    return row[0] if row else None


def already_frozen(pick_date: str) -> Path:
    matches = list(LOTS_DIR.glob(f"lot_*_{pick_date}.json"))
    return matches[0] if matches else None


def next_lot_number() -> int:
    return len(list(LOTS_DIR.glob("lot_*.json"))) + 1


def main():
    conn = get_conn()
    pick_date = latest_pick_date(conn)
    if pick_date is None:
        print("tracked_picks is empty -- run weekly_shortlist.py first.", file=sys.stderr)
        sys.exit(1)

    LOTS_DIR.mkdir(parents=True, exist_ok=True)
    existing = already_frozen(pick_date)
    if existing:
        print(f"Lot for pick_date {pick_date} is already frozen ({existing.name}) -- nothing to do.")
        return

    calendar = [r[0] for r in conn.execute("SELECT date FROM macro_regime_indicators ORDER BY date").fetchall()]
    scoring_date = conn.execute(
        "SELECT MAX(date) FROM daily_prices WHERE date IN (SELECT date FROM macro_regime_indicators)"
    ).fetchone()[0]

    lot_number = next_lot_number()
    lot = {
        "lot_number": lot_number,
        "pick_date": pick_date,
        "frozen_at": datetime.now().isoformat(),
        "candidates": {}, "tracking": {},
    }
    for horizon_label, n_days in HORIZON_DAYS:
        candidates = build_lot_candidates(conn, horizon_label, pick_date)
        symbols = [c["ticker"] for c in candidates]
        lot["candidates"][horizon_label] = candidates
        lot["tracking"][horizon_label] = build_horizon_tracking(
            conn, pick_date, symbols, n_days, calendar, scoring_date)
        print(f"  {horizon_label}: {len(candidates)} candidates frozen")

    path = LOTS_DIR / f"lot_{lot_number}_{pick_date}.json"
    path.write_text(json.dumps(lot, indent=2, default=str))
    print(f"Froze Lot {lot_number} (pick_date={pick_date}) to {path}")
    print("Run src/export_screener_data.py next to publish it to the site.")


if __name__ == "__main__":
    main()
