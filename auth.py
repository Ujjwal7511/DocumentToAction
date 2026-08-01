from __future__ import annotations

import hashlib
from typing import Tuple

import db


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def ensure_demo_user() -> None:
    db.init_db()
    if db.get_user_account("demo") is not None:
        return
    try:
        db.create_user_account("demo", _hash_password("demo123"))
    except ValueError:
        # Already exists (race) — fine
        pass


def create_account(username: str, password: str) -> Tuple[bool, str]:
    username = (username or "").strip()
    password = (password or "").strip()
    if not username or not password:
        return False, "Please enter both username and password."
    if len(password) < 4:
        return False, "Password must be at least 4 characters."

    db.init_db()
    existing = db.get_user_account(username)
    if existing is not None:
        return False, "That username already exists."

    try:
        db.create_user_account(username, _hash_password(password))
    except ValueError as exc:
        return False, str(exc)
    return True, "Account created successfully."


def login_user(username: str, password: str) -> Tuple[bool, str]:
    ensure_demo_user()
    username = (username or "").strip()
    password = (password or "").strip()
    record = db.get_user_account(username)
    if record is None:
        return False, "Unknown username."
    if record.get("password_hash") != _hash_password(password):
        return False, "Incorrect password."
    return True, "Logged in successfully."

