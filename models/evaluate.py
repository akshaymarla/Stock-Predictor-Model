"""
Shared evaluation utilities -- discrimination AND calibration
(model_build_spec.md Section 7: "a well-discriminating-but-badly-
calibrated model is a real problem, not a minor caveat", since the actual
deliverable is a probability someone acts on, not just a ranking).

Used identically by baselines (train_baselines.py) and the real models
(train_lightgbm.py) so every evaluation output is directly comparable.
"""
import numpy as np
from sklearn.metrics import precision_score, recall_score, roc_auc_score


def evaluate_predictions(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = 0.5) -> dict:
    """NOTE on 'actual_rate' vs the prediction itself (found live 2026-07-16,
    via a sharp catch on the naive baseline's report): this function only
    ever sees y_true/y_prob, so 'actual_rate' below is whatever fold y_true
    came from (e.g. the TEST fold for a baseline evaluated on held-out data)
    -- it is NOT necessarily the number that drove y_prob (e.g. the naive
    baseline's constant prediction comes from the TRAIN fold's rate). A
    caller reporting both must label them distinctly (see
    train_baselines.py's run_naive_baseline(), which now returns
    predicted_prob alongside actual_rate for exactly this reason) --
    conflating them under one ambiguous 'base_rate' label previously made
    the naive baseline's precision/recall look internally inconsistent
    (recall flipping 0<->1 in a way that didn't track the displayed rate,
    because the displayed rate wasn't the one driving the classification)."""
    y_pred = (y_prob >= threshold).astype(int)
    n_classes = len(set(y_true.tolist()))
    return {
        "n": int(len(y_true)),
        "actual_rate": float(np.mean(y_true)),
        "auc": float(roc_auc_score(y_true, y_prob)) if n_classes > 1 else None,
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
    }


def calibration_curve(y_true: np.ndarray, y_prob: np.ndarray, n_buckets: int = 10) -> list:
    """[{'bucket_low','bucket_high','n','predicted_mid','actual_rate'}, ...]
    -- actual_rate should track predicted_mid closely if well-calibrated;
    a model that's badly off here is misleading even with a strong AUC."""
    edges = np.linspace(0, 1, n_buckets + 1)
    rows = []
    for i in range(n_buckets):
        lo, hi = edges[i], edges[i + 1]
        mask = (y_prob >= lo) & (y_prob <= hi if i == n_buckets - 1 else y_prob < hi)
        n = int(mask.sum())
        rows.append({
            "bucket_low": float(lo), "bucket_high": float(hi), "n": n,
            "predicted_mid": float((lo + hi) / 2),
            "actual_rate": float(y_true[mask].mean()) if n > 0 else None,
        })
    return rows


def format_fold_report(fold_num: int, horizon: str, model_name: str, metrics: dict) -> str:
    auc_str = f"{metrics['auc']:.3f}" if metrics["auc"] is not None else "n/a"
    predicted_str = (f"predicted_prob={metrics['predicted_prob']:.4f} | "
                      if "predicted_prob" in metrics else "")
    return (f"  fold {fold_num} | {horizon:>3} | {model_name:<20} | "
            f"n={metrics['n']:>6} | {predicted_str}actual_rate={metrics['actual_rate']:.3f} | "
            f"AUC={auc_str} | precision={metrics['precision']:.3f} | recall={metrics['recall']:.3f}")
