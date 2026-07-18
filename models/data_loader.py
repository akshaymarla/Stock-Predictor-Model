"""
Loads and joins the training dataset for both model_14d and model_30d:
price/volume momentum (computed fresh from daily_prices), context features
(from model_feature_matrix), and labels (from model_target_labels).

FEATURE SET DECISIONS (model_build_spec.md Section 9 -- "pull from what's
already built, exclude what's not ready", flag availability explicitly):

- sector_* columns from model_feature_matrix are EXCLUDED entirely. They
  are 0%/NULL for every row in the currently available training history
  (sector_membership only has today's snapshot -- see schema.sql's note on
  model_feature_matrix, confirmed 2026-07-16). Including a column that's
  constant/NULL across 100% of training data adds noise, not signal.
  Revisit once sector_membership has accumulated enough historical
  snapshots to matter.
- Momentum features (return_5d/10d/20d, volatility_20d, volume_ratio_20d)
  are NOT in model_feature_matrix (it only has raw close/volume LEVELS,
  which aren't directly comparable across stocks or useful to a model
  without normalization) -- computed fresh here instead, using the same
  trading-day-lookback approach as compute_target_labels.py and
  fetch_macro_sector.py (via each symbol's own daily_prices series, not
  calendar-day arithmetic).
- Fundamentals (fin_*/bs_*/cf_*/ratio_*/sh_*) are included as-is from
  model_feature_matrix, NULLs and all -- LightGBM handles missing values
  natively (model_build_spec.md Section 2), and their real coverage rates
  (~52% fundamentals, ~95% shareholding, confirmed 2026-07-16) are exactly
  the kind of structural missingness that reasoning was written for.
- surveillance_flags exclusion filter (Section 9: ASM/GSM-flagged stocks
  excluded from the universe, not used as a feature) is implemented
  point-in-time-correctly (symbol under an active flag as of `date`), but
  is currently near-inert: surveillance_flags only has data from
  2026-07-13/14 (confirmed live) -- there is no historical ASM/GSM record
  to exclude against for the vast majority of the training window. This
  will start actually filtering once more days of surveillance data
  accumulate. Not silently hidden -- see the coverage note printed by
  load_training_data() and included in every training report.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from db import get_conn  # noqa: E402

FEATURE_COLUMNS_CONTEXT = [
    "fin_days_since_disclosure", "fin_sales", "fin_net_profit", "fin_opm_pct", "fin_eps",
    "bs_total_assets", "bs_borrowings",
    "cf_net_cash_flow",
    "ratio_roce_pct",
    "sh_promoter_pct", "sh_public_pct",
    "recent_order_dispute_flag_30d",
    "nifty50_return_5d", "nifty50_return_10d", "nifty50_dist_50dma_pct",
    "india_vix_close", "vix_change_5d_pts", "vix_change_5d_pct",
]

MOMENTUM_COLUMNS = [
    "return_5d", "return_10d", "return_20d", "volatility_20d",
    "volume_ratio_20d", "delivery_pct",
]

ALL_FEATURE_COLUMNS = MOMENTUM_COLUMNS + FEATURE_COLUMNS_CONTEXT


def _compute_momentum(prices: pd.DataFrame) -> pd.DataFrame:
    """prices: columns symbol, date, close, volume, delivery_pct, sorted by
    symbol, date. Adds return_Nd/volatility_20d/volume_ratio_20d, all
    computed per-symbol over TRADING days (positional, via groupby+shift/
    rolling on each symbol's own row order) -- consistent with how every
    other rolling feature in this project is defined."""
    prices = prices.sort_values(["symbol", "date"]).copy()
    g = prices.groupby("symbol")["close"]
    for n in (5, 10, 20):
        prices[f"return_{n}d"] = g.pct_change(periods=n) * 100

    daily_ret = g.pct_change()
    prices["volatility_20d"] = (
        daily_ret.groupby(prices["symbol"]).rolling(window=20, min_periods=10).std()
        .reset_index(level=0, drop=True) * 100
    )

    vol_g = prices.groupby("symbol")["volume"]
    rolling_avg_volume = vol_g.transform(lambda s: s.rolling(window=20, min_periods=5).mean())
    prices["volume_ratio_20d"] = prices["volume"] / rolling_avg_volume

    return prices


def load_training_data(conn, symbols: list = None) -> pd.DataFrame:
    """Returns one row per (symbol, date) with all momentum + context
    features and both label horizons, restricted to rows where at least
    one label horizon is available (model_target_labels' own domain)."""
    symbol_filter = ""
    params = []
    if symbols:
        placeholders = ",".join("?" * len(symbols))
        symbol_filter = f"AND symbol IN ({placeholders})"
        params = list(symbols)

    prices = pd.read_sql_query(
        f"SELECT symbol, date, close, volume, delivery_pct FROM daily_prices "
        f"WHERE 1=1 {symbol_filter} ORDER BY symbol, date", conn, params=params,
    )
    prices = _compute_momentum(prices)

    context_cols = ", ".join(["symbol", "date"] + FEATURE_COLUMNS_CONTEXT)
    context = pd.read_sql_query(
        f"SELECT {context_cols} FROM model_feature_matrix WHERE 1=1 {symbol_filter}",
        conn, params=params,
    )

    labels = pd.read_sql_query(
        f"SELECT symbol, date, alpha_14d, outperform_14d_flag, "
        f"alpha_30d, outperform_30d_flag FROM model_target_labels WHERE 1=1 {symbol_filter}",
        conn, params=params,
    )

    df = labels.merge(prices[["symbol", "date"] + MOMENTUM_COLUMNS], on=["symbol", "date"], how="left")
    df = df.merge(context, on=["symbol", "date"], how="left")

    df = apply_surveillance_exclusion(conn, df)
    return df


def apply_surveillance_exclusion(conn, df: pd.DataFrame) -> pd.DataFrame:
    """Drops (symbol, date) rows where the symbol was under an active
    ASM/GSM flag as of `date` (start_date <= date AND (end_date IS NULL OR
    end_date >= date)). See module docstring -- currently near-inert given
    surveillance_flags' limited historical coverage, implemented correctly
    for when that coverage grows."""
    flags = pd.read_sql_query(
        "SELECT symbol, start_date, end_date FROM surveillance_flags", conn,
    )
    if flags.empty:
        print("  [surveillance filter] 0 flag rows loaded -- no exclusions applied.")
        return df

    before = len(df)
    excluded_mask = pd.Series(False, index=df.index)
    for symbol, sub in flags.groupby("symbol"):
        symbol_rows = df["symbol"] == symbol
        if not symbol_rows.any():
            continue
        for _, flag in sub.iterrows():
            active = (df["date"] >= flag["start_date"]) & symbol_rows
            if pd.notna(flag["end_date"]):
                active &= df["date"] <= flag["end_date"]
            excluded_mask |= active

    df = df[~excluded_mask].copy()
    print(f"  [surveillance filter] excluded {before - len(df)} of {before} rows "
          f"({flags['symbol'].nunique()} distinct flagged symbols in surveillance_flags, "
          f"data only covers {flags['start_date'].min()} to present -- see module docstring)")
    return df


if __name__ == "__main__":
    conn = get_conn()
    df = load_training_data(conn)
    print(f"\n{len(df)} total (symbol, date) rows loaded")
    print(f"{df['symbol'].nunique()} distinct symbols")
    print(f"date range: {df['date'].min()} to {df['date'].max()}")
    print(f"\nlabel availability: 14d={df['alpha_14d'].notna().sum()}, 30d={df['alpha_30d'].notna().sum()}")
    print(f"\nfeature coverage (% non-null):")
    for col in ALL_FEATURE_COLUMNS:
        pct = 100 * df[col].notna().sum() / len(df)
        print(f"  {col}: {pct:.1f}%")
