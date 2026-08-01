"""Cross-document conflict and repetition detection."""

from __future__ import annotations

import logging
import re
from typing import Any

import db
from llm_client import LLMError, generate_json, is_configured

logger = logging.getLogger(__name__)

CONFLICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "conflict_type": {
                        "type": "string",
                        "enum": ["conflict", "repetition"],
                    },
                    "description": {"type": "string"},
                    "item_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "0-based indices into the provided item list",
                    },
                },
                "required": ["conflict_type", "description", "item_indices"],
            },
        }
    },
    "required": ["findings"],
}

# Date / deadline phrases used to spot schedule conflicts across documents
_DATE_RE = re.compile(
    r"(?i)\b("
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2}(?:,?\s*\d{4})?"
    r"|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    r"|q[1-4]|end of (?:week|month|quarter|day)|eod|eow|eom"
    r")\b"
)

_TOPIC_KEYWORDS = (
    "deadline",
    "launch",
    "migrate",
    "migration",
    "cutover",
    "partner",
    "api",
    "cookie",
    "session",
    "jwt",
    "rollback",
    "security review",
)


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _shared_topic(a: str, b: str) -> bool:
    """True when both texts mention a shared project-topic keyword."""
    la, lb = (a or "").lower(), (b or "").lower()
    return any(k in la and k in lb for k in _TOPIC_KEYWORDS)


def _heuristic_conflicts(items: list[dict]) -> list[dict]:
    """Detect repetitions and likely conflicts without an LLM."""
    findings: list[dict] = []
    n = len(items)

    # Collect items that mention concrete dates (for multi-way schedule conflicts)
    dated = []
    for it in items:
        m = _DATE_RE.search(it.get("content") or "")
        if m:
            dated.append((it, m.group(0).lower()))

    for i in range(len(dated)):
        for j in range(i + 1, len(dated)):
            a, date_a = dated[i]
            b, date_b = dated[j]
            if a.get("document_id") == b.get("document_id"):
                continue
            if date_a == date_b:
                continue
            if not _shared_topic(a.get("content"), b.get("content")) and _jaccard(
                _tokenize(a.get("content")), _tokenize(b.get("content"))
            ) < 0.15:
                continue
            findings.append(
                {
                    "conflict_type": "conflict",
                    "description": (
                        f"Conflicting dates/deadlines: \"{(a.get('content') or '')[:100]}\" "
                        f"vs \"{(b.get('content') or '')[:100]}\""
                    ),
                    "item_ids": [a.get("id"), b.get("id")],
                }
            )

    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i], items[j]
            if a.get("document_id") == b.get("document_id"):
                continue  # cross-document only
            # Ignore near-identical heading stubs
            if len(a.get("content") or "") < 20 or len(b.get("content") or "") < 20:
                continue
            ta, tb = _tokenize(a.get("content")), _tokenize(b.get("content"))
            sim = _jaccard(ta, tb)
            if sim < 0.25:
                continue

            # Same-ish content → repetition
            if sim >= 0.55 and a.get("item_type") == b.get("item_type"):
                findings.append(
                    {
                        "conflict_type": "repetition",
                        "description": (
                            f"Repeated {a.get('item_type')}: \"{(a.get('content') or '')[:80]}\" "
                            f"also appears as \"{(b.get('content') or '')[:80]}\""
                        ),
                        "item_ids": [a.get("id"), b.get("id")],
                    }
                )
                continue

            # Decisions / facts that overlap but disagree in negation words
            neg_a = bool(
                re.search(r"\b(not|no|don't|won't|never|instead)\b", (a.get("content") or "").lower())
            )
            neg_b = bool(
                re.search(r"\b(not|no|don't|won't|never|instead)\b", (b.get("content") or "").lower())
            )
            if (
                sim >= 0.4
                and neg_a != neg_b
                and a.get("item_type") in {"decision", "fact", "assumption"}
            ):
                findings.append(
                    {
                        "conflict_type": "conflict",
                        "description": (
                            f"Possible contradiction between \"{(a.get('content') or '')[:90]}\" "
                            f"and \"{(b.get('content') or '')[:90]}\""
                        ),
                        "item_ids": [a.get("id"), b.get("id")],
                    }
                )

    # Deduplicate by frozenset of item ids
    seen: set[frozenset] = set()
    unique: list[dict] = []
    for f in findings:
        key = frozenset(f["item_ids"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique


def _llm_conflicts(items: list[dict]) -> list[dict]:
    """Ask the LLM to find conflicts/repetitions across items."""
    if len(items) < 2 or not is_configured():
        return []

    # Cap payload size
    capped = items[:40]
    lines = []
    for idx, it in enumerate(capped):
        flag = "confirmed" if it.get("is_confirmed") else "interpreted"
        lines.append(
            f"[{idx}] doc={it.get('document_id')} type={it.get('item_type')} ({flag}): {it.get('content')}"
        )

    prompt = f"""Compare these extracted items from multiple project documents.
Identify:
1. conflicts — contradictory statements (e.g. different deadlines for the same task)
2. repetitions — the same fact/decision restated across documents

Use item_indices as 0-based indexes into the list below.
Only report genuine issues. Return an empty findings array if none.

Items:
{chr(10).join(lines)}
"""
    try:
        result = generate_json(prompt, CONFLICT_SCHEMA)
        findings_raw = result.get("findings") or []
        out: list[dict] = []
        for f in findings_raw:
            indices = f.get("item_indices") or []
            ids = []
            for i in indices:
                try:
                    ii = int(i)
                except (TypeError, ValueError):
                    continue
                if 0 <= ii < len(capped):
                    ids.append(capped[ii].get("id"))
            if len(ids) < 2:
                continue
            ctype = f.get("conflict_type", "conflict")
            if ctype not in {"conflict", "repetition"}:
                ctype = "conflict"
            out.append(
                {
                    "conflict_type": ctype,
                    "description": str(f.get("description") or "Potential issue detected"),
                    "item_ids": ids,
                }
            )
        return out
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("LLM conflict detection failed: %s", exc)
        return []


def detect_conflicts(session_id: int) -> list[dict]:
    """Detect and persist cross-document conflicts/repetitions for a session."""
    items = db.list_extracted_items(session_id=session_id)
    # Exclude suggested gaps from conflict pairing noise
    items = [i for i in items if i.get("item_type") != "suggested_gap"]

    docs = db.list_documents(session_id)
    if len(docs) < 2 or len(items) < 2:
        return []

    heuristic = _heuristic_conflicts(items)
    llm = _llm_conflicts(items)

    merged: dict[frozenset, dict] = {}
    for f in heuristic + llm:
        key = frozenset(f["item_ids"])
        if key not in merged:
            merged[key] = f
        elif f["conflict_type"] == "conflict":
            # Prefer conflict over repetition when both fire
            merged[key] = f

    persisted: list[dict] = []
    for f in merged.values():
        c = db.add_conflict(
            session_id=session_id,
            description=f["description"],
            item_ids=list(f["item_ids"]),
            conflict_type=f["conflict_type"],
        )
        persisted.append(c)

    return persisted


def format_conflict_detail(conflict: dict) -> dict[str, Any]:
    """Build a UI-friendly detail payload for a conflict."""
    detail_items = []
    for iid in conflict.get("item_ids") or []:
        item = db.get_extracted_item(iid)
        if not item:
            continue
        doc = db.get_document(item.get("document_id"))
        detail_items.append(
            {
                "id": item.get("id"),
                "content": item.get("content"),
                "item_type": item.get("item_type"),
                "is_confirmed": item.get("is_confirmed"),
                "source_section": item.get("source_section"),
                "filename": doc.get("filename") if doc else "?",
                "document_id": item.get("document_id"),
                "offset_start": item.get("source_offset_start"),
                "offset_end": item.get("source_offset_end"),
            }
        )
    return {
        "id": conflict.get("id"),
        "conflict_type": conflict.get("conflict_type"),
        "description": conflict.get("description"),
        "status": conflict.get("status"),
        "resolution_note": conflict.get("resolution_note"),
        "items": detail_items,
    }

