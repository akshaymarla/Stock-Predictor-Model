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

CATALYST DETECTION (retired 2026-07-19, per docs/next_phase_plan.md
Section 2): recent_order_dispute_flag_30d (keyword-regex, confirmed via
SHAP to be the lowest-ranked feature in both horizons, 0.023/0.028) was
replaced by recent_negative_catalyst_flag_30d/recent_positive_catalyst_flag_30d,
sourced from a genuinely free, real classification mechanism --
src/classify_announcements_by_subject.py discovered that
corporate_announcements.subject already IS NSE's own SEBI Regulation 30
structured event-category tag (262 distinct controlled-vocabulary values,
100% populated, e.g. "Bagging/Receiving of orders/contracts", "Pendency
of Litigation(s)/dispute(s)..."), not free text needing inference -- no
LLM/keyword-classifier needed. Real run confirmed (269,056 training-
universe rows classified: 2.0% positive, 1.0% negative, 97.0% neutral),
model_feature_matrix reassembled with real (non-placeholder) flags.

**SHAP re-check result: does NOT clear a meaningfully higher bar than the
old regex.** Both new flags still occupy the bottom 1-2 slots of 32
features in both horizons (recent_negative_catalyst_flag_30d: rank 32/32
both horizons, actually WORSE than the old combined flag; recent_positive:
rank 31/32 [14d] and 30/32 [30d], only marginally better). Working theory:
these events are likely already reflected in price/volume momentum
(return_5d/return_10d) by the time the model sees them, so a categorical
flag is largely redundant. **Retired from the model's feature set**
(excluded here, same as sector_* below) per the project's standing
"don't leave a near-zero feature as unexamined dead weight" discipline --
the underlying classification (corporate_announcements.category/
sentiment) is real and kept for potential future use (e.g. category
counts, not just a boolean flag), just not fed to model_14d/30d as-is.
src/classify_announcements.py (the LLM path) remains unused, per the
project's zero-cost-first decision -- this free approach already answered
the question an LLM pass would have, at zero cost.

INSTITUTIONAL ATTENTION FEATURES (added 2026-07-19, per
docs/institutional_attention_feature.md -- the actual test of the
project's original "institutionally neglected stocks" hypothesis, since
sh_promoter_pct measures promoter/insider ownership, a different concept):
- sh_inst_total_pct/fii_fpi_pct/mutual_fund_pct (raw levels) and
  sh_inst_qoq_change_pct/yoy_change_pct (trend) are pulled from
  model_feature_matrix as-is, same as the other fundamentals.
- sh_inst_pctrank (NOT in model_feature_matrix -- computed fresh here):
  cross-sectional percentile rank of sh_inst_total_pct within the full
  Nifty 500 universe on the same date, per Section 5's "neglect is
  relative, not absolute" requirement. sector_membership-based ranking
  isn't usable yet for the same reason sector_* columns are excluded
  above (no historical snapshots), so this ranks against the full
  universe rather than sector -- same accepted limitation, revisit
  together.
- avg_traded_value_20d is now included (previously captured in
  model_feature_matrix but never exposed to the model) specifically so
  the institutional-attention features can be evaluated JOINTLY with
  liquidity, not in isolation, per Section 5's explicit requirement.
  Note: no separate hard liquidity FILTER exists anywhere in this
  pipeline (checked directly -- only surveillance_flags is an active
  row-exclusion filter); docs/institutional_attention_feature.md Section 5
  assumed one already existed and that assumption doesn't hold. Including
  avg_traded_value_20d as a joint feature (rather than inventing an
  arbitrary cutoff threshold) lets LightGBM/SHAP surface the actual
  interaction, which is more rigorous than a fixed threshold would be.
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
    "sh_inst_total_pct", "sh_inst_fii_fpi_pct", "sh_inst_mutual_fund_pct",
    "sh_inst_qoq_change_pct", "sh_inst_yoy_change_pct",
    "avg_traded_value_20d",
    "nifty50_return_5d", "nifty50_return_10d", "nifty50_dist_50dma_pct",
    "india_vix_close", "vix_change_5d_pts", "vix_change_5d_pct",
]

MOMENTUM_COLUMNS = [
    "return_5d", "return_10d", "return_20d", "volatility_20d",
    "volume_ratio_20d", "delivery_pct",
]

# computed fresh in load_training_data(), not pulled from model_feature_matrix
DERIVED_COLUMNS = ["sh_inst_pctrank"]

ALL_FEATURE_COLUMNS = MOMENTUM_COLUMNS + FEATURE_COLUMNS_CONTEXT + DERIVED_COLUMNS


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
    df = df.merge(load_institutional_pctrank(conn), on=["symbol", "date"], how="left")

    df = apply_surveillance_exclusion(conn, df)
    return df


def load_institutional_pctrank(conn) -> pd.DataFrame:
    """Cross-sectional percentile rank of sh_inst_total_pct within the
    full Nifty 500 universe on each date (institutional_attention_feature.md
    Section 5 -- "neglect" is inherently relative, not an absolute level).
    Deliberately always computed against the FULL universe, unfiltered by
    any --symbols restriction the caller applies to load_training_data --
    a rank relative to an arbitrary training subset wouldn't mean the same
    thing as a rank relative to the real universe."""
    full = pd.read_sql_query(
        "SELECT symbol, date, sh_inst_total_pct FROM model_feature_matrix", conn,
    )
    full["sh_inst_pctrank"] = full.groupby("date")["sh_inst_total_pct"].rank(pct=True)
    return full[["symbol", "date", "sh_inst_pctrank"]]


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
