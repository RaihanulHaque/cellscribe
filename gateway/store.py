# Libre Thinker Agent gateway - SQLite storage
# SPDX-License-Identifier: GPL-3.0-or-later
"""Device registry and request-metadata log.

The database holds metadata only. Full conversations (when enabled) are
written by ``transcripts.py`` as JSON Lines; ``requests.transcript_ref``
points at the matching line.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

_DB_PATH = "/data/librethinkeragent.db"

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
    error             TEXT,
    cached_tokens     INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens  INTEGER,
    cost              REAL,
    saved             REAL,
    transcript_ref    TEXT
);

CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests (ts DESC);
CREATE INDEX IF NOT EXISTS idx_requests_device ON requests (device_id, ts DESC);
"""


_NEW_COLUMNS = (
    ("cached_tokens", "INTEGER"),
    ("cache_write_tokens", "INTEGER"),
    ("reasoning_tokens", "INTEGER"),
    ("cost", "REAL"),
    ("saved", "REAL"),
    ("transcript_ref", "TEXT"),
)


def configure(path: str) -> None:
    """Set the database path and create the schema. Call once at startup."""
    global _DB_PATH
    _DB_PATH = path
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _db() as conn:
        conn.executescript(_SCHEMA)
        # Databases created before cost tracking get the new columns in place.
        have = {row["name"] for row in conn.execute("PRAGMA table_info(requests)")}
        for column, kind in _NEW_COLUMNS:
            if column not in have:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {column} {kind}")
        dev_have = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
        if "revoked_at" not in dev_have:
            conn.execute("ALTER TABLE devices ADD COLUMN revoked_at REAL")


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
    token = "lta_" + secrets.token_urlsafe(32)
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


def list_devices(active: bool | None = None) -> list[dict[str, Any]]:
    """Devices without token hashes, with their request count and spend.

    ``active`` filters to live (True) or revoked (False) devices; None returns both.
    """
    where, args = "", ()
    if active is not None:
        where, args = "WHERE d.active = ?", (1 if active else 0,)
    with _db() as conn:
        rows = conn.execute(
            f"""SELECT d.id, d.name, d.models, d.active, d.created_at, d.last_seen_at, d.revoked_at,
                       COUNT(r.id) AS requests, SUM(r.cost) AS cost,
                       COALESCE(SUM(r.prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(r.completion_tokens), 0) AS completion_tokens
                FROM devices d LEFT JOIN requests r ON r.device_id = d.id
                {where} GROUP BY d.id ORDER BY d.id""",
            args,
        ).fetchall()
    return [dict(row) for row in rows]


def get_device(device_id: int) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return dict(row) if row else None


def revoke_device(device_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE devices SET active = 0, revoked_at = ? WHERE id = ?", (time.time(), device_id)
        )
    return cur.rowcount > 0


def set_device_models(device_id: int, models: list[str]) -> bool:
    with _db() as conn:
        cur = conn.execute("UPDATE devices SET models = ? WHERE id = ?", (",".join(models), device_id))
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
    cached_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cost: float | None = None,
    saved: float | None = None,
    transcript_ref: str | None = None,
) -> int:
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO requests
               (ts, device_id, device_name, model, status, latency_ms, stream,
                prompt_tokens, completion_tokens, error,
                cached_tokens, cache_write_tokens, reasoning_tokens, cost, saved, transcript_ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                cached_tokens,
                cache_write_tokens,
                reasoning_tokens,
                cost,
                saved,
                transcript_ref,
            ),
        )
        return int(cur.lastrowid)


def get_request(request_id: int) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def recent_requests(
    limit: int = 100,
    device_id: int | None = None,
    after_id: int | None = None,
    model: str | None = None,
    failed: bool | None = None,
    before_id: int | None = None,
    oldest_first: bool = False,
) -> list[dict[str, Any]]:
    """Newest-first request rows (or oldest-first). ``after_id`` / ``before_id`` page by request id."""
    limit = max(1, min(int(limit), 1000))
    where: list[str] = []
    args: list[Any] = []
    if device_id is not None:
        where.append("device_id = ?")
        args.append(device_id)
    if after_id is not None:
        where.append("id > ?")
        args.append(after_id)
    if before_id is not None:
        where.append("id < ?")
        args.append(before_id)
    if model:
        where.append("model = ?")
        args.append(model)
    if failed is True:
        where.append("status >= 400")
    elif failed is False:
        where.append("status < 400")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with _db() as conn:
        rows = conn.execute(
            f"SELECT * FROM requests {clause} ORDER BY id {'ASC' if oldest_first else 'DESC'} LIMIT ?", (*args, limit)
        ).fetchall()
    return [dict(row) for row in rows]


def _percentile(sorted_values: list[int], pct: float) -> int | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[index]


def stats(since: float, tz_offset_min: int = 0) -> dict[str, Any]:
    """Aggregate usage and spend for requests at or after ``since`` (epoch seconds).

    ``tz_offset_min`` shifts the daily buckets to the viewer's local time.
    """
    offset = int(tz_offset_min) * 60
    with _db() as conn:
        total = dict(
            conn.execute(
                """SELECT COUNT(*) AS requests,
                          COALESCE(SUM(status >= 400), 0) AS errors,
                          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                          COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                          COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                          COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                          COALESCE(SUM(cost), 0) AS cost,
                          COALESCE(SUM(saved), 0) AS saved,
                          COALESCE(SUM(cost IS NULL AND status < 400 AND prompt_tokens IS NOT NULL), 0) AS unpriced
                   FROM requests WHERE ts >= ?""",
                (since,),
            ).fetchone()
        )
        latencies = sorted(
            row[0]
            for row in conn.execute(
                "SELECT latency_ms FROM requests WHERE ts >= ? AND status < 400", (since,)
            )
        )
        by_model = [
            dict(row)
            for row in conn.execute(
                """SELECT model, COUNT(*) AS requests,
                          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                          COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                          SUM(cost) AS cost,
                          COALESCE(SUM(saved), 0) AS saved,
                          COALESCE(SUM(status >= 400), 0) AS errors
                   FROM requests WHERE ts >= ? GROUP BY model ORDER BY cost DESC, requests DESC""",
                (since,),
            )
        ]
        by_device = [
            dict(row)
            for row in conn.execute(
                """SELECT device_id, device_name, COUNT(*) AS requests,
                          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                          SUM(cost) AS cost,
                          COALESCE(SUM(status >= 400), 0) AS errors,
                          MAX(ts) AS last_ts
                   FROM requests WHERE ts >= ? GROUP BY device_id, device_name
                   ORDER BY cost DESC, requests DESC""",
                (since,),
            )
        ]
        by_day = [
            dict(row)
            for row in conn.execute(
                """SELECT strftime('%Y-%m-%d', ts + ?, 'unixepoch') AS day,
                          COUNT(*) AS requests,
                          COALESCE(SUM(cost), 0) AS cost,
                          COALESCE(SUM(saved), 0) AS saved,
                          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                          COALESCE(SUM(cached_tokens), 0) AS cached_tokens
                   FROM requests WHERE ts >= ? GROUP BY day ORDER BY day""",
                (offset, since),
            )
        ]
    total["latency_p50"] = _percentile(latencies, 0.5)
    total["latency_p95"] = _percentile(latencies, 0.95)
    return {"total": total, "by_model": by_model, "by_device": by_device, "by_day": by_day}

