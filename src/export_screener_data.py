"""
Exports the two data files the frontend needs, per
frontend-screener-spec.md Section 6 (Overview) and
frontend-spec-v2-sidebar-nav.md Sections 4-5 (Statistical View + Compare),
extended by the "lots" batching model (see lots_spec discussion,
2026-07-22): nifty, model_meta, and a `lots` array. Loaded on every page
view.

- frontend/data.js  -- window.SCREENER_DATA: nifty, model_meta, lots.
- frontend/universe.js -- window.UNIVERSE_SNAPSHOT: full Nifty 500
  lookup data for the Compare section. Written separately and loaded
  lazily by app.js only when that section is first opened.

Both are plain `<script src>` includes, deliberately NOT fetch()/XHR'd
.json files -- file:// pages can't fetch() JSON in most browsers (CORS),
so a plain script include is what actually lets the screener keep this
project's "single file, opens with no server" convention.

LOTS: this script does NOT train or score anything anymore -- that only
happens in weekly_shortlist.py (run explicitly, on the user's own
cadence/condition, never automatically) followed by src/freeze_lot.py,
which freezes that run's tracked_picks rows into a permanent
models/lots/lot_<N>_<pick_date>.json. This script just reads back
whichever lots already exist on disk and embeds the most recent
MAX_SITE_LOTS of them -- every lot ever frozen stays in models/lots/
forever ("we still store them in our folders"), this script only limits
what the SITE shows. Safe to re-run any time (nightly, on demand) since
it never creates or mutates a lot, only refreshes the live nifty/
model_meta/universe_snapshot pieces around them.

model_meta.status/notes are derived from the REAL tracked_picks hit rate
(reusing generate_tracking_dashboard.py's calculation) -- never hardcoded.

Usage:
    python src/export_screener_data.py
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_conn  # noqa: E402
from generate_tracking_dashboard import load_picks, hit_rate_section  # noqa: E402
from backtest import load_price_lookup, price_at_or_before  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "data.js"
UNIVERSE_OUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "universe.js"
LOTS_DIR = Path(__file__).resolve().parent.parent / "models" / "lots"
SMALL_SAMPLE_CUTOFF = 20  # same "too small to be conclusive" threshold as generate_tracking_dashboard.py
MAX_SITE_LOTS = 3  # the user's explicit choice: keep at least 3 lots visible on the site,
                    # drop the oldest once a 4th is frozen -- models/lots/ itself is never pruned


def load_company_meta(conn, symbols: list) -> dict:
    if not symbols:
        return {}
    rows = conn.execute(
        "SELECT symbol, company_name, industry FROM index_membership "
        "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM index_membership) "
        "AND symbol IN ({})".format(",".join("?" * len(symbols))), symbols,
    ).fetchall()
    return {r[0]: {"company_name": r[1], "sector": r[2]} for r in rows}


def load_price_meta(conn, symbols: list, scoring_date: str) -> dict:
    if not symbols:
        return {}
    rows = conn.execute(
        "SELECT symbol, close, prev_close FROM daily_prices WHERE date = ? AND symbol IN ({})".format(
            ",".join("?" * len(symbols))), [scoring_date] + symbols,
    ).fetchall()
    out = {}
    for symbol, close, prev_close in rows:
        day_change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else None
        out[symbol] = {"eod_price": close, "day_change_pct": day_change_pct}
    return out


def load_52w_range(conn, symbols: list, scoring_date: str) -> dict:
    if not symbols:
        return {}
    start = (datetime.strptime(scoring_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT symbol, MIN(close), MAX(close) FROM daily_prices WHERE date >= ? AND date <= ? "
        "AND close IS NOT NULL AND symbol IN ({}) GROUP BY symbol".format(
            ",".join("?" * len(symbols))), [start, scoring_date] + symbols,
    ).fetchall()
    return {r[0]: {"range_52w_low": r[1], "range_52w_high": r[2]} for r in rows}


def nifty_ticker_section(conn, scoring_date: str, calendar: list) -> dict:
    row = conn.execute("SELECT nifty50_close FROM macro_regime_indicators WHERE date = ?", (scoring_date,)).fetchone()
    close = row[0] if row else None

    prev_close = None
    if scoring_date in calendar:
        idx = calendar.index(scoring_date)
        if idx > 0:
            prev_row = conn.execute(
                "SELECT nifty50_close FROM macro_regime_indicators WHERE date = ?", (calendar[idx - 1],)
            ).fetchone()
            prev_close = prev_row[0] if prev_row else None

    day_change_pct = round((close - prev_close) / prev_close * 100, 2) if (close and prev_close) else None
    today_str = datetime.now().strftime("%Y-%m-%d")
    return {
        "close": close, "day_change_pct": day_change_pct,
        "as_of_date": scoring_date,
        # not literally today's close -- pipeline's confirmed scoring_date
        # lags real "now" (e.g. nightly hasn't run yet, market's mid-session,
        # or today's fetch is a stray/partial one weekly_shortlist.py already
        # distrusts) -- never claim a same-day live value that isn't real
        "is_stale": scoring_date != today_str,
    }


def model_meta_section(conn) -> dict:
    picks = load_picks(conn)
    resolved = [p for p in picks if p["status"] == "resolved"]
    hr = hit_rate_section(resolved)
    n, rate = hr["overall"]["n"], hr["overall"]["hit_rate"]
    run_date = datetime.now().strftime("%Y-%m-%d")

    if n == 0:
        status = "provisional"
        notes = ("No live picks have resolved yet (out-of-sample tracking just started) -- "
                 "treat these probabilities as model-confidence estimates, not a validated track record.")
    elif n < SMALL_SAMPLE_CUTOFF:
        status = "provisional"
        notes = (f"Only {n} live pick(s) resolved so far ({rate*100:.0f}% hit rate) -- "
                 f"too small a sample to be conclusive yet.")
    else:
        status = "validated"
        notes = (f"{n} live picks resolved, {rate*100:.0f}% overall hit rate -- "
                 f"see the tracking dashboard for the full breakdown.")
    return {"run_date": run_date, "status": status, "notes": notes}


def cell(close, prev_close) -> dict:
    """{'close','day_change_pct'} -- close=None (never 0 or omitted) is
    how the frontend knows a day hasn't happened yet or the row is
    otherwise unavailable (e.g. suspended) -- frontend-spec-v2 Section 4."""
    if close is None:
        return {"close": None, "day_change_pct": None}
    day_change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else None
    return {"close": close, "day_change_pct": day_change_pct}


def project_future_trading_days(last_date: str, n: int) -> list:
    """n weekend-adjusted CALENDAR-day estimates after last_date -- same
    convention as log_shortlist_picks.py's estimate_target_close_date(),
    used here only to give the tracking table's future (not-yet-happened)
    columns a stable placeholder date so the table shape doesn't change
    shape as real days fill in."""
    days = []
    d = datetime.strptime(last_date, "%Y-%m-%d")
    while len(days) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
    return days


def build_trading_day_columns(pick_date: str, n_days: int, calendar: list) -> list:
    """n_days column dates starting at pick_date (D+0) -- real trading-
    calendar dates for whatever has actually happened, weekend-adjusted
    estimates for the rest (never NSE-holiday-exact, same limitation as
    the estimate above)."""
    real_after = [d for d in calendar if d > pick_date]
    days = [pick_date] + real_after[: n_days - 1]
    if len(days) < n_days:
        days += project_future_trading_days(days[-1], n_days - len(days))
    return days[:n_days]


def load_daily_series(conn, symbols: list, start_date: str, end_date: str) -> dict:
    """{symbol: {date: (close, prev_close)}}"""
    if not symbols:
        return {}
    rows = conn.execute(
        "SELECT symbol, date, close, prev_close FROM daily_prices WHERE date >= ? AND date <= ? "
        "AND symbol IN ({})".format(",".join("?" * len(symbols))), [start_date, end_date] + symbols,
    ).fetchall()
    out = {}
    for symbol, date, close, prev_close in rows:
        out.setdefault(symbol, {})[date] = (close, prev_close)
    return out


def build_horizon_tracking(conn, pick_date: str, symbols: list, n_days: int, calendar: list, scoring_date: str) -> dict:
    """Day-by-day realized (never predicted) close + day-over-day % change
    for one lot's one horizon, for the exact `symbols` frozen into that
    lot at `pick_date`. Used both by freeze_lot.py (initial snapshot at
    freeze time) and by load_site_lots() below (recomputed fresh on
    every export, so real elapsed days keep filling in after a lot is
    made -- see that function's docstring for why the freeze-time-only
    snapshot was wrong)."""
    calendar_idx = {d: i for i, d in enumerate(calendar)}
    trading_days = build_trading_day_columns(pick_date, n_days, calendar)

    pick_idx = calendar_idx.get(pick_date)
    fetch_start = calendar[pick_idx - 1] if (pick_idx is not None and pick_idx > 0) else pick_date
    stock_series = load_daily_series(conn, symbols, fetch_start, scoring_date)
    nifty_rows = conn.execute(
        "SELECT date, nifty50_close FROM macro_regime_indicators WHERE date >= ? AND date <= ?",
        (fetch_start, scoring_date),
    ).fetchall()
    nifty_close_by_date = {d: c for d, c in nifty_rows}

    nifty_history = {}
    stocks_out = {s: {} for s in symbols}
    for d in trading_days:
        if d > scoring_date:  # hasn't actually happened yet, per pipeline's confirmed data
            nifty_history[d] = {"close": None, "day_change_pct": None}
            for s in symbols:
                stocks_out[s][d] = {"close": None, "day_change_pct": None}
            continue
        idx = calendar_idx.get(d)
        prev_d = calendar[idx - 1] if (idx is not None and idx > 0) else None
        nifty_history[d] = cell(nifty_close_by_date.get(d), nifty_close_by_date.get(prev_d) if prev_d else None)
        for s in symbols:
            close_prev = stock_series.get(s, {}).get(d)
            stocks_out[s][d] = cell(*close_prev) if close_prev else {"close": None, "day_change_pct": None}

    return {"pick_date": pick_date, "trading_days": trading_days,
            "nifty_history": nifty_history, "stocks": stocks_out}


def build_lot_candidates(conn, horizon_label: str, pick_date: str) -> list:
    """This lot's frozen candidates for one horizon -- symbols and
    probabilities come straight from tracked_picks' calibrated_prob_at_pick
    (never re-scored), enriched with display metadata (company/price/
    range) as of pick_date -- the correct point-in-time anchor for THIS
    lot, not whatever "today" happens to be when the site data is later
    refreshed."""
    rows = conn.execute(
        "SELECT symbol, calibrated_prob_at_pick FROM tracked_picks "
        "WHERE horizon = ? AND pick_date = ? ORDER BY calibrated_prob_at_pick DESC",
        (horizon_label, pick_date),
    ).fetchall()
    symbols = [r[0] for r in rows]
    prob_by_symbol = {r[0]: r[1] for r in rows}

    company_meta = load_company_meta(conn, symbols)
    price_meta = load_price_meta(conn, symbols, pick_date)
    range_meta = load_52w_range(conn, symbols, pick_date)

    candidates = []
    skipped = []
    for rank, symbol in enumerate(symbols, start=1):
        if symbol not in price_meta or symbol not in range_meta:
            skipped.append(symbol)
            continue
        meta = company_meta.get(symbol, {})
        candidates.append({
            "rank": rank, "ticker": symbol,
            "company_name": meta.get("company_name") or symbol,
            "sector": meta.get("sector") or "Unclassified",
            "eod_price": price_meta[symbol]["eod_price"],
            "day_change_pct": price_meta[symbol]["day_change_pct"],
            "range_52w_low": range_meta[symbol]["range_52w_low"],
            "range_52w_high": range_meta[symbol]["range_52w_high"],
            "prob": round(float(prob_by_symbol[symbol]), 4),
        })
    if skipped:
        print(f"    WARNING: {horizon_label}@{pick_date} skipped {len(skipped)} symbol(s) "
              f"missing price/range metadata at pick_date: {skipped}")
    return candidates


HORIZON_DAYS = {"14d": 14, "30d": 30}


def load_site_lots(conn, calendar: list, scoring_date: str, max_lots: int = MAX_SITE_LOTS) -> list:
    """Most recent `max_lots` frozen lots, read back from disk -- but
    with `tracking` (realized day-by-day closes) recomputed fresh every
    time, never left as whatever freeze_lot.py happened to see at
    freeze time. `candidates` (the actual picks/probabilities) stay
    exactly as frozen -- untouched here -- since those must never change
    after the fact. `tracking` is different in kind: it's just reporting
    real market closes that keep happening after a lot is made, so
    "frozen" was never the right word for it -- a lot made a week ago
    should show a week of real elapsed days, not the single day that had
    happened by the time it was frozen. (Found 2026-07-22: Lot 1's
    tracking table was stuck showing only its freeze-day snapshot even
    after a week of real trading days had elapsed, because freeze_lot.py
    had baked a one-time tracking snapshot into the lot file and nothing
    ever recomputed it.) The on-disk lot_*.json files are left as
    freeze_lot.py wrote them either way -- this only affects what gets
    embedded into data.js."""
    paths = sorted(LOTS_DIR.glob("lot_*.json")) if LOTS_DIR.exists() else []
    lots = [json.loads(p.read_text()) for p in paths]
    lots.sort(key=lambda lot: lot["lot_number"])
    lots = lots[-max_lots:]

    for lot in lots:
        for horizon_label, n_days in HORIZON_DAYS.items():
            symbols = [c["ticker"] for c in lot["candidates"].get(horizon_label, [])]
            if symbols:
                lot["tracking"][horizon_label] = build_horizon_tracking(
                    conn, lot["pick_date"], symbols, n_days, calendar, scoring_date)
    return lots


def period_cell(close, actual_date, last_close):
    if close is None:
        return None
    change_pct_vs_last = round((last_close - close) / close * 100, 2) if close else None
    return {"date": actual_date, "close": close, "change_pct_vs_last": change_pct_vs_last}


def build_universe_snapshot(conn, scoring_date: str, calendar: list) -> dict:
    """Full Nifty 500 (every current index_membership constituent, NOT
    filtered by the model's surveillance/liquidity eligibility rules --
    this is a general lookup tool, not a screening one) -- for the
    Compare section's search, frontend-spec-v2 Section 5. Reuses
    backtest.py's price_at_or_before()/load_price_lookup() directly for
    every lookback, same "what does a price lookup on an arbitrary date
    actually mean" convention as everywhere else in this project, rather
    than a new one just for this tool."""
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM index_membership WHERE snapshot_date = "
        "(SELECT MAX(snapshot_date) FROM index_membership)"
    ).fetchall()]
    company_meta = load_company_meta(conn, symbols)
    price_lookup = load_price_lookup(conn)
    calendar_idx = {d: i for i, d in enumerate(calendar)}
    scoring_idx = calendar_idx.get(scoring_date)

    def trading_day_back(n):
        if scoring_idx is None:
            return None
        return calendar[max(0, scoring_idx - n)]

    def calendar_back(months=0, years=0):
        target = pd.Timestamp(scoring_date) - pd.DateOffset(months=months, years=years)
        return target.strftime("%Y-%m-%d")

    stocks = {}
    skipped = 0
    for symbol in symbols:
        last_close, last_actual, _ = price_at_or_before(symbol, scoring_date, price_lookup)
        if last_close is None:
            skipped += 1
            continue
        prev_row = conn.execute(
            "SELECT prev_close FROM daily_prices WHERE symbol = ? AND date = ?", (symbol, last_actual)
        ).fetchone()
        last_prev_close = prev_row[0] if prev_row else None

        meta = company_meta.get(symbol, {})
        entry = {
            "company_name": meta.get("company_name") or symbol,
            "sector": meta.get("sector") or "Unclassified",
            "last": cell(last_close, last_prev_close),
        }
        entry["last"]["date"] = last_actual
        for key, n in (("d3", 3), ("d7", 7), ("d14", 14)):
            target_date = trading_day_back(n)
            close, actual_date, _ = (None, None, None) if target_date is None else price_at_or_before(symbol, target_date, price_lookup)
            entry[key] = period_cell(close, actual_date, last_close)
        for key, kwargs in (("m1", {"months": 1}), ("m6", {"months": 6}), ("y1", {"years": 1})):
            target_date = calendar_back(**kwargs)
            close, actual_date, _ = price_at_or_before(symbol, target_date, price_lookup)
            entry[key] = period_cell(close, actual_date, last_close)
        stocks[symbol] = entry
    if skipped:
        print(f"  universe_snapshot: skipped {skipped} symbol(s) with no price data at/before {scoring_date}")
    return {"as_of_date": scoring_date, "stocks": stocks}


def main():
    conn = get_conn()
    scoring_date = conn.execute(
        "SELECT MAX(date) FROM daily_prices WHERE date IN (SELECT date FROM macro_regime_indicators)"
    ).fetchone()[0]
    calendar = [r[0] for r in conn.execute("SELECT date FROM macro_regime_indicators ORDER BY date").fetchall()]
    print(f"Live display data as of: {scoring_date}")

    lots = load_site_lots(conn, calendar, scoring_date)
    total_lots = len(list(LOTS_DIR.glob("lot_*.json"))) if LOTS_DIR.exists() else 0
    print(f"Embedding {len(lots)} most recent lot(s) on the site "
          f"(lot_number {[l['lot_number'] for l in lots]}) -- {total_lots} total ever frozen in {LOTS_DIR}")
    if not lots:
        print("  No lots frozen yet -- run weekly_shortlist.py then src/freeze_lot.py first.")

    payload = {
        "nifty": nifty_ticker_section(conn, scoring_date, calendar),
        "model_meta": model_meta_section(conn),
        "lots": lots,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("window.SCREENER_DATA = " + json.dumps(payload, indent=2, default=str) + ";\n")
    print(f"Wrote {OUT_PATH}")

    print("Building full Nifty 500 universe snapshot for Compare section...")
    universe = build_universe_snapshot(conn, scoring_date, calendar)
    UNIVERSE_OUT_PATH.write_text("window.UNIVERSE_SNAPSHOT = " + json.dumps(universe, default=str) + ";\n")
    print(f"Wrote {len(universe['stocks'])} stocks to {UNIVERSE_OUT_PATH}")


if __name__ == "__main__":
    main()
