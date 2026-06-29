"""
Stage 4 — COMPARE.

Goal: for every matched/added/removed clause, produce one tidy "Change" record:
    - what literally changed (word-level diff),
    - whether any NUMBERS changed,
    - a category and a severity score so we can RANK the important stuff on top.

Ranking philosophy (matches the project brief):
    numbers changed  > clause added / removed > wording change > formatting-only
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .align import Pair
from .numbers import NumberChange, diff_numbers


# Severity scores. Higher = more important = shown nearer the top.
SEV_NUMBER = 100      # a figure changed — the loudest signal
SEV_STRUCTURE = 80    # a whole clause was added or removed
SEV_WORDING = 50      # the meaning/wording changed
SEV_FORMATTING = 10   # only spacing/punctuation/case differs
SEV_NONE = 0          # identical


@dataclass
class Change:
    label: str               # clause label, e.g. "1.1"
    category: str            # "Number change" | "Clause added" | ... (see below)
    severity: int            # one of the SEV_* scores
    old_text: str
    new_text: str
    number_changes: list[NumberChange] = field(default_factory=list)
    similarity: float = 1.0  # meaning-similarity of the matched pair
    diff_html: str = ""      # word-level old->new diff, as styled HTML


def compare_pairs(pairs: list[Pair]) -> list[Change]:
    """Turn aligned pairs into ranked Change records (most important first)."""
    changes: list[Change] = []
    for p in pairs:
        change = _classify(p)
        if change is not None:
            changes.append(change)

    # Sort by severity (desc), then by how much meaning shifted (lower sim first).
    changes.sort(key=lambda c: (-c.severity, c.similarity))
    return changes


def _classify(pair: Pair) -> Change | None:
    # --- Whole clause removed ---
    if pair.new is None and pair.old is not None:
        return Change(
            label=pair.old.label,
            category="Clause removed",
            severity=SEV_STRUCTURE,
            old_text=pair.old.text,
            new_text="",
            number_changes=diff_numbers(pair.old.text, ""),
            similarity=0.0,
            diff_html=_diff_html(pair.old.text, ""),
        )

    # --- Whole clause added ---
    if pair.old is None and pair.new is not None:
        return Change(
            label=pair.new.label,
            category="Clause added",
            severity=SEV_STRUCTURE,
            old_text="",
            new_text=pair.new.text,
            number_changes=diff_numbers("", pair.new.text),
            similarity=0.0,
            diff_html=_diff_html("", pair.new.text),
        )

    # --- Matched pair: figure out HOW it changed ---
    old_text, new_text = pair.old.text, pair.new.text

    if old_text == new_text:
        return None  # identical — nothing to report

    num_changes = diff_numbers(old_text, new_text)

    if num_changes:
        category, severity = "Number change", SEV_NUMBER
    elif _normalize(old_text) == _normalize(new_text):
        category, severity = "Formatting only", SEV_FORMATTING
    else:
        category, severity = "Wording change", SEV_WORDING

    return Change(
        label=pair.new.label or pair.old.label,
        category=category,
        severity=severity,
        old_text=old_text,
        new_text=new_text,
        number_changes=num_changes,
        similarity=pair.similarity,
        diff_html=_diff_html(old_text, new_text),
    )


def _normalize(text: str) -> str:
    """Collapse whitespace, drop case and most punctuation — for 'formatting only'."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)   # strip punctuation
    text = re.sub(r"\s+", " ", text)      # collapse whitespace
    return text.strip()


def _diff_html(old_text: str, new_text: str) -> str:
    """Word-level diff rendered as HTML: deletions struck red, additions green."""
    old_words = old_text.split()
    new_words = new_text.split()
    sm = SequenceMatcher(None, old_words, new_words)

    out: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        old_chunk = _esc(" ".join(old_words[i1:i2]))
        new_chunk = _esc(" ".join(new_words[j1:j2]))
        # Dark text colours are set explicitly so the diff stays readable on the
        # light highlight backgrounds regardless of the app's light/dark theme.
        deleted = (
            f"<span style='background:#ffd7d5;color:#b91c1c;"
            f"text-decoration:line-through'>{old_chunk}</span>"
        )
        inserted = f"<span style='background:#cdffd8;color:#15803d'>{new_chunk}</span>"
        if tag == "equal":
            out.append(old_chunk)
        elif tag == "delete":
            out.append(deleted)
        elif tag == "insert":
            out.append(inserted)
        elif tag == "replace":
            out.append(deleted)
            out.append(inserted)
    return " ".join(x for x in out if x)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
