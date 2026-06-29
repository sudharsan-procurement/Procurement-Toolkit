"""
Stage 3 — ALIGN.

Goal: decide which clause in the OLD document corresponds to which clause in the
NEW document — even if clauses were reordered, lightly reworded, added, or deleted.

How:
    1. Turn each clause into a "meaning fingerprint" (an embedding vector) using a
       small LOCAL model. Clauses that mean similar things get similar vectors.
    2. Build a similarity score between every old clause and every new clause.
    3. Find the single best one-to-one pairing across the whole document
       (optimal assignment). This is what makes REORDERING safe: we match by
       meaning, not by position.
    4. Pairs that score too low aren't real matches -> the old one counts as
       REMOVED and the new one as ADDED.

No API key, no internet call at run time (the model downloads once on first use).
If the model can't load, we fall back to a pure-text similarity so the app still
runs — just a little less smart about paraphrasing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .segment import Segment

# Small, fast, CPU-friendly, no API key. ~90 MB, fits the free Streamlit host.
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass
class Pair:
    old: Segment | None   # None means this clause was ADDED in the new doc
    new: Segment | None   # None means this clause was REMOVED from the old doc
    similarity: float      # 0..1 meaning-similarity for matched pairs


def _load_model():
    """Load the local embedding model, or return None if unavailable."""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(_MODEL_NAME)
    except Exception:
        return None


def _embed(model, texts: list[str]) -> np.ndarray:
    vecs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return np.asarray(vecs, dtype=np.float32)


def _similarity_matrix(old: list[Segment], new: list[Segment], model) -> np.ndarray:
    """Return an (len(old) x len(new)) matrix of 0..1 similarity scores."""
    if model is not None:
        eo = _embed(model, [s.text for s in old])
        en = _embed(model, [s.text for s in new])
        # Vectors are normalized, so a dot product == cosine similarity.
        sim = eo @ en.T
        # Cosine is in [-1, 1]; squash to [0, 1] so thresholds read naturally.
        return (sim + 1.0) / 2.0

    # ---- Fallback: no model available, use plain text similarity. ----
    from difflib import SequenceMatcher

    sim = np.zeros((len(old), len(new)), dtype=np.float32)
    for i, so in enumerate(old):
        for j, sn in enumerate(new):
            sim[i, j] = SequenceMatcher(None, so.text, sn.text).ratio()
    return sim


def align(
    old: list[Segment],
    new: list[Segment],
    threshold: float = 0.6,
) -> tuple[list[Pair], bool]:
    """
    Match old clauses to new clauses.

    Returns (pairs, used_model) where used_model tells the UI whether the smart
    local model was used or the text fallback kicked in.
    """
    model = _load_model()
    used_model = model is not None

    if not old and not new:
        return [], used_model
    if not old:
        return [Pair(old=None, new=s, similarity=0.0) for s in new], used_model
    if not new:
        return [Pair(old=s, new=None, similarity=0.0) for s in old], used_model

    sim = _similarity_matrix(old, new, model)

    # Optimal one-to-one assignment that MAXIMISES total similarity.
    # linear_sum_assignment minimises cost, so we feed it negative similarity.
    from scipy.optimize import linear_sum_assignment

    row_idx, col_idx = linear_sum_assignment(-sim)

    pairs: list[Pair] = []
    matched_old: set[int] = set()
    matched_new: set[int] = set()

    for i, j in zip(row_idx, col_idx):
        score = float(sim[i, j])
        if score >= threshold:
            pairs.append(Pair(old=old[i], new=new[j], similarity=score))
            matched_old.add(i)
            matched_new.add(j)
        # Below threshold: not a real match. Leave both to be reported separately.

    # Anything not confidently matched is a removal (old) or addition (new).
    for i, s in enumerate(old):
        if i not in matched_old:
            pairs.append(Pair(old=s, new=None, similarity=0.0))
    for j, s in enumerate(new):
        if j not in matched_new:
            pairs.append(Pair(old=None, new=s, similarity=0.0))

    return pairs, used_model
