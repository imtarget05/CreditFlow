"""TF-IDF retriever on docs/policy/*.md. No embeddings, no network."""
from __future__ import annotations

from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

CORPUS_DIR = Path(__file__).resolve().parents[2] / "docs" / "policy"


def _load() -> list[tuple[str, str]]:
    docs = []
    if not CORPUS_DIR.exists():
        return docs
    for p in sorted(CORPUS_DIR.glob("*.md")):
        docs.append((p.stem, p.read_text(encoding="utf-8")))
    return docs


def retrieve(query: str, k: int = 2) -> list[dict]:
    if not query or not query.strip():
        return []
    docs = _load()
    if not docs:
        return []
    ids, texts = zip(*docs)
    texts_list = list(texts)
    vec = TfidfVectorizer().fit(texts_list)
    m = vec.transform(texts_list)
    q = vec.transform([query])
    scores = cosine_similarity(q, m)[0]
    ranked = sorted(zip(ids, texts, scores), key=lambda t: t[2], reverse=True)[:k]
    return [{"doc_id": i, "chunk": t[:600], "score": round(float(s), 4)}
            for i, t, s in ranked if s > 0]