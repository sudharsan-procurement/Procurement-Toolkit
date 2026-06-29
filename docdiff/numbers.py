"""
Number handling — used by the COMPARE stage.

Why a whole module just for numbers? Because in contracts and tenders the numbers
ARE the substance: prices, percentages, dates, durations, penalty amounts,
notice periods. A normal text diff treats "30 days" -> "15 days" the same as any
other word change. We want changed figures flagged LOUDLY, so we pull them out
explicitly and compare them as values, not as text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Number:
    raw: str       # exactly as it appeared, e.g. "$1,250.00" or "30%"
    value: float   # parsed numeric value, e.g. 1250.0 or 30.0
    unit: str      # a rough unit tag: "money", "percent", or "" (plain)


# Matches money ($1,250.00 / USD 1,250 / £999), percentages (30%), and plain
# numbers with optional thousands separators and decimals (1,250.75 / 42).
_NUMBER_RE = re.compile(
    r"""
    (?P<money>[$£€]\s?\d[\d,]*(?:\.\d+)?)        # currency-symbol amounts
    |
    (?P<percent>\d[\d,]*(?:\.\d+)?\s?%)          # percentages
    |
    (?P<plain>(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w.]))  # plain numbers
    """,
    re.VERBOSE,
)


def extract_numbers(text: str) -> list[Number]:
    """Find every number in a piece of text and parse it into value + unit."""
    found: list[Number] = []
    for m in _NUMBER_RE.finditer(text):
        raw = m.group(0).strip()
        if m.group("money"):
            unit = "money"
        elif m.group("percent"):
            unit = "percent"
        else:
            unit = ""
        value = _to_float(raw)
        if value is not None:
            found.append(Number(raw=raw, value=value, unit=unit))
    return found


def _to_float(raw: str) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", raw)  # strip $, £, %, commas, spaces
    if cleaned in ("", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


@dataclass
class NumberChange:
    old: str        # old figure as text, or "—" if newly added
    new: str        # new figure as text, or "—" if removed
    description: str  # plain-English summary, incl. % change where it makes sense


def diff_numbers(old_text: str, new_text: str) -> list[NumberChange]:
    """
    Compare the multiset of numbers in the old vs new clause.

    We match numbers that are unchanged, then report whatever is left over as
    added / removed / changed. This is intentionally simple and order-independent
    so "fee of 5% within 30 days" vs "within 30 days a fee of 7%" still spots the
    5% -> 7% change.
    """
    old_nums = extract_numbers(old_text)
    new_nums = extract_numbers(new_text)

    # Remove the numbers that appear identically in both (matched, no change).
    old_remaining = list(old_nums)
    new_remaining = list(new_nums)
    for on in list(old_remaining):
        for nn in list(new_remaining):
            if on.value == nn.value and on.unit == nn.unit:
                old_remaining.remove(on)
                new_remaining.remove(nn)
                break

    changes: list[NumberChange] = []

    # Pair up leftovers positionally as "changed" values, then report the rest
    # as pure additions or removals.
    paired = min(len(old_remaining), len(new_remaining))
    for i in range(paired):
        on, nn = old_remaining[i], new_remaining[i]
        changes.append(
            NumberChange(old=on.raw, new=nn.raw, description=_describe(on, nn))
        )
    for on in old_remaining[paired:]:
        changes.append(NumberChange(old=on.raw, new="—", description="figure removed"))
    for nn in new_remaining[paired:]:
        changes.append(NumberChange(old="—", new=nn.raw, description="figure added"))

    return changes


def _describe(old: Number, new: Number) -> str:
    direction = "increased" if new.value > old.value else "decreased"
    if old.value != 0:
        pct = (new.value - old.value) / abs(old.value) * 100
        return f"{direction} by {abs(pct):.1f}%"
    return direction
