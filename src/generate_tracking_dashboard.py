"""
Single self-contained HTML dashboard for the live pick-tracking record --
tracking_dashboard_spec.md Section 5. Regenerate on demand or as part of
the nightly run:

    python src/generate_tracking_dashboard.py

Opens directly in a browser, no server needed -- same "no moving parts"
pattern as every other report artifact in this project. Chart.js is
loaded via CDN inside the generated file (fine for a personal, local-
review artifact per the spec).

Reuses evaluate.calibration_curve() for the hit-rate-by-confidence-bucket
check (Section 5.1) -- this is literally the live version of the same
calibration check already run on historical folds elsewhere in this
project, so it uses the exact same bucketing function rather than a
second implementation.

Day-by-day trend for open positions (Section 5.2) is computed fresh from
daily_prices/macro_regime_indicators at generation time, not stored --
see tracking_dashboard_spec.md Section 2 for why (a second stored copy
of already point-in-time-correct, continuously-updated data would just
be a second place for staleness bugs to creep in).
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import get_conn  # noqa: E402
from evaluate import calibration_curve  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent.parent / "models" / "reports" / "tracking_dashboard.html"


def load_picks(conn) -> list:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tracked_picks ORDER BY pick_date DESC, horizon, symbol").fetchall()
    return [dict(r) for r in rows]


def hit_rate_section(resolved: list) -> dict:
    """Section 1 -- overall, by horizon, and by calibrated-prob bucket.
    n shown prominently everywhere so a small early sample doesn't read
    as more meaningful than it is."""
    def rate(rows):
        n = len(rows)
        return {"n": n, "hit_rate": (sum(r["outperformed_flag"] for r in rows) / n) if n else None}

    overall = rate(resolved)
    by_horizon = {h: rate([r for r in resolved if r["horizon"] == h]) for h in ("14d", "30d")}

    buckets = {}
    for h in ("14d", "30d"):
        rows = [r for r in resolved if r["horizon"] == h]
        if not rows:
            buckets[h] = []
            continue
        y_true = [r["outperformed_flag"] for r in rows]
        y_prob = [r["calibrated_prob_at_pick"] for r in rows]
        import numpy as np
        buckets[h] = calibration_curve(np.array(y_true), np.array(y_prob), n_buckets=5)

    return {"overall": overall, "by_horizon": by_horizon, "calibration_buckets": buckets}


def load_series(conn, symbol: str, start_date: str, end_date: str) -> dict:
    rows = conn.execute(
        "SELECT date, close FROM daily_prices WHERE symbol = ? AND date >= ? AND date <= ? "
        "AND close IS NOT NULL ORDER BY date",
        (symbol, start_date, end_date),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def load_nifty_series(conn, start_date: str, end_date: str) -> dict:
    rows = conn.execute(
        "SELECT date, nifty50_close FROM macro_regime_indicators WHERE date >= ? AND date <= ? "
        "AND nifty50_close IS NOT NULL ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def open_positions_section(conn, open_picks: list, today: str) -> list:
    out = []
    for p in open_picks:
        stock_series = load_series(conn, p["symbol"], p["pick_date"], today)
        nifty_series = load_nifty_series(conn, p["pick_date"], today)
        dates = sorted(set(stock_series) & set(nifty_series))
        # base off the stored entry_price (frozen at pick time), not the
        # first row of this freshly-queried series -- entry_price is the
        # single source of truth for "what buying on pick_date meant"
        nifty_base = nifty_series.get(p["pick_date"])
        trend = []
        current_alpha = None
        if dates and nifty_base:
            for d in dates:
                stock_cum = (stock_series[d] / p["entry_price"] - 1.0) * 100
                nifty_cum = (nifty_series[d] / nifty_base - 1.0) * 100
                trend.append({"date": d, "stock_cum_return": round(stock_cum, 3), "nifty_cum_return": round(nifty_cum, 3)})
            current_alpha = round(trend[-1]["stock_cum_return"] - trend[-1]["nifty_cum_return"], 3)

        try:
            days_remaining = max(
                0, (datetime.strptime(p["target_close_date"], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days)
        except ValueError:
            days_remaining = None

        out.append({
            "symbol": p["symbol"], "horizon": p["horizon"], "pick_date": p["pick_date"],
            "target_close_date_estimate": p["target_close_date"], "days_remaining_estimate": days_remaining,
            "raw_prob_at_pick": p["raw_prob_at_pick"], "calibrated_prob_at_pick": p["calibrated_prob_at_pick"],
            "current_alpha": current_alpha, "trend": trend,
        })
    return out


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pick Tracking Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #0f1115; --panel: #171a21; --border: #2a2e37; --text: #e6e8ec;
    --muted: #9aa1ac; --accent: #4f8cff; --good: #35c98f; --bad: #ff6b6b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: var(--bg); color: var(--text);
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .subtitle { color: var(--muted); margin-bottom: 24px; font-size: 0.9rem; }
  section { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
            padding: 18px 20px; margin-bottom: 24px; }
  h2 { font-size: 1.1rem; margin-top: 0; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .stat-row { display: flex; gap: 20px; flex-wrap: wrap; margin: 12px 0; }
  .stat { background: #1e222c; border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; min-width: 140px; }
  .stat .label { font-size: 0.75rem; color: var(--muted); }
  .stat .value { font-size: 1.4rem; font-weight: 600; }
  .stat .n { font-size: 0.75rem; color: var(--muted); }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { cursor: pointer; color: var(--muted); user-select: none; white-space: nowrap; }
  th:hover { color: var(--text); }
  .pos { color: var(--good); } .neg { color: var(--bad); }
  .empty { color: var(--muted); font-style: italic; padding: 8px 0; }
  .pick-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 14px; background: #1a1d25; }
  .pick-card-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; flex-wrap: wrap; gap: 8px; }
  .pick-card-header .sym { font-weight: 600; font-size: 1.05rem; }
  .pick-card-header .meta { color: var(--muted); font-size: 0.8rem; }
  canvas { max-height: 180px; }
  .table-wrap { overflow-x: auto; }
  .bucket-bar { display: inline-block; height: 8px; background: var(--accent); border-radius: 4px; }
</style>
</head>
<body>
<h1>Pick Tracking Dashboard</h1>
<div class="subtitle">Generated __GENERATED_AT__ &middot; observability only, not a live trading system &mdash;
  see tracking_dashboard_spec.md Section 1</div>

<section>
  <h2>1. Live track record</h2>
  __TRACK_RECORD_HTML__
</section>

<section>
  <h2>2. Open positions (__N_OPEN__)</h2>
  __OPEN_POSITIONS_HTML__
</section>

<section>
  <h2>3. Resolved picks log (__N_RESOLVED__)</h2>
  __RESOLVED_TABLE_HTML__
</section>

<section>
  <h2>4. Excluded / delisted picks (__N_DELISTED__)</h2>
  __DELISTED_TABLE_HTML__
</section>

<script>
const RESOLVED = __RESOLVED_JSON__;
const OPEN_POSITIONS = __OPEN_POSITIONS_JSON__;

function fmtPct(v) {
  if (v === null || v === undefined) return "&ndash;";
  const cls = v > 0 ? "pos" : (v < 0 ? "neg" : "");
  return `<span class="${cls}">${v >= 0 ? "+" : ""}${v.toFixed(2)}%</span>`;
}

// --- Section 3: sortable resolved-picks table ---
let sortState = { key: "pick_date", dir: -1 };
function renderResolvedTable() {
  const rows = [...RESOLVED].sort((a, b) => {
    const av = a[sortState.key], bv = b[sortState.key];
    if (av === bv) return 0;
    return (av > bv ? 1 : -1) * sortState.dir;
  });
  const tbody = document.getElementById("resolved-tbody");
  if (!tbody) return;
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${r.pick_date}</td>
      <td>${r.symbol}</td>
      <td>${r.horizon}</td>
      <td>${(r.calibrated_prob_at_pick * 100).toFixed(1)}%</td>
      <td>${r.outperformed_flag ? '<span class="pos">beat Nifty</span>' : '<span class="neg">lagged Nifty</span>'}</td>
      <td>${fmtPct(r.actual_alpha)}</td>
      <td>${fmtPct(r.actual_stock_return)}</td>
      <td>${fmtPct(r.actual_nifty_return)}</td>
    </tr>`).join("");
}
document.querySelectorAll("th[data-sort]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.getAttribute("data-sort");
    sortState.dir = (sortState.key === key) ? -sortState.dir : -1;
    sortState.key = key;
    renderResolvedTable();
  });
});
renderResolvedTable();

// --- Section 2: one small line chart per open position ---
OPEN_POSITIONS.forEach((p, i) => {
  const canvas = document.getElementById(`chart-${i}`);
  if (!canvas || !p.trend.length) return;
  new Chart(canvas, {
    type: "line",
    data: {
      labels: p.trend.map(t => t.date),
      datasets: [
        { label: p.symbol, data: p.trend.map(t => t.stock_cum_return), borderColor: "#4f8cff", tension: 0.15, pointRadius: 0 },
        { label: "Nifty", data: p.trend.map(t => t.nifty_cum_return), borderColor: "#9aa1ac", tension: 0.15, pointRadius: 0 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#e6e8ec", boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: "#9aa1ac", maxTicksLimit: 6 }, grid: { color: "#2a2e37" } },
        y: { ticks: { color: "#9aa1ac", callback: v => v + "%" }, grid: { color: "#2a2e37" } },
      },
    },
  });
});
</script>
</body>
</html>
"""


def render_stat(label: str, value_str: str, n: int) -> str:
    return (f'<div class="stat"><div class="label">{label}</div>'
            f'<div class="value">{value_str}</div><div class="n">n={n}</div></div>')


def render_track_record(hr: dict) -> str:
    parts = ['<div class="stat-row">']
    ov = hr["overall"]
    parts.append(render_stat("Overall hit rate", f"{ov['hit_rate']*100:.1f}%" if ov["n"] else "no data", ov["n"]))
    for h in ("14d", "30d"):
        bh = hr["by_horizon"][h]
        parts.append(render_stat(f"{h} hit rate", f"{bh['hit_rate']*100:.1f}%" if bh["n"] else "no data", bh["n"]))
    parts.append("</div>")

    for h in ("14d", "30d"):
        buckets = [b for b in hr["calibration_buckets"][h] if b["n"] > 0]
        if not buckets:
            continue
        parts.append(f'<div style="margin-top:12px;"><strong>{h} &mdash; hit rate by calibrated-probability bucket</strong></div>')
        parts.append('<div class="table-wrap"><table><tr><th>Calibrated prob. range</th><th>n</th><th>Actual hit rate</th></tr>')
        for b in buckets:
            parts.append(f"<tr><td>{b['bucket_low']*100:.0f}&ndash;{b['bucket_high']*100:.0f}%</td>"
                          f"<td>{b['n']}</td><td>{b['actual_rate']*100:.1f}%</td></tr>")
        parts.append("</table></div>")
    if hr["overall"]["n"] < 20:
        parts.append('<p class="empty">Small sample so far &mdash; treat these numbers as directional, not conclusive.</p>')
    return "".join(parts)


def render_open_positions(open_positions: list) -> str:
    if not open_positions:
        return '<p class="empty">No open positions.</p>'
    cards = []
    for i, p in enumerate(open_positions):
        alpha_html = fmt_alpha(p["current_alpha"])
        cards.append(f"""
        <div class="pick-card">
          <div class="pick-card-header">
            <div><span class="sym">{p['symbol']}</span> <span class="meta">{p['horizon']} &middot; picked {p['pick_date']}</span></div>
            <div class="meta">raw {p['raw_prob_at_pick']*100:.1f}% / calibrated {p['calibrated_prob_at_pick']*100:.1f}%
              &middot; running alpha {alpha_html} &middot; ~{p['days_remaining_estimate']}d remaining (est.)</div>
          </div>
          <canvas id="chart-{i}"></canvas>
        </div>""")
    return "".join(cards)


def fmt_alpha(v):
    if v is None:
        return "&ndash;"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.2f}%</span>'


def render_resolved_table_shell() -> str:
    cols = [("pick_date", "Pick date"), ("symbol", "Symbol"), ("horizon", "Horizon"),
            ("calibrated_prob_at_pick", "Calib. prob"), ("outperformed_flag", "Outcome"),
            ("actual_alpha", "Alpha"), ("actual_stock_return", "Stock return"), ("actual_nifty_return", "Nifty return")]
    header = "".join(f'<th data-sort="{key}">{label}</th>' for key, label in cols)
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody id="resolved-tbody"></tbody></table></div>'


def render_delisted_table(delisted: list) -> str:
    if not delisted:
        return '<p class="empty">None.</p>'
    rows = "".join(
        f"<tr><td>{d['pick_date']}</td><td>{d['symbol']}</td><td>{d['horizon']}</td>"
        f"<td>{d['target_close_date']}</td><td>{d['calibrated_prob_at_pick']*100:.1f}%</td></tr>"
        for d in delisted)
    return (f'<div class="table-wrap"><table><thead><tr><th>Pick date</th><th>Symbol</th><th>Horizon</th>'
            f'<th>Target close date</th><th>Calib. prob at pick</th></tr></thead><tbody>{rows}</tbody></table></div>')


def main():
    conn = get_conn()
    picks = load_picks(conn)
    today = datetime.now().strftime("%Y-%m-%d")

    resolved = [p for p in picks if p["status"] == "resolved"]
    open_picks = [p for p in picks if p["status"] == "open"]
    delisted = [p for p in picks if p["status"] == "delisted_during_hold"]

    hr = hit_rate_section(resolved)
    open_positions = open_positions_section(conn, open_picks, today)

    html = HTML_TEMPLATE
    html = html.replace("__GENERATED_AT__", datetime.now().isoformat(timespec="seconds"))
    html = html.replace("__TRACK_RECORD_HTML__", render_track_record(hr))
    html = html.replace("__N_OPEN__", str(len(open_picks)))
    html = html.replace("__OPEN_POSITIONS_HTML__", render_open_positions(open_positions))
    html = html.replace("__N_RESOLVED__", str(len(resolved)))
    html = html.replace("__RESOLVED_TABLE_HTML__", render_resolved_table_shell())
    html = html.replace("__N_DELISTED__", str(len(delisted)))
    html = html.replace("__DELISTED_TABLE_HTML__", render_delisted_table(delisted))
    html = html.replace("__RESOLVED_JSON__", json.dumps(resolved, default=str))
    html = html.replace("__OPEN_POSITIONS_JSON__", json.dumps(open_positions, default=str))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html)
    print(f"Dashboard written to {OUT_PATH} "
          f"({len(resolved)} resolved, {len(open_picks)} open, {len(delisted)} delisted_during_hold)")


if __name__ == "__main__":
    main()
