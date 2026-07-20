"""
Weekly shortlist -- docs/reports_archive_and_shortlist_spec.md Part B.
NOT a backtest -- a live scoring run against the most current data
available, producing a ranked shortlist with a real per-stock explanation
attached to each name. This is the actual critical path for how this
project is used right now (confirmed: a screening aid feeding a weekly
manual-review step, not an unattended allocator) -- more directly useful
day to day than further backtest/decision-layer refinement.

PRODUCTION MODEL: trained on ALL available labeled history, not a
walk-forward evaluation fold -- a live run has no future to leak from, so
there's no reason to hold out a test set the way Section 4's honest-
evaluation folds correctly do. The one deliberate exception: still
reserves the tail (CALIB_DAYS + embargo) for isotonic calibration, per
this project's standing "never let a calibrator see data the underlying
model trained on" rule (model_build_spec.md Section 7) -- reuses
splitting.add_calibration_split() exactly as every other evaluation in
this project does, just applied to one synthetic "fold" spanning all
history instead of a walk-forward slice.

UNIVERSE: today's (latest) index_membership snapshot, filtered by:
  - surveillance_flags (ASM/GSM exclusion, active as of today)
  - a liquidity floor: bottom-decile avg_traded_value_20d excluded --
    no prior liquidity filter existed anywhere in this pipeline
    (confirmed directly while building the institutional-attention
    feature, 2026-07-19), so this defines one, deliberately relative
    (percentile-based within today's eligible universe) rather than an
    arbitrary absolute rupee figure that would need periodic re-tuning
    as markets move.
  - a data-availability floor: must have a scoreable feature row on the
    scoring date at all (excludes anything with a data gap).
Exclusion counts are printed and included in the output, not silent.

EXPLANATIONS: real per-stock SHAP values (shap.TreeExplainer on the
production model, applied to each shortlisted stock's own feature row),
not aggregate/global importance -- the whole point of this tool.

REGIME FLAG: reuses the exact signal found to precede the worst Part B
backtest drawdown (nifty50_dist_50dma_pct < 0 at rebalance time -- see
next_phase_plan.md Section 5) plus a VIX-percentile check against its own
trailing-year range. Informational only -- doesn't filter or block the
shortlist, just hands the reviewer the same context the model is
conditioning on.

Usage:
    python src/weekly_shortlist.py                     # both horizons, top 20
    python src/weekly_shortlist.py --horizon 14d --top-n 15
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_conn  # noqa: E402
from data_loader import ALL_FEATURE_COLUMNS, load_feature_frame  # noqa: E402
from splitting import add_calibration_split  # noqa: E402
from train_lightgbm import LGB_PARAMS  # noqa: E402
from report_archive import write_archive_summary  # noqa: E402

HORIZONS = [
    {"label": "14d", "flag_col": "outperform_14d_flag", "embargo_days": 14},
    {"label": "30d", "flag_col": "outperform_30d_flag", "embargo_days": 30},
]
CALIB_DAYS = 60
LIQUIDITY_PERCENTILE_FLOOR = 0.10  # bottom decile of avg_traded_value_20d excluded

FEATURE_LABELS = {
    "india_vix_close": "India VIX level", "nifty50_dist_50dma_pct": "Nifty's distance from its 50-day average",
    "nifty50_return_5d": "Nifty's 5-day return", "nifty50_return_10d": "Nifty's 10-day return",
    "vix_change_5d_pts": "VIX change (5d, points)", "vix_change_5d_pct": "VIX change (5d, %)",
    "avg_traded_value_20d": "20-day average traded value (liquidity)",
    "return_5d": "5-day price momentum", "return_10d": "10-day price momentum",
    "return_20d": "20-day price momentum", "volatility_20d": "20-day price volatility",
    "volume_ratio_20d": "volume vs. 20-day average", "delivery_pct": "delivery percentage",
    "fin_days_since_disclosure": "days since last results disclosure",
    "fin_sales": "quarterly sales", "fin_net_profit": "quarterly net profit",
    "fin_opm_pct": "operating margin", "fin_eps": "EPS",
    "bs_total_assets": "total assets", "bs_borrowings": "borrowings",
    "cf_net_cash_flow": "net cash flow", "ratio_roce_pct": "ROCE",
    "sh_promoter_pct": "promoter holding", "sh_public_pct": "public holding",
    "sh_inst_total_pct": "total institutional holding", "sh_inst_fii_fpi_pct": "FII/FPI holding",
    "sh_inst_mutual_fund_pct": "mutual fund holding",
    "sh_inst_qoq_change_pct": "institutional holding change (QoQ)",
    "sh_inst_yoy_change_pct": "institutional holding change (YoY)",
    "sh_inst_pctrank": "institutional holding percentile rank (universe)",
    "recent_negative_catalyst_flag_30d": "recent negative catalyst news (30d)",
    "recent_positive_catalyst_flag_30d": "recent positive catalyst news (30d)",
}
PCT_SCALE_FEATURES = {"sh_inst_total_pct", "sh_inst_fii_fpi_pct", "sh_inst_mutual_fund_pct",
                       "sh_promoter_pct", "sh_public_pct", "fin_opm_pct", "ratio_roce_pct", "sh_inst_pctrank"}
PP_CHANGE_FEATURES = {"sh_inst_qoq_change_pct", "sh_inst_yoy_change_pct"}
PCT_MOVE_FEATURES = {"return_5d", "return_10d", "return_20d", "nifty50_return_5d", "nifty50_return_10d",
                      "nifty50_dist_50dma_pct", "vix_change_5d_pct", "delivery_pct", "volatility_20d"}
# fin_*/bs_*/cf_* are sourced from screener.in (financial_results/balance_sheet/
# cash_flow tables, source='SCREENER'), which reports natively in Rs Crores --
# confirmed via sanity check against known real figures (e.g. BHARATFORG
# fin_net_profit=245 on 2026-07-15 matches its real ~Rs 200-350 Cr quarterly
# range; a raw-rupee reading of "Rs 245" would be absurd for a large-cap).
# avg_traded_value_20d is computed in this pipeline from daily_prices
# (close * volume), genuinely in raw rupees, so it alone needs the /1e7 step.
CRORE_FEATURES = {"fin_sales", "fin_net_profit", "bs_total_assets", "bs_borrowings", "cf_net_cash_flow"}
RUPEE_FEATURES = {"avg_traded_value_20d"}


def format_feature_value(name: str, value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "no data"
    if name in PCT_SCALE_FEATURES:
        return f"{value*100:.1f}%"
    if name in PP_CHANGE_FEATURES:
        return f"{value*100:+.1f}pp"
    if name in PCT_MOVE_FEATURES:
        return f"{value:+.1f}%"
    if name == "india_vix_close":
        return f"{value:.1f}"
    if name == "fin_days_since_disclosure":
        return f"{value:.0f} days"
    if name in CRORE_FEATURES:
        return f"Rs {value:,.1f}Cr"
    if name in RUPEE_FEATURES:
        return f"Rs {value/1e7:,.1f}Cr" if abs(value) >= 1e7 else f"Rs {value:,.0f}"
    if name == "fin_eps":
        return f"Rs {value:.2f}"
    if name in ("recent_negative_catalyst_flag_30d", "recent_positive_catalyst_flag_30d"):
        return "yes" if value else "no"
    return f"{value:.3g}"


FIN_SCOPE_FEATURES = {"fin_sales", "fin_net_profit", "fin_opm_pct", "fin_eps"}


def explain_feature(name: str, value, shap_val: float, fin_result_type: str = None) -> str:
    label = FEATURE_LABELS.get(name, name)
    val_str = format_feature_value(name, value)
    direction = "supporting" if shap_val > 0 else "weighing against"
    caveat = ""
    if name == "fin_days_since_disclosure" and value is not None and not pd.isna(value) and value > 400:
        # Confirmed 2026-07-20 (see README changelog): NOT just "this
        # quarter's disclosure wasn't matched yet" -- for a meaningful slice
        # of the universe (127/498 symbols >180d stale, 46/498 with zero
        # confirmed disclosure ever, checked directly), the single
        # confirmed disclosure_date a symbol has can be PERMANENTLY stuck
        # years in the past (e.g. FORTIS/BHARATFORG: last confirmed
        # disclosure is from mid-2023) while multiple real, more recent
        # quarters sit in financial_results with disclosure_date=NULL --
        # this isn't noise that improves next week, it's a standing gap for
        # that symbol until the underlying corporate_announcements-matching
        # is fixed. Treat every fin_*/bs_*/cf_*/ratio_* figure on this
        # stock as potentially outdated, not just "less fresh."
        caveat = " [STALE: this symbol's fundamentals may be frozen years in the past -- verify all financial figures below against a primary source before trusting them]"
    scope = ""
    if name in FIN_SCOPE_FEATURES and fin_result_type:
        # assemble_feature_matrix.py picks whichever of STANDALONE/
        # CONSOLIDATED happens to sort last among same-disclosure_date rows
        # (confirmed 2026-07-20: 89% of confirmed-disclosure rows have both
        # scopes filed on the same date, and the tie-break has no explicit
        # preference -- NOT a genuine "most recent" choice despite the
        # schema comment's wording). Surfaced here so the scope is at least
        # visible, not silently ambiguous -- does not fix the underlying
        # non-determinism.
        scope = f" [{fin_result_type.lower()}]"
    return f"{label}{scope}: {val_str} ({direction}, SHAP={shap_val:+.4f}){caveat}"


def load_universe(conn, scoring_date: str) -> tuple:
    """Returns (eligible_symbols: list, exclusions: dict) -- today's
    index_membership snapshot, filtered by surveillance_flags and a
    liquidity floor. Exclusion counts by reason, not silent."""
    latest_snapshot = conn.execute(
        "SELECT MAX(snapshot_date) FROM index_membership").fetchone()[0]
    if latest_snapshot is None:
        raise RuntimeError("index_membership is empty -- run fetch_index_membership.py first.")
    universe = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM index_membership WHERE snapshot_date = ?", (latest_snapshot,)
    ).fetchall()]
    today = datetime.now().strftime("%Y-%m-%d")

    flags = pd.read_sql_query("SELECT symbol, start_date, end_date FROM surveillance_flags", conn)
    surveilled = set()
    for _, row in flags.iterrows():
        if row["start_date"] <= today and (pd.isna(row["end_date"]) or row["end_date"] >= today):
            surveilled.add(row["symbol"])

    n_surveilled = len([s for s in universe if s in surveilled])
    universe = [s for s in universe if s not in surveilled]

    liquidity = pd.read_sql_query(
        "SELECT symbol, avg_traded_value_20d FROM daily_prices WHERE date = ?",
        conn, params=(scoring_date,))
    liquidity = liquidity[liquidity["symbol"].isin(universe)].dropna(subset=["avg_traded_value_20d"])

    n_no_data = len(universe) - len(liquidity)
    if len(liquidity) > 0:
        floor = liquidity["avg_traded_value_20d"].quantile(LIQUIDITY_PERCENTILE_FLOOR)
        liquid_symbols = set(liquidity[liquidity["avg_traded_value_20d"] >= floor]["symbol"])
    else:
        liquid_symbols = set()
    n_illiquid = len(liquidity) - len(liquid_symbols)

    eligible = [s for s in universe if s in liquid_symbols]
    exclusions = {
        "index_membership_snapshot_date": latest_snapshot,
        "total_in_snapshot": len(universe) + n_surveilled,
        "excluded_surveillance": n_surveilled,
        "excluded_no_recent_price_data": n_no_data,
        "excluded_illiquid_bottom_decile": n_illiquid,
        "eligible": len(eligible),
    }
    return eligible, exclusions


def train_production_model(conn, feature_df: pd.DataFrame, label_df: pd.DataFrame,
                            flag_col: str, embargo_days: int):
    """Trains on ALL labeled history except a tail reserved for isotonic
    calibration (see module docstring). Returns (clf, isotonic_calibrator,
    calib_auc_raw)."""
    labeled = label_df.dropna(subset=[flag_col]).copy()
    dates = sorted(labeled["date"].unique())
    synthetic_fold = {"fold": 0, "train_start": dates[0], "train_end": dates[-1]}
    split = add_calibration_split(synthetic_fold, dates, embargo_days=embargo_days, calib_days=CALIB_DAYS)

    model_train = labeled[(labeled["date"] >= split["model_train_start"]) & (labeled["date"] <= split["model_train_end"])]
    model_train = model_train.merge(feature_df, on=["symbol", "date"], how="inner")
    calib = labeled[(labeled["date"] >= split["calib_start"]) & (labeled["date"] <= split["calib_end"])]
    calib = calib.merge(feature_df, on=["symbol", "date"], how="inner")

    X_train, y_train = model_train[ALL_FEATURE_COLUMNS], model_train[flag_col].astype(int)
    clf = lgb.LGBMClassifier(**LGB_PARAMS)
    clf.fit(X_train, y_train)

    X_calib, y_calib = calib[ALL_FEATURE_COLUMNS], calib[flag_col].astype(int)
    raw_prob_calib = clf.predict_proba(X_calib)[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_prob_calib, y_calib.values)

    from sklearn.metrics import roc_auc_score
    calib_auc = roc_auc_score(y_calib, raw_prob_calib) if len(set(y_calib)) > 1 else None

    return clf, iso, split, calib_auc


def regime_context(conn, as_of_date: str) -> dict:
    row = conn.execute(
        "SELECT india_vix_close, nifty50_dist_50dma_pct, nifty50_return_10d "
        "FROM macro_regime_indicators WHERE date <= ? ORDER BY date DESC LIMIT 1", (as_of_date,)
    ).fetchone()
    vix, dist_50dma, return_10d = row if row else (None, None, None)

    hist = conn.execute(
        "SELECT india_vix_close FROM macro_regime_indicators WHERE date <= ? ORDER BY date DESC LIMIT 252",
        (as_of_date,)).fetchall()
    hist_vix = [r[0] for r in hist if r[0] is not None]
    vix_percentile = (sum(1 for v in hist_vix if v <= vix) / len(hist_vix)) if (hist_vix and vix is not None) else None

    flags = []
    if vix_percentile is not None and vix_percentile >= 0.85:
        flags.append(f"ELEVATED VIX regime (current {vix:.1f} is in the {vix_percentile*100:.0f}th percentile "
                      f"of the trailing year) -- historically a weaker-signal/higher-uncertainty regime for "
                      f"this model, per repeated SHAP/backtest findings.")
    if dist_50dma is not None and dist_50dma < 0:
        flags.append(f"Market below its 50-day average ({dist_50dma:+.2f}%) -- the same regime signature "
                      f"found to precede the worst Part B backtest drawdown (next_phase_plan.md Section 5).")

    return {"as_of_date": as_of_date, "india_vix_close": vix, "vix_percentile_1y": vix_percentile,
            "nifty50_dist_50dma_pct": dist_50dma, "nifty50_return_10d": return_10d,
            "flags": flags, "note": "informational only -- does not filter or block the shortlist"}


def build_shortlist(conn, feature_df, label_df, horizon, eligible_symbols, scoring_date, top_n):
    clf, iso, split, calib_auc = train_production_model(
        conn, feature_df, label_df, horizon["flag_col"], horizon["embargo_days"])

    day_features = feature_df[(feature_df["date"] == scoring_date) & (feature_df["symbol"].isin(eligible_symbols))].copy()
    day_features = day_features.dropna(subset=ALL_FEATURE_COLUMNS, how="all")
    if day_features.empty:
        return [], clf, iso, split, calib_auc

    X = day_features[ALL_FEATURE_COLUMNS]
    raw_prob = clf.predict_proba(X)[:, 1]
    calibrated_prob = iso.predict(raw_prob)
    day_features["raw_prob"] = raw_prob
    day_features["calibrated_prob"] = calibrated_prob
    ranked = day_features.sort_values("calibrated_prob", ascending=False).head(top_n).reset_index(drop=True)

    explainer = shap.TreeExplainer(clf)
    shap_values = explainer.shap_values(ranked[ALL_FEATURE_COLUMNS])

    # fin_result_type isn't a model feature (ALL_FEATURE_COLUMNS deliberately
    # excludes it -- see data_loader.py), just display metadata for the
    # standalone/consolidated label on fin_* explanation lines (see
    # explain_feature() -- assemble_feature_matrix.py's choice between the
    # two is an undocumented tie-break, not a real preference, confirmed
    # 2026-07-20).
    result_type_lookup = dict(pd.read_sql_query(
        "SELECT symbol, fin_result_type FROM model_feature_matrix WHERE date = ? AND symbol IN ({})".format(
            ",".join("?" * len(ranked))),
        conn, params=[scoring_date] + ranked["symbol"].tolist(),
    ).values)

    shortlist = []
    for i, row in ranked.iterrows():
        sv = shap_values[i]
        contributions = sorted(zip(ALL_FEATURE_COLUMNS, sv, row[ALL_FEATURE_COLUMNS].values),
                                key=lambda t: -abs(t[1]))[:5]
        fin_result_type = result_type_lookup.get(row["symbol"])
        shortlist.append({
            "rank": i + 1, "symbol": row["symbol"],
            "raw_prob": float(row["raw_prob"]), "calibrated_prob": float(row["calibrated_prob"]),
            "top_5_explanations": [explain_feature(name, val, shap_val, fin_result_type)
                                    for name, shap_val, val in contributions],
            "top_5_raw": [{"feature": name, "shap_value": float(shap_val), "value": None if pd.isna(val) else float(val)}
                          for name, shap_val, val in contributions],
        })
    return shortlist, clf, iso, split, calib_auc


def write_human_readable(path: Path, horizon_label: str, scoring_date: str, exclusions: dict,
                          regime: dict, shortlist: list, calib_auc):
    lines = [f"# Weekly Shortlist -- {horizon_label} horizon", f"", f"Scoring date: {scoring_date}", ""]
    lines.append("## Universe")
    lines.append(f"- Index snapshot: {exclusions['index_membership_snapshot_date']} "
                 f"({exclusions['total_in_snapshot']} names)")
    lines.append(f"- Excluded (surveillance/ASM-GSM): {exclusions['excluded_surveillance']}")
    lines.append(f"- Excluded (no recent price data): {exclusions['excluded_no_recent_price_data']}")
    lines.append(f"- Excluded (illiquid, bottom {LIQUIDITY_PERCENTILE_FLOOR:.0%}): {exclusions['excluded_illiquid_bottom_decile']}")
    lines.append(f"- **Eligible universe: {exclusions['eligible']}**")
    lines.append("")
    lines.append("## Regime context (informational only, does not filter the shortlist)")
    lines.append(f"- India VIX: {regime['india_vix_close']:.1f}"
                 + (f" ({regime['vix_percentile_1y']*100:.0f}th percentile of trailing year)" if regime['vix_percentile_1y'] is not None else ""))
    lines.append(f"- Nifty distance from 50-day average: {regime['nifty50_dist_50dma_pct']:+.2f}%")
    lines.append(f"- Nifty 10-day return: {regime['nifty50_return_10d']:+.2f}%")
    if regime["flags"]:
        for f in regime["flags"]:
            lines.append(f"- ⚠ {f}")
    else:
        lines.append("- No regime flags triggered.")
    if calib_auc is not None:
        lines.append("")
        lines.append(f"Calibration-slice raw AUC: {calib_auc:.4f} "
                     f"(informational -- if near 0.5, isotonic will correctly compress predictions "
                     f"toward the base rate rather than manufacture false confidence)")
    lines.append("")
    lines.append("## Shortlist")
    for s in shortlist:
        lines.append(f"\n### {s['rank']}. {s['symbol']}")
        lines.append(f"Calibrated probability: **{s['calibrated_prob']:.1%}** (raw model output: {s['raw_prob']:.1%})")
        lines.append("Top contributing factors:")
        for e in s["top_5_explanations"]:
            lines.append(f"  - {e}")
    path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", choices=["14d", "30d"], help="omit for both")
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()

    conn = get_conn()
    horizons = [h for h in HORIZONS if args.horizon is None or h["label"] == args.horizon]

    print("Loading feature frame (full universe)...")
    feature_df = load_feature_frame(conn)
    label_df = pd.read_sql_query(
        "SELECT symbol, date, outperform_14d_flag, outperform_30d_flag FROM model_target_labels", conn)

    # Deliberately NOT MAX(date) FROM daily_prices alone -- a stray partial
    # fetch can leave a trailing date with only a handful of symbols (found
    # live 2026-07-20: 2026-07-16 had 81/500 symbols, NOT present in
    # macro_regime_indicators, the project's canonical trading calendar --
    # see module docstring / point-in-time discipline in CLAUDE.md). Scoring
    # date must be the latest date that's both a confirmed real trading day
    # AND has broad daily_prices coverage, not just the raw table max.
    scoring_date = conn.execute(
        "SELECT MAX(date) FROM daily_prices WHERE date IN (SELECT date FROM macro_regime_indicators)"
    ).fetchone()[0]
    n_on_scoring_date = conn.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE date = ?", (scoring_date,)).fetchone()[0]
    print(f"Scoring date (latest confirmed trading day with broad feature coverage): "
          f"{scoring_date} ({n_on_scoring_date} symbols)")
    stray_max = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()[0]
    if stray_max != scoring_date:
        n_stray = conn.execute("SELECT COUNT(*) FROM daily_prices WHERE date = ?", (stray_max,)).fetchone()[0]
        print(f"  NOTE: daily_prices has a later date ({stray_max}, {n_stray} symbols) not in "
              f"macro_regime_indicators -- likely a stray/partial fetch, ignored for scoring. "
              f"Investigate before the next run if this persists.")

    eligible_symbols, exclusions = load_universe(conn, scoring_date)
    print(f"Universe: {exclusions['total_in_snapshot']} in snapshot -> {exclusions['eligible']} eligible "
          f"(excluded: {exclusions['excluded_surveillance']} surveillance, "
          f"{exclusions['excluded_no_recent_price_data']} no data, "
          f"{exclusions['excluded_illiquid_bottom_decile']} illiquid)")

    regime = regime_context(conn, scoring_date)
    print(f"Regime: VIX={regime['india_vix_close']:.1f} dist_50dma={regime['nifty50_dist_50dma_pct']:+.2f}% "
          f"return_10d={regime['nifty50_return_10d']:+.2f}%")
    for f in regime["flags"]:
        print(f"  FLAG: {f}")

    out_dir = Path(__file__).resolve().parent.parent / "models" / "shortlists"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_date = datetime.now().strftime("%Y%m%d")

    archive_payload = {"scoring_date": scoring_date, "universe": exclusions, "regime": regime, "horizons": {}}

    for horizon in horizons:
        print(f"\n{'='*100}\nHORIZON: {horizon['label']}\n{'='*100}")
        shortlist, clf, iso, split, calib_auc = build_shortlist(
            conn, feature_df, label_df, horizon, eligible_symbols, scoring_date, args.top_n)
        print(f"  model_train [{split['model_train_start']}..{split['model_train_end']}] "
              f"calib [{split['calib_start']}..{split['calib_end']}] (calib AUC={calib_auc})")
        for s in shortlist[:5]:
            print(f"  {s['rank']}. {s['symbol']}: calibrated_prob={s['calibrated_prob']:.1%}")

        json_path = out_dir / f"shortlist_{horizon['label']}_{run_date}.json"
        json_path.write_text(json.dumps({
            "horizon": horizon["label"], "scoring_date": scoring_date,
            "model_train_start": split["model_train_start"], "model_train_end": split["model_train_end"],
            "calib_start": split["calib_start"], "calib_end": split["calib_end"], "calib_auc_raw": calib_auc,
            "universe": exclusions, "regime": regime, "shortlist": shortlist,
        }, indent=2, default=str))
        print(f"  Machine-readable output: {json_path}")

        md_path = out_dir / f"shortlist_{horizon['label']}_{run_date}.md"
        write_human_readable(md_path, horizon["label"], scoring_date, exclusions, regime, shortlist, calib_auc)
        print(f"  Human-readable output: {md_path}")

        archive_payload["horizons"][horizon["label"]] = {
            "model_train_start": split["model_train_start"], "model_train_end": split["model_train_end"],
            "calib_start": split["calib_start"], "calib_end": split["calib_end"], "calib_auc_raw": calib_auc,
            "top_10_shortlist": [{"rank": s["rank"], "symbol": s["symbol"], "calibrated_prob": s["calibrated_prob"]}
                                  for s in shortlist[:10]],
        }

    write_archive_summary("weekly_shortlist", archive_payload)
    print("\nDone.")


if __name__ == "__main__":
    main()
