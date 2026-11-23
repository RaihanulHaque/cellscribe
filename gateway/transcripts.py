# Libre Thinker Agent gateway - conversation log (JSON Lines)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Full request/response transcripts, one JSON object per line, one file per day.

Layout (inside the gateway's /data volume)::

    transcripts/2026-11-23.jsonl        today, appended to
    transcripts/2026-11-22.jsonl.gz     earlier days, gzipped

Each line is self-contained (schema ``lta-transcript/1``)::

    {"schema": "lta-transcript/1", "id": "...", "time": "2026-11-23T09:15:02Z",
     "device": {"id": 3, "name": "Accounts PC"}, "model": "...", "provider": "openrouter",
     "stream": true, "status": 200, "latency_ms": 5321, "usage": {...},
     "request":  {"messages": [...], "tools": [...], "temperature": ..., ...},
     "response": {"message": {"role": "assistant", "content": ..., "tool_calls": [...],
                              "reasoning": ...}, "finish_reason": "tool_calls"},
     "error": null}

``request.messages`` + ``response.message`` is the whole exchange in the
OpenAI chat format, which is what fine-tuning tools read. The SQLite request
log points at a line with ``transcript_ref`` = ``"<day>:<byte offset>"``
(offset into the uncompressed file), so the browser can open one quickly.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import re
import shutil
import threading
import time
import uuid
from typing import Any, Iterator

SCHEMA = "lta-transcript/1"
_DIR = "/data/transcripts"
_ENABLED = True
_LOCK = threading.Lock()
_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")


def configure(directory: str, enabled: bool = True) -> None:
    global _DIR, _ENABLED
    _DIR, _ENABLED = directory, enabled
    if enabled:
        os.makedirs(directory, exist_ok=True)


def enabled() -> bool:
    return _ENABLED


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _path(day: str) -> str | None:
    """Existing file for *day* (plain while being written, else gzipped)."""
    for name in (day + ".jsonl", day + ".jsonl.gz"):
        p = os.path.join(_DIR, name)
        if os.path.exists(p):
            return p
    return None


def new_id() -> str:
    return uuid.uuid4().hex


def write(record: dict[str, Any]) -> str | None:
    """Append one record; return its ``transcript_ref`` (None when disabled or on error)."""
    if not _ENABLED:
        return None
    ts = float(record.get("ts") or time.time())
    record = {
        "schema": SCHEMA,
        "id": record.get("id") or new_id(),
        "time": _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **record,
    }
    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")
    day = _day(ts)
    try:
        with _LOCK:
            path = os.path.join(_DIR, day + ".jsonl")
            with open(path, "ab") as fh:
                offset = fh.tell()
                fh.write(line)
        return "%s:%d" % (day, offset)
    except OSError as exc:  # a full disk must not break the chat
        print("librethinkeragent: transcript write failed: %s" % exc, flush=True)
        return None


def read(ref: str) -> dict[str, Any] | None:
    try:
        day, off = ref.rsplit(":", 1)
        offset = int(off)
    except (ValueError, AttributeError):
        return None
    if not _DAY_RE.match(day + ".jsonl"):
        return None
    path = _path(day)
    if path is None:
        return None
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        fh.seek(offset)
        line = fh.readline()
    try:
        return json.loads(line)
    except ValueError:
        return None


def files() -> list[dict[str, Any]]:
    """Daily files, newest first."""
    out = []
    try:
        names = os.listdir(_DIR)
    except OSError:
        return []
    for name in names:
        m = _DAY_RE.match(name)
        if m:
            out.append({"day": m.group(1), "file": name, "bytes": os.path.getsize(os.path.join(_DIR, name)),
                        "compressed": bool(m.group(2))})
    return sorted(out, key=lambda f: f["day"], reverse=True)


def file_path(name: str) -> str | None:
    if not _DAY_RE.match(name):
        return None
    p = os.path.join(_DIR, name)
    return p if os.path.exists(p) else None


def iter_records(days: list[str]) -> Iterator[dict[str, Any]]:
    for day in sorted(days):
        path = _path(day)
        if path is None:
            continue
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rb") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def maintain(keep_days: int = 0) -> None:
    """Gzip finished days and delete days older than *keep_days* (0 = keep forever)."""
    if not _ENABLED:
        return
    today = _day(time.time())
    cutoff = _day(time.time() - keep_days * 86400) if keep_days > 0 else ""
    for f in files():
        path = os.path.join(_DIR, f["file"])
        if cutoff and f["day"] < cutoff:
            os.remove(path)
            continue
        if not f["compressed"] and f["day"] < today:
            with _LOCK:
                with open(path, "rb") as src, gzip.open(path + ".gz.tmp", "wb", compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(path + ".gz.tmp", path + ".gz")
                os.remove(path)


def finetune_example(rec: dict[str, Any], with_reasoning: bool = False) -> dict[str, Any] | None:
    """One successful exchange as ``{"messages": [...], "tools": [...]}`` (OpenAI fine-tuning format)."""
    req, resp = rec.get("request") or {}, rec.get("response") or {}
    msg = resp.get("message")
    if not isinstance(msg, dict) or (rec.get("status") or 0) >= 400 or not req.get("messages"):
        return None
    keep = ("role", "content", "tool_calls", "tool_call_id", "name")
    out_msg = {k: msg[k] for k in keep if msg.get(k) not in (None, "", [])}
    out_msg.setdefault("role", "assistant")
    if "content" not in out_msg and "tool_calls" not in out_msg:
        return None
    if with_reasoning and msg.get("reasoning"):
        out_msg["reasoning"] = msg["reasoning"]
    example: dict[str, Any] = {"messages": list(req["messages"]) + [out_msg]}
    if req.get("tools"):
        example["tools"] = req["tools"]
    return example


class StreamCollector:
    """Rebuilds the assistant message from OpenAI-style SSE chunks."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.role = "assistant"

    def feed(self, obj: dict[str, Any]) -> None:
        for choice in obj.get("choices") or []:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("role"):
                self.role = delta["role"]
            if isinstance(delta.get("content"), str):
                self.content.append(delta["content"])
            for key in ("reasoning", "reasoning_content"):
                if isinstance(delta.get(key), str):
                    self.reasoning.append(delta[key])
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index", len(self.tool_calls)))
                slot = self.tool_calls.setdefault(idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                if tc.get("type"):
                    slot["type"] = tc["type"]
                fn = tc.get("function") or {}
                if fn.get("name") and fn["name"] != slot["function"]["name"]:
                    slot["function"]["name"] += fn["name"]  # some hosts split the name, most send it once
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]

    def result(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": "".join(self.content) or None}
        if self.tool_calls:
            msg["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        if self.reasoning:
            msg["reasoning"] = "".join(self.reasoning)
        return {"message": msg, "finish_reason": self.finish_reason}
