"""PyMongo data layer for the Document-to-Action assistant.

MongoDB is the ONLY persistence backend. SQLite/SQLAlchemy have been removed.

- ``MONGODB_URI`` is required; the module fails loudly at import time if missing.
- ``MONGODB_DB_NAME`` selects the database (falls back to ``MONGODB_DB``, then
  ``document_to_action``).
- Every collection stores ``created_at`` and ``updated_at`` timestamps so future
  schema shapes can be added without migrations.
- Every read uses ``.get(field, default)`` so older documents that lack newer
  fields never crash the app.

IDs are integers (backed by a ``counters`` collection) so the rest of the app
keeps using ``session_id`` / ``document_id`` / item ids as before.
"""

from __future__ import annotations

import json
import os
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument
import streamlit as st

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = (
    os.getenv("MONGODB_DB_NAME") or os.getenv("MONGODB_DB") or "document_to_action"
).strip()

if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI is not set. Add it to a .env file, for example:\n"
        "  MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority\n"
        "  MONGODB_DB_NAME=DocumentToAction\n"
        "Create a free cluster at https://www.mongodb.com/atlas."
    )

ROOT = Path(__file__).resolve().parent
# Legacy users store path — kept for reference; auth now uses MongoDB.
USER_STORE = ROOT / "users.json"

_client: Optional[MongoClient] = None
_read_cache_clearers: list[Any] = []


def _cached_read(func):
    """Cache short-lived read queries and register them for write invalidation."""
    cached = st.cache_data(ttl=20, show_spinner=False)(func)
    _read_cache_clearers.append(cached.clear)
    return cached


def _clear_read_cache() -> None:
    for clear in _read_cache_clearers:
        clear()


def _invalidate_after_write(func):
    """Keep cached views correct immediately after any MongoDB mutation."""
    @wraps(func)
    def wrapped(*args, **kwargs):
        result = func(*args, **kwargs)
        _clear_read_cache()
        return result

    return wrapped


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    return _client


def _get_db():
    return _get_client()[MONGODB_DB_NAME]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _next_id(collection_name: str) -> int:
    counters = _get_db()["counters"]
    doc = counters.find_one_and_update(
        {"_id": collection_name},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["value"])


# ---------------------------------------------------------------------------
# Normalizers — return plain dicts; defensive .get() for schema evolution
# ---------------------------------------------------------------------------


def _out_session(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "title": doc.get("title", "Untitled Project"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at", doc.get("created_at")),
    }


def _out_document(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "session_id": doc.get("session_id"),
        "filename": doc.get("filename", ""),
        "content": doc.get("content", ""),
        "doc_type": doc.get("doc_type", "unknown"),
        "doc_type_confidence": doc.get("doc_type_confidence", 0.0),
        "uploaded_at": doc.get("uploaded_at"),
    }


def _out_item(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "document_id": doc.get("document_id"),
        "item_type": doc.get("item_type", "fact"),
        "content": doc.get("content", ""),
        "is_confirmed": doc.get("is_confirmed", True),
        "confidence": doc.get("confidence", 0.5),
        "source_section": doc.get("source_section", ""),
        "source_offset_start": doc.get("source_offset_start", 0),
        "source_offset_end": doc.get("source_offset_end", 0),
        "original_content": doc.get("original_content"),
        "is_edited": doc.get("is_edited", False),
        "user_classification": doc.get("user_classification"),
        "created_at": doc.get("created_at"),
    }


def _out_conflict(doc: dict) -> dict:
    item_ids = doc.get("item_ids")
    if item_ids is None:
        try:
            item_ids = json.loads(doc.get("item_ids_json") or "[]")
        except json.JSONDecodeError:
            item_ids = []
    return {
        "id": doc["_id"],
        "session_id": doc.get("session_id"),
        "conflict_type": doc.get("conflict_type", "conflict"),
        "description": doc.get("description", ""),
        "item_ids": item_ids,
        "status": doc.get("status", "open"),
        "resolution_note": doc.get("resolution_note", ""),
        "created_at": doc.get("created_at"),
    }


def _out_action(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "session_id": doc.get("session_id"),
        "extracted_item_id": doc.get("extracted_item_id"),
        "description": doc.get("description", ""),
        "owner": doc.get("owner"),
        "deadline": doc.get("deadline"),
        "source_document_id": doc.get("source_document_id"),
        "source_section": doc.get("source_section", ""),
        "is_confirmed": doc.get("is_confirmed", True),
        "approved": doc.get("approved", False),
        "rejected": doc.get("rejected", False),
        "status": doc.get("status", "proposed"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _out_kb(doc: dict) -> dict:
    return {
        "id": doc["_id"],
        "session_id": doc.get("session_id"),
        "extracted_item_id": doc.get("extracted_item_id"),
        "kb_title": doc.get("kb_title", ""),
        "kb_filename": doc.get("kb_filename", ""),
        "relevance_score": doc.get("relevance_score", 0.0),
        "snippet": doc.get("snippet", ""),
        "rationale": doc.get("rationale", ""),
        "created_at": doc.get("created_at"),
    }


def _out_summary(doc: dict) -> dict:
    content_json = doc.get("content_json") or "{}"
    try:
        content = json.loads(content_json)
    except json.JSONDecodeError:
        content = {}
    return {
        "id": doc["_id"],
        "session_id": doc.get("session_id"),
        "title": doc.get("title", "Reviewed Project Action Summary"),
        "content": content,
        "content_json": content_json,
        "created_at": doc.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def get_backend_name() -> str:
    """Return the active persistence backend (always MongoDB now)."""
    return "mongodb"


def init_db(db_path: Optional[str] = None) -> None:
    """Verify connectivity and create indexes. db_path is ignored (Mongo only)."""
    dbase = _get_db()
    dbase.command("ping")
    dbase["documents"].create_index("session_id")
    dbase["extracted_items"].create_index("document_id")
    dbase["conflicts"].create_index("session_id")
    dbase["action_items"].create_index("session_id")
    dbase["action_items"].create_index([("session_id", 1), ("status", 1)])
    dbase["kb_matches"].create_index("session_id")
    dbase["reviewed_summaries"].create_index("session_id")
    dbase["project_sessions"].create_index("updated_at")


# ---------------------------------------------------------------------------
# User accounts
# ---------------------------------------------------------------------------


def create_user_account(username: str, password_hash: str) -> dict:
    """Create a user account. Raises ValueError if the username is taken."""
    col = _get_db()["users"]
    existing = col.find_one({"username": username})
    if existing is not None:
        raise ValueError("That username already exists.")
    now = _utcnow()
    doc = {
        "_id": _next_id("users"),
        "username": username,
        "password_hash": password_hash,
        "created_at": now,
        "updated_at": now,
    }
    col.insert_one(doc)
    return {
        "id": doc["_id"],
        "username": doc["username"],
        "password_hash": doc["password_hash"],
        "created_at": doc.get("created_at"),
    }


def get_user_account(username: str) -> Optional[dict]:
    """Fetch a user account by username (case-sensitive)."""
    doc = _get_db()["users"].find_one({"username": username})
    if not doc:
        return None
    return {
        "id": doc["_id"],
        "username": doc["username"],
        "password_hash": doc.get("password_hash", ""),
        "created_at": doc.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Sessions & Documents
# ---------------------------------------------------------------------------


@_invalidate_after_write
def create_session(title: str = "Untitled Project") -> dict:
    """Create a new project analysis session."""
    now = _utcnow()
    doc = {
        "_id": _next_id("project_sessions"),
        "title": title or "Untitled Project",
        "created_at": now,
        "updated_at": now,
    }
    _get_db()["project_sessions"].insert_one(doc)
    return _out_session(doc)


@_cached_read
def get_project_session(session_id: int) -> Optional[dict]:
    doc = _get_db()["project_sessions"].find_one({"_id": session_id})
    return _out_session(doc) if doc else None


@_cached_read
def list_project_sessions(limit: int = 25) -> list[dict]:
    """Return recent project sessions so saved work can be reopened from the UI."""
    safe_limit = max(1, min(int(limit), 100))
    rows = (
        _get_db()["project_sessions"]
        .find({})
        .sort("updated_at", -1)
        .limit(safe_limit)
    )
    return [_out_session(doc) for doc in rows]


def _touch_session(session_id: int) -> None:
    _get_db()["project_sessions"].update_one(
        {"_id": session_id}, {"$set": {"updated_at": _utcnow()}}
    )


@_invalidate_after_write
def add_document(
    session_id: int,
    filename: str,
    content: str,
    doc_type: str = "unknown",
    doc_type_confidence: float = 0.0,
) -> dict:
    """Persist an uploaded document."""
    now = _utcnow()
    doc = {
        "_id": _next_id("documents"),
        "session_id": session_id,
        "filename": filename,
        "content": content or "",
        "doc_type": doc_type,
        "doc_type_confidence": doc_type_confidence,
        "uploaded_at": now,
        "created_at": now,
        "updated_at": now,
    }
    _get_db()["documents"].insert_one(doc)
    _touch_session(session_id)
    return _out_document(doc)


@_invalidate_after_write
def update_document_type(document_id: int, doc_type: str, confidence: float) -> None:
    _get_db()["documents"].update_one(
        {"_id": document_id},
        {"$set": {"doc_type": doc_type, "doc_type_confidence": confidence, "updated_at": _utcnow()}},
    )


@_cached_read
def list_documents(session_id: int) -> list[dict]:
    rows = (
        _get_db()["documents"].find({"session_id": session_id}).sort("_id", 1)
    )
    return [_out_document(d) for d in rows]


@_cached_read
def get_document(document_id: int) -> Optional[dict]:
    doc = _get_db()["documents"].find_one({"_id": document_id})
    return _out_document(doc) if doc else None


# ---------------------------------------------------------------------------
# Extracted items
# ---------------------------------------------------------------------------


@_invalidate_after_write
def add_extracted_item(
    document_id: int,
    item_type: str,
    content: str,
    is_confirmed: bool = True,
    confidence: float = 0.5,
    source_section: str = "",
    source_offset_start: int = 0,
    source_offset_end: int = 0,
) -> dict:
    """Insert an extracted item. Never creates approved action items."""
    now = _utcnow()
    doc = {
        "_id": _next_id("extracted_items"),
        "document_id": document_id,
        "item_type": item_type,
        "content": content,
        "is_confirmed": is_confirmed,
        "confidence": confidence,
        "source_section": source_section,
        "source_offset_start": source_offset_start,
        "source_offset_end": source_offset_end,
        "original_content": content,
        "is_edited": False,
        "user_classification": None,
        "created_at": now,
        "updated_at": now,
    }
    _get_db()["extracted_items"].insert_one(doc)
    return _out_item(doc)


@_cached_read
def list_extracted_items(
    document_id: Optional[int] = None,
    session_id: Optional[int] = None,
) -> list[dict]:
    col = _get_db()["extracted_items"]
    if document_id is not None:
        rows = col.find({"document_id": document_id}).sort("_id", 1)
    elif session_id is not None:
        doc_ids = [
            d["_id"] for d in _get_db()["documents"].find({"session_id": session_id})
        ]
        rows = col.find({"document_id": {"$in": doc_ids}}).sort("_id", 1)
    else:
        rows = col.find().sort("_id", 1)
    return [_out_item(d) for d in rows]


@_invalidate_after_write
def update_extracted_item(
    item_id: int,
    content: Optional[str] = None,
    user_classification: Optional[str] = None,
    is_confirmed: Optional[bool] = None,
) -> bool:
    """Apply user edits to an extracted item. Returns True if a document matched."""
    update: dict[str, Any] = {}
    if content is not None:
        update["content"] = content
        update["is_edited"] = True
    if user_classification is not None:
        update["user_classification"] = user_classification
    if is_confirmed is not None:
        update["is_confirmed"] = is_confirmed
    if not update:
        return True
    update["updated_at"] = _utcnow()
    result = _get_db()["extracted_items"].update_one({"_id": item_id}, {"$set": update})
    return result.matched_count == 1


@_cached_read
def get_extracted_item(item_id: int) -> Optional[dict]:
    doc = _get_db()["extracted_items"].find_one({"_id": item_id})
    return _out_item(doc) if doc else None


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


@_invalidate_after_write
def add_conflict(
    session_id: int,
    description: str,
    item_ids: list[int],
    conflict_type: str = "conflict",
) -> dict:
    """Persist a detected conflict or repetition."""
    now = _utcnow()
    doc = {
        "_id": _next_id("conflicts"),
        "session_id": session_id,
        "conflict_type": conflict_type,
        "description": description,
        "item_ids": list(item_ids),
        "status": "open",
        "resolution_note": "",
        "created_at": now,
        "updated_at": now,
    }
    _get_db()["conflicts"].insert_one(doc)
    return _out_conflict(doc)


@_cached_read
def list_conflicts(session_id: int) -> list[dict]:
    rows = _get_db()["conflicts"].find({"session_id": session_id}).sort("_id", 1)
    return [_out_conflict(d) for d in rows]


@_invalidate_after_write
def resolve_conflict(
    conflict_id: int,
    status: str,
    resolution_note: str = "",
) -> bool:
    """Resolve or annotate a conflict (user-triggered). Returns True if matched."""
    if status not in {"open", "resolved", "annotated"}:
        raise ValueError(f"Invalid conflict status: {status}")
    result = _get_db()["conflicts"].update_one(
        {"_id": conflict_id},
        {
            "$set": {
                "status": status,
                "resolution_note": resolution_note,
                "updated_at": _utcnow(),
            }
        },
    )
    return result.matched_count == 1


# ---------------------------------------------------------------------------
# Action items (approval gate)
# ---------------------------------------------------------------------------


@_invalidate_after_write
def add_action_item(
    session_id: int,
    description: str,
    owner: Optional[str] = None,
    deadline: Optional[str] = None,
    extracted_item_id: Optional[int] = None,
    source_document_id: Optional[int] = None,
    source_section: str = "",
    is_confirmed: bool = True,
) -> dict:
    """Create a *proposed* action item. approved is always False."""
    now = _utcnow()
    doc = {
        "_id": _next_id("action_items"),
        "session_id": session_id,
        "description": description,
        "owner": owner,
        "deadline": deadline,
        "extracted_item_id": extracted_item_id,
        "source_document_id": source_document_id,
        "source_section": source_section,
        "is_confirmed": is_confirmed,
        "approved": False,  # CRITICAL — proposals only; never auto-approved
        "rejected": False,
        "status": "proposed",
        "created_at": now,
        "updated_at": now,
    }
    _get_db()["action_items"].insert_one(doc)
    return _out_action(doc)


@_cached_read
def list_action_items(session_id: int) -> list[dict]:
    rows = _get_db()["action_items"].find({"session_id": session_id}).sort("_id", 1)
    return [_out_action(d) for d in rows]


@_invalidate_after_write
def update_action_item(
    action_id: int,
    description: Optional[str] = None,
    owner: Optional[str] = None,
    deadline: Optional[str] = None,
) -> bool:
    """Edit action item fields without changing approval state.

    Returns True if the document matched. Empty owner/deadline strings are
    treated as clearing the field.
    """
    update: dict[str, Any] = {}
    if description is not None:
        update["description"] = description
    if owner is not None:
        update["owner"] = owner or None
    if deadline is not None:
        update["deadline"] = deadline or None
    if not update:
        return True
    update["updated_at"] = _utcnow()
    result = _get_db()["action_items"].update_one({"_id": action_id}, {"$set": update})
    return result.matched_count == 1


@_cached_read
def get_action_item(action_id: int) -> Optional[dict]:
    doc = _get_db()["action_items"].find_one({"_id": action_id})
    return _out_action(doc) if doc else None


@_invalidate_after_write
def approve_action_item(action_id: int) -> bool:
    """Explicitly approve a proposed action item (user-triggered only)."""
    result = _get_db()["action_items"].update_one(
        {"_id": action_id},
        {
            "$set": {
                "approved": True,
                "rejected": False,
                "status": "approved",
                "updated_at": _utcnow(),
            }
        },
    )
    return result.matched_count == 1


@_invalidate_after_write
def reject_action_item(action_id: int) -> bool:
    """Explicitly reject a proposed action item (user-triggered only)."""
    result = _get_db()["action_items"].update_one(
        {"_id": action_id},
        {
            "$set": {
                "approved": False,
                "rejected": True,
                "status": "rejected",
                "updated_at": _utcnow(),
            }
        },
    )
    return result.matched_count == 1


@_invalidate_after_write
def reset_action_to_proposed(action_id: int) -> bool:
    """Force an action back to proposed (used by invariant guard / UI reset)."""
    result = _get_db()["action_items"].update_one(
        {"_id": action_id},
        {
            "$set": {
                "approved": False,
                "rejected": False,
                "status": "proposed",
                "updated_at": _utcnow(),
            }
        },
    )
    return result.matched_count == 1


# ---------------------------------------------------------------------------
# KB matches & summaries
# ---------------------------------------------------------------------------


@_invalidate_after_write
def add_kb_match(
    session_id: int,
    kb_title: str,
    kb_filename: str,
    relevance_score: float,
    snippet: str = "",
    rationale: str = "",
    extracted_item_id: Optional[int] = None,
) -> dict:
    """Persist a knowledge-base match."""
    now = _utcnow()
    doc = {
        "_id": _next_id("kb_matches"),
        "session_id": session_id,
        "kb_title": kb_title,
        "kb_filename": kb_filename,
        "relevance_score": relevance_score,
        "snippet": snippet,
        "rationale": rationale,
        "extracted_item_id": extracted_item_id,
        "created_at": now,
        "updated_at": now,
    }
    _get_db()["kb_matches"].insert_one(doc)
    return _out_kb(doc)


@_cached_read
def list_kb_matches(session_id: int) -> list[dict]:
    rows = (
        _get_db()["kb_matches"].find({"session_id": session_id}).sort("relevance_score", -1)
    )
    return [_out_kb(d) for d in rows]


@_invalidate_after_write
def save_reviewed_summary(
    session_id: int,
    title: str,
    content: dict[str, Any],
) -> dict:
    """Persist a final reviewed summary (caller must filter to approved items)."""
    now = _utcnow()
    doc = {
        "_id": _next_id("reviewed_summaries"),
        "session_id": session_id,
        "title": title,
        "content_json": json.dumps(content, indent=2),
        "created_at": now,
        "updated_at": now,
    }
    _get_db()["reviewed_summaries"].insert_one(doc)
    _touch_session(session_id)
    return _out_summary(doc)


@_cached_read
def list_summaries(session_id: int) -> list[dict]:
    rows = (
        _get_db()["reviewed_summaries"]
        .find({"session_id": session_id})
        .sort("created_at", -1)
    )
    return [_out_summary(d) for d in rows]


@_cached_read
def get_summary(summary_id: int) -> Optional[dict]:
    doc = _get_db()["reviewed_summaries"].find_one({"_id": summary_id})
    return _out_summary(doc) if doc else None


@_cached_read
def build_reviewed_summary_payload(session_id: int) -> dict[str, Any]:
    """Assemble summary content by querying MongoDB for the CURRENT state.

    Includes approved (non-rejected) actions, resolved/annotated conflicts, and
    non-rejected extracted items that are not marked unresolved. Reads are made
    directly from Mongo — no cached in-memory state is used.
    """
    docs = list_documents(session_id)
    items = list_extracted_items(session_id=session_id)
    actions = [
        a for a in list_action_items(session_id) if a.get("approved") and not a.get("rejected")
    ]
    conflicts = [
        c for c in list_conflicts(session_id) if c.get("status") in {"resolved", "annotated"}
    ]
    kb = list_kb_matches(session_id)

    included_items = []
    for it in items:
        if it.get("item_type") == "action_item":
            continue  # actions come from ActionItem collection after approval
        if it.get("user_classification") == "unresolved":
            continue
        included_items.append(
            {
                "id": it["id"],
                "type": it.get("item_type"),
                "content": it.get("content"),
                "is_confirmed": it.get("is_confirmed"),
                "user_classification": it.get("user_classification"),
                "source_document_id": it.get("document_id"),
                "source_section": it.get("source_section"),
                "source_offset_start": it.get("source_offset_start"),
                "source_offset_end": it.get("source_offset_end"),
                "is_edited": it.get("is_edited"),
            }
        )

    return {
        "session_id": session_id,
        "documents": [
            {"id": d["id"], "filename": d.get("filename"), "doc_type": d.get("doc_type")}
            for d in docs
        ],
        "extracted_items": included_items,
        "approved_actions": [
            {
                "id": a["id"],
                "description": a.get("description"),
                "owner": a.get("owner"),
                "deadline": a.get("deadline"),
                "source_document_id": a.get("source_document_id"),
                "source_section": a.get("source_section"),
                "is_confirmed": a.get("is_confirmed"),
            }
            for a in actions
        ],
        "resolved_conflicts": [
            {
                "id": c["id"],
                "type": c.get("conflict_type"),
                "description": c.get("description"),
                "status": c.get("status"),
                "resolution_note": c.get("resolution_note"),
                "item_ids": c.get("item_ids") or [],
            }
            for c in conflicts
        ],
        "kb_standards": [
            {
                "title": m.get("kb_title"),
                "filename": m.get("kb_filename"),
                "score": m.get("relevance_score"),
                "snippet": m.get("snippet"),
                "rationale": m.get("rationale"),
            }
            for m in kb
        ],
    }

