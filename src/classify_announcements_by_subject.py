"""
Deterministic sentiment classification for `corporate_announcements`,
using the `subject` field directly -- NO keyword matching, NO LLM calls.

REAL FINDING (2026-07-19, per docs/next_phase_plan.md Section 2a): `desc`
in NSE's raw corporate-announcements API response -- already captured as
`subject` in this table since the fetch script was first built, just
never used this way -- turns out to BE the SEBI Regulation 30 structured
event-category tag the doc hoped might exist. Confirmed via a live
DevTools-equivalent capture: 262 distinct controlled-vocabulary values
across the full historical dataset (e.g. "Bagging/Receiving of orders/
contracts", "Pendency of Litigation(s)/dispute(s)...", "Awarding of
order(s)/contract(s)"), 100% populated (813,037/813,037 rows). This is
the filer's OWN disclosure tag, assigned at filing time -- more reliable
than any inferred classification (regex, keyword list, or LLM), and it
was sitting unused in the DB the whole time.

This makes Section 2b/2c/2d (expanded keyword list, local classifier,
local LLM) and the LLM path (classify_announcements.py) all unnecessary
for this specific problem -- `subject` already IS the category, so this
is "map a known finite vocabulary to sentiment," not "infer a category
from free text." Simpler, free, instant, fully inspectable, and more
trustworthy than any of the alternatives scoped in the doc.

SENTIMENT_BY_SUBJECT below covers only clearly-directional categories
(order wins/expansion -> positive; litigation/regulatory action/default/
insolvency/delisting/fraud -> negative). Everything else -- administrative
filings, routine informational updates, and genuinely mixed-direction
categories (e.g. a credit rating "revision" could be up or down; a rights
issue is dilutive but capital-positive) -- defaults to 'neutral' rather
than guessed, same discipline as every other classification in this
project (explicit allowlist, safe default, not "everything unmapped
counts as something").

Populates the SAME category/sentiment/classification_model/classified_at
columns as classify_announcements.py (the LLM path) -- downstream
consumers (assemble_feature_matrix.py's recent_catalyst_flags()) don't
need to know or care which mechanism produced sentiment.

Usage:
    python src/classify_announcements_by_subject.py            # training universe only
    python src/classify_announcements_by_subject.py --all-symbols
"""
import argparse
import sys
from datetime import datetime

from db import get_conn

CLASSIFICATION_METHOD = "subject_lookup_v1"

# Category -> sentiment, from a "likely short-term market reaction"
# framing (same framing used in classify_announcements.py's prompt, kept
# consistent in case the LLM path is ever used to backfill anything this
# mapping leaves NEUTRAL). Only clearly one-directional categories are
# listed -- see module docstring for the "don't guess" reasoning behind
# leaving everything else at the neutral default.
POSITIVE_SUBJECTS = {
    "Bagging/Receiving of orders/contracts", "Awarding of order(s)/contract(s)",
    "Awarding orders/contract", "Bagging orders/contract",
    "Capacity addition", "Product launch", "Capacity addition/product launch",
    "Commencement of commercial production/operations",
    "Dividend", "Dividend Updates", "Date of payment of dividend",
    "Bonus", "Buyback", "Daily Buy Back of securities",
    "Public Announcement - Buyback of Shares", "Post Buyback Public Announcement",
    "Buyback - Tender offer", "Buyback - Open Market",
    "Adoption of new line(s) of business",
}
NEGATIVE_SUBJECTS = {
    "Pendency of Litigation(s)/dispute(s) or the outcome impacting the Company",
    "Action(s) taken or orders passed", "Action(s) initiated or orders passed",
    "Litigations/Disputes/Regulatory actions",
    "Defaults on Payment of Interest/Principal",
    "Delay/default in the payment of fines/penalties/dues etc. to authority",
    "Fraud/Default/Arrest", "Frauds/Default by employees",
    "Initiation of Forensic Audit", "Final  forensic  audit  report",
    "Audit Qualifications/Comments", "Statement on Impact of Audit Qualifications",
    "Corporate Insolvency Resolution Process",
    "CIRP - others", "CIRP - Committee meeting updates", "CIRP - Commencement",
    "CIRP - Approval of Resolution Plan", "CIRP - Filing of application",
    "CIRP - Filing of Resolution Plan", "CIRP - Change in Resolutional Professional",
    "CIRP - Revocation/rejection", "Corporate Debt Restructuring",
    "Suspension of Trading", "Voluntary Delisting", "Delisting",
    "Public Announcement - Delisting", "Public Notice - Compulsory Delisting",
    "Resignation of Statutory Auditor", "Liquidation",
    "One Time Settlement", "One time settlement", "One Time Settlement-XBRL",
    "Withdrawal of Rights Issue",
    "Reasons for Delayed/Non-submission of Financial Results",
    "Demise", "Board meeting Cancelled", "Board Meeting Adjourned",
    "Disruption of Operations", "Disruption of operations",
    "Strikes/Lockouts/Disturbances",
    "Closure of operations", "Closure of operations of any unit/division",
}
# category bucket per POSITIVE/NEGATIVE_SUBJECTS membership -- reuses the
# same controlled vocabulary as classify_announcements.py so the two
# mechanisms' output is directly comparable/interchangeable
_CATEGORY_OVERRIDES = {
    "order_win_or_new_business": {
        "Bagging/Receiving of orders/contracts", "Awarding of order(s)/contract(s)",
        "Awarding orders/contract", "Bagging orders/contract",
        "Capacity addition", "Product launch", "Capacity addition/product launch",
        "Commencement of commercial production/operations",
        "Adoption of new line(s) of business",
    },
    "corporate_action": {
        "Dividend", "Dividend Updates", "Date of payment of dividend", "Bonus",
        "Buyback", "Daily Buy Back of securities", "Public Announcement - Buyback of Shares",
        "Post Buyback Public Announcement", "Buyback - Tender offer", "Buyback - Open Market",
    },
    "regulatory_or_legal_action": {
        "Pendency of Litigation(s)/dispute(s) or the outcome impacting the Company",
        "Action(s) taken or orders passed", "Action(s) initiated or orders passed",
        "Litigations/Disputes/Regulatory actions",
        "Defaults on Payment of Interest/Principal",
        "Delay/default in the payment of fines/penalties/dues etc. to authority",
        "Fraud/Default/Arrest", "Frauds/Default by employees",
        "Initiation of Forensic Audit", "Final  forensic  audit  report",
        "Audit Qualifications/Comments", "Statement on Impact of Audit Qualifications",
        "Suspension of Trading", "Voluntary Delisting", "Delisting",
        "Public Announcement - Delisting", "Public Notice - Compulsory Delisting",
    },
    "financial_results": {
        "Reasons for Delayed/Non-submission of Financial Results",
    },
}


def classify_subject(subject: str) -> tuple:
    """Returns (category, sentiment). Unmapped subjects -> ('administrative', 'neutral')."""
    if subject in POSITIVE_SUBJECTS:
        sentiment = "positive"
    elif subject in NEGATIVE_SUBJECTS:
        sentiment = "negative"
    else:
        return "administrative", "neutral"

    for category, members in _CATEGORY_OVERRIDES.items():
        if subject in members:
            return category, sentiment
    return "other_material", sentiment  # in POSITIVE/NEGATIVE_SUBJECTS but not yet bucketed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-symbols", action="store_true",
                         help="classify every symbol, not just the training universe (daily_prices).")
    args = parser.parse_args()

    conn = get_conn()
    symbol_filter = ""
    params = []
    if not args.all_symbols:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_prices").fetchall()]
        placeholders = ",".join("?" * len(symbols))
        symbol_filter = f"AND symbol IN ({placeholders})"
        params = symbols

    rows = conn.execute(
        f"SELECT seq_id, subject FROM corporate_announcements WHERE 1=1 {symbol_filter}", params,
    ).fetchall()
    if not rows:
        print("Nothing to classify.")
        return
    print(f"{len(rows)} announcements to classify ({'full universe' if args.all_symbols else 'training universe'}).")

    classified_at = datetime.now().isoformat()
    updates = []
    sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0}
    for seq_id, subject in rows:
        category, sentiment = classify_subject(subject)
        sentiment_counts[sentiment] += 1
        updates.append((category, sentiment, CLASSIFICATION_METHOD, classified_at, seq_id))

    conn.executemany(
        "UPDATE corporate_announcements SET category=?, sentiment=?, "
        "classification_model=?, classified_at=? WHERE seq_id=?",
        updates,
    )
    conn.commit()

    print(f"Done. Classified {len(updates)} rows.")
    print(f"  positive: {sentiment_counts['positive']} ({100*sentiment_counts['positive']/len(updates):.1f}%)")
    print(f"  negative: {sentiment_counts['negative']} ({100*sentiment_counts['negative']/len(updates):.1f}%)")
    print(f"  neutral:  {sentiment_counts['neutral']} ({100*sentiment_counts['neutral']/len(updates):.1f}%)")


if __name__ == "__main__":
    main()
