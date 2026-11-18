"""
Semantic-similarity matching for Phase 4's dedup step.

WHY THIS EXISTS: the Phase 4 live replay showed exact dedup-key matching
(rules.build_dedup_key on independently-normalized LLM output) essentially
never collides — 1 exact match across 719 rows, vs. ~519 implied by the
ground truth — because nothing guarantees NORMALIZER/THEMER reproduce
identical wording or the identical theme for the same real-world failure
across two independent calls ("core dimension misjudgment" vs. "wrong core
dimensions" build different dedup keys for what is likely the same
defect). This module adds a semantic fallback: embed candidate and
baseline text and compare by cosine similarity within the same operation,
instead of requiring byte-identical strings.

This does NOT touch rules.build_dedup_key or extract_ground_truth.py's
copy of it — those still define the ground truth and remain the Stage-1
exact pre-filter. This module is the Stage-2 fallback for when Stage 1
finds nothing.

Uses the OpenAI embeddings API directly (not the Agents SDK's chat-style
Runner — embeddings aren't an agent call).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from openai import AsyncOpenAI

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / ".embedding_cache.json"


def embedding_text(failure_mode_en: str, failure_cause_en: str) -> str:
    """The exact text that gets embedded: concatenation of normalized
    English failure_mode + failure_cause, per the two-stage matching spec."""
    return f"{failure_mode_en} {failure_cause_en}".strip()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"embedding dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingCache:
    """Persisted to a local JSON file, keyed by row_id (per requirements).
    Each entry also stores the text it was computed from, so a row whose
    normalized text changed between runs (NORMALIZER/THEMER output isn't
    perfectly deterministic) gets re-embedded instead of silently reusing a
    stale vector for different content — the row_id key is honored, but
    correctness isn't sacrificed for it.
    """

    def __init__(self, path: Path = DEFAULT_CACHE_PATH):
        self.path = Path(path)
        self._entries: dict[str, dict] = {}
        self._hits = 0
        self._misses = 0
        self.load()

    def load(self) -> None:
        if self.path.exists():
            self._entries = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self._entries = {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self._entries), encoding="utf-8")

    def get(self, row_id: str, text: str) -> list[float] | None:
        entry = self._entries.get(row_id)
        if entry is not None and entry.get("text") == text:
            self._hits += 1
            return entry["embedding"]
        self._misses += 1
        return None

    def set(self, row_id: str, text: str, embedding: list[float]) -> None:
        self._entries[row_id] = {"text": text, "embedding": embedding}

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": len(self._entries)}


async def embed_texts_async(
    texts: list[str], *, model: str = DEFAULT_EMBEDDING_MODEL, client: AsyncOpenAI | None = None
) -> list[list[float]]:
    """Batched embedding call — the embeddings endpoint accepts a list of
    inputs in one request, so a whole batch costs one round trip."""
    if not texts:
        return []
    own_client = client is None
    client = client or AsyncOpenAI()
    try:
        response = await client.embeddings.create(input=texts, model=model)
        return [item.embedding for item in response.data]
    finally:
        if own_client:
            await client.close()
