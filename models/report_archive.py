"""
Compact JSON summary archive -- docs/reports_archive_and_shortlist_spec.md
Part A. Every major model/backtest run writes a small, fixed-schema
summary here (git-tracked, unlike the full verbose reports in
models/reports/ which stay gitignored) so analytical history survives
rebuilds instead of becoming permanently unrecoverable the moment a new
run overwrites the full report -- this already cost real information
once (a claimed connection between an early baseline finding and a later
fold's characteristics couldn't be checked because the underlying report
was gone and only a lossy summary of it survived in conversation memory).

Naming: <run_type>_<YYYYMMDD>.json, one file per run, never overwritten --
if the same run type happens twice in a day, appends a numeric suffix
rather than clobber the earlier file.
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path

ARCHIVE_DIR = Path(__file__).resolve().parent / "reports" / "archive"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def write_archive_summary(run_type: str, payload: dict, notes: str = "") -> Path:
    """payload: run-type-specific content (schema varies per run type --
    AUC/SHAP fields for model runs, Calmar/return/drawdown for backtest
    runs -- see docs/reports_archive_and_shortlist_spec.md Section 3).
    run_type/run_date/git_commit/notes are added automatically."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    run_date = datetime.now().strftime("%Y-%m-%d")
    summary = {
        "run_type": run_type,
        "run_date": run_date,
        "git_commit": _git_commit(),
        "notes": notes,
        **payload,
    }
    base_name = f"{run_type}_{run_date.replace('-', '')}"
    path = ARCHIVE_DIR / f"{base_name}.json"
    suffix = 2
    while path.exists():
        path = ARCHIVE_DIR / f"{base_name}_{suffix}.json"
        suffix += 1
    path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Archive summary written to {path}")
    return path
