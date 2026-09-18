"""TF-IDF retriever on docs/policy/*.md. No embeddings, no network."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

CORPUS_DIR = Path(__file__).resolve().parents[2] / "docs" / "policy"


def _load() -> list[tuple[str, str]]:
    docs = []
    if not CORPUS_DIR.exists():
        return docs
    for p in sorted(CORPUS_DIR.glob("*.md")):
        if p.name.startswith("."):
            continue
        docs.append((p.stem, p.read_text(encoding="utf-8", errors="ignore")))
    return docs



_cached_index: tuple[list[str], list[str], TfidfVectorizer, Any] | None = None


def reset_index_cache() -> None:
    """Clear cached TF-IDF index (for tests / doc updates)."""
    global _cached_index
    _cached_index = None


def _get_or_build_index() -> tuple[list[str], list[str], TfidfVectorizer, Any] | None:
    global _cached_index
    if _cached_index is None:
        docs = _load()
        if not docs:
            return None
        ids, texts = zip(*docs)
        ids_list, texts_list = list(ids), list(texts)
        vec = TfidfVectorizer().fit(texts_list)
        matrix = vec.transform(texts_list)
        _cached_index = (ids_list, texts_list, vec, matrix)
    return _cached_index


def retrieve(query: str, k: int = 2) -> list[dict]:
    if not query or not query.strip():
        return []
    index = _get_or_build_index()
    if index is None:
        return []
    ids, texts, vec, m = index
    q = vec.transform([query])
    scores = cosine_similarity(q, m)[0]
    ranked = sorted(zip(ids, texts, scores), key=lambda t: t[2], reverse=True)[:k]
    return [{"doc_id": i, "chunk": t[:600], "score": round(float(s), 4)}
            for i, t, s in ranked if s > 0]