"""
Difference summary — turn raw detected changes into a business-friendly brief.

This is a RULE-BASED engine (no AI API needed, fully offline). It reads the list
of `Change` objects produced by docdiff.compare and writes a plain-English
"Executive Summary" grouped into:

    Commercial   - pricing, payment terms, delivery timelines
    Legal        - liability, indemnity, termination, confidentiality, …
    Operational  - scope, deliverables, responsibilities, notice periods
    Risk         - increased penalties, reduced warranty, added obligations

It deliberately avoids diff jargon and explains changes in natural language.

(If a cloud LLM is wired in later, the app can swap this out — but the app must
keep working without any external API, which this guarantees.)
"""

from __future__ import annotations

import re

# Each topic: a friendly name and the keywords that identify it in clause text.
# Order matters — earlier, more specific topics win.
_TOPIC_KEYWORDS = [
    ("Payment terms", ["payment", "payable", "due within", "net ", "invoice"]),
    ("Pricing", ["price", "pricing", "fee", "cost", "charge", "rate", "discount"]),
    ("Penalty", ["penalty", "penalties", "liquidated damages", "late fee"]),
    ("Delivery timeline", ["delivery", "deliver", "milestone", "dispatch",
                           "shipment", "lead time"]),
    ("Warranty", ["warranty", "warranties", "guarantee"]),
    ("Liability", ["liability", "liable", "indemnit"]),
    ("Indemnity", ["indemnity", "indemnif"]),
    ("Termination", ["terminate", "termination"]),
    ("Confidentiality", ["confidential", "non-disclosure", "nda"]),
    ("Governing law", ["governing law", "jurisdiction", "arbitration", "dispute"]),
    ("Scope of work", ["scope", "deliverable", "services to be"]),
    ("Responsibilities", ["responsib", "obligation", "shall ensure", "shall provide"]),
    ("Notice period", ["notice"]),
    ("Term/Duration", ["term of", "duration", "period of", "commence"]),
]

# Which executive-summary bucket each topic belongs to.
_TOPIC_BUCKET = {
    "Payment terms": "commercial",
    "Pricing": "commercial",
    "Delivery timeline": "commercial",
    "Penalty": "legal",
    "Warranty": "legal",
    "Liability": "legal",
    "Indemnity": "legal",
    "Termination": "legal",
    "Confidentiality": "legal",
    "Governing law": "legal",
    "Scope of work": "operational",
    "Responsibilities": "operational",
    "Notice period": "operational",
    "Term/Duration": "operational",
}

_UNIT_WORDS = ("days", "day", "months", "month", "years", "year",
               "weeks", "week", "hours", "hour", "%", "percent")

_MAX_BULLETS = 10  # per section, to stay readable on large documents


def _topic_of(change) -> str | None:
    blob = f"{change.old_text} {change.new_text}".lower()
    for topic, keywords in _TOPIC_KEYWORDS:
        if any(kw in blob for kw in keywords):
            return topic
    return None


def _unit_after(value: str, text: str) -> str:
    """If `value` is followed by a unit word in `text` (e.g. '30 days'), return it."""
    if not value:
        return ""
    m = re.search(re.escape(value) + r"\s*([%A-Za-z]+)", text)
    if m:
        candidate = m.group(1)
        if candidate.lower().rstrip("s") in {u.rstrip("s") for u in _UNIT_WORDS}:
            return candidate
    return ""


def _bullet(change, topic: str | None) -> list[str]:
    """Natural-language sentence(s) describing one change (no diff jargon).

    A single clause can change several numbers (e.g. fee AND payment days), so
    number changes return one sentence per figure."""
    if change.category == "Clause added":
        return [f"A new {topic.lower()} clause was introduced." if topic
                else "A new clause was added."]
    if change.category == "Clause removed":
        return [f"The {topic.lower()} clause was removed." if topic
                else "A clause was removed."]
    if change.category == "Number change" and change.number_changes:
        subject = topic or "A value"
        out = []
        for nc in change.number_changes:
            unit = (_unit_after(nc.old, change.old_text)
                    or _unit_after(nc.new, change.new_text))
            if unit:
                out.append(f"{subject} changed from {nc.old} {unit} to {nc.new} {unit}.")
            else:
                out.append(f"{subject} changed from {nc.old} to {nc.new}.")
        return out
    return []


def _fmt(value: str, unit: str) -> str:
    return f"{value} {unit}" if unit else value


def _risk(change, topic: str | None) -> str | None:
    """Flag changes that increase risk/exposure, in plain language."""
    if change.category == "Clause added" and topic in (
        "Penalty", "Indemnity", "Liability", "Responsibilities"
    ):
        return f"Additional obligations introduced (new {topic.lower()} clause)."

    if change.category == "Clause removed" and topic in (
        "Warranty", "Liability", "Indemnity"
    ):
        return f"{topic} protection was removed."

    if change.category == "Number change":
        # A clause may change several numbers; pick the one relevant to the topic
        # (e.g. for payment terms / warranty we want the figure with a TIME unit).
        for nc in change.number_changes:
            desc = nc.description  # "increased by 50.0%" / "decreased by 50.0%"
            unit = (_unit_after(nc.old, change.old_text)
                    or _unit_after(nc.new, change.new_text))
            if topic == "Warranty" and "decreased" in desc:
                return f"Warranty period reduced (from {_fmt(nc.old, unit)} to {_fmt(nc.new, unit)})."
            if topic == "Payment terms" and "increased" in desc and unit:
                return f"Payment period extended (from {_fmt(nc.old, unit)} to {_fmt(nc.new, unit)})."
            if topic == "Penalty" and "increased" in desc:
                return f"Penalty amount increased (from {nc.old} to {nc.new})."
            if topic == "Liability" and "increased" in desc:
                return f"Liability exposure increased (from {nc.old} to {nc.new})."
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _cap(items: list[str], n: int = _MAX_BULLETS) -> list[str]:
    if len(items) <= n:
        return items
    return items[:n] + [f"…and {len(items) - n} more."]


def summarize_changes(changes) -> dict:
    """Build the structured executive summary from the list of Change objects."""
    material = [c for c in changes if c.category != "Formatting only"]

    sections = {"commercial": [], "legal": [], "operational": []}
    wording = {"commercial": 0, "legal": 0, "operational": 0, "other": 0}
    risks: list[str] = []

    for c in material:
        topic = _topic_of(c)
        bucket = _TOPIC_BUCKET.get(topic, "other")

        if c.category == "Wording change":
            wording[bucket] += 1
        else:
            target = bucket if bucket in sections else "operational"
            for bullet in _bullet(c, topic):
                sections[target].append(bullet)

        risk = _risk(c, topic)
        if risk:
            risks.append(risk)

    # Add an aggregate "wording revised" line per bucket so big docs stay readable.
    for bucket in ("commercial", "legal", "operational"):
        if wording[bucket]:
            sections[bucket].append(
                f"Wording was revised in {wording[bucket]} clause(s)."
            )

    return {
        "total": len(material),
        "commercial": _cap(_dedupe(sections["commercial"])),
        "legal": _cap(_dedupe(sections["legal"])),
        "operational": _cap(_dedupe(sections["operational"])),
        "risks": _cap(_dedupe(risks)),
    }


_SECTION_TITLES = [
    ("commercial", "Commercial Changes"),
    ("legal", "Legal Changes"),
    ("operational", "Operational Changes"),
    ("risks", "Risk Indicators"),
]


def summary_to_text(summary: dict) -> str:
    """Render the summary as plain text (for the Copy Summary button)."""
    lines = ["Executive Summary", "=================",
             f"{summary['total']} differences detected.", ""]
    for key, title in _SECTION_TITLES:
        bullets = summary.get(key, [])
        if bullets:
            lines.append(f"{title}:")
            lines.extend(f"  - {b}" for b in bullets)
            lines.append("")
    return "\n".join(lines).strip()
