"""
Trains model_14d and model_30d (LightGBM) -- docs/model_build_spec.md
Sections 2-3, 4-5, 9, build order step 4.

WINDOW STRATEGY (Section 2b) -- trains TWICE per horizon, not once:
once expanding-window, once rolling-window. Not a default choice --
the baseline run found direct evidence expanding windows average away
real regime variation (train base rate stuck within ~2.5pp of 0.5 while
test base rate swung 42.3%-58.3%), so Section 2b requires comparing both
rather than assuming expanding is fine.

Rolling window length: fixed at fold 1's expanding-window training size
(~486 trading days / ~1.93 years for 14d, ~480/~1.90 years for 30d --
confirmed via splitting.py's own fold construction, not a separately
chosen number). This is deliberate, not incidental: using fold 1's own
size means fold 1 is IDENTICAL between the two strategies (same train
window, same test window), so any divergence in fold 2-5 results isolates
the window-strategy effect itself rather than conflating it with a
different starting training-set size. ~1.9 years also lands squarely in
the doc's suggested 2-3 year range.

Both strategies use the EXACT SAME test/embargo boundaries (verified
programmatically before training starts, not assumed) -- splitting.
make_walk_forward_folds() only changes train_start based on the
`expanding` flag, everything else is identical by construction.

FEATURE SET: same ALL_FEATURE_COLUMNS as train_baselines.py's simple
baseline, PLUS the context columns (fundamentals/shareholding/macro) the
simple baseline deliberately excludes -- see data_loader.py's module
docstring for exactly what's included/excluded and why (sector_* is out,
0% coverage in current history). No imputation here, unlike the logistic
regression baseline -- LightGBM's native missing-value handling is one of
the reasons this model family was chosen (model_build_spec.md Section 2),
so NaNs are passed straight through.

Usage:
    python models/train_lightgbm.py
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from db import get_conn  # noqa: E402

from data_loader import ALL_FEATURE_COLUMNS, load_training_data  # noqa: E402
from evaluate import calibration_curve, evaluate_predictions, format_fold_report  # noqa: E402
from splitting import make_walk_forward_folds  # noqa: E402
from report_archive import write_archive_summary  # noqa: E402

HORIZONS = [
    {"label": "14d", "flag_col": "outperform_14d_flag", "embargo_days": 14},
    {"label": "30d", "flag_col": "outperform_30d_flag", "embargo_days": 30},
]

# Deliberately simple, un-tuned defaults -- model_build_spec.md build order
# step 6 explicitly defers hyperparameter tuning until AFTER this basic
# walk-forward loop with baselines is trustworthy. Not tuned yet on purpose.
LGB_PARAMS = dict(
    n_estimators=300,
    learning_rate=0.05,
    num_leaves=31,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_samples=100,
    random_state=42,
    n_jobs=-1,
    verbose=-1,
)


def train_and_eval(train_df, test_df, flag_col: str):
    X_train, y_train = train_df[ALL_FEATURE_COLUMNS], train_df[flag_col].astype(int)
    X_test, y_test = test_df[ALL_FEATURE_COLUMNS], test_df[flag_col].astype(int)

    clf = lgb.LGBMClassifier(**LGB_PARAMS)
    clf.fit(X_train, y_train)
    y_prob = clf.predict_proba(X_test)[:, 1]

    metrics = evaluate_predictions(y_test.values, y_prob)
    cal = calibration_curve(y_test.values, y_prob)
    importances = dict(zip(ALL_FEATURE_COLUMNS, [float(v) for v in clf.feature_importances_]))
    return metrics, cal, importances


def main():
    conn = get_conn()
    print("Loading training data (full universe)...")
    df = load_training_data(conn)

    report = {"horizons": {}, "rolling_window_days": {}}

    for h in HORIZONS:
        label, flag_col, embargo = h["label"], h["flag_col"], h["embargo_days"]
        print(f"\n{'='*100}\nHORIZON: {label}\n{'='*100}")

        labeled = df.dropna(subset=[flag_col]).copy()
        dates = sorted(labeled["date"].unique())

        expanding_folds = make_walk_forward_folds(dates, embargo_days=embargo, n_folds=5, expanding=True)
        rolling_folds = make_walk_forward_folds(dates, embargo_days=embargo, n_folds=5, expanding=False)

        # test/embargo boundaries MUST be identical between strategies --
        # verify, don't assume (model_build_spec.md: "identical fold/embargo
        # boundaries already validated in the baseline run").
        for ef, rf in zip(expanding_folds, rolling_folds):
            assert ef["test_start"] == rf["test_start"] and ef["test_end"] == rf["test_end"] \
                and ef["embargo_start"] == rf["embargo_start"], \
                f"fold {ef['fold']}: test/embargo boundaries diverged between strategies -- this should never happen"

        rolling_window_days = dates.index(rolling_folds[0]["train_end"]) - dates.index(rolling_folds[0]["train_start"]) + 1
        report["rolling_window_days"][label] = rolling_window_days
        print(f"Rolling window fixed at {rolling_window_days} trading days (~{rolling_window_days/252:.2f} years), "
              f"= fold 1's expanding-window training size.")

        fold_results = []
        for strategy_name, folds in [("expanding", expanding_folds), ("rolling", rolling_folds)]:
            for fold in folds:
                train_df = labeled[(labeled["date"] >= fold["train_start"]) & (labeled["date"] <= fold["train_end"])]
                test_df = labeled[(labeled["date"] >= fold["test_start"]) & (labeled["date"] <= fold["test_end"])]

                metrics, cal, importances = train_and_eval(train_df, test_df, flag_col)
                metrics["strategy"] = strategy_name
                metrics["fold"] = fold["fold"]
                metrics["train_start"] = fold["train_start"]
                metrics["train_end"] = fold["train_end"]
                metrics["train_rows"] = len(train_df)

                print(format_fold_report(fold["fold"], label, f"lightgbm_{strategy_name}", metrics))

                fold_results.append({**metrics, "calibration": cal, "feature_importances": importances})

        report["horizons"][label] = fold_results

    out_path = Path(__file__).resolve().parent / "reports" / "lightgbm_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nSaved full report to {out_path}")

    archive_payload = {"horizons": {}}
    for label, fold_results in report["horizons"].items():
        archive_payload["horizons"][label] = {"folds": [
            {
                "fold": f["fold"], "strategy": f["strategy"],
                "train_start": f["train_start"], "train_end": f["train_end"],
                "train_rows": f["train_rows"],
                "n_test": f["n"], "actual_rate": f["actual_rate"], "auc": f["auc"],
                "top_5_feature_importances_NOTE": "default split-count importance, NOT SHAP -- see shap_calibration archive for SHAP",
                "top_5_feature_importances": sorted(f["feature_importances"].items(), key=lambda x: -x[1])[:5],
            }
            for f in fold_results
        ]}
    write_archive_summary("lightgbm", archive_payload,
                           notes="rolling_window_days: " + json.dumps(report["rolling_window_days"]))


if __name__ == "__main__":
    main()
