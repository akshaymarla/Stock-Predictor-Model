"""
Portfolio-level economic backtest -- docs/next_phase_plan.md Part B,
Section 4. Everything evaluated so far (AUC, calibration, SHAP) describes
the *model*; this answers whether it's actually *useful*: does a simple
top-N strategy built on it beat buy-and-hold Nifty and a random-stock
baseline from the same universe, after real transaction costs.

UNIVERSE PROXY (2026-07-19, explicit user decision -- see README
changelog): `index_membership` has only 2 snapshot dates (2026-07-13,
2026-07-14), both effectively "today" -- no historical reconstruction
exists yet (same limitation flagged in CLAUDE.md and confirmed for
`sector_membership` in Part A). Using `index_membership` as the universe
at each historical rebalance date, as originally specced, is not
possible today. This uses "has a `daily_prices` row on this date" as the
point-in-time proxy instead -- correctly excludes stocks before their
real listing date and stocks with no current price data, but CANNOT
detect a stock's removal from the Nifty 500 for reasons other than it no
longer trading at all (e.g. an index reconstitution that dropped a
still-trading company). This is a real, documented limitation, not a
full fix -- true historical index reconstruction remains a separate,
not-yet-started task. Every report below states this caveat explicitly.

FOLD/MODEL: reuses the exact same 5 rolling-window folds already
validated in models/shap_and_calibration.py (make_walk_forward_folds(...,
expanding=False), per model_build_spec.md Section 2b's empirical pick).
Each fold trains its OWN model on that fold's train window and only
scores rebalance dates inside that SAME fold's test window -- never a
single "final" model applied across the whole backtest period.

REBALANCE DATES: non-overlapping, spaced by the label horizon in TRADING
days (via macro_regime_indicators.date as the market calendar, same
convention as compute_target_labels.py), starting at the fold's
test_start.

FORWARD RETURNS / MID-HOLD DELISTING: computed directly from
`daily_prices` (NOT `model_target_labels`, which silently EXCLUDES
incomplete-window rows -- correct for training labels, wrong for a
backtest that needs to know what actually happened to a held position).
A held stock's exit price is its close on the exact target sell date if
available, or its LAST available close before that date otherwise
(explicit mid-hold-dropout handling, counted and flagged per fold in the
report -- not silently absorbed into the return number).

TRANSACTION COSTS -- sourced live 2026-07-19 from Zerodha's published
charges page (zerodha.com/charges/) and NSE's own SEBI-turnover-fees
page, not assumed/hardcoded from possibly-stale training knowledge: STT
0.1% both legs, NSE exchange charges 0.00307% both legs, SEBI charges
Rs 10/crore both legs, stamp duty 0.015% buy side only, GST 18% on
(brokerage + SEBI + exchange charges), DP charges Rs 15.34 flat per
scrip on the sell side. Two scenarios below: OPTIMISTIC (zero brokerage,
the modern Indian discount-broker norm, no slippage) and CONSERVATIVE
(adds a modest brokerage + slippage allowance for market impact) -- see
TRANSACTION_COST_SCENARIOS for exact, adjustable parameters.

BENCHMARKS, every report: buy-and-hold Nifty (macro_regime_indicators.
nifty50_close) over the identical fold test window, and an equal-weight
random-N-stock portfolio from the SAME eligible universe at each
rebalance date (averaged over 20 random draws per rebalance date, to
avoid a single noisy draw misleadingly favoring either side -- same
"beat the naive baseline" discipline as train_baselines.py).

Usage:
    python models/backtest.py
"""
import bisect
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from db import get_conn  # noqa: E402

from data_loader import ALL_FEATURE_COLUMNS, load_feature_frame  # noqa: E402
from splitting import make_walk_forward_folds  # noqa: E402
from train_lightgbm import LGB_PARAMS  # noqa: E402
from report_archive import write_archive_summary  # noqa: E402

HORIZONS = [
    {"label": "14d", "flag_col": "outperform_14d_flag", "embargo_days": 14, "hold_days": 14},
    {"label": "30d", "flag_col": "outperform_30d_flag", "embargo_days": 30, "hold_days": 30},
]
N_VALUES = [10, 20, 30]
RANDOM_DRAWS = 20
RANDOM_SEED = 42
# Real rupee portfolio size, equal-weighted across N positions -- needed
# because the DP charge (Rs 15.34, see STATUTORY_COSTS) is a FLAT rupee
# amount, not a percentage of trade value, so it only makes sense against
# a real position size, not a scale-invariant normalized return. Percentage
# costs (STT, exchange, SEBI, stamp duty, brokerage, slippage) don't
# depend on this value, but the DP charge's relative weight does --
# adjustable, clearly labeled, not silently baked in.
PORTFOLIO_VALUE_RUPEES = 1_000_000

# --- transaction costs, sourced live 2026-07-19 (see module docstring) ---
STATUTORY_COSTS = {
    "stt_pct": 0.001,               # 0.1%, both legs
    "exchange_txn_pct": 0.0000307,  # NSE, both legs
    "sebi_pct": 0.000001,           # Rs 10/crore, both legs
    "stamp_duty_buy_pct": 0.00015,  # 0.015%, BUY side only
    "gst_pct": 0.18,                # on (brokerage + sebi + exchange_txn)
    "dp_charge_flat": 15.34,        # Rs per scrip, SELL side only, flat not %-based
}
TRANSACTION_COST_SCENARIOS = {
    "optimistic": {"brokerage_pct": 0.0, "slippage_pct": 0.0},
    "conservative": {"brokerage_pct": 0.0003, "slippage_pct": 0.001},  # 0.03%/0.1% per leg
}


def leg_cost(value: float, scenario: dict, is_buy: bool) -> float:
    """One-way (buy or sell) transaction cost in rupees for a trade of
    the given value, under a given cost scenario."""
    s = STATUTORY_COSTS
    brokerage = value * scenario["brokerage_pct"]
    exchange = value * s["exchange_txn_pct"]
    sebi = value * s["sebi_pct"]
    slippage = value * scenario["slippage_pct"]
    stt = value * s["stt_pct"]
    gst = s["gst_pct"] * (brokerage + sebi + exchange)
    cost = brokerage + exchange + sebi + slippage + stt + gst
    if is_buy:
        cost += value * s["stamp_duty_buy_pct"]
    else:
        cost += s["dp_charge_flat"]
    return cost


def build_trading_calendar(conn) -> list:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM macro_regime_indicators ORDER BY date").fetchall()]


def rebalance_dates_for_fold(calendar: list, test_start: str, test_end: str, hold_days: int) -> list:
    """Non-overlapping rebalance dates spaced by hold_days TRADING days,
    starting at test_start, stopping once the following full hold period
    would run past test_end."""
    start_idx = bisect.bisect_left(calendar, test_start)
    end_idx = bisect.bisect_right(calendar, test_end) - 1
    dates = []
    idx = start_idx
    while idx <= end_idx:
        dates.append(calendar[idx])
        idx += hold_days
    return dates


def load_price_lookup(conn) -> dict:
    """{symbol: (dates_list, closes_list)}, both sorted ascending --
    precomputed as parallel arrays (not a list of tuples) so
    price_at_or_before()/eligible_universe() can bisect directly without
    rebuilding a dates list on every one of the ~750k calls this backtest
    makes (3 N-values x 21 draws x 2 cost scenarios x ~30 held symbols x
    ~200 rebalance dates) -- a real, measured perf concern for a table
    this size (~557k daily_prices rows), not premature optimization."""
    rows = conn.execute("SELECT symbol, date, close FROM daily_prices ORDER BY symbol, date").fetchall()
    by_symbol = {}
    for symbol, date, close in rows:
        by_symbol.setdefault(symbol, ([], []))
        by_symbol[symbol][0].append(date)
        by_symbol[symbol][1].append(close)
    return by_symbol


def load_surveillance_lookup(conn) -> dict:
    """{symbol: [(start_date, end_date_or_None), ...]}"""
    rows = conn.execute("SELECT symbol, start_date, end_date FROM surveillance_flags").fetchall()
    lookup = {}
    for symbol, start, end in rows:
        lookup.setdefault(symbol, []).append((start, end))
    return lookup


def is_surveilled(symbol: str, date: str, surveillance_lookup: dict) -> bool:
    for start, end in surveillance_lookup.get(symbol, []):
        if start <= date and (end is None or date <= end):
            return True
    return False


def price_at_or_before(symbol: str, date: str, price_lookup: dict):
    """(price, actual_date, is_before) for the latest close <= date, or
    (None, None, None) if the symbol has no price data at or before date."""
    entry = price_lookup.get(symbol)
    if not entry:
        return None, None, None
    dates, closes = entry
    idx = bisect.bisect_right(dates, date) - 1
    if idx < 0:
        return None, None, None
    actual_date = dates[idx]
    return closes[idx], actual_date, actual_date != date


def compute_forward_return(symbol: str, buy_date: str, target_sell_date: str, price_lookup: dict):
    """Returns (net_return, exit_date, mid_hold_dropout: bool) using the
    close on target_sell_date if available, or the last available close
    before it otherwise (explicit mid-hold delisting/suspension
    handling -- see module docstring)."""
    buy_close, buy_actual, _ = price_at_or_before(symbol, buy_date, price_lookup)
    if buy_close is None:
        return None, None, False
    sell_close, sell_actual, dropped_out = price_at_or_before(symbol, target_sell_date, price_lookup)
    if sell_close is None or sell_actual < buy_actual:
        return None, None, False
    ret = (sell_close / buy_close) - 1.0
    return ret, sell_actual, bool(dropped_out)


def eligible_universe(rebalance_date: str, price_lookup: dict, surveillance_lookup: dict) -> list:
    """Symbols with a daily_prices row exactly on rebalance_date (the
    point-in-time universe proxy -- see module docstring) that are NOT
    under an active surveillance flag as of that date."""
    eligible = []
    for symbol, (dates, _) in price_lookup.items():
        idx = bisect.bisect_left(dates, rebalance_date)
        if idx < len(dates) and dates[idx] == rebalance_date:
            if not is_surveilled(symbol, rebalance_date, surveillance_lookup):
                eligible.append(symbol)
    return eligible


def train_fold_model(feature_df: pd.DataFrame, label_df: pd.DataFrame, fold: dict, flag_col: str):
    train = label_df.dropna(subset=[flag_col])
    train = train[(train["date"] >= fold["train_start"]) & (train["date"] <= fold["train_end"])]
    train = train.merge(feature_df, on=["symbol", "date"], how="inner")
    X_train, y_train = train[ALL_FEATURE_COLUMNS], train[flag_col].astype(int)
    clf = lgb.LGBMClassifier(**LGB_PARAMS)
    clf.fit(X_train, y_train)
    return clf


def net_return_with_costs(gross_return: float, scenario: dict, position_value: float) -> float:
    """gross_return -> net return after round-trip transaction costs, for
    a single position of `position_value` real rupees -- must be a real
    rupee amount, not a normalized 1.0, because the DP charge (a flat
    rupee amount, see STATUTORY_COSTS) doesn't scale with the return, only
    with the actual position size."""
    buy_value = position_value
    sell_value = position_value * (1.0 + gross_return)
    buy_cost = leg_cost(buy_value, scenario, is_buy=True)
    sell_cost = leg_cost(sell_value, scenario, is_buy=False)
    return (sell_value - buy_value - buy_cost - sell_cost) / buy_value


def run_period(symbols: list, buy_date: str, sell_date: str, price_lookup: dict, scenario: dict):
    """Returns (mean_net_return, n_dropouts, n_held) for an equal-weight
    basket of `symbols` bought on buy_date, held to sell_date. Position
    size = PORTFOLIO_VALUE_RUPEES / len(symbols) (equal weight across
    however many names are actually in the basket)."""
    if not symbols:
        return None, 0, 0
    position_value = PORTFOLIO_VALUE_RUPEES / len(symbols)
    net_returns = []
    n_dropouts = 0
    for symbol in symbols:
        gross, exit_date, dropped = compute_forward_return(symbol, buy_date, sell_date, price_lookup)
        if gross is None:
            continue
        net_returns.append(net_return_with_costs(gross, scenario, position_value))
        if dropped:
            n_dropouts += 1
    if not net_returns:
        return None, n_dropouts, 0
    return float(np.mean(net_returns)), n_dropouts, len(net_returns)


def max_drawdown(equity_curve: list) -> float:
    peak = equity_curve[0]
    mdd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak)
    return mdd


def run_fold(conn, feature_df, label_df, fold, horizon, price_lookup, surveillance_lookup, calendar):
    clf = train_fold_model(feature_df, label_df, fold, horizon["flag_col"])
    rebalance_dates = rebalance_dates_for_fold(calendar, fold["test_start"], fold["test_end"], horizon["hold_days"])
    if len(rebalance_dates) < 1:
        return None

    result = {"fold": fold["fold"], "test_start": fold["test_start"], "test_end": fold["test_end"],
              "n_rebalances": len(rebalance_dates), "periods": []}

    rng = random.Random(RANDOM_SEED + fold["fold"])

    for i, buy_date in enumerate(rebalance_dates):
        target_sell_date = calendar[min(bisect.bisect_left(calendar, buy_date) + horizon["hold_days"],
                                         len(calendar) - 1)]
        eligible = eligible_universe(buy_date, price_lookup, surveillance_lookup)
        if len(eligible) < max(N_VALUES):
            continue

        # score eligible universe with this fold's model
        day_features = feature_df[(feature_df["date"] == buy_date) & (feature_df["symbol"].isin(eligible))].copy()
        day_features = day_features.dropna(subset=ALL_FEATURE_COLUMNS, how="all")
        if day_features.empty:
            continue
        X = day_features[ALL_FEATURE_COLUMNS]
        day_features["pred_prob"] = clf.predict_proba(X)[:, 1]
        ranked = day_features.sort_values("pred_prob", ascending=False)["symbol"].tolist()

        # nifty benchmark over this exact period
        nifty_row = conn.execute(
            "SELECT nifty50_close FROM macro_regime_indicators WHERE date = ?", (buy_date,)).fetchone()
        nifty_row_end = conn.execute(
            "SELECT nifty50_close FROM macro_regime_indicators WHERE date = ?", (target_sell_date,)).fetchone()
        nifty_return = (nifty_row_end[0] / nifty_row[0] - 1.0) if (nifty_row and nifty_row_end) else None

        period_result = {"buy_date": buy_date, "sell_date": target_sell_date,
                          "n_eligible": len(eligible), "nifty_return": nifty_return, "by_n": {}}

        for n in N_VALUES:
            top_n = ranked[:n]
            random_draws = [rng.sample(eligible, n) for _ in range(RANDOM_DRAWS)]
            period_result["by_n"][n] = {"model": {}, "random": {}}
            for scenario_name, scenario in TRANSACTION_COST_SCENARIOS.items():
                model_ret, model_dropouts, model_held = run_period(top_n, buy_date, target_sell_date, price_lookup, scenario)
                random_rets = []
                random_dropouts = 0
                for draw in random_draws:
                    r, d, _ = run_period(draw, buy_date, target_sell_date, price_lookup, scenario)
                    if r is not None:
                        random_rets.append(r)
                        random_dropouts += d
                period_result["by_n"][n]["model"][scenario_name] = {
                    "net_return": model_ret, "n_dropouts": model_dropouts, "n_held": model_held}
                period_result["by_n"][n]["random"][scenario_name] = {
                    "net_return": float(np.mean(random_rets)) if random_rets else None,
                    "n_dropouts": random_dropouts, "n_draws": len(random_rets)}

        result["periods"].append(period_result)

    return result


def summarize_fold(fold_result: dict) -> dict:
    """Cumulative return, max drawdown, hit rate per (N, scenario), for
    both model and random strategies."""
    summary = {}
    for n in N_VALUES:
        summary[n] = {}
        for scenario_name in TRANSACTION_COST_SCENARIOS:
            for strategy in ("model", "random"):
                key = f"{strategy}_{scenario_name}"
                equity = [1.0]
                hits = 0
                n_periods = 0
                for period in fold_result["periods"]:
                    entry = period["by_n"][n][strategy][scenario_name]
                    ret = entry["net_return"]
                    if ret is None:
                        continue
                    equity.append(equity[-1] * (1 + ret))
                    if period["nifty_return"] is not None:
                        n_periods += 1
                        if ret > period["nifty_return"]:
                            hits += 1
                summary[n][key] = {
                    "cumulative_return": equity[-1] - 1.0,
                    "max_drawdown": max_drawdown(equity),
                    "hit_rate": hits / n_periods if n_periods else None,
                    "n_periods": n_periods,
                }
        # nifty buy-and-hold for this fold, compounded across the same periods
        nifty_equity = [1.0]
        for period in fold_result["periods"]:
            if period["nifty_return"] is not None:
                nifty_equity.append(nifty_equity[-1] * (1 + period["nifty_return"]))
        summary[n]["nifty_buyhold"] = {
            "cumulative_return": nifty_equity[-1] - 1.0,
            "max_drawdown": max_drawdown(nifty_equity),
        }
    return summary


def main():
    conn = get_conn()
    calendar = build_trading_calendar(conn)
    price_lookup = load_price_lookup(conn)
    surveillance_lookup = load_surveillance_lookup(conn)

    print("Loading feature frame (full universe)...")
    feature_df = load_feature_frame(conn)

    label_df = pd.read_sql_query(
        "SELECT symbol, date, outperform_14d_flag, outperform_30d_flag FROM model_target_labels", conn)

    report = {"generated_at": datetime.now().isoformat(),
              "universe_proxy_caveat": (
                  "index_membership has no historical snapshots (only 2026-07-13/14) -- "
                  "universe at each rebalance date uses daily_prices presence as a proxy, "
                  "which cannot detect index removals other than a stock ceasing to trade. "
                  "See models/backtest.py module docstring."),
              "portfolio_value_rupees": PORTFOLIO_VALUE_RUPEES,
              "transaction_cost_scenarios": TRANSACTION_COST_SCENARIOS,
              "statutory_costs": STATUTORY_COSTS,
              "horizons": {}}

    for horizon in HORIZONS:
        print(f"\n{'='*100}\nHORIZON: {horizon['label']} (rolling window)\n{'='*100}")
        labeled_dates = sorted(label_df.dropna(subset=[horizon["flag_col"]])["date"].unique())
        folds = make_walk_forward_folds(labeled_dates, embargo_days=horizon["embargo_days"], n_folds=5, expanding=False)

        fold_results = []
        for fold in folds:
            print(f"  fold {fold['fold']}: train [{fold['train_start']}..{fold['train_end']}] "
                  f"test [{fold['test_start']}..{fold['test_end']}]")
            fold_result = run_fold(conn, feature_df, label_df, fold, horizon, price_lookup, surveillance_lookup, calendar)
            if fold_result is None:
                print(f"    skipped (no valid rebalance dates)")
                continue
            fold_result["summary"] = summarize_fold(fold_result)
            fold_results.append(fold_result)
            print(f"    {fold_result['n_rebalances']} rebalances, {len(fold_result['periods'])} scored periods")
            for n in N_VALUES:
                m = fold_result["summary"][n]["model_optimistic"]
                r = fold_result["summary"][n]["random_optimistic"]
                nb = fold_result["summary"][n]["nifty_buyhold"]
                print(f"      N={n:2d} (optimistic costs): model={m['cumulative_return']:+.4f} "
                      f"random={r['cumulative_return']:+.4f} nifty={nb['cumulative_return']:+.4f} "
                      f"hit_rate={m['hit_rate']}")

        report["horizons"][horizon["label"]] = fold_results

    out_path = Path(__file__).resolve().parent / "reports" / "backtest_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved full report to {out_path}")

    archive_payload = {"horizons": {}}
    for label, fold_results in report["horizons"].items():
        archive_payload["horizons"][label] = {"folds": [
            {
                "fold": f["fold"], "test_start": f["test_start"], "test_end": f["test_end"],
                "n_rebalances": f["n_rebalances"],
                "by_n": {
                    str(n): {
                        "model_optimistic_return": f["summary"][n]["model_optimistic"]["cumulative_return"],
                        "model_optimistic_max_dd": f["summary"][n]["model_optimistic"]["max_drawdown"],
                        "model_optimistic_hit_rate": f["summary"][n]["model_optimistic"]["hit_rate"],
                        "random_optimistic_return": f["summary"][n]["random_optimistic"]["cumulative_return"],
                        "nifty_buyhold_return": f["summary"][n]["nifty_buyhold"]["cumulative_return"],
                    }
                    for n in N_VALUES
                },
            }
            for f in fold_results
        ]}
    write_archive_summary("backtest", archive_payload,
                           notes="universe proxy: daily_prices presence, not true historical index_membership -- see module docstring")


if __name__ == "__main__":
    main()
