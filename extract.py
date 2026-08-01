"""Core extraction pipeline via llm_client.generate_json.

Produces facts, decisions, assumptions, risks, open questions, and *proposed*
action items. Action items are persisted only through db.add_action_item(),
which enforces approved=False — this module never sets approved=True.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import db
from ingest import split_into_sections
from llm_client import LLMError, generate_json, is_configured

logger = logging.getLogger(__name__)

ITEM_TYPES = [
    "fact",
    "decision",
    "assumption",
    "risk",
    "open_question",
    "action_item",
]

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_type": {"type": "string", "enum": ITEM_TYPES},
                    "content": {"type": "string"},
                    "is_confirmed": {
                        "type": "boolean",
                        "description": (
                            "True if directly stated in the source text; "
                            "False if inferred or interpreted."
                        ),
                    },
                    "confidence": {"type": "number"},
                    "source_section": {"type": "string"},
                    "source_quote": {
                        "type": "string",
                        "description": "Exact or near-exact quote used for offset linking.",
                    },
                    "owner": {
                        "type": "string",
                        "description": "Owner if stated (action items only); else empty.",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "Deadline if stated (action items only); else empty.",
                    },
                },
                "required": [
                    "item_type",
                    "content",
                    "is_confirmed",
                    "confidence",
                    "source_section",
                    "source_quote",
                ],
            },
        }
    },
    "required": ["items"],
}

GAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "related_item_type": {"type": "string"},
                    "rationale": {"type": "string"},
                    "suggested_action": {"type": "string"},
                },
                "required": ["content", "rationale", "suggested_action"],
            },
        }
    },
    "required": ["gaps"],
}


def _find_offsets(text: str, quote: str, section_hint: Optional[dict] = None) -> tuple[int, int]:
    """Locate quote offsets in the full document text."""
    if not text:
        return 0, 0
    needle = (quote or "").strip()
    if not needle:
        if section_hint:
            return int(section_hint.get("start", 0)), int(section_hint.get("end", 0))
        return 0, min(80, len(text))

    # Exact match
    idx = text.find(needle)
    if idx >= 0:
        return idx, idx + len(needle)

    # Case-insensitive
    idx = text.lower().find(needle.lower())
    if idx >= 0:
        return idx, idx + len(needle)

    # First ~40 chars of quote
    short = needle[:40]
    idx = text.lower().find(short.lower())
    if idx >= 0:
        return idx, min(idx + len(needle), len(text))

    if section_hint:
        return int(section_hint.get("start", 0)), int(section_hint.get("end", 0))
    return 0, min(80, len(text))


def _section_for_heading(sections: list[dict], heading: str) -> Optional[dict]:
    """Match a section by heading (fuzzy)."""
    if not heading:
        return sections[0] if sections else None
    target = heading.strip().lower().lstrip("#").strip()
    for sec in sections:
        if sec["heading"].lower() == target:
            return sec
    for sec in sections:
        if target in sec["heading"].lower() or sec["heading"].lower() in target:
            return sec
    return sections[0] if sections else None


def _heuristic_extract(text: str, sections: list[dict]) -> list[dict]:
    """Lightweight regex extraction when LLM is unavailable."""
    items: list[dict] = []
    if not text.strip():
        return items

    action_patterns = [
        re.compile(r"(?i)(?:action(?:\s*item)?|todo|follow[- ]?up)\s*[:\-–]\s*(.+)"),
        re.compile(r"(?i)^(?:[-*•]\s*)?(?:TODO|ACTION)\s*[:\-–]?\s*(.+)"),
    ]
    decision_patterns = [
        re.compile(r"(?i)(?:we\s+decided|decision\s*[:\-–]|agreed\s+to)\s*(.+)"),
    ]
    risk_patterns = [
        re.compile(r"(?i)(?:risk|blocker|concern)\s*[:\-–]\s*(.+)"),
    ]
    question_patterns = [
        re.compile(
            r"(?i)(?:open\s+question|unresolved|tbd|need\s+to\s+clarify)\s*[:\-–]\s*(.+)"
        ),
        re.compile(r".+\?\s*$"),
    ]
    # Skip markdown/section headings that are not real content
    heading_line = re.compile(r"^#{1,6}\s+\S+")

    for sec in sections or [
        {"heading": "Document", "start": 0, "end": len(text), "text": text}
    ]:
        for line in sec["text"].splitlines():
            line = line.strip()
            if not line or len(line) < 8:
                continue
            if heading_line.match(line):
                continue
            # Bare section labels like "Open questions" / "Action items"
            if re.fullmatch(
                r"(?i)(open questions?|action items?|decisions?|risks?|agenda|assumptions?)",
                line.rstrip(":"),
            ):
                continue
            matched = False
            for pat in action_patterns:
                m = pat.search(line)
                if m:
                    items.append(
                        {
                            "item_type": "action_item",
                            "content": m.group(1).strip() if m.lastindex else line,
                            "is_confirmed": True,
                            "confidence": 0.55,
                            "source_section": sec["heading"],
                            "source_quote": line[:120],
                            "owner": "",
                            "deadline": "",
                        }
                    )
                    matched = True
                    break
            if matched:
                continue
            for pat in decision_patterns:
                m = pat.search(line)
                if m:
                    items.append(
                        {
                            "item_type": "decision",
                            "content": m.group(1).strip() if m.lastindex else line,
                            "is_confirmed": True,
                            "confidence": 0.55,
                            "source_section": sec["heading"],
                            "source_quote": line[:120],
                            "owner": "",
                            "deadline": "",
                        }
                    )
                    matched = True
                    break
            if matched:
                continue
            for pat in risk_patterns:
                m = pat.search(line)
                if m:
                    items.append(
                        {
                            "item_type": "risk",
                            "content": m.group(1).strip() if m.lastindex else line,
                            "is_confirmed": True,
                            "confidence": 0.5,
                            "source_section": sec["heading"],
                            "source_quote": line[:120],
                            "owner": "",
                            "deadline": "",
                        }
                    )
                    matched = True
                    break
            if matched:
                continue
            for pat in question_patterns:
                if pat.search(line):
                    items.append(
                        {
                            "item_type": "open_question",
                            "content": line.rstrip("?"),
                            "is_confirmed": True,
                            "confidence": 0.5,
                            "source_section": sec["heading"],
                            "source_quote": line[:120],
                            "owner": "",
                            "deadline": "",
                        }
                    )
                    break

    # Surface explicit date/deadline statements as facts for conflict detection
    date_line = re.compile(
        r"(?i).*\b(march|april|may|june|july|august|september|october|november|december|"
        r"jan(?:uary)?|feb(?:ruary)?)\s+\d{1,2}(?:,?\s*\d{4})?\b.*"
    )
    for sec in sections or []:
        for line in sec["text"].splitlines():
            line = line.strip()
            if heading_line.match(line) or len(line) < 12:
                continue
            if date_line.match(line) and any(
                k in line.lower()
                for k in ("deadline", "by ", "until", "target", "launch", "migrate", "through", "end")
            ):
                items.append(
                    {
                        "item_type": "fact",
                        "content": line,
                        "is_confirmed": True,
                        "confidence": 0.6,
                        "source_section": sec["heading"],
                        "source_quote": line[:120],
                        "owner": "",
                        "deadline": "",
                    }
                )

    # Capture a few lead sentences as facts if nothing found
    if not items:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        for sent in sentences[:5]:
            sent = sent.strip()
            if len(sent) < 20:
                continue
            items.append(
                {
                    "item_type": "fact",
                    "content": sent,
                    "is_confirmed": True,
                    "confidence": 0.4,
                    "source_section": sections[0]["heading"] if sections else "Document",
                    "source_quote": sent[:120],
                    "owner": "",
                    "deadline": "",
                }
            )
    return items


def extract_from_document(document_id: int) -> list[dict]:
    """Run extraction on a stored document and persist results.

    Action-item rows are also written to the ActionItem collection as *proposals*
    (approved=False). This function never approves actions.
    """
    document = db.get_document(document_id)
    if not document:
        raise ValueError(f"Document {document_id} not found")

    text = document.get("content") or ""
    sections = split_into_sections(text)

    if not text.strip():
        logger.info("Document %s is empty — skipping extraction", document_id)
        return []

    raw_items = _call_extractor(
        text, document.get("filename", ""), document.get("doc_type", "unknown"), sections
    )
    persisted: list[dict] = []

    for raw in raw_items:
        item_type = str(raw.get("item_type", "fact"))
        if item_type not in ITEM_TYPES:
            item_type = "fact"
        content = str(raw.get("content", "")).strip()
        if not content:
            continue

        is_confirmed = bool(raw.get("is_confirmed", True))
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        source_section = str(raw.get("source_section", "") or "Document")
        quote = str(raw.get("source_quote", "") or content[:80])
        sec = _section_for_heading(sections, source_section)
        start, end = _find_offsets(text, quote, sec)
        if sec and not source_section:
            source_section = sec["heading"]

        item = db.add_extracted_item(
            document_id=document_id,
            item_type=item_type,
            content=content,
            is_confirmed=is_confirmed,
            confidence=confidence,
            source_section=source_section,
            source_offset_start=start,
            source_offset_end=end,
        )
        persisted.append(item)

        if item_type == "action_item":
            # PROPOSAL ONLY — approved remains False by data-layer contract
            db.add_action_item(
                session_id=document.get("session_id"),
                description=content,
                owner=(str(raw.get("owner") or "").strip() or None),
                deadline=(str(raw.get("deadline") or "").strip() or None),
                extracted_item_id=item.get("id"),
                source_document_id=document_id,
                source_section=source_section,
                is_confirmed=is_confirmed,
            )

    return persisted


def _call_extractor(
    text: str,
    filename: str,
    doc_type: str,
    sections: list[dict],
) -> list[dict]:
    """Invoke LLM extraction or fall back to heuristics."""
    if not is_configured():
        logger.warning("LLM not configured — using heuristic extraction")
        return _heuristic_extract(text, sections)

    section_index = "\n".join(
        f"- [{s['heading']}] chars {s['start']}-{s['end']}" for s in sections
    ) or "(no headings — use Paragraph N)"

    excerpt = text[:12000]
    prompt = f"""You are an expert project analyst. Extract structured information from this document.

Rules:
1. item_type must be one of: {", ".join(ITEM_TYPES)}
2. is_confirmed=true ONLY when the content is a direct statement in the text.
   is_confirmed=false when you are inferring or interpreting.
3. Include source_section matching a heading from the section index when possible.
4. Include a short source_quote copied from the document for offset linking.
5. For action_item entries, extract owner and deadline only if explicitly stated;
   otherwise leave them as empty strings. Do NOT invent owners or deadlines.
6. Do NOT assign or approve tasks — only propose action items that appear in
   or are clearly implied by the text.
7. If the document has little extractable content, return an empty items array.

Filename: {filename}
Detected type: {doc_type}

Section index:
{section_index}

Document:
---
{excerpt}
---
"""
    try:
        result = generate_json(prompt, EXTRACTION_SCHEMA)
        items = result.get("items") or []
        if not isinstance(items, list):
            return _heuristic_extract(text, sections)
        return items
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Extraction LLM call failed: %s — falling back to heuristics", exc)
        return _heuristic_extract(text, sections)


def suggest_missing_gaps(session_id: int) -> list[dict]:
    """Suggest missing questions/actions implied but not stated.

    Suggestions are stored as extracted items of type 'suggested_gap' and, when
    a suggested_action is present, also as *proposed* ActionItems (approved=False).
    """
    documents = db.list_documents(session_id)
    items = db.list_extracted_items(session_id=session_id)
    actions = db.list_action_items(session_id)

    if not documents:
        return []

    summary_lines = []
    for d in documents:
        summary_lines.append(f"Document '{d.get('filename')}' ({d.get('doc_type')}):")
        summary_lines.append((d.get("content") or "")[:3000])
        summary_lines.append("")

    summary_lines.append("Already extracted:")
    for it in items:
        flag = "confirmed" if it.get("is_confirmed") else "interpreted"
        summary_lines.append(f"- [{it.get('item_type')}/{flag}] {it.get('content')}")

    summary_lines.append("Proposed actions:")
    for a in actions:
        summary_lines.append(
            f"- {a.get('description')} | owner={a.get('owner') or 'MISSING'} | deadline={a.get('deadline') or 'MISSING'}"
        )

    corpus = "\n".join(summary_lines)[:14000]

    # Heuristic gaps always run (works offline / without API key)
    heuristic_gaps = _heuristic_gaps(items, actions)
    llm_gaps: list[dict] = []

    if is_configured():
        prompt = f"""Given these project documents and extracted items, suggest missing questions
or actions that are IMPLIED but not explicitly stated.

Examples of good gaps:
- A decision was recorded but no owner or deadline was assigned.
- An API change is planned but no rollback plan is mentioned.
- A risk is noted without a mitigation action.

Return gaps with content, rationale, and suggested_action.
Do NOT invent tasks that are unrelated. Do NOT mark anything as approved.

Corpus:
---
{corpus}
---
"""
        try:
            result = generate_json(prompt, GAP_SCHEMA)
            llm_gaps = result.get("gaps") or []
        except (LLMError, Exception) as exc:  # noqa: BLE001
            logger.warning("Gap suggestion LLM failed: %s", exc)

    combined = llm_gaps + heuristic_gaps
    # Deduplicate by content prefix
    seen: set[str] = set()
    persisted: list[dict] = []
    # Attach gaps to the first document for source linking
    anchor_doc = documents[0]

    for gap in combined:
        content = str(gap.get("content") or "").strip()
        if not content:
            continue
        key = content.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        rationale = str(gap.get("rationale") or "")
        suggested = str(gap.get("suggested_action") or "").strip()
        full = content if not rationale else f"{content} (Why: {rationale})"

        item = db.add_extracted_item(
            document_id=anchor_doc.get("id"),
            item_type="suggested_gap",
            content=full,
            is_confirmed=False,  # always interpreted — never a direct statement
            confidence=0.45,
            source_section="Cross-document gap analysis",
            source_offset_start=0,
            source_offset_end=0,
        )
        persisted.append(item)

        if suggested:
            db.add_action_item(
                session_id=session_id,
                description=suggested,
                owner=None,
                deadline=None,
                extracted_item_id=item.get("id"),
                source_document_id=anchor_doc.get("id"),
                source_section="Suggested gap",
                is_confirmed=False,
            )

    return persisted


def _heuristic_gaps(items: list[dict], actions: list[dict]) -> list[dict]:
    """Rule-based gap detection that does not need an LLM."""
    gaps: list[dict] = []
    decisions = [i for i in items if i.get("item_type") == "decision"]
    risks = [i for i in items if i.get("item_type") == "risk"]

    for d in decisions:
        # Decision without a related action mentioning similar text
        related = [
            a
            for a in actions
            if any(
                tok in (a.get("description") or "").lower()
                for tok in (d.get("content") or "").lower().split()[:4]
                if len(tok) > 3
            )
        ]
        if not related:
            gaps.append(
                {
                    "content": f"Decision has no follow-up action: {(d.get('content') or '')[:120]}",
                    "rationale": "Decisions should usually have an owned follow-up.",
                    "suggested_action": f"Assign an owner and deadline for: {(d.get('content') or '')[:100]}",
                }
            )

    for a in actions:
        if not a.get("owner"):
            gaps.append(
                {
                    "content": f"Action has no owner: {(a.get('description') or '')[:120]}",
                    "rationale": "Action items without owners are unlikely to be completed.",
                    "suggested_action": f"Assign an owner for: {(a.get('description') or '')[:100]}",
                }
            )
        if not a.get("deadline"):
            gaps.append(
                {
                    "content": f"Action has no deadline: {(a.get('description') or '')[:120]}",
                    "rationale": "Deadlines make progress trackable.",
                    "suggested_action": f"Set a deadline for: {(a.get('description') or '')[:100]}",
                }
            )

    for r in risks:
        gaps.append(
            {
                "content": f"Risk may lack mitigation: {(r.get('content') or '')[:120]}",
                "rationale": "Risks should have an explicit mitigation or monitoring action.",
                "suggested_action": f"Define mitigation for risk: {(r.get('content') or '')[:100]}",
            }
        )

    # Cap heuristic noise
    return gaps[:8]


def run_full_extraction(session_id: int) -> dict[str, Any]:
    """Extract from all documents in a session, then suggest gaps.

    Returns counts for UI feedback. Does not approve any action items.
    """
    documents = db.list_documents(session_id)
    total_items = 0
    for doc in documents:
        if not (doc.get("content") or "").strip():
            continue
        items = extract_from_document(doc.get("id"))
        total_items += len(items)

    gaps = suggest_missing_gaps(session_id)
    actions = db.list_action_items(session_id)
    # Hard invariant check
    for a in actions:
        if a.get("approved"):
            # Should be unreachable — correct if somehow set
            logger.error(
                "INVARIANT VIOLATION: action %s approved by pipeline — resetting",
                a.get("id"),
            )
            db.reset_action_to_proposed(a.get("id"))

    return {
        "documents_processed": len(documents),
        "items_extracted": total_items,
        "gaps_suggested": len(gaps),
        "actions_proposed": len(actions),
    }

