import pytest


def test_signup_and_login_use_the_same_persisted_store():
    """Sign up creates a user that can subsequently log in via MongoDB."""
    import auth
    import db

    db.init_db()

    # Ensure clean state
    existing = db.get_user_account("alice")
    if existing is not None:
        _db = db._get_db()
        _db["users"].delete_one({"username": "alice"})

    ok, msg = auth.create_account("alice", "secret123")
    assert ok is True, msg

    try:
        assert auth.login_user("alice", "secret123")[0] is True
        assert auth.login_user("alice", "wrongpass")[0] is False
    finally:
        # Clean up
        _db = db._get_db()
        _db["users"].delete_one({"username": "alice"})
