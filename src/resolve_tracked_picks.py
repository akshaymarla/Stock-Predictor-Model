"""
Resolves tracked_picks whose hold period has actually elapsed on the
real trading calendar -- tracking_dashboard_spec.md Section 4. Meant to
run regularly (folded into run_nightly.sh); safe to re-run any time,
since it only ever touches rows still `status = 'open'`.

Reuses compute_target_labels.py's forward-window logic (trading-calendar
indexing + the exact same _pct_return() percentage-return math) for the
actual resolution -- never a second implementation of the same
calculation. Same mid-hold-dropout stance as compute_target_labels.py/
backtest.py: "return from date A to date B" is only well-defined for the
exact two dates asked for, so a symbol missing a close on the exact
target trading day is marked `delisted_during_hold`, never given a
fabricated substitute return.

target_close_date stored on tracked_picks at pick time
(log_shortlist_picks.py) is only a calendar-day ESTIMATE -- the real
trading calendar (macro_regime_indicators) doesn't extend into the
future, so the exact Nth-trading-day date can't be known until it
actually happens. This script recomputes the true trading-day target
date from the calendar (same idx+N approach as compute_target_labels.py)
and overwrites target_close_date with the exact value whenever a pick
resolves. If the real calendar hasn't yet accumulated a full N trading
days past pick_date (rare, e.g. an unusually holiday-heavy stretch), the
pick is left `open` and re-checked on the next run -- never resolved on
a truncated window.

Usage:
    python src/resolve_tracked_picks.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_conn  # noqa: E402
from compute_target_labels import load_trading_calendar, load_nifty_closes, _pct_return  # noqa: E402

HORIZON_TRADING_DAYS = {"14d": 14, "30d": 30}


def load_price_series(conn, symbol: str) -> dict:
    rows = conn.execute(
        "SELECT date, close FROM daily_prices WHERE symbol = ? AND close IS NOT NULL ORDER BY date",
        (symbol,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def resolve_pick(conn, symbol: str, horizon: str, pick_date: str,
                  calendar: list, calendar_idx: dict, nifty_closes: dict):
    """Returns a result dict, or None if the real trading calendar hasn't
    reached the Nth trading day past pick_date yet (still open)."""
    idx = calendar_idx.get(pick_date)
    if idx is None:
        # pick_date wasn't a recognized trading day in the calendar --
        # shouldn't happen (weekly_shortlist.py only scores on confirmed
        # trading days), flag rather than guess by leaving it open.
        print(f"  WARNING: {symbol}/{horizon}/{pick_date} -- pick_date not found in trading calendar, skipping.")
        return None

    n = HORIZON_TRADING_DAYS[horizon]
    target_idx = idx + n
    if target_idx >= len(calendar):
        return None  # hold period hasn't actually elapsed on the real calendar yet

    target_date = calendar[target_idx]
    price_series = load_price_series(conn, symbol)
    stock_return = _pct_return(price_series, pick_date, target_date)
    if stock_return is None:
        return {"status": "delisted_during_hold", "target_close_date": target_date}

    nifty_return = _pct_return(nifty_closes, pick_date, target_date)
    alpha = round(stock_return - nifty_return, 4) if nifty_return is not None else None
    if alpha is None:
        return None  # missing Nifty close for the target date -- shouldn't happen, check back next run

    return {
        "status": "resolved",
        "target_close_date": target_date,
        "exit_price": price_series[target_date],
        "actual_stock_return": stock_return,
        "actual_nifty_return": nifty_return,
        "actual_alpha": alpha,
        "outperformed_flag": 1 if alpha > 0 else 0,
    }


def main():
    conn = get_conn()
    calendar = load_trading_calendar(conn)
    if not calendar:
        print("macro_regime_indicators is empty -- run fetch_macro_sector.py first.", file=sys.stderr)
        sys.exit(1)
    calendar_idx = {d: i for i, d in enumerate(calendar)}
    nifty_closes = load_nifty_closes(conn)
    today = datetime.now().strftime("%Y-%m-%d")

    open_picks = conn.execute(
        "SELECT symbol, horizon, pick_date FROM tracked_picks "
        "WHERE status = 'open' AND target_close_date <= ?", (today,)
    ).fetchall()
    print(f"{len(open_picks)} open pick(s) past their estimated target date -- "
          f"checking against the real trading calendar...")

    n_resolved = n_delisted = n_still_open = 0
    for symbol, horizon, pick_date in open_picks:
        result = resolve_pick(conn, symbol, horizon, pick_date, calendar, calendar_idx, nifty_closes)
        if result is None:
            n_still_open += 1
            continue
        resolved_at = datetime.now().isoformat()
        if result["status"] == "delisted_during_hold":
            conn.execute(
                "UPDATE tracked_picks SET status = 'delisted_during_hold', target_close_date = ?, "
                "resolved_at = ? WHERE symbol = ? AND horizon = ? AND pick_date = ?",
                (result["target_close_date"], resolved_at, symbol, horizon, pick_date),
            )
            n_delisted += 1
        else:
            conn.execute(
                "UPDATE tracked_picks SET status = 'resolved', target_close_date = ?, exit_price = ?, "
                "actual_stock_return = ?, actual_nifty_return = ?, actual_alpha = ?, outperformed_flag = ?, "
                "resolved_at = ? WHERE symbol = ? AND horizon = ? AND pick_date = ?",
                (result["target_close_date"], result["exit_price"], result["actual_stock_return"],
                 result["actual_nifty_return"], result["actual_alpha"], result["outperformed_flag"],
                 resolved_at, symbol, horizon, pick_date),
            )
            n_resolved += 1
    conn.commit()
    print(f"Done. resolved={n_resolved} delisted_during_hold={n_delisted} "
          f"still_open_calendar_not_there_yet={n_still_open}")


if __name__ == "__main__":
    main()
