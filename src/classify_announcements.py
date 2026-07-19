"""
LLM classification pass over `corporate_announcements` -- replaces the
earlier keyword-regex catalyst detection in assemble_feature_matrix.py
(ORDER_DISPUTE_KEYWORDS / has_recent_order_dispute), which SHAP confirmed
was the lowest-ranked feature in both model_14d/model_30d (0.023/0.028),
consistent with the brittleness flagged when it was first built (regex
misses most real phrasings). See docs/next_phase_plan.md Section 2.

STATUS (2026-07-19): script is built and its parsing/upsert logic is
verified via --mock (a canned fake response, no network/API calls) -- but
the REAL classification run has NOT happened yet. Blocked on missing
ANTHROPIC_API_KEY / anthropic package in this environment; both the
`category` and `sentiment` columns in `corporate_announcements` are still
100% NULL for the training-universe scope this script targets. This is
the same "built but not yet populated" situation as sector_membership --
see README changelog. Once credentials exist:
    pip install anthropic
    export ANTHROPIC_API_KEY=...
    python src/classify_announcements.py

CLASSIFIES BOTH `subject` AND `details` (not just subject) -- same lesson
learned earlier building financial_results' disclosure matching: NSE often
files substantive news under a generic subject like "Outcome of Board
Meeting", with the real content only in `details`.

SCOPE: restricted to symbols in the current training universe
(daily_prices' distinct symbols, ~539) rather than the full 813K-row/
2,524-symbol table, per the explicit scope decision in this project's
2026-07-19 handoff -- cuts volume substantially while covering everything
the model could actually use. Pass --all-symbols to lift this restriction.

IDEMPOTENT / RESUMABLE: only ever selects `WHERE category IS NULL`, same
pattern as fetch_institutional_breakdown.py -- an interrupted run just
picks up where it left off on re-invocation, no separate checkpoint file
needed. Commits after every batch.

POINT-IN-TIME NOTE: classification is applied to text already known as of
`announcement_date` -- it doesn't introduce new information, just extracts
structure from what was already disclosed. The feature-assembly join
(assemble_feature_matrix.py) still keys off `announcement_date`, never
`classified_at` (when we happened to run the LLM pass) -- running this
script today does not leak into historical feature rows.

Usage:
    python src/classify_announcements.py --mock --limit 5   # verify logic, no API calls
    python src/classify_announcements.py --limit 100         # small real test batch
    python src/classify_announcements.py                     # full training-universe run
"""
import argparse
import json
import sys
import time
from datetime import datetime

from db import get_conn

MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 20

# Controlled vocabulary for `category` -- descriptive/debugging value.
# Downstream features (recent_negative_catalyst_flag_30d /
# recent_positive_catalyst_flag_30d in assemble_feature_matrix.py) key off
# `sentiment` directly, not off a hardcoded category->sentiment mapping --
# simpler and more robust than assuming e.g. "corporate_action" always
# means one thing (a buyback and a rights issue are both "corporate
# action" but pull sentiment in opposite directions).
CATEGORIES = (
    "order_win_or_new_business", "regulatory_or_legal_action",
    "financial_results", "corporate_action", "merger_acquisition",
    "management_change", "credit_rating_change", "administrative",
    "other_material",
)
SENTIMENTS = ("positive", "negative", "neutral")

PROMPT_TEMPLATE = """You are classifying Indian stock exchange (NSE/BSE) corporate announcements for a quantitative trading model. For each announcement below, assign:
- "category": exactly one of {categories}
- "sentiment": exactly one of {sentiments} -- from the perspective of a likely SHORT-TERM MARKET REACTION to this specific news (e.g. a new large order is "positive", a regulatory penalty or lawsuit is "negative", a routine board-meeting intimation with no substantive content is "neutral"), NOT the announcing company's own tone.

Announcements:
{items}

Respond with ONLY a JSON array, one object per announcement, in the same order, each with exactly these keys: "id" (copy the given id), "category", "sentiment". No other text."""


def load_unclassified(conn, symbols: list = None, limit: int = None) -> list:
    """[(seq_id, subject, details), ...] where category IS NULL, restricted
    to the given symbols (or the training universe if none given)."""
    if symbols is None:
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM daily_prices").fetchall()]
    if not symbols:
        return []
    placeholders = ",".join("?" * len(symbols))
    sql = (
        f"SELECT seq_id, subject, details FROM corporate_announcements "
        f"WHERE category IS NULL AND symbol IN ({placeholders}) "
        f"ORDER BY announcement_date"
    )
    params = list(symbols)
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def build_prompt(batch: list) -> str:
    items = "\n".join(
        f'id={i}: subject="{(subject or "").strip()[:200]}" details="{(details or "").strip()[:500]}"'
        for i, (seq_id, subject, details) in enumerate(batch)
    )
    return PROMPT_TEMPLATE.format(
        categories=", ".join(CATEGORIES), sentiments=", ".join(SENTIMENTS), items=items,
    )


def call_llm(prompt: str) -> str:
    import anthropic  # imported here, not at module level, so --mock works without the package installed
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def call_llm_mock(prompt: str) -> str:
    """Canned fake response for --mock -- lets batching/parsing/upsert
    logic be verified end-to-end without real API credentials. Returns a
    plausible classification for however many `id=` entries are in the
    prompt, cycling through a small set of realistic examples."""
    n = prompt.count("id=")
    examples = [
        {"category": "order_win_or_new_business", "sentiment": "positive"},
        {"category": "regulatory_or_legal_action", "sentiment": "negative"},
        {"category": "administrative", "sentiment": "neutral"},
        {"category": "financial_results", "sentiment": "neutral"},
    ]
    return json.dumps([{"id": i, **examples[i % len(examples)]} for i in range(n)])


def parse_response(raw_text: str, batch: list) -> list:
    """Returns [(seq_id, category, sentiment), ...] for successfully
    parsed items. Malformed/out-of-vocabulary entries are skipped with a
    warning, not silently substituted with a guessed value -- same
    discipline as every other parser in this project (e.g.
    fetch_institutional_breakdown.py's UNCLASSIFIED warning)."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    FAILED to parse LLM response as JSON: {e}. "
              f"Raw (first 300 chars): {raw_text[:300]!r}", file=sys.stderr)
        return []

    results = []
    for item in parsed:
        try:
            idx = item["id"]
            category, sentiment = item["category"], item["sentiment"]
        except (KeyError, TypeError):
            print(f"    SKIPPED malformed item: {item!r}", file=sys.stderr)
            continue
        if not (0 <= idx < len(batch)):
            print(f"    SKIPPED item with out-of-range id={idx!r}", file=sys.stderr)
            continue
        if category not in CATEGORIES or sentiment not in SENTIMENTS:
            print(f"    SKIPPED item id={idx} with out-of-vocabulary "
                  f"category={category!r}/sentiment={sentiment!r}", file=sys.stderr)
            continue
        seq_id = batch[idx][0]
        results.append((seq_id, category, sentiment))
    return results


def upsert(conn, results: list, classified_at: str):
    if not results:
        return
    conn.executemany(
        "UPDATE corporate_announcements SET category=?, sentiment=?, "
        "classification_model=?, classified_at=? WHERE seq_id=?",
        [(cat, sent, MODEL, classified_at, seq_id) for seq_id, cat, sent in results],
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                         help="restrict to these symbols. Omit for the full training universe (daily_prices).")
    parser.add_argument("--all-symbols", action="store_true",
                         help="classify every symbol in corporate_announcements, not just the training universe.")
    parser.add_argument("--limit", type=int, help="max announcements to classify (for a small test run).")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--sleep", type=float, default=0.5, help="seconds between API calls")
    parser.add_argument("--mock", action="store_true",
                         help="use a canned fake response instead of a real API call -- verifies "
                              "batching/parsing/upsert logic with zero network access and no credentials.")
    args = parser.parse_args()

    conn = get_conn()
    symbols = None if args.all_symbols else args.symbols
    rows = load_unclassified(conn, symbols=symbols, limit=args.limit)
    if not rows:
        print("Nothing to classify -- 0 unclassified rows for the given scope.")
        return
    print(f"{len(rows)} unclassified announcements to process "
          f"({'full universe' if args.all_symbols else 'training universe' if symbols is None else f'{len(symbols)} symbols'}).")

    llm_fn = call_llm_mock if args.mock else call_llm
    if args.mock:
        print("--mock: using a canned fake response, no real API calls will be made.")

    total_classified, total_skipped = 0, 0
    for start in range(0, len(rows), args.batch_size):
        batch = rows[start:start + args.batch_size]
        prompt = build_prompt(batch)
        try:
            raw = llm_fn(prompt)
        except Exception as e:
            print(f"  batch [{start}:{start+len(batch)}] FAILED: {e}", file=sys.stderr)
            continue
        results = parse_response(raw, batch)
        upsert(conn, results, datetime.now().isoformat())
        total_classified += len(results)
        total_skipped += len(batch) - len(results)
        print(f"  [{start+len(batch)}/{len(rows)}] classified {len(results)}/{len(batch)} in this batch "
              f"({total_classified} total so far)")
        if not args.mock:
            time.sleep(args.sleep)

    print(f"\nDone. Classified {total_classified} rows, skipped {total_skipped} "
          f"(malformed/out-of-vocabulary LLM output -- see stderr warnings above).")


if __name__ == "__main__":
    main()
