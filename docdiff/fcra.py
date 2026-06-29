"""
FCRA Risk Engine — deterministic checks + an optional grounded AI review.

Pipeline (mirrors the rest of the app):
    parse the agreement text (reuse docdiff.extract / OCR)  -> done by the caller
    analyze_fcra(text)            -> deterministic findings + overall rating
    build_fcra_excel(result)      -> downloadable report
    ai_fcra_review(text, result, provider) -> analyst-style markdown (LLM), or ""

The deterministic engine always works offline. The AI layer is *grounded*: the
prompt carries the shipped FCRA rule references, and the model is told to rely on
those (not its own possibly-stale legal memory) — the honest way to keep a legal
tool current is to keep docdiff/fcra_rules.py current.

NOT LEGAL ADVICE — see docdiff/fcra_rules.py METADATA["disclaimer"].
"""

from __future__ import annotations

import re

from .fcra_rules import METADATA, RULES

# Order high → low so reports and ratings are stable.
_SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "info": 0}
_RISK_FLAG_SEVERITIES = ("high", "medium", "low")  # "info" is contextual, not a risk

# A risky keyword preceded by one of these is usually a *prohibition* (the
# compliant form, e.g. "shall NOT transfer the funds to…"), not a risk — so we
# skip that match. The AI layer re-reads the full clause and can still catch
# anything this simple guard gets wrong.
_NEGATION_CUES = re.compile(
    r"\b(not|no|never|cannot|can't|won't|shall not|may not|must not|"
    r"prohibit(?:ed|s)?|forbid(?:den)?|refrain|without (?:prior )?)\b",
    re.IGNORECASE,
)


def _is_negated(text: str, start: int, window: int = 35) -> bool:
    """True if a negation cue appears just before position `start`."""
    return bool(_NEGATION_CUES.search(text[max(0, start - window):start]))


def _context(text: str, start: int, end: int, width: int = 90) -> str:
    """A trimmed one-line snippet around a match, for display."""
    a = max(0, start - width)
    b = min(len(text), end + width)
    snippet = text[a:b].replace("\n", " ")
    snippet = re.sub(r"\s+", " ", snippet).strip()
    return ("…" if a > 0 else "") + snippet + ("…" if b < len(text) else "")


def _scan_presence(rule: dict, text: str) -> tuple[bool, list[str]]:
    """Return (matched?, snippets) for presence/threshold rules."""
    snippets: list[str] = []
    threshold = rule.get("threshold_percent")
    for pat in rule["patterns"]:
        for m in re.finditer(pat, text, re.IGNORECASE):
            if threshold is not None:
                # The pattern captures a percentage; only flag if it exceeds cap.
                num = next((g for g in m.groups() if g and g.isdigit()), None)
                if num is None or int(num) < threshold:
                    continue
            elif _is_negated(text, m.start()):
                # Likely a prohibition clause (the compliant form) — not a risk.
                continue
            snippets.append(_context(text, m.start(), m.end()))
            if len(snippets) >= 3:
                return True, snippets
    return (len(snippets) > 0), snippets


def _present(rule: dict, text: str) -> bool:
    """True if any of the rule's patterns appear (for absence checks)."""
    return any(re.search(p, text, re.IGNORECASE) for p in rule["patterns"])


def analyze_fcra(text: str) -> dict:
    """Run every rule over `text` and return a structured result bundle.

    Result:
        findings   list of triggered risks (presence/threshold matched, or an
                   absence safeguard missing), each a dict ready for display
        checklist  every absence-mode safeguard with present True/False
        rating     "High" | "Medium" | "Low"
        counts     {high, medium, low, info}
        metadata   the knowledge-base metadata (version, disclaimer, sources)
    """
    text = text or ""
    findings: list[dict] = []
    checklist: list[dict] = []
    counts = {"high": 0, "medium": 0, "low": 0, "info": 0}

    for rule in RULES:
        mode = rule.get("mode", "presence")
        base = {
            "id": rule["id"],
            "title": rule["title"],
            "category": rule["category"],
            "severity": rule["severity"],
            "reference": rule["reference"],
            "explanation": rule["explanation"],
            "recommendation": rule["recommendation"],
        }

        if mode == "absence":
            present = _present(rule, text)
            checklist.append({**base, "present": present})
            if not present:
                findings.append({**base, "kind": "missing", "evidence": []})
                counts[rule["severity"]] = counts.get(rule["severity"], 0) + 1
        else:  # presence or threshold
            matched, snippets = _scan_presence(rule, text)
            if matched:
                findings.append({**base, "kind": "found", "evidence": snippets})
                counts[rule["severity"]] = counts.get(rule["severity"], 0) + 1

    # Overall rating from the highest-severity *risk* finding (info excluded).
    rating = "Low"
    risk_findings = [f for f in findings if f["severity"] in _RISK_FLAG_SEVERITIES]
    if any(f["severity"] == "high" for f in risk_findings):
        rating = "High"
    elif any(f["severity"] == "medium" for f in risk_findings):
        rating = "Medium"

    # Sort findings by severity desc for display.
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f["severity"], 0), reverse=True)

    return {
        "findings": findings,
        "checklist": checklist,
        "rating": rating,
        "counts": counts,
        "risk_count": len(risk_findings),
        "metadata": METADATA,
    }


# --- Grounded AI review -------------------------------------------------------
def _rules_digest() -> str:
    """A compact list of the shipped rules to ground the LLM (no model memory)."""
    lines = []
    for r in RULES:
        lines.append(f"- [{r['severity'].upper()}] {r['id']} — {r['title']} "
                     f"(ref: {r['reference']})")
    return "\n".join(lines)


def _findings_digest(result: dict) -> str:
    if not result["findings"]:
        return "No deterministic findings were triggered."
    lines = []
    for f in result["findings"]:
        tag = "PRESENT" if f["kind"] == "found" else "MISSING SAFEGUARD"
        lines.append(f"- [{f['severity'].upper()}] {f['id']} {f['title']} "
                     f"({tag}; ref {f['reference']})")
    return "\n".join(lines)


def build_fcra_prompt(text: str, result: dict) -> str:
    """Prompt the LLM to review FCRA risk, grounded ONLY in the shipped rules."""
    return (
        "You are an FCRA compliance analyst in India. Review the funding/grant "
        "agreement text below for risks under the Foreign Contribution "
        "(Regulation) Act, 2010 (as amended in 2020 and the 2022 Rules).\n\n"
        "IMPORTANT: Base your analysis ONLY on the FCRA rule references provided "
        "here. Do NOT invent provisions or cite anything not listed. If something "
        "is unclear or may depend on the latest MHA notification, say so and "
        "advise verifying at fcraonline.nic.in.\n\n"
        "FCRA RULE REFERENCES (the only basis for your analysis):\n"
        + _rules_digest()
        + "\n\nDETERMINISTIC PRE-SCAN FINDINGS (from a keyword engine — confirm, "
        "refine, and explain these against the agreement text; do not blindly "
        "trust them):\n"
        + _findings_digest(result)
        + f"\n\nThe pre-scan overall rating was: {result['rating']}.\n\n"
        "Write a concise, professional review in markdown with these sections:\n"
        "1. **Overall FCRA risk rating** — High / Medium / Low, one line why.\n"
        "2. **Key risks** — each material risk, where it appears in the text, why "
        "it matters, and the FCRA section it relates to (from the references "
        "above).\n"
        "3. **Missing safeguards** — FCRA protections the agreement should add.\n"
        "4. **Recommended actions** — specific, practical fixes.\n"
        "5. End with: *This is an automated checklist, not legal advice — verify "
        "against the latest MHA notifications and consult a professional.*\n\n"
        "AGREEMENT TEXT:\n" + (text or "")[:8000]
    )


def ai_fcra_review(text: str, result: dict, provider) -> str:
    """Analyst-style FCRA review via the configured LLM, or "" if none/failed.

    Safe with any provider: non-LLM providers return "" (the UI then shows the
    deterministic findings only)."""
    return provider.narrate(build_fcra_prompt(text, result))


# --- Excel report -------------------------------------------------------------
def build_fcra_excel(result: dict) -> bytes:
    """Write the findings + checklist to an .xlsx report."""
    import io
    import pandas as pd

    findings_rows = [{
        "Severity": f["severity"].upper(),
        "Risk ID": f["id"],
        "Title": f["title"],
        "Category": f["category"],
        "Type": "Present in text" if f["kind"] == "found" else "Missing safeguard",
        "FCRA reference": f["reference"],
        "Why it matters": f["explanation"],
        "Recommendation": f["recommendation"],
        "Evidence": " | ".join(f.get("evidence") or []),
    } for f in result["findings"]]

    checklist_rows = [{
        "Safeguard": c["title"],
        "Present?": "Yes" if c["present"] else "No",
        "Severity if missing": c["severity"].upper(),
        "FCRA reference": c["reference"],
    } for c in result["checklist"]]

    meta = result["metadata"]
    summary_rows = [
        {"Field": "Overall rating", "Value": result["rating"]},
        {"Field": "High-risk findings", "Value": result["counts"].get("high", 0)},
        {"Field": "Medium-risk findings", "Value": result["counts"].get("medium", 0)},
        {"Field": "Knowledge-base version", "Value": meta["version"]},
        {"Field": "Last reviewed", "Value": meta["last_reviewed"]},
        {"Field": "Disclaimer", "Value": meta["disclaimer"]},
    ]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame(summary_rows).to_excel(xl, sheet_name="Summary", index=False)
        pd.DataFrame(findings_rows or [{"Severity": "", "Risk ID": "",
                     "Title": "No findings"}]).to_excel(
            xl, sheet_name="Findings", index=False)
        pd.DataFrame(checklist_rows).to_excel(xl, sheet_name="Checklist", index=False)
    return buf.getvalue()
