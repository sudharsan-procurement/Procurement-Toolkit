"""
Stage 2 — SEGMENT.

Goal: split one long blob of text into meaningful chunks ("segments").

For contracts, the natural chunk is a numbered clause:
    "1.", "1.1", "1.1.2", "12.3.4", "Article 5", "Section 7", "(a)", "(iv)" ...

Strategy (rule-based, no AI):
    - Walk the text line by line.
    - When a line STARTS with something that looks like a clause number/heading,
      start a new segment.
    - Otherwise, append the line to the current segment.

If the document has no clause numbering at all (e.g. a plain memo), we fall back
to splitting on blank lines into paragraphs, so the tool still works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Segment:
    label: str   # the clause number/heading we detected, e.g. "1.1" or "(para 4)"
    text: str    # the full clause text (including the label line)


# A line is treated as the START of a new clause if it begins with one of these.
# We keep the patterns conservative so ordinary sentences aren't mistaken for clauses.
_CLAUSE_PATTERNS = [
    r"\d+(?:\.\d+)*\.?",                 # 1   1.   1.1   1.1.2   12.3.4
    r"(?:ARTICLE|Article|SECTION|Section|Clause|CLAUSE)\s+[\dIVXLC]+",  # Article 5
    r"\([a-zA-Z]\)",                     # (a) (b) (c)
    r"\((?:i{1,3}|iv|v|vi{0,3}|ix|x)\)",  # (i) (ii) (iii) (iv) ...  roman
    r"[A-Z]\.",                          # A.  B.  (single capital + dot)
]
_CLAUSE_START = re.compile(
    r"^\s*(" + "|".join(_CLAUSE_PATTERNS) + r")\s+\S"
)


def segment(text: str) -> list[Segment]:
    """Split text into clause segments, falling back to paragraphs if needed."""
    lines = [ln.rstrip() for ln in text.splitlines()]

    segments = _segment_by_clause(lines)

    # If clause detection barely fired, this probably isn't a numbered contract.
    # Fall back to paragraph splitting so the tool still produces useful output.
    if len(segments) < 3:
        segments = _segment_by_paragraph(text)

    # Drop empty/whitespace-only segments.
    return [s for s in segments if s.text.strip()]


def _segment_by_clause(lines: list[str]) -> list[Segment]:
    segments: list[Segment] = []
    current_label = "preamble"
    current_lines: list[str] = []

    def flush():
        if current_lines:
            joined = "\n".join(current_lines).strip()
            if joined:
                segments.append(Segment(label=current_label, text=joined))

    for line in lines:
        match = _CLAUSE_START.match(line)
        if match:
            flush()
            current_label = match.group(1).strip()
            current_lines = [line.strip()]
        else:
            current_lines.append(line)

    flush()
    return segments


def _segment_by_paragraph(text: str) -> list[Segment]:
    # Split on one-or-more blank lines.
    blocks = re.split(r"\n\s*\n", text)
    segments: list[Segment] = []
    n = 1
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        segments.append(Segment(label=f"para {n}", text=block))
        n += 1
    return segments
