# CellScribe gateway - SQLite storage
# SPDX-License-Identifier: GPL-3.0-or-later
"""Device registry and request-metadata log.

Only request metadata is stored. Prompt and completion text are never
written to the database.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

_DB_PATH = "/data/cellscribe.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    token_hash    TEXT    NOT NULL UNIQUE,
    models        TEXT    NOT NULL DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    REAL    NOT NULL,
    last_seen_at  REAL
);

CREATE TABLE IF NOT EXISTS requests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL    NOT NULL,
    device_id         INTEGER,
    device_name       TEXT    NOT NULL DEFAULT '',
    model             TEXT    NOT NULL DEFAULT '',
    status            INTEGER NOT NULL,
    latency_ms        INTEGER NOT NULL,
    stream            INTEGER NOT NULL DEFAULT 0,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests (ts DESC);
CREATE INDEX IF NOT EXISTS idx_requests_device ON requests (device_id, ts DESC);
"""


def configure(path: str) -> None:
    """Set the database path and create the schema. Call once at startup."""
    global _DB_PATH
    _DB_PATH = path
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _db() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _db():
    """Open, commit, and always close a connection."""
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _split_models(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def create_device(name: str, models: list[str] | None = None) -> tuple[int, str]:
    """Create a device and return ``(id, plaintext_token)``.

    The plaintext token is returned exactly once; only its hash is stored.
    """
    token = "cs_" + secrets.token_urlsafe(32)
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO devices (name, token_hash, models, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (name.strip(), hash_token(token), ",".join(models or []), time.time()),
        )
        return int(cur.lastrowid), token


def find_device_by_token(token: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM devices WHERE token_hash = ? AND active = 1",
            (hash_token(token),),
        ).fetchone()
    return dict(row) if row else None


def list_devices() -> list[dict[str, Any]]:
    """Devices without token hashes."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, name, models, active, created_at, last_seen_at FROM devices ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def get_device(device_id: int) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return dict(row) if row else None


def revoke_device(device_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute("UPDATE devices SET active = 0 WHERE id = ?", (device_id,))
    return cur.rowcount > 0


def touch_device(device_id: int) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE devices SET last_seen_at = ? WHERE id = ?", (time.time(), device_id)
        )


def device_models(device: dict[str, Any]) -> list[str]:
    return _split_models(str(device.get("models") or ""))


def log_request(
    *,
    device_id: int | None,
    device_name: str,
    model: str,
    status: int,
    latency_ms: int,
    stream: bool,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error: str | None = None,
) -> None:
    with _db() as conn:
        conn.execute(
            """INSERT INTO requests
               (ts, device_id, device_name, model, status, latency_ms, stream,
                prompt_tokens, completion_tokens, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                device_id,
                device_name,
                model,
                status,
                latency_ms,
                1 if stream else 0,
                prompt_tokens,
                completion_tokens,
                (error or "")[:500] or None,
            ),
        )


def recent_requests(limit: int = 100, device_id: int | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    with _db() as conn:
        if device_id is None:
            rows = conn.execute(
                "SELECT * FROM requests ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM requests WHERE device_id = ? ORDER BY ts DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]
