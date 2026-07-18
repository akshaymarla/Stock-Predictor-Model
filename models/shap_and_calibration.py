"""
SHAP feature attribution + calibration correction for model_14d/model_30d,
ROLLING-WINDOW strategy only (the strategy picked in train_lightgbm.py per
model_build_spec.md Section 2b -- equal-or-better mean AUC, meaningfully
better most-recent-fold result).

TWO THINGS DONE TOGETHER, per explicit request after the LightGBM report
review:

1. SHAP values, specifically to confirm or correct the split-count-
   importance finding that sh_promoter_pct is 30d's top feature ON AVERAGE
   ACROSS FOLDS (not every fold -- fold 5 alone has fin_eps on top
   instead). This bears directly on the project's institutional-neglect
   thesis, not just model mechanics, so it gets the more rigorous check
   before anyone treats it as real. Split-count/gain importance
   (train_lightgbm.py's feature_importances_) has known biases -- can
   overweight frequently-splitting continuous features relative to their
   real predictive contribution -- SHAP (mean |SHAP value| per feature,
   from shap.TreeExplainer) doesn't share that specific bias.

2. Calibration correction (Platt scaling via 1D logistic regression, and
   isotonic regression), fit on a PROPER held-out slice -- NOT the same
   data the underlying LightGBM model trained on, and NOT the test set
   used for final evaluation either. See splitting.add_calibration_split()
   for the leakage reasoning (same discipline as the original train/test
   embargo, applied a second time within the training window). Re-checks
   the fold-5-specific miscalibration pattern found in train_lightgbm.py's
   report (fold 5 stayed under-confident even at high predicted
   probabilities, unlike folds 1-4's classic S-shape) to confirm the
   correction actually helps rather than just shifting the problem around.

NOTE on AUC: Platt scaling and isotonic regression are both MONOTONIC
transforms of a single input (the model's raw probability) -- AUC is
invariant under any monotonic transform by definition, so AUC is IDENTICAL
before and after calibration correction, always. Only the probability
VALUES change to better match actual rates; ranking/discrimination doesn't
change and isn't supposed to. Don't expect or report an AUC "improvement"
from calibration -- that would indicate a bug, not a good result.

Usage:
    python models/shap_and_calibration.py
"""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import shap
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from db import get_conn  # noqa: E402

from data_loader import ALL_FEATURE_COLUMNS, load_training_data  # noqa: E402
from evaluate import calibration_curve, evaluate_predictions  # noqa: E402
from splitting import add_calibration_split, make_walk_forward_folds  # noqa: E402
from train_lightgbm import LGB_PARAMS  # noqa: E402

HORIZONS = [
    {"label": "14d", "flag_col": "outperform_14d_flag", "embargo_days": 14},
    {"label": "30d", "flag_col": "outperform_30d_flag", "embargo_days": 30},
]
CALIB_DAYS = 60


def fit_calibrators(raw_prob_calib: np.ndarray, y_calib: np.ndarray):
    platt = LogisticRegression()
    platt.fit(raw_prob_calib.reshape(-1, 1), y_calib)

    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_prob_calib, y_calib)

    return platt, iso


def apply_platt(platt: LogisticRegression, raw_prob: np.ndarray) -> np.ndarray:
    return platt.predict_proba(raw_prob.reshape(-1, 1))[:, 1]


def run_fold(labeled, fold, flag_col: str):
    model_train = labeled[(labeled["date"] >= fold["model_train_start"]) & (labeled["date"] <= fold["model_train_end"])]
    calib = labeled[(labeled["date"] >= fold["calib_start"]) & (labeled["date"] <= fold["calib_end"])]
    test = labeled[(labeled["date"] >= fold["test_start"]) & (labeled["date"] <= fold["test_end"])]

    X_train, y_train = model_train[ALL_FEATURE_COLUMNS], model_train[flag_col].astype(int)
    X_calib, y_calib = calib[ALL_FEATURE_COLUMNS], calib[flag_col].astype(int)
    X_test, y_test = test[ALL_FEATURE_COLUMNS], test[flag_col].astype(int)

    clf = lgb.LGBMClassifier(**LGB_PARAMS)
    clf.fit(X_train, y_train)

    raw_prob_calib = clf.predict_proba(X_calib)[:, 1]
    raw_prob_test = clf.predict_proba(X_test)[:, 1]

    platt, iso = fit_calibrators(raw_prob_calib, y_calib.values)
    platt_prob_test = apply_platt(platt, raw_prob_test)
    iso_prob_test = iso.predict(raw_prob_test)

    metrics_raw = evaluate_predictions(y_test.values, raw_prob_test)
    metrics_platt = evaluate_predictions(y_test.values, platt_prob_test)
    metrics_iso = evaluate_predictions(y_test.values, iso_prob_test)

    cal_raw = calibration_curve(y_test.values, raw_prob_test)
    cal_platt = calibration_curve(y_test.values, platt_prob_test)
    cal_iso = calibration_curve(y_test.values, iso_prob_test)

    explainer = shap.TreeExplainer(clf)
    shap_values = explainer.shap_values(X_test)
    mean_abs_shap = dict(zip(ALL_FEATURE_COLUMNS, np.abs(shap_values).mean(axis=0).tolist()))

    return {
        "fold": fold["fold"],
        "model_train_start": fold["model_train_start"], "model_train_end": fold["model_train_end"],
        "calib_start": fold["calib_start"], "calib_end": fold["calib_end"],
        "test_start": fold["test_start"], "test_end": fold["test_end"],
        "n_model_train": len(model_train), "n_calib": len(calib), "n_test": len(test),
        "raw": metrics_raw, "platt": metrics_platt, "isotonic": metrics_iso,
        "calibration_raw": cal_raw, "calibration_platt": cal_platt, "calibration_isotonic": cal_iso,
        "mean_abs_shap": mean_abs_shap,
    }


def main():
    conn = get_conn()
    print("Loading training data (full universe)...")
    df = load_training_data(conn)

    report = {"horizons": {}}

    for h in HORIZONS:
        label, flag_col, embargo = h["label"], h["flag_col"], h["embargo_days"]
        print(f"\n{'='*100}\nHORIZON: {label} (rolling window)\n{'='*100}")

        labeled = df.dropna(subset=[flag_col]).copy()
        dates = sorted(labeled["date"].unique())
        rolling_folds = make_walk_forward_folds(dates, embargo_days=embargo, n_folds=5, expanding=False)

        fold_results = []
        for fold in rolling_folds:
            split_fold = add_calibration_split(fold, dates, embargo_days=embargo, calib_days=CALIB_DAYS)
            print(f"  fold {fold['fold']}: model_train [{split_fold['model_train_start']}..{split_fold['model_train_end']}] "
                  f"calib [{split_fold['calib_start']}..{split_fold['calib_end']}] "
                  f"test [{split_fold['test_start']}..{split_fold['test_end']}]")
            result = run_fold(labeled, split_fold, flag_col)
            fold_results.append(result)

            r, p, i = result["raw"], result["platt"], result["isotonic"]
            print(f"    n_train={result['n_model_train']} n_calib={result['n_calib']} n_test={result['n_test']}")
            print(f"    AUC: raw={r['auc']:.4f} platt={p['auc']:.4f} isotonic={i['auc']:.4f} (should match -- monotonic transform)")

        report["horizons"][label] = fold_results

    out_path = Path(__file__).resolve().parent / "reports" / "shap_calibration_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nSaved full report to {out_path}")


if __name__ == "__main__":
    main()
