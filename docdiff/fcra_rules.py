"""
FCRA risk knowledge base — EDITABLE rule-set.

Scope: the Foreign Contribution (Regulation) Act, 2010 (India), as amended by the
FCRA (Amendment) Act, 2020 and the FCRA (Amendment) Rules, 2022 (effective
1 July 2022). It is meant for reviewing **funding / grant agreements, MoUs and
sub-grant / consultancy agreements** funded by foreign contribution.

⚠️  NOT LEGAL ADVICE. FCRA rules change through MHA notifications. This file is a
maintainable starting checklist with citations and a review date — VERIFY against
the latest notifications at https://fcraonline.nic.in before relying on it, and
consult a qualified professional. Keeping this current is a deliberate, easy edit:
add/adjust entries in RULES below and bump METADATA["last_reviewed"].

How a rule works (see docdiff/fcra.py for the engine)
-----------------------------------------------------
Each rule is a dict:
    id           short stable code (e.g. "FC-TRANSFER")
    title        human title
    category     grouping shown in the report
    severity     "high" | "medium" | "low" | "info"
    mode         "presence"  -> risk is flagged when the patterns ARE found
                 "absence"   -> a safeguard is flagged MISSING when patterns are
                                NOT found (a compliance checklist item)
                 "threshold" -> like presence, but only flags when a captured
                                percentage exceeds `threshold_percent`
    patterns     list of case-insensitive regex strings
    threshold_percent  (threshold mode only) integer; flag when captured % >= it
    reference    the FCRA section / rule + amendment it derives from
    explanation  why it matters
    recommendation  what to do about it
"""

from __future__ import annotations

# Bump this whenever you review/update the rules below.
METADATA = {
    "act": "Foreign Contribution (Regulation) Act, 2010 (India)",
    "amendments": [
        "FCRA (Amendment) Act, 2020",
        "FCRA (Amendment) Rules, 2022 (effective 1 July 2022)",
    ],
    "version": "2022.07",
    "last_reviewed": "2024-09",
    "sources": [
        "https://fcraonline.nic.in",
        "https://www.mha.gov.in",
    ],
    "disclaimer": (
        "Automated checklist against the Foreign Contribution (Regulation) Act, "
        "2010 (as amended in 2020 and the 2022 Rules). This is NOT legal advice "
        "and may not reflect the very latest MHA notifications — verify at "
        "fcraonline.nic.in and consult a qualified professional."
    ),
}


RULES = [
    # --- Funds movement -------------------------------------------------------
    {
        "id": "FC-TRANSFER",
        "title": "Onward transfer / sub-granting of foreign contribution",
        "category": "Funds transfer",
        "severity": "high",
        "mode": "presence",
        "patterns": [
            r"sub[-\s]?grant", r"on[-\s]?ward grant", r"re[-\s]?grant",
            r"pass[-\s]?through", r"down[-\s]?stream (partner|recipient|ngo)",
            r"transfer(?:ring)?\s+(?:of\s+)?(?:the\s+)?(?:foreign\s+contribution|funds|grant|monies)\s+to",
            r"disburse[^.\n]{0,30}\bto\b[^.\n]{0,30}(partner|sub[-\s]?recipient|another (?:organisation|organization|ngo|entity))",
            r"channel[^.\n]{0,30}funds?[^.\n]{0,20}to",
        ],
        "reference": "FCRA 2010 s.7 (as amended by the 2020 Amendment Act; Rule 24 omitted)",
        "explanation": (
            "Since the 2020 amendment, foreign contribution CANNOT be transferred "
            "to any other person/organisation — even one registered under FCRA. "
            "Any sub-granting or onward transfer clause is a serious risk."
        ),
        "recommendation": (
            "Remove onward-transfer / sub-grant obligations, or restructure so each "
            "recipient receives FC directly under its own FCRA registration."
        ),
    },
    {
        "id": "FC-COMMINGLE",
        "title": "Mixing foreign contribution with other (local/domestic) funds",
        "category": "Banking & segregation",
        "severity": "high",
        "mode": "presence",
        "patterns": [
            r"co[-\s]?mingl", r"commingl",
            r"pool(?:ed|ing)?[^.\n]{0,30}(local|domestic|other|own) funds",
            r"single (?:bank )?account for all (?:funds|receipts)",
            r"same account[^.\n]{0,20}(local|domestic|indian) (?:funds|contributions)",
        ],
        "reference": "FCRA 2010 s.17 (designated FCRA account; FC kept separate)",
        "explanation": (
            "Foreign contribution must be kept in a dedicated FCRA account and not "
            "mixed with any local/domestic funds. Commingling breaches s.17."
        ),
        "recommendation": (
            "Keep FC in the designated FCRA account only; never pool it with local "
            "funds. Use a separate FCRA utilisation account for onward operations."
        ),
    },
    # --- Utilisation ----------------------------------------------------------
    {
        "id": "FC-SPECULATIVE",
        "title": "Use of foreign contribution for speculative / profit activities",
        "category": "Utilisation",
        "severity": "high",
        "mode": "presence",
        "patterns": [
            r"speculat", r"mutual fund", r"equit(?:y|ies)\b", r"\bshares?\b",
            r"stock market", r"derivative", r"trading in",
            r"invest[^.\n]{0,30}(securities|capital market|profit)",
        ],
        "reference": "FCRA Rules, Rule 4 (speculative activities); s.8 utilisation",
        "explanation": (
            "FCRA forbids using foreign contribution for speculative business or "
            "profit-making activities (e.g. mutual funds, shares, derivatives)."
        ),
        "recommendation": (
            "Restrict utilisation to the registered programmatic purpose; exclude "
            "any investment of FC in speculative instruments."
        ),
    },
    {
        "id": "FC-ADMIN-CAP",
        "title": "Administrative expenses above the 20% cap",
        "category": "Utilisation",
        "severity": "high",
        "mode": "threshold",
        "threshold_percent": 20,
        "patterns": [
            r"(?:administrative|admin|overhead|indirect|management)\s+(?:expense|cost|charge|fee)s?[^.\n%]{0,40}?(\d{1,3})\s*%",
            r"(\d{1,3})\s*%[^.\n]{0,30}(?:administrative|admin|overhead|indirect) (?:expense|cost)",
        ],
        "reference": "FCRA 2010 s.8 (as amended 2020) — admin cap reduced 50% → 20%",
        "explanation": (
            "Administrative expenses met out of foreign contribution are capped at "
            "20% of the FC utilised in a financial year (down from 50% pre-2020). "
            "A higher administrative/overhead percentage is a compliance risk "
            "(spending above 20% needs prior Central Government approval)."
        ),
        "recommendation": (
            "Cap administrative/overhead recovery at 20% of FC utilised, or obtain "
            "prior approval. Confirm the budget's indirect-cost line stays ≤20%."
        ),
    },
    {
        "id": "FC-UNRESTRICTED-PURPOSE",
        "title": "Vague / unrestricted use of funds",
        "category": "Utilisation",
        "severity": "medium",
        "mode": "presence",
        "patterns": [
            r"\bany purpose\b", r"unrestricted (?:use|funds|grant)",
            r"general (?:purposes|corporate purposes)",
            r"at (?:its|the recipient'?s) (?:sole )?discretion[^.\n]{0,30}(?:use|utilis|spend)",
        ],
        "reference": "FCRA 2010 s.8 — FC used only for the registered definite purpose",
        "explanation": (
            "Foreign contribution must be used only for the definite cultural, "
            "economic, educational, religious or social programme it was received "
            "for. Open-ended/unrestricted use language is a risk."
        ),
        "recommendation": (
            "Tie utilisation to a defined project scope/budget aligned with the "
            "recipient's FCRA-registered purposes; avoid 'any purpose' language."
        ),
    },
    # --- Eligibility ----------------------------------------------------------
    {
        "id": "FC-PROHIBITED-PERSON",
        "title": "Recipient may be a person barred from accepting FC",
        "category": "Eligibility",
        "severity": "high",
        "mode": "presence",
        "patterns": [
            r"political part", r"election candidate", r"candidate for election",
            r"\b(member of (?:parliament|legislature)|legislator)\b",
            r"government servant", r"judge\b", r"public servant",
            r"(?:newspaper|news channel|media) (?:company|organisation|organization)",
            r"correspondent[s]?\b|columnist|cartoonist|editor[s]?\b",
        ],
        "reference": "FCRA 2010 s.3 — persons prohibited from accepting FC",
        "explanation": (
            "Section 3 bars certain persons from accepting foreign contribution — "
            "election candidates, legislators, political parties, government "
            "servants, judges, and registered newspapers / media correspondents."
        ),
        "recommendation": (
            "Confirm the recipient is not a person prohibited under s.3 before "
            "accepting any foreign contribution."
        ),
    },
    # --- Required safeguards (absence checks → compliance checklist) ----------
    {
        "id": "FC-REGISTRATION-REF",
        "title": "FCRA registration / prior permission referenced",
        "category": "Registration",
        "severity": "high",
        "mode": "absence",
        "patterns": [
            r"fcra", r"foreign contribution (?:\(regulation\) )?act",
            r"prior permission", r"registered under the foreign contribution",
            r"fcra registration", r"registration number",
        ],
        "reference": "FCRA 2010 s.11 — registration or prior permission required",
        "explanation": (
            "A recipient must hold a valid FCRA registration (valid 5 years) or "
            "prior permission BEFORE accepting foreign contribution. The agreement "
            "does not appear to reference any FCRA registration / prior permission."
        ),
        "recommendation": (
            "State the recipient's FCRA registration number (or prior permission) "
            "and its validity, and make acceptance conditional on it being valid."
        ),
    },
    {
        "id": "FC-DESIGNATED-ACCOUNT",
        "title": "Designated FCRA bank account (SBI New Delhi) referenced",
        "category": "Banking & segregation",
        "severity": "high",
        "mode": "absence",
        "patterns": [
            r"state bank of india[^.\n]{0,40}(new delhi|sansad marg)",
            r"fcra account", r"designated (?:fc|fcra) account",
            r"sbi[^.\n]{0,30}new delhi",
        ],
        "reference": "FCRA 2010 s.17 (2020) — FC received only in SBI New Delhi Main Branch FCRA account",
        "explanation": (
            "Since the 2020 amendment, foreign contribution may be received ONLY in "
            "the designated 'FCRA Account' at the State Bank of India, New Delhi "
            "Main Branch (11 Sansad Marg). The agreement does not appear to direct "
            "funds to that designated account."
        ),
        "recommendation": (
            "Specify receipt of foreign contribution into the SBI New Delhi Main "
            "Branch FCRA account; do not nominate any other bank/branch for FC."
        ),
    },
    {
        "id": "FC-COMPLIANCE-CLAUSE",
        "title": "FCRA compliance representation / covenant present",
        "category": "Contract safeguards",
        "severity": "medium",
        "mode": "absence",
        "patterns": [
            r"compl(?:y|iance)[^.\n]{0,40}(fcra|foreign contribution|applicable laws?)",
            r"foreign contribution (?:\(regulation\) )?act",
            r"represent[^.\n]{0,40}(fcra|foreign contribution)",
        ],
        "reference": "Good practice under FCRA 2010 (ss.8, 11, 17, 18)",
        "explanation": (
            "The agreement does not appear to include an FCRA compliance "
            "representation/covenant binding the recipient to use, bank and report "
            "the funds in accordance with FCRA."
        ),
        "recommendation": (
            "Add an FCRA compliance clause: valid registration, segregated "
            "designated account, ≤20% admin, no onward transfer, and FCRA reporting."
        ),
    },
    {
        "id": "FC-REPORTING-CLAUSE",
        "title": "FCRA reporting / utilisation obligations present",
        "category": "Reporting",
        "severity": "medium",
        "mode": "absence",
        "patterns": [
            r"annual return", r"\bfc[-\s]?4\b", r"utilis(?:ation|ed) (?:report|certificate)",
            r"separate (?:books|account)s? of account", r"audited (?:utilisation|accounts)",
        ],
        "reference": "FCRA Rules — annual return Form FC-4 by 31 December; separate books of account",
        "explanation": (
            "The agreement does not appear to require FCRA reporting (annual return "
            "FC-4 by 31 December, separate books of account, utilisation reporting). "
            "Note: the earlier quarterly website-disclosure (Rule 13(b)) was omitted "
            "w.e.f. 1 July 2022, so its absence is NOT a defect."
        ),
        "recommendation": (
            "Require the recipient to maintain separate FCRA books and file the "
            "annual FC-4 return, and to share utilisation reports/certificates."
        ),
    },
    # --- Context flag ---------------------------------------------------------
    {
        "id": "FC-FOREIGN-SOURCE",
        "title": "Funder appears to be a foreign source (FCRA applies)",
        "category": "Applicability",
        "severity": "info",
        "mode": "presence",
        "patterns": [
            r"\bUSD\b|\bEUR\b|\bGBP\b|US\$|€|£",
            r"foreign (?:donor|source|grant|funder|company|foundation|agency)",
            r"international (?:agency|organisation|organization|foundation)",
            r"overseas (?:donor|grant|funder)",
        ],
        "reference": "FCRA 2010 s.2(1)(h)/(j) — 'foreign contribution' & 'foreign source'",
        "explanation": (
            "The document references a foreign currency / foreign donor, indicating "
            "the funds may be 'foreign contribution' from a 'foreign source' — so "
            "FCRA applies and the checks in this report are relevant."
        ),
        "recommendation": (
            "Confirm the funder's status as a foreign source and treat the funds as "
            "foreign contribution under FCRA."
        ),
    },
]
