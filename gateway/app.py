# CellScribe gateway - OpenAI-compatible proxy in front of Mistral
# SPDX-License-Identifier: GPL-3.0-or-later
"""Central AI gateway for LibreOffice WriterAgent clients.

The Mistral API key stays on this server. Each office PC holds a per-device
token that the gateway validates before proxying the request upstream.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import store

UPSTREAM_URL = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1").rstrip("/")
UPSTREAM_KEY = os.environ.get("MISTRAL_API_KEY", "")
DEFAULT_MODEL = os.environ.get("CELLSCRIBE_DEFAULT_MODEL", "mistral-small-latest")
ALLOWED_MODELS = [
    m.strip()
    for m in os.environ.get("CELLSCRIBE_ALLOWED_MODELS", DEFAULT_MODEL).split(",")
    if m.strip()
] or [DEFAULT_MODEL]
if DEFAULT_MODEL not in ALLOWED_MODELS:
    ALLOWED_MODELS.append(DEFAULT_MODEL)
ADMIN_TOKEN = os.environ.get("CELLSCRIBE_ADMIN_TOKEN", "")
DB_PATH = os.environ.get("CELLSCRIBE_DB", "/data/cellscribe.db")
REQUEST_TIMEOUT = float(os.environ.get("CELLSCRIBE_TIMEOUT", "300"))

app = FastAPI(title="CellScribe Gateway", version="0.1.0")
client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def startup() -> None:
    global client
    if not UPSTREAM_KEY:
        raise RuntimeError("MISTRAL_API_KEY is required")
    if not ADMIN_TOKEN:
        raise RuntimeError("CELLSCRIBE_ADMIN_TOKEN is required")
    store.configure(DB_PATH)
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0))


@app.on_event("shutdown")
async def shutdown() -> None:
    if client is not None:
        await client.aclose()


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    """Return errors in the OpenAI shape so clients surface a readable message."""
    return JSONResponse(
        {"error": {"message": str(exc.detail), "type": "gateway_error"}},
        status_code=exc.status_code,
    )


def _require_admin(x_admin_token: str = Header(default="")) -> None:
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _require_device(authorization: str = Header(default="")) -> dict[str, Any]:
    """Validate the per-device bearer token."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    device = store.find_device_by_token(token)
    if device is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked device token")
    return device


def _models_for_device(device: dict[str, Any]) -> list[str]:
    allowed = store.device_models(device)
    return allowed or ALLOWED_MODELS


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True}


@app.get("/")
async def dashboard() -> Any:
    """Serve the small admin dashboard (data calls still need the admin token)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Dashboard not installed")
    return FileResponse(path)


@app.get("/v1/models")
async def list_models(device: dict[str, Any] = Depends(_require_device)) -> dict[str, Any]:
    """OpenAI-compatible model list, filtered to what this device may use."""
    models = _models_for_device(device)
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": 0, "owned_by": "cellscribe"}
            for name in models
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request, device: dict[str, Any] = Depends(_require_device)
) -> Any:
    """Proxy a chat completion to Mistral, logging metadata only."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = str(body.get("model") or DEFAULT_MODEL)
    allowed = _models_for_device(device)
    if model not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model}' is not permitted for this device",
        )

    payload = dict(body)
    payload["model"] = model
    stream = bool(payload.get("stream"))
    started = time.monotonic()
    store.touch_device(int(device["id"]))

    headers = {
        "Authorization": f"Bearer {UPSTREAM_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{UPSTREAM_URL}/chat/completions"

    try:
        if stream:
            upstream = await client.send(
                client.build_request("POST", url, json=payload, headers=headers),
                stream=True,
            )
            if upstream.status_code >= 400:
                await upstream.aread()
                await upstream.aclose()
                store.log_request(
                    device_id=int(device["id"]),
                    device_name=device["name"],
                    model=model,
                    status=upstream.status_code,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    stream=True,
                    error=f"upstream_{upstream.status_code}",
                )
                return JSONResponse(
                    {"error": {"message": "Upstream request failed", "type": "upstream_error"}},
                    status_code=upstream.status_code,
                )

            async def relay() -> Any:
                usage: dict[str, Any] = {}
                pending = ""
                try:
                    async for chunk in upstream.aiter_bytes():
                        # Best-effort usage capture from SSE data lines.
                        pending += chunk.decode("utf-8", "ignore")
                        lines = pending.split("\n")
                        pending = lines.pop()
                        for line in lines:
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            try:
                                obj = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            found = obj.get("usage") if isinstance(obj, dict) else None
                            if isinstance(found, dict):
                                usage["prompt_tokens"] = found.get("prompt_tokens")
                                usage["completion_tokens"] = found.get("completion_tokens")
                        yield chunk
                finally:
                    await upstream.aclose()
                    store.log_request(
                        device_id=int(device["id"]),
                        device_name=device["name"],
                        model=model,
                        status=upstream.status_code,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        stream=True,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                    )

            return StreamingResponse(
                relay(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        response = await client.post(url, json=payload, headers=headers)
        latency = int((time.monotonic() - started) * 1000)
        usage = {}
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                usage = response.json().get("usage") or {}
            except Exception:
                usage = {}
        store.log_request(
            device_id=int(device["id"]),
            device_name=device["name"],
            model=model,
            status=response.status_code,
            latency_ms=latency,
            stream=False,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            error=None if response.status_code < 400 else f"upstream_{response.status_code}",
        )
        return JSONResponse(
            content=response.json() if response.headers.get("content-type", "").startswith("application/json") else {"error": {"message": "Upstream returned a non-JSON response"}},
            status_code=response.status_code,
        )
    except httpx.HTTPError as exc:
        store.log_request(
            device_id=int(device["id"]),
            device_name=device["name"],
            model=model,
            status=502,
            latency_ms=int((time.monotonic() - started) * 1000),
            stream=stream,
            error=str(exc)[:200],
        )
        raise HTTPException(status_code=502, detail="Upstream request failed")


@app.get("/admin/devices")
async def admin_devices(_: None = Depends(_require_admin)) -> list[dict[str, Any]]:
    return store.list_devices()


@app.post("/admin/devices")
async def admin_create_device(request: Request, _: None = Depends(_require_admin)) -> dict[str, Any]:
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    models = [m for m in (body.get("models") or []) if m in ALLOWED_MODELS]
    device_id, token = store.create_device(name, models)
    return {"id": device_id, "name": name, "models": models, "token": token}


@app.post("/admin/devices/{device_id}/revoke")
async def admin_revoke(device_id: int, _: None = Depends(_require_admin)) -> dict[str, Any]:
    if not store.revoke_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"revoked": device_id}


@app.get("/admin/logs")
async def admin_logs(
    limit: int = 100, device_id: int | None = None, _: None = Depends(_require_admin)
) -> list[dict[str, Any]]:
    return store.recent_requests(limit=limit, device_id=device_id)


@app.get("/admin/models")
async def admin_models(_: None = Depends(_require_admin)) -> list[str]:
    return ALLOWED_MODELS
