"""
Parses the institutional shareholding breakdown out of the XBRL filings
shareholding_pattern.attachment_url already points to, into
`shareholding_institutional_breakdown` -- docs/institutional_attention_feature.md.

NOT a new data source. Confirmed live 2026-07-16 (real TRENT filing,
SHP_1692317_14072026044019_WEB.xml): NSE's shareholding-pattern summary
API (what fetch_shareholding_pattern.py already parses) only exposes the
coarse promoter/public/employee-trust split, but the underlying XBRL
filing -- whose URL we already capture in shareholding_pattern.attachment_url
-- has the FULL SEBI Table III breakdown via a
CategoryOfShareholdersAxis XBRL dimension: ~40 leaf categories including
MutualFundsOrUTI, InstitutionsForeignPortfolioInvestorCategoryOne/Two,
Banks, InsuranceCompanies, AlternativeInvestmentFunds, and more.

STRUCTURE, confirmed by direct inspection (not assumed from the XBRL
taxonomy docs): each category is tagged via an <xbrli:context> whose
<xbrldi:explicitMember dimension="...CategoryOfShareholdersAxis"> names the
category (e.g. "in-bse-shp:MutualFundsOrUTIMember"), and the actual percent-
of-total-shares figure is the <ShareholdingAsAPercentageOfTotalNumberOfShares>
element whose contextRef points at that context. InstitutionsDomesticMember
and InstitutionsForeignMember are ROLLUP aggregates, not leaf categories --
confirmed their values exactly equal the sum of their own children (e.g.
InstitutionsDomesticMember 0.2327 = MutualFunds 0.1464 + Banks 0.0014 +
Insurance 0.0609 + AIF 0.0051 + ProvidentFunds 0.0169 + SovereignWealthDomestic
0.0019) -- using these NSE-computed rollups directly for total_institutional_pct
avoids our own re-summation silently drifting from the official total if a
category is missed or the taxonomy changes.

Usage:
    python src/fetch_institutional_breakdown.py --symbols RELIANCE TCS
    python src/fetch_institutional_breakdown.py --limit 20    # first N not-yet-parsed rows, for testing
"""
import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO

import requests

from db import get_conn

XBRLI_NS = "{http://www.xbrl.org/2003/instance}"
XBRLDI_NS = "{http://xbrl.org/2006/xbrldi}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

# leaf category (XBRL member local name, without namespace prefix) -> our
# column. Confirmed live 2026-07-16 against a real TRENT filing (see module
# docstring) -- BUT extended 2026-07-19 after a full-dataset survey found 81
# distinct member names, spanning at least 3 XBRL taxonomy eras with
# genuinely different naming, including literal typos NSE's own schema
# carried for a while ("Catergory" for "Category", "Goverments" for
# "Governments", "Isis" for "Is", lowercase "Coporatewhere"). A first version
# of this mapping only covered the newest era's names -- for ~7,000 rows
# (the older-era filings) this silently left mutual_fund_pct/fii_fpi_pct/
# financial_institution_pct empty AND dumped genuinely non-institutional
# categories (PublicShareholdingMember, NonInstitutionsMember, etc, under
# their older-era names) into other_institution_pct, badly inflating it.
# Fixed by surveying every real name that appears in the data (not guessing
# from XBRL taxonomy docs) and explicitly classifying all 81 -- see
# UNCLASSIFIED_MEMBERS_SEEN below for the safety net if a NEW name shows up
# in a future fetch that isn't in this list yet.
CATEGORY_TO_COLUMN = {
    "MutualFundsOrUTIMember": "mutual_fund_pct",
    "MutualFundsOrUtiMember": "mutual_fund_pct",

    "InstitutionsForeignPortfolioInvestorCategoryOneMember": "fii_fpi_pct",
    "InstitutionsForeignPortfolioInvestorCategoryTwoMember": "fii_fpi_pct",
    "InstitutionsForeignPortfolioInvestorCatergoryOneMember": "fii_fpi_pct",  # sic -- real NSE typo
    "InstitutionsForeignPortfolioInvestorCatergoryTwoMember": "fii_fpi_pct",  # sic -- real NSE typo
    "InstitutionsForeignPortfolioInvestorMember": "fii_fpi_pct",  # older era, no Category I/II split
    "ForeignPortfolioInvestorMember": "fii_fpi_pct",
    "ForeignInstitutionsMember": "fii_fpi_pct",  # pre-FPI-regime era name (India's FII->FPI transition, ~2014)

    "BanksMember": "financial_institution_pct",
    "OtherFinancialInstitutionsMember": "financial_institution_pct",
    "NBFCsRegisteredWithRBIMember": "financial_institution_pct",
    "NBFCsRegisteredWithRbiMember": "financial_institution_pct",
    "FinancialInstitutionOrBanksMember": "financial_institution_pct",
    "IndianFinancialInstitutionsOrBanksMember": "financial_institution_pct",

    "InsuranceCompaniesMember": "insurance_pct",

    "AlternativeInvestmentFundsMember": "alternate_investment_fund_pct",
}

# leaf categories that ARE institutional but don't map to a named column --
# roll into other_institution_pct. Explicit allowlist, not "everything
# unrecognized" -- see UNCLASSIFIED_MEMBERS_SEEN.
OTHER_INSTITUTIONAL_MEMBERS = {
    "VentureCapitalFundsMember", "ForeignVentureCapitalInvestorsMember",
    "ProvidentFundsOrPensionFundsMember",
    "SovereignWealthFundsDomesticMember", "SovereignWealthFundsForeignMember",
    "AssetReconstructionCompaniesMember",
    "OverseasDepositoriesMember",
    "OtherInstitutionsDomesticMember", "OtherInstitutionsForeignMember",
    "OtherInstitutionsMember",  # older era's combined "other institutions"
}

# rollup aggregates -- used ONLY to derive total_institutional_pct, NEVER
# summed as a leaf (would double-count their own children). Both the
# newer split form and the older single combined form are real, seen live.
ROLLUP_MEMBERS = {"InstitutionsDomesticMember", "InstitutionsForeignMember", "InstitutionsMember"}

# explicitly non-institutional -- excluded from both named columns and
# other_institution_pct. Confirmed via full-dataset survey (2026-07-19),
# covers every non-institutional name actually seen across all XBRL eras.
NON_INSTITUTIONAL_MEMBERS = {
    "ShareholdingPatternMember", "PublicShareholdingMember",
    "ShareholdingOfPromoterAndPromoterGroupMember", "NonInstitutionsMember",
    "IndianMember", "ForeignMember", "OtherIndianShareholdersMember", "OtherForeignShareholdersMember",
    "OtherNonInstitutionsMember",
    "IndividualsOrHinduUndividedFamilyMember",
    "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakhMember",
    "ResidentIndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakhMember",
    "IndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakhMember",  # older era, no "Resident" prefix
    "IndividualShareholdersHoldingNominalShareCapitalInExcessOfRsTwoLakhMember",
    "NonResidentIndividualsOrForeignIndividualsMember", "NonResidentIndiansMember",
    "ForeignNationalsMember", "ForeignCompaniesMember",
    "BodiesCorporateMember",
    "EmployeeBenefitsTrustsMember", "EmployeeTrustsMember",
    "InvestorEducationAndProtectionFundMember",
    "KeyManagerialPersonnelMember", "DirectorsAndDirectorsRelativesMember",
    "CentralGovernmentOrPresidentOfIndiaMember", "StateGovernmentsOrGovernorsMember",
    "CentralGovernmentOrStateGovernmentSMember", "CentralGovernmentOrStateGovernmentSOrPresidentOfIndiaMember",
    "GovernmentsMember", "GovermentsMember",  # sic -- real NSE typo (missing "n")
    "ForeignGovernmentMember",
    "RelativesOfPromotersOtherThanPromoterGroupMember",
    "ForeignDirectInvestmentMember",
    "AssociateCompaniesOrSubsidiariesMember",
    "ShareholdingByCompaniesOrBodiesCorporateWhereCentralOrStateGovernmentIsPromoterMember",
    "ShareholdingByCompaniesOrBodiesCorporatewhereCentralOrStateGovernmentIsPromoterMember",  # sic -- lowercase "w"
    "TrustsWhereAnyPersonBelongingToPromoterAndPromoterGroupIsTrusteeOrBeneficiaryOrAuthorOfTrustMember",
    "TrustsWhereAnyPersonBelongingToPromoterAndPromoterGroupIsisTrusteeOrBeneficiaryOrAuthorOfTrustMember",  # sic -- real NSE typo ("Isis")
    "SharesHeldByNonPromoterNonPublicShareholdersMember",
    "CustodianOrDRHolderMember",
    # trading-member sub-categories -- negligible (2-3 rows each across the
    # whole dataset) but real, and not institutional in the sense this
    # feature cares about
    "TradingMembersAndAssociatesOfTradingMembers", "CorporateTradingMember",
    "IndividualTradingMember", "TradingMemberBank", "AssociateTradingMemberCorporate",
    "AssociateTradingMemberIndividual", "AssociateTradingMemberHUF",
    "AssociateTradingMemberFinancialInstitutionsOrBanks", "TradingMemberOther",
    "AssociateTradingMemberOther", "AssociateTradingMemberFDIBanks",
}
VALUE_TAG = "ShareholdingAsAPercentageOfTotalNumberOfShares"


def fetch_xbrl(session: requests.Session, url: str) -> bytes:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def _looks_like_total(v):
    return v is not None and (abs(v - 1.0) < 0.05 or abs(v - 100.0) < 5.0)


def normalize_scale(values: dict) -> dict:
    """Returns {category_member_name: fraction_float}, always on a 0-1
    scale.

    SCALE NORMALIZATION -- confirmed live 2026-07-18, found via a real
    data-quality check after the first full fetch (HDFCBANK's 2025-08-28
    filing showed total_institutional_pct=84.65, way outside a plausible
    0-1 range): NSE/BSE revised the shareholding-pattern XBRL taxonomy at
    least once (namespace is date-stamped, e.g.
    'bseindia.com/xbrl/shp/2025-05-31/in-bse-shp' vs '.../2025-10-31/...'),
    and ShareholdingAsAPercentageOfTotalNumberOfShares means something
    different across versions: the newer schema uses a decimal fraction
    (0.2582 = 25.82%), an older one uses a raw percentage NUMBER (25.82
    directly). Primary signal: ShareholdingPatternMember (the grand total,
    which must always equal 100% of the company by definition) -- it reads
    '1' in the newer schema, '100.00' in the older one.

    FALLBACK -- confirmed live 2026-07-19, found via a post-reprocess
    outlier check that still showed 3 bad rows, all symbol=BSE: BSE Ltd's
    own filings use the 'in-bse-shp' taxonomy where ShareholdingPatternMember
    does NOT reliably hold the grand total (one real filing had it at 7.94,
    an unrelated sub-value) -- checked directly against the downloaded XBRL
    (SHP_187478_1099821_19042024105056_WEB.xml). When
    ShareholdingPatternMember doesn't look like a plausible total (not near
    1, not near 100), fall back to the regulatory-guaranteed 3-way split
    (Promoter + Public + Non-Promoter-Non-Public shareholding), which must
    sum to exactly 100% by SEBI definition regardless of taxonomy version or
    which specific "total" member name a filer's taxonomy happens to use."""
    total = values.get("ShareholdingPatternMember")
    if not _looks_like_total(total):
        fallback_total = (
            values.get("ShareholdingOfPromoterAndPromoterGroupMember", 0.0)
            + values.get("PublicShareholdingMember", 0.0)
            + values.get("SharesHeldByNonPromoterNonPublicShareholdersMember", 0.0)
        )
        if fallback_total > 0:
            total = fallback_total

    if total is not None and total > 10:
        # percentage-number convention (total ~= 100) -- normalize every
        # value in this filing to a decimal fraction (total ~= 1)
        return {k: v / 100.0 for k, v in values.items()}
    return values


def parse_xbrl(content: bytes) -> dict:
    """Returns {category_member_name: fraction_float} (0-1 scale, always --
    see normalize_scale()) for every category found via the
    CategoryOfShareholdersAxis dimension -- leaf categories AND the two
    rollup aggregates, keyed by their raw XBRL member name (e.g.
    'MutualFundsOrUTIMember')."""
    root = ET.fromstring(content)

    ctx_to_member = {}
    for ctx in root.findall(f"{XBRLI_NS}context"):
        cid = ctx.get("id")
        for em in ctx.iter(f"{XBRLDI_NS}explicitMember"):
            member_full = em.text or ""
            member = member_full.split(":")[-1]  # strip 'in-bse-shp:' / 'in-nse-shp:' prefix
            ctx_to_member[cid] = member

    values = {}
    for elem in root:
        tag = elem.tag.split("}")[-1]
        if tag != VALUE_TAG:
            continue
        cref = elem.get("contextRef")
        member = ctx_to_member.get(cref)
        if not member or elem.text is None:
            continue
        try:
            values[member] = float(elem.text)
        except ValueError:
            continue

    return normalize_scale(values)


def build_row(symbol: str, quarter_end_date: str, disclosure_date, category_values: dict,
              source: str, fetched_at: str):
    columns = {
        "mutual_fund_pct": None, "fii_fpi_pct": None, "financial_institution_pct": None,
        "insurance_pct": None, "alternate_investment_fund_pct": None,
    }
    other_pct = 0.0
    have_other = False
    for member, val in category_values.items():
        if member in ROLLUP_MEMBERS or member in NON_INSTITUTIONAL_MEMBERS:
            continue
        col = CATEGORY_TO_COLUMN.get(member)
        if col:
            columns[col] = (columns[col] or 0.0) + val
        elif member in OTHER_INSTITUTIONAL_MEMBERS:
            other_pct += val
            have_other = True
        else:
            # unrecognized member name -- safe default is to EXCLUDE it
            # (undercounting-with-a-warning), not silently fold it into
            # other_institution_pct (which is exactly the bug found
            # 2026-07-19: unrecognized non-institutional names from an
            # older XBRL era were silently inflating this column). Loud on
            # purpose so a genuinely new taxonomy era gets noticed, not
            # quietly mis-tallied.
            print(f"    UNCLASSIFIED member '{member}' (value={val}) for {symbol} "
                  f"{quarter_end_date} -- excluded from all totals, not summed. "
                  f"Add it to CATEGORY_TO_COLUMN/OTHER_INSTITUTIONAL_MEMBERS/"
                  f"NON_INSTITUTIONAL_MEMBERS in this script once classified.",
                  file=sys.stderr)

    # total_institutional_pct: prefer the split rollup (newer era), fall
    # back to the older era's single combined rollup. Never sum both forms
    # together (they're alternates, not additive) or the leaf categories
    # (would double-count against whichever rollup is present).
    total_institutional = None
    if "InstitutionsDomesticMember" in category_values or "InstitutionsForeignMember" in category_values:
        total_institutional = (category_values.get("InstitutionsDomesticMember", 0.0)
                                + category_values.get("InstitutionsForeignMember", 0.0))
    elif "InstitutionsMember" in category_values:
        total_institutional = category_values["InstitutionsMember"]

    return (
        symbol, quarter_end_date, disclosure_date,
        columns["mutual_fund_pct"], columns["fii_fpi_pct"], columns["financial_institution_pct"],
        columns["insurance_pct"], columns["alternate_investment_fund_pct"],
        other_pct if have_other else None,
        total_institutional,
        json.dumps(category_values),
        source, fetched_at,
    )


def upsert(conn, rows: list):
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO shareholding_institutional_breakdown
            (symbol, quarter_end_date, disclosure_date, mutual_fund_pct, fii_fpi_pct,
             financial_institution_pct, insurance_pct, alternate_investment_fund_pct,
             other_institution_pct, total_institutional_pct, raw_categories_json,
             source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, quarter_end_date) DO UPDATE SET
            disclosure_date=excluded.disclosure_date,
            mutual_fund_pct=excluded.mutual_fund_pct,
            fii_fpi_pct=excluded.fii_fpi_pct,
            financial_institution_pct=excluded.financial_institution_pct,
            insurance_pct=excluded.insurance_pct,
            alternate_investment_fund_pct=excluded.alternate_investment_fund_pct,
            other_institution_pct=excluded.other_institution_pct,
            total_institutional_pct=excluded.total_institutional_pct,
            raw_categories_json=excluded.raw_categories_json,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()


def reprocess(conn, symbols: list = None):
    """Re-derives every column from the already-captured raw_categories_json
    -- NO network calls. Use after fixing CATEGORY_TO_COLUMN/
    OTHER_INSTITUTIONAL_MEMBERS/NON_INSTITUTIONAL_MEMBERS (e.g. a new,
    previously-unclassified member name turns up) instead of re-fetching
    ~14,000 documents again for a purely local classification fix -- see
    the 2026-07-19 changelog entry in README.md for why this exists."""
    symbol_filter = ""
    params = []
    if symbols:
        placeholders = ",".join("?" * len(symbols))
        symbol_filter = f"WHERE symbol IN ({placeholders})"
        params = list(symbols)

    rows_in = conn.execute(
        f"SELECT symbol, quarter_end_date, disclosure_date, raw_categories_json, "
        f"source, fetched_at FROM shareholding_institutional_breakdown {symbol_filter}",
        params,
    ).fetchall()
    print(f"Reprocessing {len(rows_in)} existing rows from raw_categories_json (no network calls)...")

    rows_out = []
    for symbol, quarter_end_date, disclosure_date, raw_json, source, fetched_at in rows_in:
        category_values = normalize_scale(json.loads(raw_json))
        rows_out.append(build_row(symbol, quarter_end_date, disclosure_date, category_values, source, fetched_at))

    upsert(conn, rows_out)
    print(f"Done. Reprocessed {len(rows_out)} rows.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", help="omit for every symbol in shareholding_pattern")
    parser.add_argument("--limit", type=int, help="only process the first N not-yet-parsed rows -- for testing")
    parser.add_argument("--sleep", type=float, default=0.5, help="seconds to sleep between XBRL fetches")
    parser.add_argument("--reprocess", action="store_true",
                         help="re-derive all columns from already-captured raw_categories_json, "
                              "no network calls -- use after fixing the category mapping")
    args = parser.parse_args()

    conn = get_conn()

    if args.reprocess:
        reprocess(conn, args.symbols)
        return

    symbol_filter = ""
    params = []
    if args.symbols:
        placeholders = ",".join("?" * len(args.symbols))
        symbol_filter = f"AND sp.symbol IN ({placeholders})"
        params = list(args.symbols)

    query = f"""
        SELECT sp.symbol, sp.period_end_date, sp.disclosure_date, sp.attachment_url
        FROM shareholding_pattern sp
        LEFT JOIN shareholding_institutional_breakdown sib
            ON sib.symbol = sp.symbol AND sib.quarter_end_date = sp.period_end_date
        WHERE sp.attachment_url IS NOT NULL AND sp.attachment_url != ''
          AND sib.symbol IS NULL
          {symbol_filter}
        ORDER BY sp.symbol, sp.period_end_date
    """
    if args.limit:
        query += f" LIMIT {args.limit}"

    to_fetch = conn.execute(query, params).fetchall()
    print(f"{len(to_fetch)} not-yet-parsed (symbol, quarter) rows to fetch.")

    session = requests.Session()
    session.headers.update(HEADERS)
    fetched_at = datetime.now().isoformat()

    ok, failed = 0, 0
    for i, (symbol, period_end_date, disclosure_date, url) in enumerate(to_fetch):
        if (i + 1) % 25 == 0 or i == 0:
            print(f"[{i+1}/{len(to_fetch)}] {symbol} {period_end_date} ...")
        try:
            content = fetch_xbrl(session, url)
            category_values = parse_xbrl(content)
            if not category_values:
                print(f"    WARNING {symbol} {period_end_date}: parsed 0 categories from {url}", file=sys.stderr)
                failed += 1
                continue
            row = build_row(symbol, period_end_date, disclosure_date, category_values, "NSE", fetched_at)
            upsert(conn, [row])
            ok += 1
        except Exception as e:
            print(f"    FAILED {symbol} {period_end_date}: {e}", file=sys.stderr)
            failed += 1
        time.sleep(args.sleep)

    print(f"Done. {ok} parsed OK, {failed} failed.")


if __name__ == "__main__":
    main()
