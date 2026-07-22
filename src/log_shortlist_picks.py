"""
Persists every stock in each week's shortlist into `tracked_picks` --
tracking_dashboard_spec.md Section 3. Wired directly into
weekly_shortlist.py's own run (not a separate manual step), so every
future shortlist run logs automatically and the live track record starts
accumulating going forward.

FROZEN AT INSERT TIME: calibrated_prob_at_pick and top_factors_json are
never updated after this runs, even if the production model gets
retrained before this pick resolves -- the whole point of tracking is
"what did the model say, back then." Uses ON CONFLICT DO NOTHING (not DO
UPDATE) for exactly this reason: a re-run against the same
(symbol, horizon, pick_date) must never silently overwrite an existing
frozen record.

Entry price reuses backtest.py's price_at_or_before() directly (latest
close <= pick_date) rather than reimplementing the same "what does
buying on this date actually mean" logic a second way.

target_close_date is a weekend-adjusted CALENDAR-day estimate of
pick_date + horizon TRADING days, not the exact trading-day date --
macro_regime_indicators (the project's trading calendar) only contains
days that have already happened, so the real Nth-trading-day date can't
be known yet at pick time. This estimate is only ever used as a "check
back around here" trigger; resolve_tracked_picks.py recomputes the exact
trading-day date from the real calendar once it exists and overwrites
this field before resolving. Because the estimate ignores the handful of
NSE holidays each year (only weekends), it's always a safe upper bound
on the true trading-day date -- never an underestimate that would cause
a premature/wrong resolution attempt.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import price_at_or_before, load_price_lookup  # noqa: E402

HORIZON_TRADING_DAYS = {"14d": 14, "30d": 30}


def estimate_target_close_date(pick_date: str, trading_days: int) -> str:
    d = datetime.strptime(pick_date, "%Y-%m-%d")
    remaining = trading_days
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            remaining -= 1
    return d.strftime("%Y-%m-%d")


def log_picks(conn, horizon_label: str, scoring_date: str, shortlist: list) -> int:
    """Inserts one tracked_picks row per shortlisted stock. Returns the
    number of rows actually inserted (existing frozen rows for the same
    key are left untouched, not counted)."""
    price_lookup = load_price_lookup(conn)
    fetched_at = datetime.now().isoformat()
    trading_days = HORIZON_TRADING_DAYS[horizon_label]
    target_close_date = estimate_target_close_date(scoring_date, trading_days)

    rows = []
    skipped = []
    for s in shortlist:
        entry_price, _, _ = price_at_or_before(s["symbol"], scoring_date, price_lookup)
        if entry_price is None:
            skipped.append(s["symbol"])
            continue
        rows.append((
            s["symbol"], horizon_label, scoring_date, entry_price,
            s["raw_prob"], s["calibrated_prob"], json.dumps(s["top_5_raw"], default=str),
            target_close_date, "open", fetched_at,
        ))

    if skipped:
        print(f"  log_shortlist_picks: skipped {len(skipped)} symbol(s) with no price data "
              f"at/before {scoring_date}: {skipped}")

    before = conn.execute("SELECT COUNT(*) FROM tracked_picks").fetchone()[0]
    conn.executemany(
        """
        INSERT INTO tracked_picks
            (symbol, horizon, pick_date, entry_price, raw_prob_at_pick, calibrated_prob_at_pick,
             top_factors_json, target_close_date, status, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, horizon, pick_date) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM tracked_picks").fetchone()[0]
    inserted = after - before
    print(f"  Logged {inserted}/{len(rows)} tracked_picks row(s) for {horizon_label} @ {scoring_date} "
          f"(target_close_date estimate: {target_close_date}).")
    return inserted
