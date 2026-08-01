import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db


def test_backend_is_mongodb():
    """The app is MongoDB-only after the SQLite/SQLAlchemy migration."""
    assert db.get_backend_name() == "mongodb"
    assert db.MONGODB_URI, "MONGODB_URI must be set in .env for tests"


def test_session_and_document_crud(monkeypatch):
    """Create a session, add a document, and read it back via Mongo."""
    sess = db.create_session(title="Migration Test")
    assert sess["id"] > 0
    try:
        doc = db.add_document(
            session_id=sess["id"],
            filename="test.md",
            content="# Hello\n\nSome content.",
            doc_type="meeting_notes",
            doc_type_confidence=0.9,
        )
        assert doc["id"] > 0
        fetched = db.get_document(doc["id"])
        assert fetched["filename"] == "test.md"
        assert fetched["content"].startswith("# Hello")
        assert fetched["session_id"] == sess["id"]

        docs = db.list_documents(sess["id"])
        assert len(docs) == 1
        assert docs[0]["id"] == doc["id"]
    finally:
        # Clean up test data
        _db = db._get_db()
        _db["documents"].delete_many({"session_id": sess["id"]})
        _db["project_sessions"].delete_one({"_id": sess["id"]})


def test_action_item_proposal_and_approval_flow(monkeypatch):
    """Proposed actions start unapproved; only approve_action_item flips it."""
    sess = db.create_session(title="Approval Flow Test")
    try:
        action = db.add_action_item(
            session_id=sess["id"],
            description="Migrate partner API auth to JWT",
            owner=None,
            deadline=None,
        )
        assert action["approved"] is False
        assert action["status"] == "proposed"
        assert action["rejected"] is False

        # Editing should NOT change approval state
        ok = db.update_action_item(action["id"], owner="Maya", deadline="2026-03-20")
        assert ok is True
        updated = db.get_action_item(action["id"])
        assert updated["owner"] == "Maya"
        assert updated["approved"] is False
        assert updated["status"] == "proposed"

        # Explicit approval only via approve_action_item
        ok = db.approve_action_item(action["id"])
        assert ok is True
        approved = db.get_action_item(action["id"])
        assert approved["approved"] is True
        assert approved["status"] == "approved"

        # Reject then reset back to proposed
        ok = db.reject_action_item(action["id"])
        assert ok is True
        rejected = db.get_action_item(action["id"])
        assert rejected["rejected"] is True
        assert rejected["approved"] is False

        ok = db.reset_action_to_proposed(action["id"])
        assert ok is True
        reset = db.get_action_item(action["id"])
        assert reset["status"] == "proposed"
    finally:
        _db = db._get_db()
        _db["action_items"].delete_many({"session_id": sess["id"]})
        _db["project_sessions"].delete_one({"_id": sess["id"]})

