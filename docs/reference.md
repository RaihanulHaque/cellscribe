# Reference

## Gateway HTTP API

Device endpoints require `Authorization: Bearer <device-token>`; admin endpoints require
`X-Admin-Token: <admin-token>`.

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | Liveness probe. |
| `GET` | `/`, `/requests`, `/conversation`, `/logs`, `/revoked` | none | Dashboard, request log, conversation viewer, log files and revoked-devices pages (their data calls need the admin token). |
| `GET` | `/setup/LibreThinkerAgent.oxt` | none | The patched extension with the gateway URL embedded; contains no secret. |
| `GET` | `/setup.sh` | device | Generated installer and config script for that device. |
| `GET` | `/v1/models` | device | Models this device may use, OpenAI-shaped. |
| `POST` | `/v1/chat/completions` | device | Proxied to OpenRouter or Mistral by model id; supports `stream: true`. |
| `GET` | `/admin/devices?status=active\|revoked\|all` | admin | List devices (no token hashes) with request count and spend. |
| `POST` | `/admin/devices` | admin | Create a device; returns its token once. |
| `POST` | `/admin/devices/{id}/models` | admin | Replace a device's model allow-list; `[]` means all models. |
| `POST` | `/admin/devices/{id}/revoke` | admin | Revoke immediately. |
| `GET` | `/admin/logs?limit=100` | admin | Request metadata, newest first. Filters: `device_id`, `model`, `result=ok\|failed`, `after_id`, `before_id`; `order=asc` for oldest first. Rows carry `transcript_ref` when the full exchange was saved. |
| `GET` | `/admin/requests/{id}/transcript` | admin | The saved exchange for one request: `{"request": <metadata row>, "transcript": <lta-transcript/1 record>}`. |
| `GET` | `/admin/transcripts` | admin | Conversation-log status and daily files (`day`, `file`, `bytes`, `compressed`). |
| `GET` | `/admin/transcripts/file/{name}` | admin | Download one daily file (`2026-11-23.jsonl` or `.jsonl.gz`). |
| `GET` | `/admin/transcripts/finetune.jsonl?start=YYYY-MM-DD&end=YYYY-MM-DD&reasoning=false` | admin | Successful requests in the range as `{"messages", "tools"}` lines for fine-tuning. |
| `GET` | `/admin/stats?range=7d&tz=0` | admin | Spend, tokens, cache hit rate and latency totals, per model, PC and day. `range`: `today`, `24h`, `7d`, `30d`, `all`; `tz` is minutes from UTC. |
| `GET` | `/admin/models` | admin | Models the gateway can serve. |

Create a device without the dashboard:

```bash
curl -s -X POST http://<gateway-ip>:8080/admin/devices \
  -H "X-Admin-Token: $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"acc-pc-01","models":[]}'
```

The response contains the plaintext `token`. The gateway keeps only its SHA-256 hash and cannot
show it again.

Errors use OpenAI's shape so LibreOffice shows a readable message, for example:

```json
{"error": {"message": "Upstream error (401): API key expired.", "type": "upstream_error"}}
```

## Repository layout

```text
.
├── gateway/
│   ├── app.py               FastAPI proxy: auth, provider routing, retries, admin routes
│   ├── patches.py           Exact patches applied to WriterAgent when building LibreThinkerAgent.oxt
│   ├── packager.py          Builds LibreThinkerAgent.oxt (token-entry menu item + patches)
│   ├── store.py             SQLite device registry and request log
│   ├── admin.html           Admin dashboard (live stats, costs, requests)
│   ├── requests.html        Full request log with filters and paging
│   ├── revoked.html         Revoked-devices archive page
│   ├── admin.css, admin.js  Styles and helpers shared by both pages
│   ├── brand/               Libre Thinker Agent logo (SVG source and PNG/JPG sizes used in the extension)
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   ├── setup-pc.sh          Office PC installer; served per device as /setup.sh, or run standalone
│   ├── smoke_test.sh        Non-destructive health and security checks for a running gateway
│   ├── transcripts.py       Conversation log: daily JSON Lines files, viewer and fine-tuning export
│   ├── backup.sh            Database snapshot and conversation-log copy (for cron on the server)
│   └── .env.example         Template; copy to gateway/.env and fill in
├── provision/
│   └── provision_pc.py      Writes writeragent.json for a Linux PC
├── docs/                    PC setup options, WriterAgent changes, tested behaviour, API reference
├── samples/                 Test files: a sales register (CSV) and five office .docx documents
├── vendor/
│   └── WriterAgent.oxt      Pinned upstream extension package (unmodified)
└── README.md
```

### Pinned extension package

`vendor/WriterAgent.oxt`

- Release tag 0.8.84 (the package reports 0.8.85)
- SHA-256 `445e495556b1e67e20b5777eb96c7b6e1cb082a5bd871b4f9f6b46f5f99bf73c`
- Tested on LibreOffice 24.8.7 (Ubuntu 24.10), 26.2 (Ubuntu 26.04) and 26.8 (PPA on Ubuntu 24.04 and Linux Mint 22)

The upstream release asset was replaced on 2026-11-01, after its 2026-09-19 release date, so keep
this copy. To upgrade, replace the file, rebuild the gateway, and confirm every patch in
`gateway/patches.py` still applies (the build fails loudly if one does not).
