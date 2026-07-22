"""
Exports the two data files the frontend needs, per
frontend-screener-spec.md Section 6 (Overview) and
frontend-spec-v2-sidebar-nav.md Sections 4-5 (Statistical View + Compare):

- frontend/data.js  -- window.SCREENER_DATA: nifty, model_meta,
  candidates, tracking. Loaded on every page view.
- frontend/universe.js -- window.UNIVERSE_SNAPSHOT: full Nifty 500
  lookup data for the Compare section. Written separately and loaded
  lazily by app.js only when that section is first opened -- it's a
  meaningfully bigger export (full 500 vs. top-N) and most sessions
  never touch Compare, per frontend-spec-v2 Section 5's own
  build-time-decision note.

Both are plain `<script src>` includes, deliberately NOT fetch()/XHR'd
.json files -- file:// pages can't fetch() JSON in most browsers (CORS),
so a plain script include is what actually lets the screener keep this
project's "single file, opens with no server" convention (same as
models/reports/tracking_dashboard.html).

CANDIDATE SCOPE (data.candidates): each horizon's actual top-N selection
(TOP_N below), not the full eligible universe -- originally shipped as
the full ~440-name universe (frontend-screener-spec.md Section 9 Q4) but
reverted same day, confirmed by real use to be unscannable. Both horizon
models still score the FULL eligible universe internally (reusing
weekly_shortlist.py's train_production_model()/load_universe() directly,
skipping its SHAP pass since the card UI never shows per-stock factors)
so the top-N ranking itself stays exact.

TRACKING TABLE (data.tracking): built from the REAL `tracked_picks` table
(the actual picks weekly_shortlist.py already logged), NOT a fresh top-N
recomputed by this script -- see tracking_section()'s docstring for why
that distinction matters (a fresh-every-run set would never accumulate
real tracking history).

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
from data_loader import ALL_FEATURE_COLUMNS, load_feature_frame  # noqa: E402
from weekly_shortlist import load_universe, train_production_model, HORIZONS  # noqa: E402
from generate_tracking_dashboard import load_picks, hit_rate_section  # noqa: E402
from backtest import load_price_lookup, price_at_or_before  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "data.js"
UNIVERSE_OUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "universe.js"
SMALL_SAMPLE_CUTOFF = 20  # same "too small to be conclusive" threshold as generate_tracking_dashboard.py
TOP_N = 20  # matches weekly_shortlist.py's own top-N convention -- showing the full ~440-name
            # eligible universe made the deck unscannable in practice (confirmed by real use),
            # so this exports each horizon's actual top-N selection, not everything scored


def score_full_universe(conn, feature_df, label_df, horizon, eligible_symbols, scoring_date) -> dict:
    """{symbol: calibrated_prob} for every eligible symbol with a scoreable
    feature row -- same production-model training as
    weekly_shortlist.build_shortlist(), no top-N truncation, no SHAP."""
    clf, iso, split, calib_auc = train_production_model(
        conn, feature_df, label_df, horizon["flag_col"], horizon["embargo_days"])

    day_features = feature_df[(feature_df["date"] == scoring_date) & (feature_df["symbol"].isin(eligible_symbols))].copy()
    day_features = day_features.dropna(subset=ALL_FEATURE_COLUMNS, how="all")
    if day_features.empty:
        return {}

    X = day_features[ALL_FEATURE_COLUMNS]
    raw_prob = clf.predict_proba(X)[:, 1]
    calibrated_prob = iso.predict(raw_prob)
    print(f"  {horizon['label']}: calib AUC={calib_auc}, {len(day_features)} symbols scored")
    return dict(zip(day_features["symbol"], calibrated_prob))


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


def tracking_section(conn, scoring_date: str, calendar: list) -> dict:
    """Day-by-day realized (never predicted) close + day-over-day %
    change for each horizon's ACTUAL frozen picks -- deliberately built
    from the real `tracked_picks` table (the picks weekly_shortlist.py
    already logged), not a fresh top-N recomputed by this script. A
    fresh-every-run recomputation would reset to an all-null table on
    every export instead of accumulating real tracking history, since
    this script's own top-N naturally changes as the model/data moves
    day to day -- tracked_picks is what stays frozen at pick time,
    exactly the property this table needs. See README changelog for the
    full reasoning. Ticker set matches the Overview deck today only
    because both happen to be scored from the same 2026-07-15 data as of
    this run -- in general they can diverge slightly, by design (Overview
    = freshest live ranking, this = the actual picks being tracked)."""
    calendar_idx = {d: i for i, d in enumerate(calendar)}
    picks = load_picks(conn)

    tracking = {}
    for horizon, n_days in (("14d", 14), ("30d", 30)):
        horizon_picks = [p for p in picks if p["horizon"] == horizon]
        if not horizon_picks:
            tracking[horizon] = None
            continue
        pick_date = max(p["pick_date"] for p in horizon_picks)
        symbols = sorted({p["symbol"] for p in horizon_picks if p["pick_date"] == pick_date})

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

        tracking[horizon] = {
            "pick_date": pick_date, "trading_days": trading_days,
            "nifty_history": nifty_history, "stocks": stocks_out,
        }
    return tracking


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
    print("Loading feature frame (full universe)...")
    feature_df = load_feature_frame(conn)
    label_df = pd.read_sql_query(
        "SELECT symbol, date, outperform_14d_flag, outperform_30d_flag FROM model_target_labels", conn)

    scoring_date = conn.execute(
        "SELECT MAX(date) FROM daily_prices WHERE date IN (SELECT date FROM macro_regime_indicators)"
    ).fetchone()[0]
    eligible_symbols, exclusions = load_universe(conn, scoring_date)
    print(f"Scoring date: {scoring_date} -- {len(eligible_symbols)} eligible symbols")

    probs_by_horizon = {}
    for horizon in HORIZONS:
        print(f"Scoring full universe for {horizon['label']}...")
        probs_by_horizon[horizon["label"]] = score_full_universe(
            conn, feature_df, label_df, horizon, eligible_symbols, scoring_date)

    # every candidate needs BOTH probs (Section 6's contract) -- a symbol
    # scored for one horizon but not the other (unusable feature row on
    # this exact date for just that one model) is excluded from ranking
    # consideration for either horizon, never shown with a fabricated/missing prob
    both_scored = set(probs_by_horizon["14d"]) & set(probs_by_horizon["30d"])
    n_only_one_horizon = len(set(probs_by_horizon["14d"]) | set(probs_by_horizon["30d"])) - len(both_scored)
    if n_only_one_horizon:
        print(f"  {n_only_one_horizon} symbol(s) scored for only one horizon, excluded from ranking")

    # each horizon's actual top-N selection, computed from the full
    # eligible-universe scoring above (so the ranking itself is still
    # correct) then unioned -- a stock can appear in only one horizon's
    # tab, which is the real point (its 14D and 30D rank usually differ)
    top_by_horizon = {
        h: sorted(both_scored, key=lambda s: -probs_by_horizon[h][s])[:TOP_N]
        for h in probs_by_horizon
    }
    scored_symbols = sorted(set(top_by_horizon["14d"]) | set(top_by_horizon["30d"]))
    print(f"  Exporting top-{TOP_N} per horizon -- {len(scored_symbols)} unique symbol(s) across both "
          f"(out of {len(both_scored)} fully-scored, {len(eligible_symbols)} eligible)")

    company_meta = load_company_meta(conn, scored_symbols)
    price_meta = load_price_meta(conn, scored_symbols, scoring_date)
    range_meta = load_52w_range(conn, scored_symbols, scoring_date)

    candidates = []
    skipped = []
    for symbol in scored_symbols:
        if symbol not in company_meta or symbol not in price_meta or symbol not in range_meta:
            skipped.append(symbol)
            continue
        candidates.append({
            "ticker": symbol,
            "company_name": company_meta[symbol]["company_name"] or symbol,
            "sector": company_meta[symbol]["sector"] or "Unclassified",
            "eod_price": price_meta[symbol]["eod_price"],
            "day_change_pct": price_meta[symbol]["day_change_pct"],
            "range_52w_low": range_meta[symbol]["range_52w_low"],
            "range_52w_high": range_meta[symbol]["range_52w_high"],
            "prob_14d": round(float(probs_by_horizon["14d"][symbol]), 4),
            "prob_30d": round(float(probs_by_horizon["30d"][symbol]), 4),
        })
    if skipped:
        print(f"  Skipped {len(skipped)} symbol(s) missing company/price/range metadata: {skipped}")

    calendar = [r[0] for r in conn.execute("SELECT date FROM macro_regime_indicators ORDER BY date").fetchall()]

    print("Building tracking table from real tracked_picks...")
    tracking = tracking_section(conn, scoring_date, calendar)

    payload = {
        "nifty": nifty_ticker_section(conn, scoring_date, calendar),
        "model_meta": model_meta_section(conn),
        "candidates": candidates,
        "tracking": tracking,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("window.SCREENER_DATA = " + json.dumps(payload, indent=2, default=str) + ";\n")
    print(f"Wrote {len(candidates)} candidates to {OUT_PATH}")

    print("Building full Nifty 500 universe snapshot for Compare section...")
    universe = build_universe_snapshot(conn, scoring_date, calendar)
    UNIVERSE_OUT_PATH.write_text("window.UNIVERSE_SNAPSHOT = " + json.dumps(universe, default=str) + ";\n")
    print(f"Wrote {len(universe['stocks'])} stocks to {UNIVERSE_OUT_PATH}")


if __name__ == "__main__":
    main()
