"""
Quote Intelligence — the provider-INDEPENDENT business logic.

Services (kept modular so each can grow independently):
    parse_quote_file   -> Document Parser + OCR (reuses extract / tables)
    analyze_quotes     -> AI Analysis Service (orchestration)
    _score_vendors     -> Scoring Engine (weighted procurement score)
    _commercial/_technical/_risk -> analysis generators
    _recommendation    -> Recommendation Engine

The AIProvider only does extraction (and optional narration). Everything here —
scoring weights, comparisons, ranking, risk rules — stays the same no matter which
provider is used, so the AI brain can be swapped freely.
"""

from __future__ import annotations

import re

from .ai_providers import QUOTE_FIELDS

# Weighted scoring factors (must sum to 1.0).
SCORE_WEIGHTS = {
    "Cost Competitiveness": 0.40,
    "Delivery Timeline": 0.20,
    "Warranty": 0.15,
    "Payment Terms": 0.10,
    "Technical Compliance": 0.15,
}
LOW_CONFIDENCE = 0.75  # fields below this are flagged for manual verification


# --- Document Parser + OCR service -------------------------------------------
def parse_quote_file(file_bytes: bytes, filename: str) -> dict:
    """Read a quote file into {text, items_df, note}. Reuses extract + tables."""
    from .extract import extract

    result = {"text": "", "items_df": None, "note": ""}
    try:
        ex = extract(file_bytes, filename)
        result["text"] = ex.text
        result["note"] = ex.note
    except Exception as e:  # noqa: BLE001
        result["note"] = f"Text extraction failed: {e}"

    try:
        from .tables import file_to_dataframe
        result["items_df"] = file_to_dataframe(file_bytes, filename)
    except Exception:
        result["items_df"] = None  # line-item table is optional
    return result


# --- Numeric helpers ----------------------------------------------------------
def _num(value):
    if value is None:
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", str(value))
    return float(m.group(0).replace(",", "")) if m else None


def _days(value):
    """Convert a duration phrase to days (weeks*7, months*30)."""
    if not value:
        return None
    s = str(value).lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month|year)", s)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]


def _months(value):
    if not value:
        return None
    s = str(value).lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(month|year|yr)", s)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return n * (12 if unit in ("year", "yr") else 1)


def _val(fields, key):
    cell = fields.get(key) or {}
    return cell.get("value")


# --- AI Analysis Service (orchestration) -------------------------------------
def analyze_quotes(files: list[tuple], provider) -> dict:
    """
    files: list of (filename, file_bytes). Returns the full analysis bundle.
    """
    vendors = []
    raw_quotes = []
    for filename, file_bytes in files:
        parsed = parse_quote_file(file_bytes, filename)
        vendor_hint = filename.rsplit(".", 1)[0]
        fields = provider.extract(parsed["text"], parsed["items_df"], vendor_hint)
        name = (_val(fields, "Vendor Name") or vendor_hint).strip()
        vendors.append({
            "name": name,
            "file": filename,
            "fields": fields,
            "note": parsed["note"],
            "text": parsed["text"],
            "total": _num(_val(fields, "Total Value")),
            "delivery_days": _days(_val(fields, "Delivery Timeline")),
            "warranty_months": _months(_val(fields, "Warranty Details")),
        })
        raw_quotes.append((name, parsed["text"]))

    scores = _score_vendors(vendors)
    ranking = sorted(scores, key=lambda s: s["total_score"], reverse=True)
    vendors_with_total = sum(1 for v in vendors if v["total"] is not None)

    analysis = {
        "vendors": vendors,
        "scores": scores,
        "ranking": ranking,
        "commercial": _commercial(vendors),
        "technical": _technical(vendors),
        "risk": _risk(vendors),
        "low_confidence": _low_confidence(vendors),
        "provider_name": provider.name,
        "vendors_with_total": vendors_with_total,
        # Holistic LLM reasoning (empty string unless a real LLM provider is used).
        "reasoned": provider.reason(raw_quotes),
    }
    analysis["recommendation"] = _recommendation(ranking, vendors, provider)
    return analysis


# --- Scoring Engine -----------------------------------------------------------
def _normalize_lower_better(values: dict):
    """Smaller value = better. Returns {name: 0..100}; unknowns get a neutral 60."""
    known = {n: v for n, v in values.items() if v is not None and v > 0}
    if not known:
        return {n: 60.0 for n in values}
    best = min(known.values())
    return {n: (best / values[n] * 100 if values.get(n) else 60.0) for n in values}


def _normalize_higher_better(values: dict):
    known = {n: v for n, v in values.items() if v is not None and v > 0}
    if not known:
        return {n: 50.0 for n in values}
    best = max(known.values())
    return {n: (values[n] / best * 100 if values.get(n) else 50.0) for n in values}


def _payment_score(payment_text):
    """Rough rubric: credit terms are better for the buyer than full advance."""
    if not payment_text:
        return 60.0
    s = str(payment_text).lower()
    if "advance" in s and ("100" in s or "full" in s):
        return 30.0
    m = re.search(r"(\d+)\s*days?\s*credit|net\s*(\d+)", s)
    if m:
        days = int(m.group(1) or m.group(2))
        return min(100.0, 60 + days)  # more credit days → higher
    if "advance" in s:
        return 50.0
    return 65.0


def _technical_score(fields):
    """Heuristic completeness: how many key fields are present (LLM does better)."""
    keys = ["Warranty Details", "Delivery Timeline", "GST / Taxes",
            "Total Value", "Validity Period"]
    present = sum(1 for k in keys if _val(fields, k))
    return 50.0 + (present / len(keys)) * 50.0


def _score_vendors(vendors) -> list:
    names = [v["name"] for v in vendors]
    cost = _normalize_lower_better({v["name"]: v["total"] for v in vendors})
    delivery = _normalize_lower_better({v["name"]: v["delivery_days"] for v in vendors})
    warranty = _normalize_higher_better({v["name"]: v["warranty_months"] for v in vendors})
    payment = {v["name"]: _payment_score(_val(v["fields"], "Payment Terms")) for v in vendors}
    technical = {v["name"]: _technical_score(v["fields"]) for v in vendors}

    out = []
    for v in vendors:
        n = v["name"]
        components = {
            "Cost Competitiveness": cost[n],
            "Delivery Timeline": delivery[n],
            "Warranty": warranty[n],
            "Payment Terms": payment[n],
            "Technical Compliance": technical[n],
        }
        total = sum(components[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
        out.append({"name": n, "components": components, "total_score": round(total, 1)})
    return out


# --- Analysis generators ------------------------------------------------------
def _commercial(vendors) -> list[str]:
    bullets = []
    totals = {v["name"]: v["total"] for v in vendors if v["total"]}
    if totals:
        avg = sum(totals.values()) / len(totals)
        cheapest = min(totals, key=totals.get)
        diff = (avg - totals[cheapest]) / avg * 100 if avg else 0
        bullets.append(f"{cheapest} is the lowest-priced quote, about "
                       f"{diff:.1f}% below the average.")
        dearest = max(totals, key=totals.get)
        if dearest != cheapest:
            gap = (totals[dearest] - totals[cheapest]) / totals[cheapest] * 100
            bullets.append(f"{dearest} is the most expensive — {gap:.1f}% above "
                           f"{cheapest}.")
    warr = {v["name"]: v["warranty_months"] for v in vendors if v["warranty_months"]}
    if warr:
        best = max(warr, key=warr.get)
        bullets.append(f"{best} offers the longest warranty "
                       f"({int(warr[best])} months).")
    deliv = {v["name"]: v["delivery_days"] for v in vendors if v["delivery_days"]}
    if deliv:
        fastest = min(deliv, key=deliv.get)
        bullets.append(f"{fastest} has the shortest delivery timeline "
                       f"({int(deliv[fastest])} days).")
    for v in vendors:
        pay = _val(v["fields"], "Payment Terms")
        if pay:
            bullets.append(f"{v['name']} payment terms: {pay}.")
    return bullets


def _technical(vendors) -> list[str]:
    bullets = []
    for v in vendors:
        missing = [f for f in ("Warranty Details", "Delivery Timeline", "GST / Taxes")
                   if not _val(v["fields"], f)]
        if not missing:
            bullets.append(f"{v['name']} provides complete warranty, delivery and "
                           f"tax details.")
        else:
            bullets.append(f"{v['name']} does not specify: "
                           + ", ".join(m.lower() for m in missing) + ".")
    return bullets


def _risk(vendors) -> list[str]:
    bullets = []
    for v in vendors:
        if not _val(v["fields"], "Warranty Details"):
            bullets.append(f"Warranty information missing in {v['name']} quote.")
        if not _val(v["fields"], "Delivery Timeline"):
            bullets.append(f"Delivery commitment not specified in {v['name']} quote.")
        if not _val(v["fields"], "GST / Taxes"):
            bullets.append(f"GST / tax details not found in {v['name']} quote.")
        if v["total"] is None:
            bullets.append(f"Total value could not be read from {v['name']} quote.")
    if not bullets:
        bullets.append("No major documentation gaps detected across the quotes.")
    return bullets


def _low_confidence(vendors) -> list[dict]:
    flagged = []
    for v in vendors:
        for field, cell in v["fields"].items():
            conf = cell.get("confidence", 0.0)
            if cell.get("value") and conf < LOW_CONFIDENCE:
                flagged.append({"vendor": v["name"], "field": field,
                                "value": cell.get("value"), "confidence": conf})
    return flagged


# --- Recommendation Engine ----------------------------------------------------
def _recommendation(ranking, vendors, provider) -> str:
    if not ranking:
        return "No quotations could be analyzed."
    top = ranking[0]
    by_name = {v["name"]: v for v in vendors}
    tv = by_name.get(top["name"], {})

    # Deterministic narrative (works everywhere, including the free cloud host).
    parts = [f"{len(vendors)} quotation(s) were analyzed."]
    totals = {v["name"]: v["total"] for v in vendors if v["total"]}
    if totals:
        cheapest = min(totals, key=totals.get)
        parts.append(f"{cheapest} offers the lowest total cost.")
    if tv.get("warranty_months"):
        parts.append(f"{top['name']} provides a {int(tv['warranty_months'])}-month "
                     f"warranty.")
    if tv.get("delivery_days"):
        parts.append(f"{top['name']} commits to delivery in about "
                     f"{int(tv['delivery_days'])} days.")
    parts.append(f"On the weighted procurement score (cost, delivery, warranty, "
                 f"payment, technical), {top['name']} ranks highest at "
                 f"{top['total_score']:.0f}/100 and appears to offer the best overall "
                 f"value.")
    deterministic = " ".join(parts)

    # If a richer provider (e.g. Ollama) is available, let it narrate instead.
    context_lines = [f"Ranking (weighted score): " +
                     ", ".join(f"{r['name']} {r['total_score']:.0f}/100" for r in ranking)]
    for v in vendors:
        context_lines.append(
            f"{v['name']}: total={v['total']}, delivery_days={v['delivery_days']}, "
            f"warranty_months={v['warranty_months']}, "
            f"payment={_val(v['fields'], 'Payment Terms')}")
    narrated = provider.recommend("\n".join(context_lines))
    return narrated or deterministic
