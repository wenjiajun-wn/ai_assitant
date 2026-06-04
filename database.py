"""
Database layer — SQLite persistence.
Handles: connection, table creation, JSON migration, event CRUD, password hashing.
"""

import os
import json
import sqlite3
import hashlib
from pathlib import Path

DB_DIR  = Path(__file__).parent / "data"
DB_FILE = DB_DIR / "calendar_data.db"
os.makedirs(DB_DIR, exist_ok=True)


def _get_db():
    """Get a SQLite connection for the current thread (WAL mode)."""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _uid():
    import uuid
    return uuid.uuid4().hex[:12]


def init_db():
    """Create tables if not exist, migrate from old JSON if needed."""
    conn = _get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id        TEXT NOT NULL,
        user_id   TEXT NOT NULL DEFAULT 'default',
        title     TEXT,
        date      TEXT,
        description TEXT DEFAULT '',
        color     TEXT DEFAULT '#5b6abf',
        PRIMARY KEY (id, user_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        email         TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        api_token     TEXT NOT NULL
    )""")
    # Migration: old table had 'username' column
    col_info = conn.execute("PRAGMA table_info(users)").fetchall()
    cols = [c["name"] for c in col_info]
    if "username" in cols:
        conn.execute("DROP TABLE users")
        conn.execute("""CREATE TABLE users (
            email         TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            api_token     TEXT NOT NULL
        )""")
    conn.commit()

    # Auto-migrate from old JSON file
    json_file = Path(__file__).parent / "calendar_data.json"
    if json_file.exists():
        try:
            raw = json.loads(json_file.read_text("utf-8"))
        except:
            raw = None
        if isinstance(raw, list):
            raw = {"events": {"default": raw}}
        if isinstance(raw, dict) and "events" in raw:
            n = 0
            for uid, evts in raw["events"].items():
                for e in evts:
                    conn.execute(
                        "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)",
                        (e.get("id", _uid()), uid, e.get("title"), e.get("date"),
                         e.get("description", ""), e.get("color", "#5b6abf")))
                    n += 1
            conn.commit()
            json_file.rename(json_file.with_suffix(".json.migrated"))
            print(f"Migrated {n} events from JSON to SQLite")
    conn.close()


def load_events(user_id=None):
    """Return all events for a user, sorted by date."""
    if user_id is None:
        user_id = "default"
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, title, date, description, color FROM events WHERE user_id = ? ORDER BY date",
        (user_id,)
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "title": r["title"], "date": r["date"],
             "description": r["description"], "color": r["color"]} for r in rows]


def save_events(user_id, events):
    """Replace all events for a user (DELETE + INSERT in transaction)."""
    conn = _get_db()
    conn.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
    for e in events:
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            (e.get("id", _uid()), user_id, e.get("title"), e.get("date"),
             e.get("description", ""), e.get("color", "#5b6abf")))
    conn.commit()
    conn.close()


def hash_password(pw):
    """SHA-256 hash for passwords."""
    return hashlib.sha256(pw.encode()).hexdigest()


def get_user_by_token(token):
    """Look up email by API token, returns None if not found."""
    conn = _get_db()
    row = conn.execute("SELECT email FROM users WHERE api_token = ?", (token,)).fetchone()
    conn.close()
    return row["email"] if row else None


def verify_password(email, password):
    """Check email/password.
    Returns ("ok", token) on success,
            ("no_user", "") if email not found,
            ("wrong_pw", "") if password incorrect."""
    conn = _get_db()
    row = conn.execute(
        "SELECT password_hash, api_token FROM users WHERE email = ?",
        (email,)).fetchone()
    conn.close()
    if not row:
        return "no_user", ""
    if row["password_hash"] != hash_password(password):
        return "wrong_pw", ""
    return "ok", row["api_token"]


def create_user(email, password):
    """Create a new user, returns api_token. Returns None if email taken or invalid."""
    import re, secrets
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return None  # invalid email format
    conn = _get_db()
    exists = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
    if exists:
        conn.close()
        return None
    token = secrets.token_hex(16)
    conn.execute("INSERT INTO users VALUES (?,?,?)", (email, hash_password(password), token))
    conn.commit()
    conn.close()
    return token
