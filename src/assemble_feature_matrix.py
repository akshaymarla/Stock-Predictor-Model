"""
Assembles the FEATURES-ONLY training matrix into `model_feature_matrix` --
Step 5 (the final step) of the macro/sector shock feature build
(macro_sector_shock_features.md).

Deliberately does NOT join in anything from model_target_labels -- labels
stay in their own table, joined in later at training time by whoever
trains the model, so future-looking data can never accidentally end up on
the feature side. See schema.sql's note on model_feature_matrix for the
full reasoning and the three bugs fixed here (all from
macro_sector_shock_features.md Section 5):
  1. sector_membership joined via "most recent snapshot_date <= date"
     (exact-date join would null out sector features for almost every row
     -- snapshots are sparse, same reasoning as index_membership).
  2. "Last known financials as of date" via proper ranking (most recent
     disclosure_date <= date), not a bare-column MAX() -- MAX() only
     happens to work in SQLite specifically, breaks on Postgres.
  3. Announcement-derived features check BOTH subject AND details.

POINT-IN-TIME NOTE: fundamentals/shareholding features only use rows with
disclosure_date IS NOT NULL -- financial_results/balance_sheet/cash_flow/
ratios/shareholding_pattern can have NULL disclosure_date (period known,
but no confirmed disclosure date within the SEBI window -- see
screener_common.find_disclosure()), which is correctly unusable as a
point-in-time join key.

TWO MORE BUGS FIXED HERE (2026-07-20, docs/next_phase_plan.md Section 0c
-- found while verifying real weekly_shortlist.py output against actual
financials, FORTIS's net_profit off by ~20x):

  4. STANDALONE/CONSOLIDATED tie-break was arbitrary. financial_results,
     balance_sheet, cash_flow, and ratios all carry a `result_type`
     column, and 89% of confirmed-disclosure (symbol, disclosure_date)
     groups in financial_results have BOTH scopes filed on the same date
     (real filings always report both together) -- the old
     `load_disclosure_series()` had no secondary ORDER BY key, so
     `most_recent_as_of()`'s bisect picked whichever row SQLite happened
     to return last for a tied date, an accident of physical row order,
     not the "most recent" schema.sql's comment implied. Now explicitly
     orders CONSOLIDATED after STANDALONE on tied dates so bisect's
     tie-break is deterministic and picks CONSOLIDATED -- the more
     complete figure, and what a human would actually cross-check against
     news coverage (confirmed via FORTIS: consolidated net profit is the
     figure multiple outlets report, not standalone).
  5. Stale disclosures were used as if current, with no cutoff. FORTIS
     and BHARATFORG were both frozen on a single confirmed disclosure
     from 2023 while real, more recent quarters sat unmatched
     (disclosure_date IS NULL) in the same table -- 46/498 symbols have
     zero confirmed disclosures ever, 127/498 are >180 days stale.
     `most_recent_as_of()` now takes `max_staleness_days` and returns None
     (not a stale row) once the gap between the as-of date and the
     matched disclosure exceeds it -- applied uniformly to every
     disclosure-based lookup (fin/bs/cf/ratio/sh/institutional), not just
     financial_results, since they all share this exact same join
     pattern and are equally exposed. See MAX_DISCLOSURE_STALENESS_DAYS
     below for the specific cutoff and reasoning.

Multi-sector membership (a stock can legitimately be in more than one
sectoral index, e.g. a large private bank in both 'Nifty Bank' and
'Nifty Private Bank') is handled by AVERAGING sector_daily_benchmarks
across every sector_name the symbol belongs to as of `date` -- an
explicit, documented policy, not an arbitrary first-row pick.

Feature domain: every (symbol, date) already in daily_prices (not
restricted to dates with a label -- this keeps the table usable for live
inference too, not just historical training).

Usage:
    python src/assemble_feature_matrix.py              # full universe
    python src/assemble_feature_matrix.py --symbols RELIANCE TCS
"""
import argparse
import bisect
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from db import get_conn


def load_symbols(conn, symbols_arg) -> list:
    if symbols_arg:
        return symbols_arg
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM daily_prices ORDER BY symbol").fetchall()]


def load_macro(conn) -> dict:
    rows = conn.execute(
        "SELECT date, nifty50_close, nifty50_return_5d, nifty50_return_10d, "
        "nifty50_dist_50dma_pct, india_vix_close, vix_change_5d_pts, vix_change_5d_pct "
        "FROM macro_regime_indicators"
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def load_sector_benchmarks(conn) -> dict:
    """{(sector_name, date): (return_3d, return_5d, return_14d, relative_alpha_14d)}"""
    rows = conn.execute(
        "SELECT sector_name, date, sector_return_3d, sector_return_5d, "
        "sector_return_14d, sector_relative_alpha_14d FROM sector_daily_benchmarks"
    ).fetchall()
    return {(r[0], r[1]): r[2:] for r in rows}


def load_sector_membership_by_symbol(conn) -> dict:
    """{symbol: sorted [(snapshot_date, [sector_name, ...]), ...]}"""
    rows = conn.execute(
        "SELECT symbol, sector_name, snapshot_date FROM sector_membership "
        "ORDER BY symbol, snapshot_date"
    ).fetchall()
    by_symbol_date = defaultdict(lambda: defaultdict(list))
    for symbol, sector_name, snapshot_date in rows:
        by_symbol_date[symbol][snapshot_date].append(sector_name)
    result = {}
    for symbol, date_map in by_symbol_date.items():
        result[symbol] = sorted(date_map.items())  # [(date, [sectors]), ...]
    return result


# See module docstring, bug 5. ~2.67 quarters -- generous enough that one
# genuinely late filing doesn't flap between "current" and "stale", tight
# enough to catch a disclosure truly stuck multiple quarters back (the
# FORTIS/BHARATFORG failure mode was 1000+ days). Applied uniformly to
# every disclosure-based join (fin/bs/cf/ratio/sh/institutional) rather
# than tuned per-table -- they're all quarterly-cadence SEBI disclosures,
# a single shared cutoff is more inspectable than five silently-different
# ones.
MAX_DISCLOSURE_STALENESS_DAYS = 240

# Tables whose rows can collide on the same disclosure_date (STANDALONE +
# CONSOLIDATED filed together for the same event) -- see module docstring,
# bug 4. shareholding_pattern has no result_type column, not affected.
HAS_RESULT_TYPE = {"financial_results", "balance_sheet", "cash_flow", "ratios"}


def load_disclosure_series(conn, table: str, columns: list, symbol: str) -> list:
    """Rows with disclosure_date IS NOT NULL for one symbol, sorted by
    disclosure_date -- the only safe join key (see module docstring). For
    tables with a result_type column, ties on the same disclosure_date are
    explicitly broken to put CONSOLIDATED last, so most_recent_as_of()'s
    bisect (which picks the LAST matching row for a tied date) picks
    CONSOLIDATED deterministically instead of an arbitrary SQL row order
    (module docstring, bug 4)."""
    col_sql = ", ".join(columns)
    tie_break = (", CASE WHEN result_type = 'CONSOLIDATED' THEN 1 ELSE 0 END"
                 if table in HAS_RESULT_TYPE else "")
    rows = conn.execute(
        f"SELECT disclosure_date, {col_sql} FROM {table} "
        f"WHERE symbol = ? AND disclosure_date IS NOT NULL "
        f"ORDER BY disclosure_date{tie_break}",
        (symbol,),
    ).fetchall()
    return rows  # [(disclosure_date, col1, col2, ...), ...]


def most_recent_as_of(series: list, date: str, max_staleness_days: int = MAX_DISCLOSURE_STALENESS_DAYS):
    """series: sorted [(disclosure_date, ...), ...], ties on disclosure_date
    already broken to prefer CONSOLIDATED (see load_disclosure_series).
    Returns the row with the latest disclosure_date <= date, or None if
    no such row exists OR the match is older than max_staleness_days
    (module docstring, bug 5 -- a disclosure this old is more likely an
    unmatched-newer-quarter gap than genuine current information)."""
    dates = [r[0] for r in series]
    idx = bisect.bisect_right(dates, date) - 1
    if idx < 0:
        return None
    row = series[idx]
    if max_staleness_days is not None:
        gap_days = (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(row[0], "%Y-%m-%d")).days
        if gap_days > max_staleness_days:
            return None
    return row


def load_institutional_series(conn, symbol: str) -> list:
    """[(disclosure_date, quarter_end_date, total_institutional_pct,
    fii_fpi_pct, mutual_fund_pct), ...] sorted by disclosure_date --
    disclosure_date IS NOT NULL is the point-in-time join constraint (same
    discipline as load_disclosure_series). quarter_end_date is carried
    alongside purely to measure the calendar gap between quarters for the
    YoY trend guard in institutional_trend_as_of() below -- never used as
    the join key itself."""
    return conn.execute(
        "SELECT disclosure_date, quarter_end_date, total_institutional_pct, "
        "fii_fpi_pct, mutual_fund_pct FROM shareholding_institutional_breakdown "
        "WHERE symbol = ? AND disclosure_date IS NOT NULL ORDER BY disclosure_date",
        (symbol,),
    ).fetchall()


def institutional_trend_as_of(series: list, date: str):
    """series: sorted by disclosure_date, as returned by
    load_institutional_series(). Returns (as_of_row, qoq_change, yoy_change)
    -- qoq_change/yoy_change are computed from the raw quarterly figures at
    assembly time (docs/institutional_attention_feature.md Section 5), not
    pre-baked into shareholding_institutional_breakdown. YoY only computed
    when a disclosed quarter is found 300-400 days before the as-of
    quarter's quarter_end_date; irregular filing gaps (a skipped or extra
    quarter, seen live for symbol BSE) otherwise leave it correctly NULL
    rather than compare against the wrong quarter. Same staleness cutoff
    as most_recent_as_of() (module docstring, bug 5) -- applied here too
    since this uses its own bisect rather than most_recent_as_of()."""
    dates = [r[0] for r in series]
    idx = bisect.bisect_right(dates, date) - 1
    if idx < 0:
        return None, None, None
    row = series[idx]
    gap_days = (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(row[0], "%Y-%m-%d")).days
    if gap_days > MAX_DISCLOSURE_STALENESS_DAYS:
        return None, None, None
    total = row[2]

    qoq_change = None
    if idx > 0 and series[idx - 1][2] is not None and total is not None:
        qoq_change = total - series[idx - 1][2]

    yoy_change = None
    if total is not None:
        row_qed = datetime.strptime(row[1], "%Y-%m-%d")
        for j in range(idx - 1, -1, -1):
            days_diff = (row_qed - datetime.strptime(series[j][1], "%Y-%m-%d")).days
            if days_diff > 400:
                break
            if 300 <= days_diff <= 400 and series[j][2] is not None:
                yoy_change = total - series[j][2]
                break

    return row, qoq_change, yoy_change


def load_announcements(conn, symbol: str) -> list:
    rows = conn.execute(
        "SELECT announcement_date, sentiment FROM corporate_announcements "
        "WHERE symbol = ? ORDER BY announcement_date",
        (symbol,),
    ).fetchall()
    return rows


def recent_catalyst_flags(announcements: list, ann_dates: list, date: str, window_days: int = 30) -> tuple:
    """(negative_flag, positive_flag) -- replaces the earlier keyword-regex
    has_recent_order_dispute() (see docs/next_phase_plan.md Section 2, SHAP
    confirmed the regex flag was the lowest-ranked feature in both
    horizons). Sourced from corporate_announcements.sentiment, set by
    src/classify_announcements.py's LLM classification pass -- reads as
    all-zero until that script's real run completes (blocked as of
    2026-07-19 on missing API credentials, see README changelog), same
    "built but not yet populated" situation as sector_membership. Both
    flags are 0 (not NULL) when no classified announcement falls in the
    window, same convention as the flag they replace."""
    window_start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=window_days)).strftime("%Y-%m-%d")
    lo = bisect.bisect_left(ann_dates, window_start)
    hi = bisect.bisect_right(ann_dates, date)
    negative_flag, positive_flag = 0, 0
    for ann_date, sentiment in announcements[lo:hi]:
        if sentiment == "negative":
            negative_flag = 1
        elif sentiment == "positive":
            positive_flag = 1
    return negative_flag, positive_flag


def most_recent_sector_snapshot(sector_snapshots: list, date: str):
    """sector_snapshots: sorted [(snapshot_date, [sector_name,...]), ...].
    Returns the sector list for the latest snapshot_date <= date, or []."""
    dates = [s[0] for s in sector_snapshots]
    idx = bisect.bisect_right(dates, date) - 1
    return sector_snapshots[idx][1] if idx >= 0 else []


def compute_symbol_rows(symbol: str, price_series: list, macro: dict,
                         sector_benchmarks: dict, sector_snapshots: list,
                         fin_series: list, bs_series: list, cf_series: list,
                         ratio_series: list, sh_series: list, inst_series: list,
                         announcements: list, ann_dates: list, fetched_at: str) -> list:
    rows = []
    for date, close, volume, avg_traded_value_20d in price_series:
        m = macro.get(date, (None,) * 7)

        sectors_as_of = most_recent_sector_snapshot(sector_snapshots, date)
        sector_metrics = [sector_benchmarks[(s, date)] for s in sectors_as_of
                           if (s, date) in sector_benchmarks]
        if sector_metrics:
            n = len(sector_metrics)
            avg_ret_3d = sum(x[0] for x in sector_metrics if x[0] is not None) / n
            avg_ret_5d = sum(x[1] for x in sector_metrics if x[1] is not None) / n
            avg_ret_14d = sum(x[2] for x in sector_metrics if x[2] is not None) / n
            avg_alpha_14d = sum(x[3] for x in sector_metrics if x[3] is not None) / n
        else:
            avg_ret_3d = avg_ret_5d = avg_ret_14d = avg_alpha_14d = None

        fin = most_recent_as_of(fin_series, date)
        bs = most_recent_as_of(bs_series, date)
        cf = most_recent_as_of(cf_series, date)
        ratio = most_recent_as_of(ratio_series, date)
        sh = most_recent_as_of(sh_series, date)
        inst, inst_qoq, inst_yoy = institutional_trend_as_of(inst_series, date)

        fin_days_since = None
        if fin:
            fin_days_since = (datetime.strptime(date, "%Y-%m-%d")
                               - datetime.strptime(fin[0], "%Y-%m-%d")).days

        negative_catalyst_flag, positive_catalyst_flag = recent_catalyst_flags(announcements, ann_dates, date)

        # fin_series columns: (disclosure_date, sales, net_profit, opm_pct, eps, result_type)
        rows.append((
            symbol, date,
            close, volume, avg_traded_value_20d,
            *m,
            len(sectors_as_of), avg_ret_3d, avg_ret_5d, avg_ret_14d, avg_alpha_14d,
            fin[0] if fin else None, fin[5] if fin else None, fin_days_since,
            fin[1] if fin else None, fin[2] if fin else None,
            fin[3] if fin else None, fin[4] if fin else None,
            bs[0] if bs else None, bs[1] if bs else None, bs[2] if bs else None,
            cf[0] if cf else None, cf[1] if cf else None,
            ratio[0] if ratio else None, ratio[1] if ratio else None,
            sh[0] if sh else None, sh[1] if sh else None, sh[2] if sh else None,
            inst[0] if inst else None, inst[2] if inst else None,
            inst[3] if inst else None, inst[4] if inst else None,
            inst_qoq, inst_yoy,
            negative_catalyst_flag, positive_catalyst_flag,
            fetched_at,
        ))
    return rows


def upsert(conn, rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO model_feature_matrix
            (symbol, date, close, volume, avg_traded_value_20d,
             nifty50_close, nifty50_return_5d, nifty50_return_10d, nifty50_dist_50dma_pct,
             india_vix_close, vix_change_5d_pts, vix_change_5d_pct,
             sector_count, avg_sector_return_3d, avg_sector_return_5d,
             avg_sector_return_14d, avg_sector_relative_alpha_14d,
             fin_disclosure_date, fin_result_type, fin_days_since_disclosure,
             fin_sales, fin_net_profit, fin_opm_pct, fin_eps,
             bs_disclosure_date, bs_total_assets, bs_borrowings,
             cf_disclosure_date, cf_net_cash_flow,
             ratio_disclosure_date, ratio_roce_pct,
             sh_disclosure_date, sh_promoter_pct, sh_public_pct,
             sh_inst_disclosure_date, sh_inst_total_pct, sh_inst_fii_fpi_pct,
             sh_inst_mutual_fund_pct, sh_inst_qoq_change_pct, sh_inst_yoy_change_pct,
             recent_negative_catalyst_flag_30d, recent_positive_catalyst_flag_30d, fetched_at)
        VALUES (?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?,?, ?,?,?, ?,?, ?,?, ?,?,?,
                ?,?,?,?,?,?, ?,?,?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            close=excluded.close, volume=excluded.volume,
            avg_traded_value_20d=excluded.avg_traded_value_20d,
            nifty50_close=excluded.nifty50_close, nifty50_return_5d=excluded.nifty50_return_5d,
            nifty50_return_10d=excluded.nifty50_return_10d,
            nifty50_dist_50dma_pct=excluded.nifty50_dist_50dma_pct,
            india_vix_close=excluded.india_vix_close,
            vix_change_5d_pts=excluded.vix_change_5d_pts, vix_change_5d_pct=excluded.vix_change_5d_pct,
            sector_count=excluded.sector_count, avg_sector_return_3d=excluded.avg_sector_return_3d,
            avg_sector_return_5d=excluded.avg_sector_return_5d,
            avg_sector_return_14d=excluded.avg_sector_return_14d,
            avg_sector_relative_alpha_14d=excluded.avg_sector_relative_alpha_14d,
            fin_disclosure_date=excluded.fin_disclosure_date, fin_result_type=excluded.fin_result_type,
            fin_days_since_disclosure=excluded.fin_days_since_disclosure,
            fin_sales=excluded.fin_sales, fin_net_profit=excluded.fin_net_profit,
            fin_opm_pct=excluded.fin_opm_pct, fin_eps=excluded.fin_eps,
            bs_disclosure_date=excluded.bs_disclosure_date, bs_total_assets=excluded.bs_total_assets,
            bs_borrowings=excluded.bs_borrowings,
            cf_disclosure_date=excluded.cf_disclosure_date, cf_net_cash_flow=excluded.cf_net_cash_flow,
            ratio_disclosure_date=excluded.ratio_disclosure_date, ratio_roce_pct=excluded.ratio_roce_pct,
            sh_disclosure_date=excluded.sh_disclosure_date, sh_promoter_pct=excluded.sh_promoter_pct,
            sh_public_pct=excluded.sh_public_pct,
            sh_inst_disclosure_date=excluded.sh_inst_disclosure_date,
            sh_inst_total_pct=excluded.sh_inst_total_pct,
            sh_inst_fii_fpi_pct=excluded.sh_inst_fii_fpi_pct,
            sh_inst_mutual_fund_pct=excluded.sh_inst_mutual_fund_pct,
            sh_inst_qoq_change_pct=excluded.sh_inst_qoq_change_pct,
            sh_inst_yoy_change_pct=excluded.sh_inst_yoy_change_pct,
            recent_negative_catalyst_flag_30d=excluded.recent_negative_catalyst_flag_30d,
            recent_positive_catalyst_flag_30d=excluded.recent_positive_catalyst_flag_30d,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                         help="omit for every symbol with daily_prices rows")
    args = parser.parse_args()

    conn = get_conn()
    macro = load_macro(conn)
    sector_benchmarks = load_sector_benchmarks(conn)
    sector_membership = load_sector_membership_by_symbol(conn)

    symbols = load_symbols(conn, args.symbols)
    fetched_at = datetime.now().isoformat()

    total = 0
    for i, symbol in enumerate(symbols):
        price_series = conn.execute(
            "SELECT date, close, volume, avg_traded_value_20d FROM daily_prices "
            "WHERE symbol = ? ORDER BY date", (symbol,),
        ).fetchall()
        if not price_series:
            continue

        fin_series = load_disclosure_series(
            conn, "financial_results",
            ["sales", "net_profit", "opm_pct", "eps", "result_type"], symbol)
        bs_series = load_disclosure_series(
            conn, "balance_sheet", ["total_assets", "borrowings"], symbol)
        cf_series = load_disclosure_series(conn, "cash_flow", ["net_cash_flow"], symbol)
        ratio_series = load_disclosure_series(conn, "ratios", ["roce_pct"], symbol)
        sh_series = load_disclosure_series(
            conn, "shareholding_pattern", ["promoter_pct", "public_pct"], symbol)
        inst_series = load_institutional_series(conn, symbol)

        announcements = load_announcements(conn, symbol)
        ann_dates = [a[0] for a in announcements]

        sector_snapshots = sector_membership.get(symbol, [])

        rows = compute_symbol_rows(
            symbol, price_series, macro, sector_benchmarks, sector_snapshots,
            fin_series, bs_series, cf_series, ratio_series, sh_series, inst_series,
            announcements, ann_dates, fetched_at,
        )
        if rows:
            upsert(conn, rows)
            total += len(rows)
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(symbols)}] {total} feature rows so far...")

    print(f"Done. Upserted {total} model_feature_matrix rows across {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
