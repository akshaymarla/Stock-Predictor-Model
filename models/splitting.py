"""
Walk-forward, embargoed/purged cross-validation splitting utility.

THE core leakage risk this exists to prevent (model_build_spec.md Section 4):
the label for date t is "did the stock beat Nifty over the next N days"
(N = 14 or 30). label(t) and label(t+1) share N-1 of their N forward-
looking days -- adjacent rows' labels are highly correlated, not
independent. A naive chronological split ("train before date X, test
after") looks clean by date but isn't clean by information content: rows
just before the split have label windows extending PAST the split, so
test-period information effectively leaks backward into training labels.

THE FIX: leave a gap of at least N trading days (the label horizon, not
calendar days -- matches how compute_target_labels.py itself counts
forward windows) between the end of any training slice and the start of
the following test slice.

    [-------- train --------][ embargo >= N trading days ][-------- test --------]

Both baselines (models/train_baselines.py) and the real LightGBM models
(models/train_lightgbm.py) MUST call make_walk_forward_folds() with the
same arguments for a given horizon -- a separately-reimplemented split
could subtly differ and produce a misleadingly favorable comparison
between them (model_build_spec.md Section 6: "an unfair comparison is
worse than no comparison").
"""


def make_walk_forward_folds(dates: list, embargo_days: int, n_folds: int = 5,
                             min_train_frac: float = 0.4, expanding: bool = True) -> list:
    """
    dates: sorted list of ALL distinct trading dates present in the labeled
        dataset (as strings, e.g. from model_target_labels).
    embargo_days: gap length in TRADING days between the end of train and
        the start of test. Must be >= the label horizon (14 or 30) being
        evaluated -- the caller is responsible for passing the right value
        per horizon, this function doesn't know which label it's for.
    n_folds: number of walk-forward folds to produce.
    min_train_frac: fraction of the full date range reserved for the FIRST
        fold's training window before any folds are carved out of the
        remainder -- keeps the earliest fold from training on too little
        data to be meaningful.
    expanding: True (default) = each fold's training window starts from
        dates[0] and grows (expanding window). False = rolling window,
        training window is the same fixed size as fold 1's, sliding
        forward each fold.

    Returns a list of fold dicts, each:
        {'fold': i, 'train_start', 'train_end', 'embargo_start', 'embargo_end',
         'test_start', 'test_end'}
    all as date strings (inclusive boundaries). Test windows are
    consecutive and non-overlapping across folds.
    """
    n = len(dates)
    if n < 50:
        raise ValueError(f"only {n} distinct dates -- too few to build meaningful folds")

    min_train_days = int(n * min_train_frac)
    remaining = n - min_train_days
    # remaining days must cover n_folds * (embargo + test_window) -- solve for
    # test_window_days such that this fits, giving each fold an equal-size
    # test window.
    per_fold = remaining // n_folds
    test_window_days = per_fold - embargo_days
    if test_window_days < 5:
        raise ValueError(
            f"not enough data for {n_folds} folds with a {embargo_days}-day embargo: "
            f"only {test_window_days} days would be left per test window (need >= 5). "
            f"Reduce n_folds, reduce min_train_frac, or get more data."
        )

    folds = []
    for i in range(n_folds):
        test_start_idx = min_train_days + i * per_fold + embargo_days
        test_end_idx = test_start_idx + test_window_days - 1
        embargo_start_idx = min_train_days + i * per_fold
        embargo_end_idx = test_start_idx - 1

        train_start_idx = 0 if expanding else max(0, embargo_start_idx - min_train_days)
        train_end_idx = embargo_start_idx - 1

        folds.append({
            "fold": i + 1,
            "train_start": dates[train_start_idx],
            "train_end": dates[train_end_idx],
            "embargo_start": dates[embargo_start_idx],
            "embargo_end": dates[embargo_end_idx],
            "test_start": dates[test_start_idx],
            "test_end": dates[test_end_idx],
        })
    return folds


def assign_split(date: str, fold: dict) -> str:
    """'train', 'embargo', 'test', or None (outside this fold's range entirely)."""
    if fold["train_start"] <= date <= fold["train_end"]:
        return "train"
    if fold["embargo_start"] <= date <= fold["embargo_end"]:
        return "embargo"
    if fold["test_start"] <= date <= fold["test_end"]:
        return "test"
    return None


def add_calibration_split(fold: dict, dates: list, embargo_days: int, calib_days: int = 60) -> dict:
    """Further divides an existing fold's train window into
    model_train + a SECOND embargo + a calibration hold-out slice, for
    fitting a calibration correction (Platt/isotonic, model_build_spec.md
    Section 7) on data the underlying model never trained on -- same
    leakage discipline as the original train/test embargo (Section 4), now
    applied a second time: the calibration slice's labels would otherwise
    overlap with the tail of the model-fit training data the same way
    test labels would.

    The calibration slice occupies the TAIL of the original train window
    (ending exactly at the original train_end), with model_train shrunk to
    make room for it plus its own embargo. The original embargo/test
    boundaries are UNCHANGED -- results stay comparable to a fold built by
    make_walk_forward_folds() alone.

        [--- model_train ---][embargo][-- calib --][embargo][-- test --]
                                       (unchanged: fold's embargo/test)

    Returns fold's original keys plus: model_train_start, model_train_end,
    calib_embargo_start, calib_embargo_end, calib_start, calib_end.
    """
    idx = {d: i for i, d in enumerate(dates)}
    calib_end_idx = idx[fold["train_end"]]
    calib_start_idx = calib_end_idx - calib_days + 1
    calib_embargo_end_idx = calib_start_idx - 1
    calib_embargo_start_idx = calib_embargo_end_idx - embargo_days + 1
    model_train_end_idx = calib_embargo_start_idx - 1
    model_train_start_idx = idx[fold["train_start"]]

    if model_train_start_idx > model_train_end_idx:
        raise ValueError(
            f"fold {fold.get('fold')}: not enough training data to carve out a "
            f"{calib_days}-day calibration slice + {embargo_days}-day embargo on top "
            f"of the existing train window -- reduce calib_days or use a longer "
            f"rolling/expanding window."
        )

    return {
        **fold,
        "model_train_start": dates[model_train_start_idx],
        "model_train_end": dates[model_train_end_idx],
        "calib_embargo_start": dates[calib_embargo_start_idx],
        "calib_embargo_end": dates[calib_embargo_end_idx],
        "calib_start": dates[calib_start_idx],
        "calib_end": dates[calib_end_idx],
    }
