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


# --- Optional AI layer (used when an LLM provider is configured) --------------
# The rule-based summary above always works offline. When the user has enabled an
# LLM provider (Gemini cloud or local Ollama), we can additionally ask it for an
# analyst-style review of the SAME detected changes. The provider abstraction
# keeps this engine-agnostic; non-LLM providers return "" and the caller simply
# shows the rule-based summary instead.

def build_change_digest(changes, max_changes: int = 40, snippet: int = 240) -> str:
    """Compact, token-bounded text describing the material changes for an LLM.

    We send only the *changed* clauses (old -> new), not the whole document, and
    cap both the number of changes and each snippet's length so the request stays
    small and cheap.
    """
    material = [c for c in changes if c.category != "Formatting only"]
    lines: list[str] = []
    for c in material[:max_changes]:
        head = f"[{c.category}] clause {c.label}"
        if c.number_changes:
            nums = "; ".join(f"{nc.old} -> {nc.new} ({nc.description})"
                             for nc in c.number_changes)
            head += f" | figures: {nums}"
        lines.append(head)
        if c.category == "Clause added":
            lines.append(f"  NEW: {(c.new_text or '')[:snippet]}")
        elif c.category == "Clause removed":
            lines.append(f"  REMOVED: {(c.old_text or '')[:snippet]}")
        else:
            lines.append(f"  OLD: {(c.old_text or '')[:snippet]}")
            lines.append(f"  NEW: {(c.new_text or '')[:snippet]}")
    if len(material) > max_changes:
        lines.append(f"...and {len(material) - max_changes} more change(s).")
    return "\n".join(lines)


def build_contract_summary_prompt(digest: str) -> str:
    """Prompt for an analyst-style review of contract/agreement changes."""
    return (
        "You are a contracts analyst reviewing what changed between an ORIGINAL "
        "and a REVISED version of a legal document (e.g. a contract or "
        "agreement). Below is the list of detected changes (old -> new). Write a "
        "concise, professional review in markdown for a non-lawyer "
        "decision-maker.\n\n"
        "Cover, using short sections and bullet points:\n"
        "1. **What changed** — group related changes (commercial, legal, "
        "operational).\n"
        "2. **Why it matters** — the practical impact of each material change.\n"
        "3. **Risk flags** — anything that increases the reader's risk or "
        "obligations (higher penalties, reduced warranty/liability protection, "
        "longer payment terms, new obligations, removed protections). Call these "
        "out clearly.\n"
        "4. **Bottom line** — 1-2 sentences: is this revision favourable, "
        "neutral, or unfavourable to the reader, and what to double-check.\n\n"
        "Be specific with the figures shown. Do NOT invent changes that aren't "
        "listed. Keep it tight.\n\n"
        "DETECTED CHANGES:\n" + digest
    )


def ai_summarize_changes(changes, provider) -> str:
    """Analyst-style markdown review of the changes, or "" if no LLM is active.

    Safe to call with any provider: a non-LLM provider (or a failed cloud call)
    returns "", so the UI just falls back to the rule-based executive summary.
    """
    material = [c for c in changes if c.category != "Formatting only"]
    if not material:
        return ""
    digest = build_change_digest(changes)
    return provider.narrate(build_contract_summary_prompt(digest))
