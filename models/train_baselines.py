"""
Baselines for model_14d and model_30d -- model_build_spec.md Section 6.
Non-negotiable per the project's own principle: build and report these
BEFORE the real model, not after. Both use the EXACT SAME walk-forward/
embargo folds (splitting.py) the real LightGBM models will use later, so
the comparison is fair (an unfair comparison is worse than no comparison).

Two baselines:
1. NAIVE: predict the training fold's historical base rate for every test
   row. Checks empirically whether "beats Nifty" is actually close to 50/50
   -- a cap-weighted index vs. an equal-weighted stock universe means it
   plausibly is NOT, and that's worth knowing before interpreting anything
   else.
2. SIMPLE: logistic regression on price/volume MOMENTUM features only (no
   fundamentals, no announcements, no macro/sector) -- isolates how much
   the "extra" data actually contributes once the full LightGBM model adds
   it in.

Usage:
    python models/train_baselines.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from db import get_conn  # noqa: E402

from data_loader import MOMENTUM_COLUMNS, load_training_data  # noqa: E402
from evaluate import calibration_curve, evaluate_predictions, format_fold_report  # noqa: E402
from splitting import make_walk_forward_folds  # noqa: E402
from report_archive import write_archive_summary  # noqa: E402

HORIZONS = [
    {"label": "14d", "alpha_col": "alpha_14d", "flag_col": "outperform_14d_flag", "embargo_days": 14},
    {"label": "30d", "alpha_col": "alpha_30d", "flag_col": "outperform_30d_flag", "embargo_days": 30},
]


def run_naive_baseline(train_df, test_df, flag_col: str) -> tuple:
    """Returns (y_true, y_prob, train_base_rate). train_base_rate is
    returned explicitly (not just baked into y_prob) so callers can report
    it alongside the test fold's actual_rate -- conflating the two under
    one label previously made the reported precision/recall look
    internally inconsistent (found live 2026-07-16): the classification
    decision comes from comparing TRAIN base rate to the 0.5 threshold,
    not the TEST base rate a report might display next to it. Confirmed
    empirically that TRAIN base rate (an expanding window, so it averages
    out short-term swings) sits within ~1pp of 0.5 in every fold here --
    meaning the naive baseline's predicted class is essentially decided by
    noise, and precision/recall at a fixed threshold amplify that into a
    full 0-or-1 flip. AUC (mathematically exactly 0.5 for any constant
    predictor, by construction) is the metric that actually reflects this
    baseline's true zero-information nature -- treat precision/recall here
    as diagnostic, not a number to compare against the real model."""
    train_base_rate = train_df[flag_col].mean()
    y_prob = np.full(len(test_df), train_base_rate)
    return test_df[flag_col].values, y_prob, train_base_rate


def run_simple_baseline(train_df, test_df, flag_col: str) -> tuple:
    X_train = train_df[MOMENTUM_COLUMNS].values
    X_test = test_df[MOMENTUM_COLUMNS].values
    y_train = train_df[flag_col].values

    imputer = SimpleImputer(strategy="median").fit(X_train)
    X_train = imputer.transform(X_train)
    X_test = imputer.transform(X_test)

    scaler = StandardScaler().fit(X_train)
    X_train = scaler.transform(X_train)
    X_test = scaler.transform(X_test)

    clf = LogisticRegression(max_iter=1000).fit(X_train, y_train)
    y_prob = clf.predict_proba(X_test)[:, 1]
    return test_df[flag_col].values, y_prob


def main():
    conn = get_conn()
    print("Loading training data (full universe)...")
    df = load_training_data(conn)

    report = {"horizons": {}}

    for h in HORIZONS:
        label, flag_col, embargo = h["label"], h["flag_col"], h["embargo_days"]
        print(f"\n{'='*100}\nHORIZON: {label}\n{'='*100}")

        labeled = df.dropna(subset=[flag_col]).copy()
        dates = sorted(labeled["date"].unique())
        folds = make_walk_forward_folds(dates, embargo_days=embargo, n_folds=5)

        fold_results = []
        for fold in folds:
            train_df = labeled[(labeled["date"] >= fold["train_start"]) & (labeled["date"] <= fold["train_end"])]
            test_df = labeled[(labeled["date"] >= fold["test_start"]) & (labeled["date"] <= fold["test_end"])]

            # momentum features can still be NaN for very early per-symbol
            # rows (not enough trading history yet for a 20d window) --
            # drop those from the SIMPLE baseline only, naive doesn't need features
            simple_train = train_df.dropna(subset=MOMENTUM_COLUMNS)
            simple_test = test_df.dropna(subset=MOMENTUM_COLUMNS)

            y_true_naive, y_prob_naive, train_base_rate = run_naive_baseline(train_df, test_df, flag_col)
            y_true_simple, y_prob_simple = run_simple_baseline(simple_train, simple_test, flag_col)

            naive_metrics = evaluate_predictions(y_true_naive, y_prob_naive)
            naive_metrics["predicted_prob"] = float(train_base_rate)
            simple_metrics = evaluate_predictions(y_true_simple, y_prob_simple)

            print(format_fold_report(fold["fold"], label, "naive", naive_metrics))
            print(format_fold_report(fold["fold"], label, "simple_logreg", simple_metrics))
            if (train_base_rate >= 0.5) != (naive_metrics["actual_rate"] >= 0.5):
                print(f"    NOTE: train_base_rate ({train_base_rate:.4f}) and test actual_rate "
                      f"({naive_metrics['actual_rate']:.4f}) sit on OPPOSITE sides of 0.5 this fold "
                      f"-- naive's precision/recall reflect that flip, not a real signal.")

            fold_results.append({
                "fold": fold["fold"],
                "train_start": fold["train_start"], "train_end": fold["train_end"],
                "test_start": fold["test_start"], "test_end": fold["test_end"],
                "naive": naive_metrics,
                "simple_logreg": simple_metrics,
                "simple_logreg_calibration": calibration_curve(y_true_simple, y_prob_simple),
            })

        report["horizons"][label] = fold_results

    out_path = Path(__file__).resolve().parent / "reports" / "baselines_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nSaved full report to {out_path}")

    archive_payload = {"horizons": {}}
    for label, fold_results in report["horizons"].items():
        archive_payload["horizons"][label] = {"folds": [
            {
                "fold": f["fold"],
                "train_start": f["train_start"], "train_end": f["train_end"],
                "test_start": f["test_start"], "test_end": f["test_end"],
                "n_test": f["simple_logreg"]["n"],
                "actual_rate": f["naive"]["actual_rate"],
                "naive_predicted_prob": f["naive"]["predicted_prob"],
                "auc_naive": f["naive"]["auc"], "auc_simple_logreg": f["simple_logreg"]["auc"],
            }
            for f in fold_results
        ]}
    write_archive_summary("baseline", archive_payload)


if __name__ == "__main__":
    main()
