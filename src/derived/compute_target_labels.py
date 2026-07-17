"""
Computes forward-looking training labels into `model_target_labels` --
Step 4 of the macro/sector shock feature build
(macro_sector_shock_features.md Section 6).

NOT a fetch script -- no external HTTP, purely derived from daily_prices
(stock closes) and macro_regime_indicators (NIFTY 50 closes, already
backfilled by fetch_macro_sector.py). Run fetch_macro_sector.py first,
covering at least the same date range as daily_prices, or every row here
will have NULL nifty_return_Nd/alpha_Nd for lack of a NIFTY 50 close to
compare against.

POINT-IN-TIME NOTE -- read before touching this file:
This table intentionally uses FUTURE data relative to `date` (the label,
not a feature) -- see the long comment on model_target_labels in
schema.sql. Never join this into the feature side of a training matrix.

Forward windows are in TRADING days, using macro_regime_indicators.date
as the market calendar (it's already a confirmed real-trading-day list by
construction -- see fetch_macro_sector.py). A stock's OWN price series can
have gaps the market calendar doesn't (suspension, delisting) -- if the
stock has no daily_prices row on the exact target trading day, that
label is left NULL rather than substituting a nearby date, since "return
from date A to date B" is only well-defined for the exact two dates asked
for. Rows near the end of available price history without a full 14d/30d
forward window yet are excluded entirely, not computed on a truncated
window (would silently understate/overstate real alpha).

Usage:
    python src/compute_target_labels.py              # full universe
    python src/compute_target_labels.py --symbols RELIANCE TCS
"""
import argparse
import sys
from datetime import datetime

from core.db import get_conn

FORWARD_WINDOWS = (14, 30)


def load_trading_calendar(conn) -> list:
    rows = conn.execute(
        "SELECT date FROM macro_regime_indicators ORDER BY date"
    ).fetchall()
    return [r[0] for r in rows]


def load_nifty_closes(conn) -> dict:
    rows = conn.execute(
        "SELECT date, nifty50_close FROM macro_regime_indicators "
        "WHERE nifty50_close IS NOT NULL"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def load_symbols(conn, symbols_arg) -> list:
    if symbols_arg:
        return symbols_arg
    rows = conn.execute("SELECT DISTINCT symbol FROM daily_prices ORDER BY symbol").fetchall()
    return [r[0] for r in rows]


def load_price_series(conn, symbol: str) -> dict:
    rows = conn.execute(
        "SELECT date, close FROM daily_prices WHERE symbol = ? AND close IS NOT NULL ORDER BY date",
        (symbol,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _pct_return(series: dict, d_from: str, d_to: str):
    if d_from not in series or d_to not in series:
        return None
    base = series[d_from]
    if base == 0:
        return None
    return round((series[d_to] - series[d_from]) / base * 100, 4)


def compute_symbol_rows(symbol: str, price_series: dict, nifty_closes: dict,
                         calendar: list, calendar_idx: dict, fetched_at: str) -> list:
    rows = []
    for d in sorted(price_series.keys()):
        idx = calendar_idx.get(d)
        if idx is None:
            # date isn't a recognized trading day in macro_regime_indicators'
            # calendar (e.g. daily_prices covers a date range wider than
            # the macro backfill) -- can't compute a trading-day-accurate
            # forward window without it, skip rather than guess.
            continue

        window_results = {}
        skip_row = True
        for n in FORWARD_WINDOWS:
            target_idx = idx + n
            if target_idx >= len(calendar):
                window_results[n] = (None, None, None, None)
                continue
            target_date = calendar[target_idx]

            stock_return = _pct_return(price_series, d, target_date)
            nifty_return = _pct_return(nifty_closes, d, target_date)
            alpha = (round(stock_return - nifty_return, 4)
                     if stock_return is not None and nifty_return is not None else None)
            flag = (1 if alpha > 0 else 0) if alpha is not None else None
            window_results[n] = (stock_return, nifty_return, alpha, flag)
            if alpha is not None:
                skip_row = False

        if skip_row:
            # neither window produced a usable alpha (e.g. this date is
            # past the last date with a full 14d/30d forward window, or
            # the stock has no matching future price row) -- nothing
            # meaningful to store.
            continue

        r14 = window_results[14]
        r30 = window_results[30]
        rows.append((
            symbol, d,
            r14[0], r14[1], r14[2], r14[3],
            r30[0], r30[1], r30[2], r30[3],
            fetched_at,
        ))
    return rows


def upsert(conn, rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO model_target_labels
            (symbol, date, stock_return_14d, nifty_return_14d, alpha_14d, outperform_14d_flag,
             stock_return_30d, nifty_return_30d, alpha_30d, outperform_30d_flag, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            stock_return_14d=excluded.stock_return_14d,
            nifty_return_14d=excluded.nifty_return_14d,
            alpha_14d=excluded.alpha_14d,
            outperform_14d_flag=excluded.outperform_14d_flag,
            stock_return_30d=excluded.stock_return_30d,
            nifty_return_30d=excluded.nifty_return_30d,
            alpha_30d=excluded.alpha_30d,
            outperform_30d_flag=excluded.outperform_30d_flag,
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
    calendar = load_trading_calendar(conn)
    if not calendar:
        print("macro_regime_indicators is empty -- run fetch_macro_sector.py "
              "first (needs to cover the same range as daily_prices).", file=sys.stderr)
        sys.exit(1)
    calendar_idx = {d: i for i, d in enumerate(calendar)}
    nifty_closes = load_nifty_closes(conn)

    symbols = load_symbols(conn, args.symbols)
    fetched_at = datetime.now().isoformat()

    total = 0
    for i, symbol in enumerate(symbols):
        price_series = load_price_series(conn, symbol)
        if not price_series:
            continue
        rows = compute_symbol_rows(symbol, price_series, nifty_closes,
                                    calendar, calendar_idx, fetched_at)
        if rows:
            upsert(conn, rows)
            total += len(rows)
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(symbols)}] {total} label rows so far...")

    print(f"Done. Upserted {total} model_target_labels rows across {len(symbols)} symbols.")


if __name__ == "__main__":
    main()
