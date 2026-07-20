"""
Decision layer -- docs/next_phase_plan.md Part B, Section 5. Explicitly
downstream of Section 4's backtest (models/backtest.py): tests concrete
design choices against the SAME folds/models/costs, not decided by
default or built in a vacuum.

Reuses backtest.py's fold/model/price/cost infrastructure directly
(same trained models per fold, same rebalance dates, same transaction
cost model) so every variant here is a fair, apples-to-apples comparison
against the Section 4 baseline (equal-weight, always-fully-invested,
top-N) rather than a differently-scoped re-run.

THREE QUESTIONS FROM THE SPEC, one motivated by a real Section-4 finding:

1. Position sizing: equal-weight vs. probability-weighted (higher
   predicted probability gets more capital).
2. Minimum probability threshold: skip a position (or the whole period)
   when confidence is low, rather than forcing capital into a full N
   names regardless.
3. Regime-based exposure scaling (added after a real Section-4 finding,
   not speculative): fold 3's max drawdown ran notably worse than
   Nifty's (-20.0% vs -11.4% at 14d N=20) -- a pure ranking strategy with
   no defensive hedge concentrates losses in a broad selloff rather than
   being protected from one. Confirmed fold 3 WAS identifiable in
   advance: it's the only fold (both horizons) where
   `nifty50_dist_50dma_pct` reads negative at test_start, and its
   `nifty50_return_10d` is the most negative of any fold -- features the
   model already weights heavily for individual stock ranking
   (`nifty50_dist_50dma_pct` is a top-2 SHAP feature) but the Section 4
   strategy never used at the portfolio-exposure level. Tests whether
   scaling down total exposure when the regime signal is negative
   actually improves the drawdown profile without giving up too much
   upside elsewhere -- tested explicitly, not assumed.

All three use the generalized run_period_weighted() below (a superset of
backtest.py's run_period(): per-symbol weights instead of an assumed
equal split, and unallocated weight earns 0% -- i.e. sits in cash -- so
reduced exposure is "hold some cash," not "hold fewer, still-full-size
positions").

Usage:
    python models/decision_layer.py
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
from backtest import (  # noqa: E402
    HORIZONS, TRANSACTION_COST_SCENARIOS, PORTFOLIO_VALUE_RUPEES, RANDOM_SEED,
    build_trading_calendar, load_price_lookup, load_surveillance_lookup,
    rebalance_dates_for_fold, eligible_universe, compute_forward_return,
    net_return_with_costs, train_fold_model, max_drawdown,
)
from report_archive import write_archive_summary  # noqa: E402

N = 20  # reference N throughout -- Section 4's middle-ground value, keeps the report readable
REGIME_FEATURE = "nifty50_dist_50dma_pct"
REGIME_EXPOSURE_VARIANTS = {
    "full_exposure_baseline": None,          # Section 4's strategy, for comparison
    "half_exposure_on_negative_regime": 0.5, # halve exposure when regime signal < 0
    "zero_exposure_on_negative_regime": 0.0, # fully defensive (all cash) when regime signal < 0
}
MIN_PROB_THRESHOLD = 0.5  # only take a position the model rates more-likely-than-not


def run_period_weighted(symbol_weights: dict, buy_date: str, sell_date: str,
                         price_lookup: dict, scenario: dict, total_capital: float):
    """symbol_weights: {symbol: weight}, 0 <= weight <= 1, not required to
    sum to 1.0 -- unallocated weight (1 - sum(weights)) earns 0% (sits in
    cash), which is exactly how reduced exposure is expressed. Returns
    (portfolio_return, n_dropouts, n_held)."""
    net_dollar_pnl = 0.0
    n_dropouts = 0
    n_held = 0
    for symbol, weight in symbol_weights.items():
        if weight <= 0:
            continue
        position_value = total_capital * weight
        gross, exit_date, dropped = compute_forward_return(symbol, buy_date, sell_date, price_lookup)
        if gross is None:
            continue
        net_ret = net_return_with_costs(gross, scenario, position_value)
        net_dollar_pnl += position_value * net_ret
        n_held += 1
        if dropped:
            n_dropouts += 1
    return (net_dollar_pnl / total_capital if total_capital else None), n_dropouts, n_held


def equal_weights(symbols: list, exposure: float = 1.0) -> dict:
    if not symbols:
        return {}
    w = exposure / len(symbols)
    return {s: w for s in symbols}


def probability_weights(symbols_probs: list, exposure: float = 1.0) -> dict:
    """symbols_probs: [(symbol, prob), ...]. Weight proportional to
    (prob - 0.5), floored at a small positive epsilon so a pick that just
    barely made the top-N still gets *some* capital rather than ~0 --
    this is a conviction tilt, not a hard cutoff (that's
    MIN_PROB_THRESHOLD's job, tested separately)."""
    if not symbols_probs:
        return {}
    raw = {s: max(p - 0.5, 0.01) for s, p in symbols_probs}
    total = sum(raw.values())
    return {s: exposure * v / total for s, v in raw.items()}


def regime_exposure(conn, buy_date: str, reduced_exposure) -> float:
    """1.0 if reduced_exposure is None (baseline) or the regime signal is
    non-negative; reduced_exposure otherwise (0.5 or 0.0)."""
    if reduced_exposure is None:
        return 1.0
    row = conn.execute(
        f"SELECT {REGIME_FEATURE} FROM macro_regime_indicators WHERE date <= ? "
        f"ORDER BY date DESC LIMIT 1", (buy_date,)).fetchone()
    if row is None or row[0] is None:
        return 1.0
    return 1.0 if row[0] >= 0 else reduced_exposure


def run_variant(conn, feature_df, label_df, fold, horizon, price_lookup, surveillance_lookup,
                 calendar, clf, variant: dict):
    """variant: {'sizing': 'equal'|'prob_weighted', 'min_prob': float|None,
    'reduced_exposure': float|None}."""
    rebalance_dates = rebalance_dates_for_fold(calendar, fold["test_start"], fold["test_end"], horizon["hold_days"])
    periods = []
    for buy_date in rebalance_dates:
        target_sell_date = calendar[min(bisect.bisect_left(calendar, buy_date) + horizon["hold_days"],
                                         len(calendar) - 1)]
        eligible = eligible_universe(buy_date, price_lookup, surveillance_lookup)
        if len(eligible) < N:
            continue

        day_features = feature_df[(feature_df["date"] == buy_date) & (feature_df["symbol"].isin(eligible))].copy()
        day_features = day_features.dropna(subset=ALL_FEATURE_COLUMNS, how="all")
        if day_features.empty:
            continue
        X = day_features[ALL_FEATURE_COLUMNS]
        day_features["pred_prob"] = clf.predict_proba(X)[:, 1]
        ranked = day_features.sort_values("pred_prob", ascending=False)

        top_n = ranked.head(N)
        if variant["min_prob"] is not None:
            top_n = top_n[top_n["pred_prob"] >= variant["min_prob"]]

        symbols_probs = list(zip(top_n["symbol"], top_n["pred_prob"]))
        exposure = regime_exposure(conn, buy_date, variant["reduced_exposure"])

        if variant["sizing"] == "prob_weighted":
            weights = probability_weights(symbols_probs, exposure)
        else:
            weights = equal_weights([s for s, _ in symbols_probs], exposure)

        nifty_row = conn.execute(
            "SELECT nifty50_close FROM macro_regime_indicators WHERE date = ?", (buy_date,)).fetchone()
        nifty_row_end = conn.execute(
            "SELECT nifty50_close FROM macro_regime_indicators WHERE date = ?", (target_sell_date,)).fetchone()
        nifty_return = (nifty_row_end[0] / nifty_row[0] - 1.0) if (nifty_row and nifty_row_end) else None

        for scenario_name, scenario in TRANSACTION_COST_SCENARIOS.items():
            position_value_total = PORTFOLIO_VALUE_RUPEES
            ret, n_drop, n_held = run_period_weighted(
                weights, buy_date, target_sell_date, price_lookup, scenario, position_value_total)
            periods.append({
                "buy_date": buy_date, "scenario": scenario_name, "net_return": ret,
                "n_held": n_held, "n_dropouts": n_drop, "exposure": exposure,
                "nifty_return": nifty_return,
            })
    return periods


def summarize(periods: list, scenario_name: str) -> dict:
    relevant = [p for p in periods if p["scenario"] == scenario_name and p["net_return"] is not None]
    if not relevant:
        return {"cumulative_return": None, "max_drawdown": None, "hit_rate": None, "n_periods": 0}
    equity = [1.0]
    hits, n_periods = 0, 0
    for p in relevant:
        equity.append(equity[-1] * (1 + p["net_return"]))
        if p["nifty_return"] is not None:
            n_periods += 1
            if p["net_return"] > p["nifty_return"]:
                hits += 1
    return {
        "cumulative_return": equity[-1] - 1.0,
        "max_drawdown": max_drawdown(equity),
        "hit_rate": hits / n_periods if n_periods else None,
        "n_periods": n_periods,
    }


def main():
    conn = get_conn()
    calendar = build_trading_calendar(conn)
    price_lookup = load_price_lookup(conn)
    surveillance_lookup = load_surveillance_lookup(conn)

    print("Loading feature frame (full universe)...")
    feature_df = load_feature_frame(conn)
    label_df = pd.read_sql_query(
        "SELECT symbol, date, outperform_14d_flag, outperform_30d_flag FROM model_target_labels", conn)

    variants = {
        "baseline_equal_full":       {"sizing": "equal", "min_prob": None, "reduced_exposure": None},
        "prob_weighted_full":        {"sizing": "prob_weighted", "min_prob": None, "reduced_exposure": None},
        "min_prob_threshold":        {"sizing": "equal", "min_prob": MIN_PROB_THRESHOLD, "reduced_exposure": None},
        "regime_half_exposure":      {"sizing": "equal", "min_prob": None, "reduced_exposure": 0.5},
        "regime_zero_exposure":      {"sizing": "equal", "min_prob": None, "reduced_exposure": 0.0},
    }

    report = {"generated_at": datetime.now().isoformat(), "N": N,
              "min_prob_threshold": MIN_PROB_THRESHOLD, "regime_feature": REGIME_FEATURE,
              "horizons": {}}

    for horizon in HORIZONS:
        print(f"\n{'='*100}\nHORIZON: {horizon['label']}\n{'='*100}")
        labeled_dates = sorted(label_df.dropna(subset=[horizon["flag_col"]])["date"].unique())
        folds = make_walk_forward_folds(labeled_dates, embargo_days=horizon["embargo_days"], n_folds=5, expanding=False)

        horizon_report = {}
        for fold in folds:
            print(f"\n  fold {fold['fold']}: test [{fold['test_start']}..{fold['test_end']}]")
            clf = train_fold_model(feature_df, label_df, fold, horizon["flag_col"])
            fold_report = {}
            for variant_name, variant in variants.items():
                periods = run_variant(conn, feature_df, label_df, fold, horizon, price_lookup,
                                       surveillance_lookup, calendar, clf, variant)
                s_opt = summarize(periods, "optimistic")
                fold_report[variant_name] = {"periods": periods, "summary_optimistic": s_opt}
                print(f"    {variant_name:28s} cum_return={s_opt['cumulative_return']:+.4f} "
                      f"max_dd={s_opt['max_drawdown']:.4f} hit_rate={s_opt['hit_rate']}")
            horizon_report[fold["fold"]] = fold_report
        report["horizons"][horizon["label"]] = horizon_report

    out_path = Path(__file__).resolve().parent / "reports" / "decision_layer_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved full report to {out_path}")

    # Compounded equity curve + Calmar per variant, chained across all folds --
    # per-fold simple averaging understates drawdown protection (a loss
    # compounds against subsequent gains rather than averaging away cleanly,
    # see README changelog 2026-07-20) -- the archive summary uses the
    # correct lens by construction, not the misleading one.
    archive_payload = {"horizons": {}}
    for label, horizon_report in report["horizons"].items():
        archive_payload["horizons"][label] = {"variants": {}}
        for variant_name in variants:
            equity = [1.0]
            for fold_num in sorted(horizon_report.keys(), key=int):
                for p in horizon_report[fold_num][variant_name]["periods"]:
                    if p["scenario"] == "optimistic" and p["net_return"] is not None:
                        equity.append(equity[-1] * (1 + p["net_return"]))
            total_return = equity[-1] - 1.0
            mdd = max_drawdown(equity)
            calmar = total_return / abs(mdd) if mdd != 0 else None
            archive_payload["horizons"][label]["variants"][variant_name] = {
                "compounded_return": total_return, "max_drawdown": mdd, "calmar": calmar,
            }
    write_archive_summary("decision_layer", archive_payload,
                           notes="compounded/Calmar chained sequentially across all 5 folds, optimistic costs, N=" + str(N))


if __name__ == "__main__":
    main()
