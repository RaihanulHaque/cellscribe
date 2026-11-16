# CellScribe

AI assistance in LibreOffice **Calc** and **Writer** for an office LAN, in the spirit of
Gemini in Google Sheets and Docs, without giving any employee a copy of the AI provider's API key.

CellScribe is a small self-hosted gateway, plus a patched build of the
[WriterAgent](https://github.com/KeithCu/writeragent) extension that it hands out to office PCs.
Employees ask questions and give instructions in a sidebar; the extension talks only to the
gateway, and only the gateway holds the provider key (OpenRouter and/or Mistral).

- **One key, one place.** Provider keys live in the gateway's environment file, never on a PC.
- **One token per PC.** Each computer gets its own revocable bearer token.
- **Two-minute PC setup.** Install one `.oxt` file and paste a token, or run one generated script.
- **Answers you can trust.** Calc sees the whole sheet with exactly computed totals; Writer sees
  the whole document. Both were tested end to end on realistic office work (see
  [Tested behaviour](#tested-behaviour)).
- **Usage visibility.** The gateway logs who called which model, when, and whether it worked,
  without recording document text.

---

## Contents

- [How it works](#how-it-works)
- [What employees can do](#what-employees-can-do)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Setting up office PCs](#setting-up-office-pcs)
- [Verifying the setup](#verifying-the-setup)
- [Configuration reference](#configuration-reference)
- [Models and providers](#models-and-providers)
- [What CellScribe changes in WriterAgent](#what-cellscribe-changes-in-writeragent)
- [Tested behaviour](#tested-behaviour)
- [Gateway HTTP API](#gateway-http-api)
- [Admin dashboard](#admin-dashboard)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Limitations and roadmap](#limitations-and-roadmap)
- [Repository layout](#repository-layout)
- [Upstream extension notes](#upstream-extension-notes)
- [Licence](#licence)

---

## How it works

```text
   Admin browser                Gateway (this repo)                 AI provider
        │                             │                     (OpenRouter / Mistral)
        │  register a PC              │                                │
        ├────────────────────────────►│                                │
        │  ◄──── device token ────────┤                                │
        │                        ┌────┴─────┐                          │
        │                        │ SQLite   │  devices + request       │
        │                        │          │  metadata only           │
        │                        └────┬─────┘                          │
   Office PC                          │                                │
   LibreOffice + CellScribe.oxt       │                                │
        │  POST /v1/chat/completions  │                                │
        │  Authorization: Bearer cs_… │  picks provider by model id,   │
        ├────────────────────────────►│  adds that provider's key      │
        │                             ├───────────────────────────────►│
        │  ◄────── streamed reply ────┤◄───────────────────────────────┤
```

1. The admin starts the gateway in Docker and registers each office PC in the dashboard, receiving
   a device token that is shown once.
2. The PC installs `CellScribe.oxt` (served by the gateway) and stores its token, either through a
   menu item in LibreOffice or a one-line setup script.
3. When an employee uses the CellScribe sidebar, the extension sends requests to the gateway with
   its device token.
4. The gateway validates the token, checks the model is allowed for that PC, chooses the provider
   from the model id (`vendor/model` → OpenRouter, bare ids → Mistral), attaches that provider's
   key, forwards the request (retrying brief network failures), and streams the answer back.
5. Request metadata is written to SQLite. Prompts and document text are not stored.

WriterAgent reads its settings from `writeragent.json` in the LibreOffice profile and re-reads it
within seconds of a change, so central changes apply without restarting LibreOffice.

### Why not just put the key on each PC?

The key would then live in every employee's home directory in plain text, be copied into backups,
and be impossible to revoke per person. A gateway keeps the key in one place, lets you revoke a
single PC, and lets you change model or provider centrally.

---

## What employees can do

Open the **CellScribe** panel in LibreOffice's right-hand sidebar and type a request.

**In Calc**, for example:

- "Give me a short summary of this sales register for management."
- "What is the total outstanding amount from unpaid invoices, and which customer owes us the most?"
- "Add a VAT column (15% of Amount) and a total row."
- "Sort the data by Amount, highest first." · "Highlight all unpaid invoices in light red."
- "Make the header bold, format Amount with thousands separators, and freeze the header row."
- "Get the Rajshahi region's branches and plot the amounts by category."

**In Writer**, for example:

- "Summarise this report." · "Who is responsible for the hatchery project, and what is the deadline?"
- "Fix all spelling and grammar mistakes." · "Rewrite this paragraph to sound more formal."
- "Turn these notes into meeting minutes with an action items table."
- "Write a formal letter to the supplier based on these points."
- "Translate this report into Bangla." · "Format the section titles as proper headings."

Questions are answered in the sidebar without touching the document. Changes are made with live
formulas (Calc) and the document's own styles (Writer). Progress shows as plain lines such as
"• Writing to O1:R6…" and "• Creating the chart…". Each document keeps its own chat history.

Ready-made test files are in [`samples/`](samples/).

---

## Requirements

| Component | Requirement |
| --- | --- |
| Gateway host | Any Linux machine with Docker and Docker Compose |
| Gateway network | Reachable from office PCs on the LAN; **do not expose it to the internet** |
| Office PCs | Linux or Windows with LibreOffice **24.8 or newer** |
| Office PC tooling | Nothing for the token-entry route; `bash` and `curl` for `setup.sh`; Python 3.8+ only for `provision_pc.py` |
| AI provider | An [OpenRouter](https://openrouter.ai) key with credit, and/or a Mistral API key |

### LibreOffice 24.8 is mandatory

WriterAgent declares `LibreOffice-minimal-version 24.8`. On an older version, for example
**24.2, the default on Ubuntu 24.04 LTS**, installation fails with:

```text
ERROR: Exception occurred: unsatisfied dependencies at ./desktop/source/deployment/registry/package/dp_package.cxx:614
ERROR: unopkg failed.
```

Check each PC with `libreoffice --version`. On Ubuntu 24.04, upgrade through the LibreOffice PPA:

```bash
sudo add-apt-repository ppa:libreoffice/ppa
sudo apt update
sudo apt install libreoffice
```

Do not patch the package to lower the version requirement.

### Conda or pyenv on the office PC

LibreOffice runs Python extensions with its own Python, found through `python3` on `PATH`. An
Anaconda/Miniconda (or pyenv) environment on `PATH` breaks that: `unopkg` dies with
`std::bad_alloc` and the extension never installs, even though LibreOffice itself works.

The generated `setup.sh` handles this automatically. For manual installs, use a clean `PATH`:

```bash
PATH=/usr/bin:/bin unopkg add -f CellScribe.oxt
```

Start LibreOffice from the application menu rather than a conda-activated terminal. If conda is
only used interactively, `conda config --set auto_activate_base false` avoids the problem entirely.

---

## Quick start

### 1. Configure and start the gateway

```bash
cd gateway
cp .env.example .env
```

Edit **`gateway/.env`** (Docker reads only this file; a `.env` in the repository root is ignored)
and set at least:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-...        # and/or MISTRAL_API_KEY=...
CELLSCRIBE_ADMIN_TOKEN=a-long-random-string
```

Generate the admin token with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

```bash
docker compose up -d --build
docker compose ps          # expect "healthy"
```

After editing `.env` later, apply it with `docker compose up -d --force-recreate`.

### 2. Find the gateway address

```bash
hostname -I
```

Use the address on the office LAN (for example `192.168.1.20`). From another PC:

```bash
curl http://<gateway-ip>:8080/healthz     # {"ok":true}
```

If that times out, open the firewall: `sudo ufw allow 8080/tcp`. Give the gateway a static DHCP
lease or a DNS name so its address does not change.

### 3. Register a PC

Open `http://<gateway-ip>:8080/`, paste the admin token, enter a PC name, and click
**Create token**. Leave all models ticked unless the PC should be restricted. The token is shown
**once**; only its hash is stored.

### 4. Set up the PC

See [Setting up office PCs](#setting-up-office-pcs). The quickest route: install `CellScribe.oxt`,
restart LibreOffice, and paste the token under **Tools → CellScribe: Enter access token…**.

---

## Setting up office PCs

Close LibreOffice on the PC first. Every route ends in the same state: the extension installed and
`writeragent.json` pointing at the gateway with that PC's token.

### Option A: install one file and paste a token (no command line)

The gateway serves one extension file, identical for every PC and containing no secret:
`http://<gateway-ip>:8080/setup/CellScribe.oxt`. The dashboard links it under each new token.

1. Double-click `CellScribe.oxt` (or **Tools → Extension Manager → Add**) and restart LibreOffice.
2. Open **Tools → CellScribe: Enter access token…** and paste the PC's token.

The extension checks the token with the gateway (a wrong or revoked token is rejected with a
message), picks the default model from those the PC may use, and writes `writeragent.json`.

For scripted rollouts the same action accepts the token directly
(`org.cellscribe.config:SetToken` with a `Token` property), skipping the dialog.

### Option B: one command (Linux)

The dashboard shows this command next to each new token:

```bash
curl -fsSL -H "Authorization: Bearer cs_..." http://<gateway-ip>:8080/setup.sh | bash
```

The script is generated for that device. It downloads `CellScribe.oxt`, installs it with `unopkg`
(clearing conda from `PATH` and a stale lock if needed), and writes `writeragent.json`, merging with
`jq` when available and otherwise backing up and replacing the file. It writes the gateway's
default model; add `?model=<id>` to the URL to choose another. Set `CELLSCRIBE_PUBLIC_URL` if PCs
reach the gateway under a different address than the one in the request.

### Option C: `provision_pc.py`

```text
python3 provision/provision_pc.py --gateway URL --token TOKEN [options]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--gateway` | *required* | Gateway base URL. A trailing `/v1` is stripped automatically. |
| `--token` | *required* | Device token from the dashboard. |
| `--model` | `deepseek/deepseek-v4.1-flash-1017` | Model written into the extension config. |
| `--user` | current user | Linux user whose LibreOffice profile to configure. |
| `--profile` | auto-detected | Override the profile path, e.g. for Flatpak installs. |
| `--install-extension` | off | Also run `unopkg add vendor/WriterAgent.oxt`. This is the **unpatched** package; prefer installing the gateway's `CellScribe.oxt`. |
| `--dry-run` | off | Print the resulting config without writing anything. |

The script finds the newest profile under `~/.config/libreoffice/*/user`, preserves unrelated
settings in an existing `writeragent.json`, sets `endpoint`, `text_model`, `model` and the token,
backs up the old file to `writeragent.json.bak.<timestamp>`, and writes it with `0600` permissions.

### Rolling out to many PCs

Tokens are per PC, so register the PCs (dashboard or admin API), collect the tokens, and have your
configuration-management tool or login script run Option B or C with the matching token.
`unopkg add` installs into the **current user's** profile; on shared PCs, set up each account.

### Flatpak LibreOffice

```bash
flatpak run --command=unopkg org.libreoffice.LibreOffice add CellScribe.oxt

python3 provision/provision_pc.py \
  --gateway http://<gateway-ip>:8080 \
  --token cs_... \
  --profile ~/.var/app/org.libreoffice.LibreOffice/config/libreoffice/4/user
```

### Updating PCs

When the gateway is rebuilt with a new `CellScribe.oxt`, reinstall it on each PC with LibreOffice
closed (`unopkg add -f CellScribe.oxt`, or Option A again). The token and settings are kept.

---

## Verifying the setup

From the gateway host:

```bash
curl -s http://<gateway-ip>:8080/healthz                                          # {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' http://<gateway-ip>:8080/admin/devices   # 401 without a token
```

On an office PC, open a file from [`samples/`](samples/) (for example `sales_register.csv` in Calc),
open the **CellScribe** panel and ask "Give me a short summary of this sheet". The request should
appear in the dashboard's request log within seconds. If it does not, the PC is not using the
gateway; see [Troubleshooting](#troubleshooting).

---

## Configuration reference

All settings are environment variables in `gateway/.env`.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | one of the two | — | Serves model ids with a vendor prefix, e.g. `deepseek/deepseek-v4.1-flash-1017`. |
| `MISTRAL_API_KEY` | one of the two | — | Serves bare Mistral ids, e.g. `mistral-large-latest`. |
| `CELLSCRIBE_ADMIN_TOKEN` | yes | — | Protects `/admin/*` and the dashboard's data calls. |
| `CELLSCRIBE_DEFAULT_MODEL` | no | `deepseek/deepseek-v4.1-flash-1017` | Model used when a request names none, and the model new PCs are set to. |
| `CELLSCRIBE_ALLOWED_MODELS` | no | default model | Comma-separated allow-list offered to PCs. Models whose provider has no key are dropped at startup. |
| `OPENROUTER_BASE_URL` | no | `https://openrouter.ai/api/v1` | OpenRouter base URL. |
| `MISTRAL_BASE_URL` | no | `https://api.mistral.ai/v1` | Base URL for bare model ids; point it at another OpenAI-compatible server if needed. |
| `CELLSCRIBE_RETRIES` | no | `2` | Retries for transient upstream failures (dropped connection, 429/502/503/504) before an error reaches the PC. |
| `CELLSCRIBE_TIMEOUT` | no | `300` | Upstream request timeout in seconds. |
| `CELLSCRIBE_PUBLIC_URL` | no | from request | Gateway URL embedded in `setup.sh` and `CellScribe.oxt`. |
| `CELLSCRIBE_DB` | no | `/data/cellscribe.db` | SQLite path inside the container. |
| `CELLSCRIBE_OXT` | no | `/app/vendor/WriterAgent.oxt` | Base extension package the gateway patches into `CellScribe.oxt`. |

Data persists in the compose volume (it appears as `gateway_cellscribe-data`), so restarts keep
devices and logs. `docker compose down -v` deletes everything, including all tokens.

---

## Models and providers

Routing is by model id: anything containing `/` goes to OpenRouter, anything else to Mistral.
OpenRouter is asked to use only hosts that support every request parameter, tools included.
Provider error messages (expired key, no credit, unknown model) are passed through to the PC and
shown in the dashboard log.

| Model | Use | Notes |
| --- | --- | --- |
| `deepseek/deepseek-v4.1-flash-1017` (OpenRouter) | **Default.** Questions and edits in Calc and Writer | All tests below. About $0.01 per task. |
| `mistral-large-latest` | Questions and edits | Good results; costs more. |
| `mistral-medium-latest` | Questions; simple edits | Sometimes skips small details such as headers. |
| `mistral-small-latest` | Questions only | Accurate answers, but edits land off by one row and it often replies instead of editing. |

A PC registered with every model ticked stores "all" and follows later changes to
`CELLSCRIBE_ALLOWED_MODELS`. To change an existing PC, tick the models in the dashboard and click
**Apply ticked models** on its row (or `POST /admin/devices/{id}/models` with `{"models": []}` for
all). The sidebar's model picker is hidden, so employees cannot switch to a weaker model by
accident.

---

## What CellScribe changes in WriterAgent

`vendor/WriterAgent.oxt` is kept byte-for-byte as pinned. When a PC downloads `CellScribe.oxt`, the
gateway builds it from that package: it adds the token-entry menu item (`gateway/packager.py`),
removes WriterAgent's update feed so the Extension Manager cannot swap the build for upstream, and
applies the exact search-and-replace patches in `gateway/patches.py`. Each patch must match exactly
once, so a WriterAgent upgrade that changes the code underneath fails the build instead of shipping
a half-patched extension. The extension keeps WriterAgent's identifier, so it installs over an
existing WriterAgent.

| Area | WriterAgent default | CellScribe | Why |
| --- | --- | --- | --- |
| Calc: what the model sees | sheet size and row 1 only | the sheet's values as CSV, a column map (letter → header), an exact per-column profile (sums, min/max, counts, date ranges), group totals by category and month, and other sheets when they fit; total rows are excluded from figures | The model never saw the data, so summaries were invented or built from 80-cell reads. Models also miscount wide rows and mis-add long columns. |
| Calc prompt | read with tools, compute with `=PY`, "colorful spreadsheets" | answer questions from the data in one reply; write whole tables in one call with ordinary formulas; requested work before styling | `=PY` needs Python packages office PCs lack; decoration used up to 9 steps per task and starved the request. |
| `sort_range` | silently did nothing | sorts | pyuno passed the sort fields as an untyped sequence, which Calc ignores. |
| `set_style` | no number formats, no freeze panes | `number_format` (e.g. `#,##0`) and `freeze_at` (e.g. `A2`) | Routine office formatting the model could not do at all. |
| Calc tool arguments | a list sent as a JSON string, or one value per listed cell, was rejected | accepted | Each rejection cost a full step. |
| `read_cell_range` limit | 80 cells | 400 cells | Fewer steps when the model does read. |
| Writer: what the model sees | text read by cursor steps, which undercount tables | full text whenever the cursor read comes up short (documents without tracked changes) | Everything after a table was silently missing. Models then "repaired" correct meeting minutes by deleting sections they could not see (0/4 → 4/4 after the fix). |
| Writer prompt | "thoughtful use of color", no notion of a step budget | requested work first; one write per new part; whole-document translations and rewrites in one write; named styles only; no placeholders; check every fact is kept | Minutes took 20 steps and 5 minutes of colours and custom styles; translation went paragraph by paragraph and would stop half-way on long documents. |
| Writer full rewrites | no check | the tool reports passages a rewrite dropped (fuzzy match; translations and restructures are not flagged) | A safety net so a rewrite cannot silently lose content. |
| Context size | 8,000 characters | 60,000 characters (~15k tokens) | Lets the model see a whole working document. |
| Steps per request | 15 | 20 | The sidebar runs one tool per model call; a table plus a chart needs about 10. |
| Memory | one global `USER.md` added to every prompt | off | A preference saved in one document leaked into unrelated ones. Chat history stays per document. |
| Sidebar | raw `[Running tool: …]` lines, model reasoning, ~24 controls, "WriterAgent" | plain progress lines, no reasoning text, Ask/Send/Stop/Clear plus Settings and menu, CellScribe branding and greeting | Office users saw developer output. Model, mode and image pickers, Python, LaTeX, search and voice are hidden, not removed. |

---

## Tested behaviour

Tested on 16 November 2026 with `deepseek/deepseek-v4.1-flash-1017` through the gateway. Each task
ran through the real WriterAgent agent (same prompt, tools and one-tool-per-call loop as the
sidebar) in LibreOffice 26.2, on a fresh copy of the file, and the resulting document was checked
automatically against figures computed from the source data. Several tasks were also run by hand in
the sidebar.

**Calc** ([`samples/sales_register.csv`](samples/sales_register.csv): 150 invoices, Jan–Mar 2026),
12 of 12 correct (10 by the automatic checks; the other two were confirmed by hand, see below):

| Task | Steps | Time |
| --- | --- | --- |
| Management summary; outstanding dues; filtered count; revenue per month (questions) | 1 each | 2–9 s |
| VAT and "Total incl. VAT" columns for 150 rows | 6 | 15 s |
| Totals row | 2 | 5 s |
| New "Region Summary" sheet with live formulas | 4 | 13 s |
| Sort by amount, rows kept intact | 2 | 10 s |
| Highlight the 26 unpaid invoices | 2 | 30 s |
| Bold header, `#,##0` amounts, frozen header row | 4 | 17 s |
| Bar chart of revenue by region | 3 | 17 s |
| A region's branches and amounts by category, with chart | 3 | 24 s |

The two hand-checked results: the management summary gave unpaid invoices as an amount
(৳2,383,004) where the check looked for the count, and the region breakdown built branch totals
plus a category table where the check expected a branch × category grid. All figures were exact.

**Writer** ([`samples/`](samples/) `.docx` files), 12 of 12 correct:

| Task | Steps | Time |
| --- | --- | --- |
| Summarise a report; answer a question about it | 1 | 2–4 s |
| Notes → meeting minutes with action table | 2 | 7 s |
| Letter from bullet points; formal rewrite of one paragraph | 2 | 4–13 s |
| Proofread a memo (9 planted errors) | 7 | 30 s |
| Translate a memo / a 2-page report with a table into Bangla | 3 | 10–21 s |
| Add a risk table in the right section; apply heading styles | 6–8 | 14–32 s |
| Find and replace across the document | 3 | 8 s |
| Add an executive summary | 14 | 45 s |

Typical cost: under one US cent per task (all 24 tasks together: about $0.20). Questions are answered without changing the document.

---

## Gateway HTTP API

Device endpoints require `Authorization: Bearer <device-token>`; admin endpoints require
`X-Admin-Token: <admin-token>`.

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | Liveness probe. |
| `GET` | `/` | none | Admin dashboard HTML (its data calls need the admin token). |
| `GET` | `/setup/CellScribe.oxt` | none | The patched extension with the gateway URL embedded; contains no secret. |
| `GET` | `/setup.sh` | device | Generated installer and config script for that device. |
| `GET` | `/v1/models` | device | Models this device may use, OpenAI-shaped. |
| `POST` | `/v1/chat/completions` | device | Proxied to OpenRouter or Mistral by model id; supports `stream: true`. |
| `GET` | `/admin/devices` | admin | List devices (no token hashes). |
| `POST` | `/admin/devices` | admin | Create a device; returns its token once. |
| `POST` | `/admin/devices/{id}/models` | admin | Replace a device's model allow-list; `[]` means all models. |
| `POST` | `/admin/devices/{id}/revoke` | admin | Revoke immediately. |
| `GET` | `/admin/logs?limit=100` | admin | Recent request metadata. |
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

---

## Admin dashboard

`http://<gateway-ip>:8080/` provides:

- **Register a PC**: name plus model checkboxes; the token and setup instructions are shown once.
- **Devices**: status, allowed models, last-seen time, **Apply ticked models** and **Revoke**.
- **Recent requests**: time, device, model, HTTP status, latency, token counts and the provider's
  error message, if any.

The admin token is kept in the browser's `localStorage`, so use a trusted machine.

---

## Security notes

**Treat the gateway as a credential service.**

- It holds the provider keys and every device token. Keep it on the LAN and never publish port
  8080 to the internet.
- Device tokens are bearer tokens. Over plain HTTP, anyone on the LAN can capture one. Put the
  gateway behind a TLS reverse proxy (Caddy or nginx) or a VPN before real use, then point PCs at
  `https://…`.
- **Do not run the gateway on port 11434 or 1234.** WriterAgent treats those ports as Ollama/LM
  Studio and skips the authorization header. Port 8080 is safe.
- Revocation takes effect on the device's next request. Treat a token pasted into a chat or ticket
  as leaked.
- Rotate provider keys by editing `gateway/.env` and running `docker compose up -d --force-recreate`.
- Request logging is metadata only; prompts and document text are not stored.
- **Document content still goes to the model provider** (OpenRouter and the host it routes to, or
  Mistral). The gateway protects the key, not the content. Confirm company policy allows this. To
  keep content on the LAN, point `MISTRAL_BASE_URL` at a locally hosted OpenAI-compatible model.
- WriterAgent's MCP server and public tunnel are off by default; leave them off. The MCP server has
  no authentication.

The container publishes `0.0.0.0:8080`. For tighter control, bind one interface
(`"192.168.1.20:8080:8080"` in `docker-compose.yml`) or allow only the office subnet in the
firewall.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `unsatisfied dependencies … dp_package.cxx:614` on install | LibreOffice older than 24.8. Upgrade (see [Requirements](#requirements)). |
| `std::bad_alloc` on install | A conda or pyenv Python on `PATH`. Use `setup.sh`, or `PATH=/usr/bin:/bin unopkg add -f CellScribe.oxt`. |
| `unopkg add` fails while LibreOffice is open | Quit LibreOffice fully (`pkill -f soffice.bin`) and retry. |
| Extension installed, but the sidebar does nothing | LibreOffice was started from a conda-activated terminal. Start it from the application menu. |
| No requests from a PC in the dashboard log | The PC is not using the gateway: its `endpoint` in `writeragent.json` points straight at a provider. Run **Tools → CellScribe: Enter access token…** again. |
| "Test connection" succeeds but chatting fails with `API key expired` | Same cause: the PC talks to OpenRouter directly with an old key. OpenRouter lists models without checking the key, so the test passes. Re-enter the token as above. |
| Every request fails with `Upstream error (401): API key expired` | The gateway's provider key expired. Put a new key in `gateway/.env` and run `docker compose up -d --force-recreate`. |
| `Upstream error (402)` or a credit message | The OpenRouter account is out of credit or over its key limit. Top up or raise the limit. |
| `HTTP 401 … Unknown or revoked device token` | Wrong or revoked token. Issue a new one and re-enter it. |
| `HTTP 403 … not permitted for this device` | The model is not in that PC's allow-list. Use **Apply ticked models** in the dashboard. |
| The CellScribe panel opens very narrow | Drag its left edge to widen it; LibreOffice remembers the width. |
| Writer replies in the sidebar but the document is unchanged | The model answered instead of editing. Use the default model or `mistral-large-latest`, not `mistral-small-latest`. |
| A file opens with a "locked" warning | LibreOffice was killed while the file was open and left `.~lock.<name>#` next to it. With LibreOffice closed, delete that file. |
| Worked yesterday, now connection refused | The gateway's LAN address changed. Give it a static lease or a DNS name. |
| Container exits at startup | No provider key, no servable model in `CELLSCRIBE_ALLOWED_MODELS`, or `CELLSCRIBE_ADMIN_TOKEN` missing. Check `docker compose logs`. |

Useful commands:

```bash
docker compose logs -f --tail=50        # gateway output
docker compose ps                       # health and ports
curl -s http://127.0.0.1:8080/healthz   # from the gateway host
```

WriterAgent's own log on a PC is `writeragent_debug.log` in the profile's `user/config` folder.

---

## Limitations and roadmap

- **Chat only.** Only `/v1/chat/completions` and `/v1/models` are proxied; WriterAgent features that
  need embeddings, images or audio fail.
- **No rate limiting or quotas.** Every valid token can spend the whole key's budget; use a spending
  limit on the provider key.
- **Plain HTTP by default.** TLS needs the reverse proxy described above.
- **Long and tracked-change documents.** Writer documents over ~60,000 characters fall back to
  WriterAgent's head-and-tail excerpt, and documents with tracked changes keep WriterAgent's original
  reading; neither case was part of the tests.
- **Mistral's newer models** return replies as "thinking + text" blocks. The tests used DeepSeek; how
  the sidebar displays Mistral's block replies has not been checked.
- **Slow cases.** Inserting new text directly before a heading copies the heading's look, and the
  model then fixes the paragraphs one by one (the 45 s executive summary above). Results are correct.
- **No device agent.** Setup is a script or a menu item; nothing enrolls PCs, pushes updates or
  ships logs automatically.

Planned next: a small agent that enrolls machines and ships `writeragent_debug.log`, and offering the
WriterAgent fixes (sort, Writer snapshot) upstream.

---

## Repository layout

```text
.
├── gateway/
│   ├── app.py               FastAPI proxy: auth, provider routing, retries, admin routes
│   ├── patches.py           Exact patches applied to WriterAgent when building CellScribe.oxt
│   ├── packager.py          Builds CellScribe.oxt (token-entry menu item + patches)
│   ├── store.py             SQLite device registry and request log
│   ├── admin.html           Single-page admin dashboard
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── .env.example         Template; copy to gateway/.env and fill in
├── provision/
│   └── provision_pc.py      Writes writeragent.json for a Linux PC
├── samples/                 Test files: a sales register (CSV) and five office .docx documents
├── vendor/
│   └── WriterAgent.oxt      Pinned upstream extension package (unmodified)
└── README.md
```

### Pinned extension package

`vendor/WriterAgent.oxt`

- Release tag 0.8.84 (the package reports 0.8.85)
- SHA-256 `445e495556b1e67e20b5777eb96c7b6e1cb082a5bd871b4f9f6b46f5f99bf73c`
- Tested on LibreOffice 24.8.7 (Ubuntu 24.10) and LibreOffice 26.2

The upstream release asset was replaced on 2026-11-01, after its 2026-09-19 release date, so keep
this copy. To upgrade, replace the file, rebuild the gateway, and confirm every patch in
`gateway/patches.py` still applies (the build fails loudly if one does not).

---

## Upstream extension notes

CellScribe builds on [WriterAgent](https://github.com/KeithCu/writeragent) rather than its own
extension.

- Licence: GPL-3.0-or-later. Distributing `CellScribe.oxt` outside your organisation means
  distributing a modified WriterAgent, so its source (this repository) must be available too.
- The patches are deliberately small and isolated in `gateway/patches.py`. The bug fixes (sort, the
  Writer snapshot after tables) are good candidates to contribute upstream.
- **Why not LibreThinker:** it supports Writer only, and requests made with your own API key send the
  key and document text through `api.librethinker.com`.

Open items before wider rollout:

- A security review before use with confidential documents. So far: a keyword scan of the package
  and a startup check with no key set, which showed no unexpected remote connections.
- Confirm company policy allows sending document content to the chosen provider.
- The extension can download extra tools such as the Harper grammar checker from GitHub; not tested.

---

## Licence

GPL-3.0-or-later. The full text is in [LICENSE](LICENSE).

The bundled WriterAgent extension is also GPL-3.0-or-later, and CellScribe's own files carry
matching `SPDX-License-Identifier: GPL-3.0-or-later` headers. Running it inside your organisation
does not trigger the source-sharing rules; distributing it outside does.
