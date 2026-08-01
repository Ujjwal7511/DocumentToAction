"""Organizational knowledge-base loader and lightweight TF-IDF retrieval.

No vector database — scikit-learn TfidfVectorizer + cosine similarity over a
small folder of markdown/JSON standards documents.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KB_DIR = Path(__file__).resolve().parent / "knowledge_base"


@dataclass
class KBDocument:
    """A single knowledge-base standard document."""

    filename: str
    title: str
    content: str
    path: Path


def _title_from_markdown(text: str, filename: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return Path(filename).stem.replace("_", " ").replace("-", " ").title()


def load_knowledge_base(kb_dir: Optional[Path] = None) -> list[KBDocument]:
    """Load all markdown/JSON standards from the KB folder."""
    root = kb_dir or KB_DIR
    if not root.exists():
        logger.warning("Knowledge base directory missing: %s", root)
        return []

    docs: list[KBDocument] = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in {".md", ".markdown", ".json", ".txt"}:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read KB file %s: %s", path, exc)
            continue

        if path.suffix.lower() == ".json":
            try:
                data = json.loads(raw)
                title = str(data.get("title") or path.stem)
                content = str(data.get("content") or data.get("body") or raw)
            except json.JSONDecodeError:
                title = path.stem
                content = raw
        else:
            title = _title_from_markdown(raw, path.name)
            content = raw

        if not content.strip():
            continue
        docs.append(KBDocument(filename=path.name, title=title, content=content, path=path))

    return docs


def _snippet(text: str, max_len: int = 280) -> str:
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def retrieve_relevant_standards(
    query: str,
    top_k: int = 3,
    min_score: float = 0.05,
    kb_dir: Optional[Path] = None,
) -> list[dict]:
    """Return the top-k KB standards relevant to *query* via TF-IDF cosine similarity.

    Each result: {title, filename, score, snippet, content}.
    Returns an empty list when the KB is empty or the query is blank.
    """
    query = (query or "").strip()
    docs = load_knowledge_base(kb_dir)
    if not query or not docs:
        return []

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn is required for KB retrieval. pip install scikit-learn"
        ) from exc

    corpus = [d.content for d in docs]
    vectorizer = TfidfVectorizer(
        stop_words="english",
        max_df=0.95,
        min_df=1,
        ngram_range=(1, 2),
    )
    try:
        doc_matrix = vectorizer.fit_transform(corpus)
        query_vec = vectorizer.transform([query])
    except ValueError:
        # Empty vocabulary (e.g. all-stopword docs)
        return []

    scores = cosine_similarity(query_vec, doc_matrix).flatten()
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

    results: list[dict] = []
    for idx, score in ranked[:top_k]:
        if float(score) < min_score:
            continue
        d = docs[idx]
        results.append(
            {
                "title": d.title,
                "filename": d.filename,
                "score": float(score),
                "snippet": _snippet(d.content),
                "content": d.content,
            }
        )
    return results


def match_session_to_kb(
    session_id: int,
    top_k: int = 4,
    min_score: float = 0.08,
) -> list:
    """Build a query from session extracts, retrieve standards, and persist matches."""
    import db

    items = db.list_extracted_items(session_id=session_id)
    docs = db.list_documents(session_id)

    parts: list[str] = []
    for d in docs:
        parts.append(d.get("filename") or "")
        parts.append((d.get("content") or "")[:2000])
    for it in items:
        parts.append(it.get("content") or "")

    query = "\n".join(parts)
    if not query.strip():
        return []

    hits = retrieve_relevant_standards(query, top_k=top_k, min_score=min_score)
    persisted = []
    for hit in hits:
        # Attach to the highest-overlap extracted item when possible
        best_item_id = None
        best_overlap = 0.0
        hit_tokens = set(hit["title"].lower().split()) | set(
            hit["snippet"].lower().split()[:20]
        )
        for it in items:
            it_tokens = set((it.get("content") or "").lower().split())
            if not it_tokens:
                continue
            overlap = len(hit_tokens & it_tokens) / max(len(hit_tokens), 1)
            if overlap > best_overlap:
                best_overlap = overlap
                best_item_id = it.get("id")

        rationale = (
            f"TF-IDF cosine similarity {hit['score']:.3f} against session content."
        )
        match = db.add_kb_match(
            session_id=session_id,
            kb_title=hit["title"],
            kb_filename=hit["filename"],
            relevance_score=hit["score"],
            snippet=hit["snippet"],
            rationale=rationale,
            extracted_item_id=best_item_id,
        )
        persisted.append(match)

    return persisted


def format_kb_match(match: dict) -> dict:
    """UI-friendly representation of a KBMatch row."""
    return {
        "id": match.get("id"),
        "title": match.get("kb_title"),
        "filename": match.get("kb_filename"),
        "score": match.get("relevance_score"),
        "snippet": match.get("snippet"),
        "rationale": match.get("rationale"),
        "extracted_item_id": match.get("extracted_item_id"),
    }

