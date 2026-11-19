# Libre Thinker Agent gateway - OpenAI-compatible proxy in front of Mistral
# SPDX-License-Identifier: GPL-3.0-or-later
"""Central AI gateway for LibreOffice WriterAgent clients.

The Mistral API key stays on this server. Each office PC holds a per-device
token that the gateway validates before proxying the request upstream.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

import packager
import store

def _provider_table() -> dict[str, dict[str, Any]]:
    """Upstream providers that have a key configured.

    Routing is by model id: ids with a vendor prefix ("deepseek/...",
    "mistralai/...") go to OpenRouter, bare ids ("mistral-large-latest") go to
    Mistral directly.
    """
    providers: dict[str, dict[str, Any]] = {}
    mistral_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if mistral_key:
        providers["mistral"] = {
            "url": os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1").rstrip("/"),
            "key": mistral_key,
            "headers": {},
        }
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        providers["openrouter"] = {
            "url": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            "key": openrouter_key,
            # Attribution headers OpenRouter shows in its dashboard.
            "headers": {"HTTP-Referer": "https://github.com/Sysnova-Information-Systems-Ltd/libre-agent", "X-Title": "Libre Thinker Agent"},
        }
    return providers


def provider_for(model: str) -> str:
    return "openrouter" if "/" in model else "mistral"


PROVIDERS = _provider_table()
def _env(name: str, default: str = "") -> str:
    """LIBRETHINKER_<name>, falling back to the pre-rename CELLSCRIBE_<name> so older .env files keep working."""
    value = os.environ.get("LIBRETHINKER_" + name)
    if value is None:
        value = os.environ.get("CELLSCRIBE_" + name)
    return default if value is None else value


DEFAULT_MODEL = _env("DEFAULT_MODEL", "deepseek/deepseek-v4.1-flash-1017")
_configured_models = [
    m.strip()
    for m in _env("ALLOWED_MODELS", DEFAULT_MODEL).split(",")
    if m.strip()
] or [DEFAULT_MODEL]
if DEFAULT_MODEL not in _configured_models:
    _configured_models.insert(0, DEFAULT_MODEL)
# Offer only models whose provider has a key; anything else would fail upstream.
ALLOWED_MODELS = [m for m in _configured_models if provider_for(m) in PROVIDERS]
DROPPED_MODELS = [m for m in _configured_models if m not in ALLOWED_MODELS]
ADMIN_TOKEN = _env("ADMIN_TOKEN", "")
DB_PATH = _env("DB", "/data/librethinker.db")
REQUEST_TIMEOUT = float(_env("TIMEOUT", "300"))
PUBLIC_URL = _env("PUBLIC_URL", "").rstrip("/")
OXT_PATH = _env("OXT", "/app/vendor/WriterAgent.oxt")

app = FastAPI(title="Libre Thinker Agent Gateway", version="0.1.0")
client: httpx.AsyncClient | None = None

# --- Pricing -----------------------------------------------------------------
# USD per token: {"prompt", "completion", "cache_read", "cache_write"}.
# OpenRouter publishes prices for every model it serves; they are fetched at
# startup and refreshed daily. LIBRETHINKER_PRICES overrides or adds models
# (needed for Mistral direct), as USD per million tokens:
#   {"mistral-large-latest": {"in": 2, "out": 6, "cache_read": 0.2}}
PRICES: dict[str, dict[str, float]] = {}


def _load_price_overrides() -> dict[str, dict[str, float]]:
    raw = _env("PRICES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("librethinker: LIBRETHINKER_PRICES is not valid JSON; ignoring it", flush=True)
        return {}
    table: dict[str, dict[str, float]] = {}
    for name, entry in (data.items() if isinstance(data, dict) else []):
        if not isinstance(entry, dict):
            continue
        table[str(name)] = {
            "prompt": float(entry.get("in", 0)) / 1e6,
            "completion": float(entry.get("out", 0)) / 1e6,
            "cache_read": float(entry["cache_read"]) / 1e6 if "cache_read" in entry else 0.0,
            "cache_write": float(entry["cache_write"]) / 1e6 if "cache_write" in entry else 0.0,
        }
    return table


def _per_token(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def refresh_prices() -> None:
    """Best-effort fetch of OpenRouter's public price list (no key needed)."""
    if "openrouter" not in PROVIDERS or client is None:
        return
    try:
        res = await client.get(f"{PROVIDERS['openrouter']['url']}/models", timeout=20)
        res.raise_for_status()
        for item in res.json().get("data", []):
            pricing = item.get("pricing") or {}
            PRICES[str(item.get("id"))] = {
                "prompt": _per_token(pricing.get("prompt")),
                "completion": _per_token(pricing.get("completion")),
                "cache_read": _per_token(pricing.get("input_cache_read")),
                "cache_write": _per_token(pricing.get("input_cache_write")),
            }
    except Exception as exc:  # pricing is optional; never block the gateway on it
        print(f"librethinker: could not fetch OpenRouter prices ({exc})", flush=True)
    PRICES.update(_load_price_overrides())


async def _price_refresher() -> None:
    while True:
        await refresh_prices()
        await asyncio.sleep(24 * 3600)


def usage_fields(model: str, usage: dict[str, Any] | None) -> dict[str, Any]:
    """Token, cache and cost fields for one request, from the provider's usage block.

    Cost is the provider's own figure when it reports one (OpenRouter does, with
    cache discounts applied); otherwise it is computed from the price table, and
    stays None when the model has no known price. ``saved`` is what the cache hits
    cost less than the same tokens at the full input price, so it needs prices too.
    """
    if not isinstance(usage, dict) or not usage:
        return {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    details = usage.get("prompt_tokens_details") or {}
    out_details = usage.get("completion_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:  # DeepSeek's own API names it differently
        cached = usage.get("prompt_cache_hit_tokens")
    cache_write = details.get("cache_write_tokens")
    price = PRICES.get(model)

    cost = usage.get("cost")
    if cost is None and price and prompt is not None:
        hit = cached or 0
        wrote = cache_write or 0
        fresh = max(0, prompt - hit - wrote)
        cost = (
            fresh * price["prompt"]
            + hit * (price["cache_read"] or price["prompt"])
            + wrote * (price["cache_write"] or price["prompt"])
            + (completion or 0) * price["completion"]
        )
    saved = None
    if price and cached and price["cache_read"]:
        saved = cached * max(0.0, price["prompt"] - price["cache_read"])
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": out_details.get("reasoning_tokens"),
        "cost": float(cost) if cost is not None else None,
        "saved": saved,
    }


@app.on_event("startup")
async def startup() -> None:
    global client
    if not PROVIDERS:
        raise RuntimeError("Set OPENROUTER_API_KEY and/or MISTRAL_API_KEY")
    if not ALLOWED_MODELS:
        raise RuntimeError(
            "None of LIBRETHINKER_ALLOWED_MODELS can be served with the configured keys "
            "(ids with a '/' need OPENROUTER_API_KEY; bare ids need MISTRAL_API_KEY)"
        )
    if DROPPED_MODELS:
        print(f"librethinker: no provider key for {', '.join(DROPPED_MODELS)}; not offering them", flush=True)
    if not ADMIN_TOKEN:
        raise RuntimeError("LIBRETHINKER_ADMIN_TOKEN is required")
    store.configure(DB_PATH)
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0))
    PRICES.update(_load_price_overrides())
    asyncio.create_task(_price_refresher())


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
    """A device's own allow-list (if any), limited to what the gateway can serve."""
    allowed = [m for m in store.device_models(device) if m in ALLOWED_MODELS]
    return allowed or ALLOWED_MODELS


# Transient upstream failures worth one more try: OpenRouter occasionally drops
# a connection mid-response, and hosts briefly return 502/503/504 or 429.
RETRY_STATUSES = {429, 502, 503, 504}
RETRY_ATTEMPTS = int(_env("RETRIES", "2"))


async def _send_with_retry(build: Any, *, stream: bool) -> httpx.Response:
    """Send a request, retrying transient failures before any byte reaches the client."""
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            if stream:
                response = await client.send(build(), stream=True)
            else:
                response = await client.send(build())
                await response.aread()  # a dropped body raises here, inside the retry
            if response.status_code in RETRY_STATUSES and attempt < RETRY_ATTEMPTS:
                await response.aclose()
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return response
        except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
    assert last_exc is not None
    raise last_exc


def _upstream_error(status: int, raw: bytes) -> tuple[dict[str, Any], str]:
    """Readable OpenAI-shaped error from an upstream failure, plus a short log line.

    Provider error bodies describe the request (bad model, context too long,
    no credit); they never contain our key, so pass the message through.
    """
    message = ""
    try:
        data = json.loads(raw.decode("utf-8", "ignore") or "{}")
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            message = str(err.get("message") or "")
            meta = err.get("metadata")
            if isinstance(meta, dict) and meta.get("raw"):
                message = f"{message}: {meta['raw']}" if message else str(meta["raw"])
        elif isinstance(err, str):
            message = err
        elif isinstance(data, dict):
            message = str(data.get("message") or data.get("detail") or "")
    except (ValueError, AttributeError):
        message = raw.decode("utf-8", "ignore").strip()
    message = (message or f"HTTP {status}")[:500]
    return (
        {"error": {"message": f"Upstream error ({status}): {message}", "type": "upstream_error"}},
        f"upstream_{status}: {message}"[:200],
    )


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True}


@app.get("/")
async def dashboard() -> Any:
    """Serve the small admin dashboard (data calls still need the admin token)."""
    return _page("admin.html")


@app.get("/requests")
async def requests_page() -> Any:
    """Full request log with filters and paging."""
    return _page("requests.html")


@app.get("/revoked")
async def revoked_page() -> Any:
    """Archive of revoked devices, kept off the main dashboard."""
    return _page("revoked.html")


@app.get("/assets/{name}")
async def dashboard_asset(name: str) -> Any:
    """Shared dashboard stylesheet and script (public files, no data)."""
    if name not in ("admin.css", "admin.js", "logo.svg", "logo_64.png"):
        raise HTTPException(status_code=404, detail="Not found")
    return _page(name)


def _page(name: str) -> Any:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Dashboard not installed")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


SETUP_TEMPLATE = r'''#!/usr/bin/env bash
# Libre Thinker Agent PC setup - generated by the gateway for one device.
set -euo pipefail

GATEWAY=__GATEWAY__
TOKEN=__TOKEN__
MODEL=__MODEL__

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
if ! command -v unopkg >/dev/null; then
  echo "unopkg not found: install LibreOffice 24.8 or newer" >&2
  if command -v flatpak >/dev/null && flatpak info org.libreoffice.LibreOffice >/dev/null 2>&1; then
    echo "Flatpak LibreOffice found; install the extension by hand instead:" >&2
    echo "  flatpak run --command=unopkg org.libreoffice.LibreOffice add <LibreThinkerAgent.oxt>" >&2
    echo "  then run provision/provision_pc.py with --gateway and --token" >&2
  fi
  exit 1
fi

# A conda/mamba environment on PATH breaks LibreOffice's Python loader: unopkg
# then mixes conda's stdlib with the system libpython and dies with
# "std::bad_alloc", even though LibreOffice itself works fine. Put the system
# directories first, drop conda entries, and clear Python overrides.
clean_path=$(printf '%s' "${PATH:-}" | tr ':' '\n' \
  | grep -v -E '/(ana|mini)conda[0-9]*/|/(mini|mamba)forge[0-9]*/|/condabin(/|$)' \
  | paste -sd: - || true)
PATH="/usr/local/bin:/usr/bin:/bin${clean_path:+:$clean_path}"
unset PYTHONHOME PYTHONPATH CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PYTHON_EXE || true
export PATH

if command -v pgrep >/dev/null && pgrep -x soffice.bin >/dev/null; then
  echo "Close LibreOffice first, then re-run." >&2; exit 1
fi

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
echo "Downloading WriterAgent extension..."
curl -fsSL -H "Authorization: Bearer $TOKEN" "$GATEWAY/setup/LibreThinkerAgent.oxt" -o "$tmp/LibreThinkerAgent.oxt"
# Install the extension, retrying once if a stale lock blocks LibreOffice.
install_oxt() {
  unopkg remove org.extension.writeragent >/dev/null 2>&1 || true
  unopkg add -f "$tmp/LibreThinkerAgent.oxt" 2>&1
}

add_out=$(install_oxt) && add_ok=1 || add_ok=0
if [ "$add_ok" = 0 ]; then
  case "$add_out" in
    *"lock file"*)
      # No LibreOffice is running (checked above), so the lock is stale: an
      # earlier session was killed and never removed it. Clear it and retry.
      echo "Removing a stale LibreOffice lock left by an earlier session..."
      rm -f "$HOME"/.config/libreoffice/*/.lock 2>/dev/null || true
      add_out=$(install_oxt) && add_ok=1 || add_ok=0 ;;
  esac
fi
if [ "$add_ok" = 0 ]; then
  printf '%s\n' "$add_out" >&2
  case "$add_out" in
    *bad_alloc*|*Bad_alloc*)
      echo "hint: LibreOffice's Python loader could not start. That is usually a" >&2
      echo "      second Python (conda, pyenv, or PYTHONHOME) visible to the shell," >&2
      echo "      not an outdated LibreOffice. Try: PATH=/usr/bin:/bin bash setup.sh" >&2 ;;
  esac
  echo "unopkg add failed. LibreOffice 24.8 or newer is required." >&2
  exit 1
fi
if list_out=$(unopkg list 2>&1); then
  case "$list_out" in
    *org.extension.writeragent*) echo "Extension installed and registered." ;;
    *) echo "warning: extension installed but not listed as registered." >&2 ;;
  esac
fi

# The profile only exists after LibreOffice (or unopkg) has run once.
profile=$(ls -dt "$HOME"/.config/libreoffice/*/user 2>/dev/null | head -n1 || true)
[ -n "$profile" ] || { echo "No LibreOffice profile found; start LibreOffice once and re-run." >&2; exit 1; }
mkdir -p "$profile/config"
cfg="$profile/config/writeragent.json"

new=$(cat <<JSON
{"endpoint": "$GATEWAY", "text_model": "$MODEL", "model": "$MODEL",
 "api_keys_by_endpoint": {"$GATEWAY": "$TOKEN"},
 "enable_agent_log": true, "request_timeout": 300}
JSON
)
if [ -f "$cfg" ]; then
  cp -p "$cfg" "$cfg.bak.$(date +%s)"
  if command -v jq >/dev/null && grep -v '^[[:space:]]*//' "$cfg" | jq -e . >/dev/null 2>&1; then
    new=$(grep -v '^[[:space:]]*//' "$cfg" | jq --argjson n "$new" '. * $n')
  fi
fi
umask 077
printf '%s\n' "$new" > "$cfg"
chmod 600 "$cfg"
echo "Done. Configured $cfg"
echo "Open LibreOffice and try =PROMPT(\"hello\") in Calc."
'''


def _public_base(request: Request) -> str:
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    if not re.fullmatch(r"https?://[A-Za-z0-9.\-:\[\]]+", base):
        raise HTTPException(status_code=400, detail="Cannot determine a safe gateway URL; set LIBRETHINKER_PUBLIC_URL")
    return base


def _setup_params(request: Request, device: dict[str, Any]) -> tuple[str, str, str]:
    """Gateway URL, device token and model to embed in a setup script."""
    base = _public_base(request)
    token = request.headers["authorization"].split(" ", 1)[1].strip()
    allowed = _models_for_device(device)
    # Prefer the gateway default when the device may use it: it is the model
    # that reliably calls apply_document_content in Writer. Otherwise use the
    # device's first allowed model.
    default = DEFAULT_MODEL if DEFAULT_MODEL in allowed else allowed[0]
    model = request.query_params.get("model") or default
    if model not in allowed:
        raise HTTPException(status_code=403, detail=f"Model '{model}' is not permitted for this device")
    return base, token, model


@app.get("/setup/LibreThinkerAgent.oxt")
@app.get("/setup/LibreThinker.oxt", include_in_schema=False)  # link in setup scripts made before the rename
async def setup_oxt(request: Request) -> Response:
    """The single extension (WriterAgent + token entry); identical for every PC."""
    if not os.path.exists(OXT_PATH):
        raise HTTPException(status_code=404, detail="Extension package not installed on the gateway")
    data = packager.build_oxt(OXT_PATH, _public_base(request), DEFAULT_MODEL)
    return Response(
        data,
        media_type="application/vnd.openofficeorg.extension",
        headers={"Content-Disposition": 'attachment; filename="LibreThinkerAgent.oxt"'},
    )


@app.get("/setup.sh")
async def setup_script(request: Request, device: dict[str, Any] = Depends(_require_device)) -> PlainTextResponse:
    """Self-contained installer for one device: curl -H 'Authorization: Bearer TOKEN' URL | bash."""
    base, token, model = _setup_params(request, device)
    script = (
        SETUP_TEMPLATE.replace("__GATEWAY__", shlex.quote(base))
        .replace("__TOKEN__", shlex.quote(token))
        .replace("__MODEL__", shlex.quote(model))
    )
    return PlainTextResponse(script)


@app.get("/v1/models")
async def list_models(device: dict[str, Any] = Depends(_require_device)) -> dict[str, Any]:
    """OpenAI-compatible model list, filtered to what this device may use."""
    models = _models_for_device(device)
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": 0, "owned_by": "librethinker"}
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

    provider_name = provider_for(model)
    provider = PROVIDERS[provider_name]
    if provider_name == "openrouter":
        # Route only to hosts that support every parameter we send (tools in
        # particular); otherwise OpenRouter may pick a host that ignores them.
        prefs = payload.get("provider") if isinstance(payload.get("provider"), dict) else {}
        payload["provider"] = {"require_parameters": True, **prefs}
        # Ask for the real cost (cache discounts included) in the usage block.
        payload.setdefault("usage", {"include": True})
        if stream:
            payload.setdefault("stream_options", {"include_usage": True})

    headers = {
        "Authorization": f"Bearer {provider['key']}",
        "Content-Type": "application/json",
        **provider["headers"],
    }
    url = f"{provider['url']}/chat/completions"

    try:
        if stream:
            upstream = await _send_with_retry(
                lambda: client.build_request("POST", url, json=payload, headers=headers),
                stream=True,
            )
            if upstream.status_code >= 400:
                raw = await upstream.aread()
                await upstream.aclose()
                content, log_error = _upstream_error(upstream.status_code, raw)
                store.log_request(
                    device_id=int(device["id"]),
                    device_name=device["name"],
                    model=model,
                    status=upstream.status_code,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    stream=True,
                    error=log_error,
                )
                return JSONResponse(content, status_code=upstream.status_code)

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
                                usage = found
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
                        **usage_fields(model, usage),
                    )

            return StreamingResponse(
                relay(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        response = await _send_with_retry(
            lambda: client.build_request("POST", url, json=payload, headers=headers),
            stream=False,
        )
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            content, log_error = _upstream_error(response.status_code, response.content)
            store.log_request(
                device_id=int(device["id"]),
                device_name=device["name"],
                model=model,
                status=response.status_code,
                latency_ms=latency,
                stream=False,
                error=log_error,
            )
            return JSONResponse(content, status_code=response.status_code)
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
            **usage_fields(model, usage),
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
async def admin_devices(
    status: str = "all", _: None = Depends(_require_admin)
) -> list[dict[str, Any]]:
    """Devices with their request count and spend. status: active | revoked | all."""
    active = {"active": True, "revoked": False}.get(status)
    return store.list_devices(active=active)


@app.post("/admin/devices")
async def admin_create_device(request: Request, _: None = Depends(_require_admin)) -> dict[str, Any]:
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    models = [m for m in (body.get("models") or []) if m in ALLOWED_MODELS]
    device_id, token = store.create_device(name, models)
    return {"id": device_id, "name": name, "models": models, "token": token}


@app.post("/admin/devices/{device_id}/models")
async def admin_set_models(device_id: int, request: Request, _: None = Depends(_require_admin)) -> dict[str, Any]:
    """Replace a device's model allow-list; an empty list means every gateway model."""
    body = await request.json()
    models = [m for m in (body.get("models") or []) if m in ALLOWED_MODELS]
    if not store.set_device_models(device_id, models):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"id": device_id, "models": models}


@app.post("/admin/devices/{device_id}/revoke")
async def admin_revoke(device_id: int, _: None = Depends(_require_admin)) -> dict[str, Any]:
    if not store.revoke_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"revoked": device_id}


@app.get("/admin/logs")
async def admin_logs(
    limit: int = 100,
    device_id: int | None = None,
    after_id: int | None = None,
    before_id: int | None = None,
    model: str | None = None,
    result: str = "all",
    _: None = Depends(_require_admin),
) -> list[dict[str, Any]]:
    """Request metadata, newest first. result: all | ok | failed."""
    failed = {"failed": True, "ok": False}.get(result)
    return store.recent_requests(
        limit=limit,
        device_id=device_id,
        after_id=after_id,
        before_id=before_id,
        model=model or None,
        failed=failed,
    )


RANGES = {"today": None, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": 0}


@app.get("/admin/stats")
async def admin_stats(
    range: str = "7d", tz: int = 0, _: None = Depends(_require_admin)
) -> dict[str, Any]:
    """Usage, cache and cost totals. range: today | 24h | 7d | 30d | all.

    ``tz`` is the viewer's offset from UTC in minutes, so "today" and the daily
    buckets follow their clock.
    """
    if range not in RANGES:
        raise HTTPException(status_code=400, detail="range must be one of " + ", ".join(RANGES))
    now = time.time()
    if range == "today":
        local = now + tz * 60
        since = local - (local % 86400) - tz * 60
    else:
        since = now - RANGES[range] if RANGES[range] else 0
    data = store.stats(since, tz_offset_min=tz)
    data["range"] = range
    data["priced_models"] = sum(1 for m in ALLOWED_MODELS if m in PRICES)
    return data


@app.get("/admin/models")
async def admin_models(_: None = Depends(_require_admin)) -> list[str]:
    return ALLOWED_MODELS
