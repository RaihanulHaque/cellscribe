# CellScribe

Project to bring LLMs (Mistral for now) into LibreOffice Writer and Calc.

## Decision

Use [WriterAgent](https://github.com/KeithCu/writeragent) rather than building a new extension.
Reported as covering the project's needs.

Verified on 2026-11-02: one headless `=PROMPT()` call in Calc through `mistral-small-latest` returned
the expected text with no cell error, and the only remote connection sampled was to a Mistral IP.
Not verified headlessly: sidebar chat, tool calling and document edits by the model.

- Licence: GPL-3.0-or-later. Source-sharing rules apply only when distributing a modified version.
- Missing features: contribute upstream rather than forking.

## Tested package

`vendor/WriterAgent.oxt`

- Release tag: 0.8.84 (the package itself reports version 0.8.85)
- SHA-256: `445e495556b1e67e20b5777eb96c7b6e1cb082a5bd871b4f9f6b46f5f99bf73c`
- Tested on LibreOffice 24.8.7, Ubuntu 24.10

The release asset was replaced on 2026-11-01, after the 2026-09-19 release date, so keep this copy
and test updates before a wider rollout.

Install (close LibreOffice first):

```bash
unopkg add vendor/WriterAgent.oxt
```

Settings: **WriterAgent → Settings**, endpoint `https://api.mistral.ai`, plus an API key and model.
Do not commit API keys or LibreOffice profiles to this folder.

## Centralised setup (gateway)

Instead of every employee entering a Mistral key and endpoint by hand, all office PCs point at a
small gateway on the LAN. The Mistral key exists only on the gateway.

```text
Admin dashboard ──> gateway ──> api.mistral.ai   (key lives here)
                       ^
                       │  per-PC token, metadata-only request log
                       │
          LibreOffice + WriterAgent on each Linux PC
```

### What lives where

| Item | Location |
| --- | --- |
| Mistral API key | `gateway/.env` on the server only |
| Per-PC token | Gateway database + that PC's `writeragent.json` |
| Endpoint / model | Written into `writeragent.json` by `provision/provision_pc.py` |
| Request log | Gateway SQLite: device, model, status, latency, token counts. No prompt or document text. |

The gateway is OpenAI-compatible, so WriterAgent needs no patching. Its config file is
`writeragent.json` in the LibreOffice profile (`~/.config/libreoffice/4/user/config/`), and it
re-reads the file within a few seconds of a change.

### 1. Start the gateway

```bash
cd gateway
cp .env.example .env          # set MISTRAL_API_KEY and CELLSCRIBE_ADMIN_TOKEN
docker compose up -d --build
```

Open `http://<gateway-ip>:8080/`, paste the admin token, and register a PC. The device token is
shown **once** — copy it into the provisioning step.

Generate the admin token with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2. Provision a Linux PC

Run as the desktop user who uses LibreOffice:

```bash
python3 provision/provision_pc.py \
  --gateway http://10.0.0.5:8080 \
  --token cs_... \
  --model mistral-small-latest \
  --install-extension
```

The script backs up the existing config, preserves unrelated settings, and writes the endpoint,
model, and device token. The Mistral key is never placed on the PC.

### Security notes

- The gateway holds every credential. Keep it on the LAN, do not publish port 8080 to the internet.
- Device tokens are bearer tokens. On plain HTTP anyone on the LAN can sniff them; put the gateway
  behind a TLS reverse proxy (Caddy/nginx) or a VPN before real use.
- Do **not** run the gateway on ports 11434 or 1234. WriterAgent treats those as Ollama/LM Studio
  and would skip the auth header.
- Admin endpoints require `X-Admin-Token`; device endpoints require `Authorization: Bearer <token>`.
- Request logging is metadata only. Prompt content is deliberately not stored.
- Revoking a device takes effect on its next request.

### Known gaps

- Only `/v1/chat/completions` and `/v1/models` are proxied. Embeddings, images, audio and speech
  would hit the gateway and fail; add upstream routes if those features are needed.
- No automated install or log shipping yet. Planned next: a small Linux agent that enrolls a PC,
  applies config changes, and forwards `writeragent_debug.log`. After that, dashboard/ERP
  integration.
- No rate limiting or per-user quotas yet.

## Why not LibreThinker

It only supports Writer, and requests using your own API key send the key and document text through
`api.librethinker.com`.

## Open items

- Security review before using confidential documents. So far: keyword scan of the package and a
  headless startup check with no key set, which showed no remote connections.
- Confirm company policy allows sending document content to Mistral.
- It can download extra tools, such as the Harper grammar checker from GitHub. Not yet tested.
- The debug log shows `_geometric_open_job timed out after 30.0s` in headless runs. Check whether it
  also appears with a normal window (`writeragent_debug.log` in the profile's `user/config`).
- Leave the MCP server and public tunnel off. Both are off by default, and the MCP server has no auth.
