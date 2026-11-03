# CellScribe

Bring large language models into LibreOffice Writer and Calc for an office LAN — without giving
every employee a copy of the provider's API key.

CellScribe is a small, self-hosted gateway plus a provisioning script. Employees use the
[WriterAgent](https://github.com/KeithCu/writeragent) extension as normal; the extension talks to a
gateway on your network, and only the gateway knows the Mistral key.

- **One key, one place.** The provider key lives in the gateway's environment file.
- **One token per PC.** Each computer gets its own revocable bearer token.
- **No manual setup per employee.** A script writes the extension's config file.
- **Usage visibility.** The gateway logs who called which model, when, and whether it worked —
  without recording document text.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Provisioning an office PC](#provisioning-an-office-pc)
- [Verifying the setup](#verifying-the-setup)
- [Configuration reference](#configuration-reference)
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
   Admin browser                Gateway (this repo)              Mistral
        │                             │                             │
        │  register a PC              │                             │
        ├────────────────────────────►│                             │
        │  ◄──── device token ────────┤                             │
        │                             │                             │
        │                        ┌────┴─────┐                       │
        │                        │ SQLite   │  devices + request    │
        │                        │          │  metadata only        │
        │                        └────┬─────┘                       │
        │                             │                             │
   Office PC                          │                             │
   LibreOffice + WriterAgent          │                             │
        │  POST /v1/chat/completions  │                             │
        │  Authorization: Bearer cs_… │                             │
        ├────────────────────────────►│  adds MISTRAL_API_KEY       │
        │                             ├────────────────────────────►│
        │  ◄────── streamed reply ────┤◄────────────────────────────┤
```

1. The admin starts the gateway in Docker and registers each office PC, receiving a device token
   shown once.
2. `provision/provision_pc.py` writes that token, the gateway URL, and the model choice into
   WriterAgent's own config file (`writeragent.json`) in the LibreOffice user profile.
3. When an employee uses WriterAgent, it sends requests to the gateway with its device token.
4. The gateway validates the token, checks the model is allowed for that device, attaches the real
   Mistral key, forwards the request, and streams the answer back.
5. Metadata about the request is written to SQLite. Prompt and document text are not stored.

WriterAgent needs no patching. Its config file already supports a custom endpoint and per-endpoint
keys, and it re-reads `writeragent.json` within a few seconds of a change — so central changes apply
without restarting LibreOffice.

### Why not just put the key on each PC?

Because the key would then live in every employee's home directory in plain text, be copied into
backups, and be impossible to revoke per person. A gateway makes the key revocable in one place and
lets you change model or provider centrally.

---

## Requirements

| Component | Requirement |
| --- | --- |
| Gateway host | Any Linux machine with Docker and Docker Compose |
| Gateway network | Reachable from office PCs on the LAN; **do not expose to the internet** |
| Office PCs | Linux with LibreOffice **24.8 or newer** |
| Extension | `vendor/WriterAgent.oxt` (included) |
| Office PC tooling | `bash` and `curl` (Python 3.8+ only if you use `provision_pc.py`) |
| Mistral | An API key with access to the models you intend to allow |

### LibreOffice 24.8 is mandatory

WriterAgent declares `LibreOffice-minimal-version 24.8` in its package metadata. If a PC has an older
version — for example **24.2, which is the default on Ubuntu 24.04 LTS** — installation fails with:

```text
ERROR: Exception occurred: unsatisfied dependencies at ./desktop/source/deployment/registry/package/dp_package.cxx:614
ERROR: unopkg failed.
```

Check each PC before rolling out:

```bash
libreoffice --version
```

Upgrade on Ubuntu 24.04 via the LibreOffice PPA:

```bash
sudo add-apt-repository ppa:libreoffice/ppa
sudo apt update
sudo apt install libreoffice
libreoffice --version
```

Do not patch the `.oxt` to lower the version requirement. The checksum below is pinned deliberately,
and the project's policy is to contribute upstream rather than fork. (The gateway's `CellScribe.oxt`
only *adds* files to the pinned package; it changes none of WriterAgent's code.)

---

## Quick start

### 1. Start the gateway

```bash
cd gateway
cp .env.example .env
```

Edit `gateway/.env` and set at least:

```dotenv
MISTRAL_API_KEY=your-mistral-key
CELLSCRIBE_ADMIN_TOKEN=a-long-random-string
```

Generate the admin token with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then start it:

```bash
docker compose up -d --build
docker compose ps          # expect "healthy"
```

### 2. Find the gateway address

```bash
hostname -I
```

Use the address on the office LAN (for example `192.168.1.20` or `10.0.0.5`). Confirm it works from
another PC:

```bash
curl http://<gateway-ip>:8080/healthz     # {"ok":true}
```

If that times out, open the firewall:

```bash
sudo ufw allow 8080/tcp
```

### 3. Open the dashboard

```text
http://<gateway-ip>:8080/
```

Paste the admin token. Register a PC, choose which models it may use, and copy the device token that
appears — it is shown **once** and only its hash is stored.

### 4. Set up that PC (no Python needed)

The dashboard shows a one-line command next to each new token. On the office PC, with LibreOffice
closed, paste it:

```bash
curl -fsSL -H "Authorization: Bearer cs_..." http://<gateway-ip>:8080/setup.sh | bash
```

The gateway generates a script for that device only. It downloads the extension from the gateway,
installs it with `unopkg`, and writes `writeragent.json` (merging with `jq` if present, otherwise
backing up and replacing the file). The PC needs only `bash`, `curl`, and LibreOffice 24.8+. Add
`?model=mistral-medium-latest` to the URL to pick a different model. Set `CELLSCRIBE_PUBLIC_URL` if
the gateway is reached under a different address than the one in the request.

#### No command at all: one extension + paste a token

For staff who should only double-click, the gateway serves a single file, identical for every PC and
containing no secret (no login needed): `http://<gateway-ip>:8080/setup/CellScribe.oxt`.

It is the bundled WriterAgent with a **CellScribe: Enter access token...** menu item added, and the
gateway URL embedded. The employee:

1. Double-clicks `CellScribe.oxt` (or **Tools > Extension Manager > Add**) and restarts LibreOffice.
2. Opens **Tools > CellScribe: Enter access token...** and pastes the token the admin gave them.

The extension checks the token against the gateway (a wrong or revoked token is rejected with a
message), picks the default model from what the device is allowed to use, and writes
`writeragent.json`. Works on Windows and Linux. The dashboard links the file under each new token.

How it is built: the gateway takes `vendor/WriterAgent.oxt` at request time and adds three files under
`cellscribe/` plus manifest entries. **WriterAgent's own code is not modified**, but its update feed is
removed from the package so the Extension Manager cannot replace this build with the upstream one; to
upgrade, replace `vendor/WriterAgent.oxt` and restart the gateway. It keeps WriterAgent's extension
identifier, so installing it over an existing WriterAgent upgrades in place.

The Mistral key is never on the PC; the token is the only secret. If a token leaks, revoke that
device and issue a new one. Share one token per PC, not one for everybody, to keep per-PC revocation
and the usage log meaningful. For scripted rollouts, the same action accepts a token argument
(`org.cellscribe.config:SetToken` with a `Token` property), skipping the dialog.

Prefer the Python script? `provision/provision_pc.py` still works and is documented below.

Close and reopen LibreOffice. Test with `=PROMPT("hello")` in a Calc cell. The request should appear
in the dashboard's request log.

---

## Provisioning an office PC

```text
python3 provision/provision_pc.py --gateway URL --token TOKEN [options]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--gateway` | *required* | Gateway base URL. A trailing `/v1` is stripped automatically. |
| `--token` | *required* | Device token from the dashboard. |
| `--model` | `mistral-small-latest` | Model written into the extension config. |
| `--user` | current user | Linux user whose LibreOffice profile to configure. |
| `--profile` | auto-detected | Override the profile path, e.g. for Flatpak installs. |
| `--install-extension` | off | Also run `unopkg add vendor/WriterAgent.oxt`. |
| `--dry-run` | off | Print the resulting config without writing anything. |

What the script does:

- Finds the newest LibreOffice profile under `~/.config/libreoffice/*/user`.
- Reads any existing `writeragent.json` and **preserves unrelated settings** such as temperature.
- Sets `endpoint`, `text_model`, `model`, and the token in `api_keys_by_endpoint`.
- Backs up the old file to `writeragent.json.bak.<timestamp>`.
- Writes the file with `0600` permissions.

The Mistral key is never written to the PC. Only the device token is.

### Rolling out to many PCs

`provision_pc.py` is designed to be called by automation rather than typed by hand. Because device
tokens are per-PC, each machine needs its own token — so the usual pattern is:

1. Register the PCs in the dashboard (or via the admin API) and collect the tokens.
2. Have your configuration-management tool or login script call the provisioning script with the
   matching token for that machine.

`unopkg add` installs into the **current user's** profile. If several people share a PC, run the
script once per user account.

### Flatpak LibreOffice

The profile lives elsewhere, and `unopkg` must be invoked through Flatpak:

```bash
flatpak run --command=unopkg org.libreoffice.LibreOffice add vendor/WriterAgent.oxt

python3 provision/provision_pc.py \
  --gateway http://<gateway-ip>:8080 \
  --token cs_... \
  --profile ~/.var/app/org.libreoffice.LibreOffice/config/libreoffice/4/user
```

---

## Verifying the setup

From the gateway host:

```bash
curl -s http://<gateway-ip>:8080/healthz                       # {"ok":true}
curl -s -o /dev/null -w '%{http_code}\n' http://<gateway-ip>:8080/admin/devices   # 401 without a token
```

From an office PC, in Calc: enter `=PROMPT("say hello")` in a cell. A reply means the whole chain —
extension, token, gateway, Mistral — works. The request appears in the dashboard log within seconds.

---

## Configuration reference

All settings are environment variables in `gateway/.env`.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `MISTRAL_API_KEY` | yes | — | Provider key. Gateway refuses to start without it. |
| `CELLSCRIBE_ADMIN_TOKEN` | yes | — | Protects `/admin/*` and the dashboard's data calls. |
| `MISTRAL_BASE_URL` | no | `https://api.mistral.ai/v1` | Upstream base URL. Point elsewhere to use another OpenAI-compatible provider. |
| `CELLSCRIBE_DEFAULT_MODEL` | no | `mistral-small-latest` | Model used when a request omits one. |
| `CELLSCRIBE_ALLOWED_MODELS` | no | default model | Comma-separated allow-list offered to devices. |
| `CELLSCRIBE_DB` | no | `/data/cellscribe.db` | SQLite path inside the container. |
| `CELLSCRIBE_TIMEOUT` | no | `300` | Upstream request timeout in seconds. |
| `CELLSCRIBE_PUBLIC_URL` | no | from request | Gateway URL embedded in generated setup scripts. |

Data persists in the compose volume defined in `docker-compose.yml` (Docker prefixes it with the
project name, so it appears as `gateway_cellscribe-data`), so restarts keep device registrations and
logs. To reset everything, `docker compose down -v` (this deletes all tokens).

---

## Gateway HTTP API

Device endpoints require `Authorization: Bearer <device-token>`.
Admin endpoints require `X-Admin-Token: <admin-token>`.

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | Liveness probe. |
| `GET` | `/` | none | Admin dashboard HTML. |
| `GET` | `/setup.sh` | device | Generated installer + config script for that device. |
| `GET` | `/setup/CellScribe.oxt` | none | WriterAgent plus token entry, gateway URL embedded. |
| `GET` | `/v1/models` | device | Models this device may use, OpenAI-shaped. |
| `POST` | `/v1/chat/completions` | device | Proxy to Mistral; supports `stream: true`. |
| `GET` | `/admin/devices` | admin | List devices (no token hashes). |
| `POST` | `/admin/devices` | admin | Create a device; returns its token once. |
| `POST` | `/admin/devices/{id}/revoke` | admin | Revoke immediately. |
| `GET` | `/admin/logs?limit=100` | admin | Recent request metadata. |
| `GET` | `/admin/models` | admin | Configured allow-list. |

Create a device without the dashboard:

```bash
curl -s -X POST http://<gateway-ip>:8080/admin/devices \
  -H "X-Admin-Token: $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"acc-pc-01","models":["mistral-small-latest"]}'
```

The response contains the plaintext `token`. Store it in your management tool; the gateway only keeps
its SHA-256 hash and cannot show it again.

Errors are returned in OpenAI's shape so LibreOffice surfaces a readable message:

```json
{"error": {"message": "Model 'mistral-large-latest' is not permitted for this device", "type": "gateway_error"}}
```

---

## Admin dashboard

`http://<gateway-ip>:8080/` provides:

- **Register a PC** — name plus model checkboxes; token shown once.
- **Devices** — status, allowed models, last-seen time, and a revoke button.
- **Recent requests** — timestamp, device, model, HTTP status, latency, token counts, error summary.

The admin token is kept in the browser's `localStorage`, so use a trusted machine. Any browser holding
that token can create and revoke devices.

---

## Security notes

**Treat this as a credential service.**

- The gateway holds the provider key and every device token. Keep it on the LAN. Never publish port
  8080 to the internet.
- Device tokens are bearer tokens. Over plain HTTP, anyone on the LAN can capture one. Put the gateway
  behind a TLS reverse proxy (Caddy or nginx) or a VPN before real use, then provision with
  `https://…`.
- **Do not run the gateway on port 11434 or 1234.** WriterAgent treats those ports as Ollama/LM Studio
  and skips the authorization header. Port 8080 is safe.
- Revocation takes effect on the device's next request. Revoke immediately if a token leaks, and treat
  a token pasted into a chat or ticket as leaked.
- Rotate the Mistral key by editing `gateway/.env` and running `docker compose up -d`.
- Request logging is metadata only. Prompts and document text are deliberately not stored. Adding
  content logging would be a separate policy decision with its own access controls and retention
  limits.
- **Document content still goes to Mistral.** The gateway protects the key, not the content. Confirm
  company policy allows this before use. A locally hosted model is the only way to keep content on the
  LAN — point `MISTRAL_BASE_URL` at an OpenAI-compatible local server if you go that way.

### Network exposure

The container publishes `0.0.0.0:8080`, so anything that can route to the host can reach it. If you
need tighter control, bind to one interface (`"172.16.9.119:8080:8080"` in `docker-compose.yml`) or
allow only the office subnet in the firewall.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `unsatisfied dependencies … dp_package.cxx:614` on install | LibreOffice older than 24.8. Run `libreoffice --version` and upgrade. |
| `unopkg add failed. Close LibreOffice and retry.` | LibreOffice is running. Quit it fully (`soffice` process gone) and retry. |
| `curl` to `/healthz` times out from another PC | Firewall, or wrong IP/interface. `sudo ufw allow 8080/tcp`; re-check `hostname -I`. |
| Dashboard loads but every action says "Admin token rejected" | Wrong `CELLSCRIBE_ADMIN_TOKEN`, or it was changed after the browser saved the old one. Clear site data and re-enter. |
| `HTTP 401` from LibreOffice | Device token wrong or revoked. Register a new device and re-provision. |
| `HTTP 403 … not permitted for this device` | Model not in that device's allow-list. Update the device in the dashboard. |
| Everything worked yesterday, now connection refused | The gateway's LAN IP changed via DHCP. Set a static lease or use a DNS name. |
| Extension installed but still asks for a key | Config not written to the right profile. Re-run provisioning without `--install-extension` and check the printed path. |
| Requests succeed but nothing in the log | The extension may be pointing at Mistral directly. Check `endpoint` in `writeragent.json`. |
| Container exits at startup | `MISTRAL_API_KEY` or `CELLSCRIBE_ADMIN_TOKEN` missing. Check `docker compose logs`. |

Useful commands:

```bash
docker compose logs -f --tail=50        # gateway output
docker compose ps                       # health and ports
curl -s http://127.0.0.1:8080/healthz   # from the gateway host
```

---

## Limitations and roadmap

Current limitations, stated plainly:

- **Chat only.** Only `/v1/chat/completions` and `/v1/models` are proxied. Embeddings, images, audio,
  and speech would reach the gateway and fail.
- **No automated agent yet.** Provisioning is a script you invoke. There is no daemon that enrolls a
  PC, pushes config changes, or ships logs on its own.
- **No rate limiting or quotas.** Every device with a valid token can spend the whole key's budget.
- **Plain HTTP by default.** TLS requires the reverse proxy described above.
- **Extension features are partly unverified.** Headless `=PROMPT()` in Calc was verified. Sidebar
  chat, tool calling, and model-driven document edits were not verified headlessly.

Planned next steps: a small Linux agent that enrolls machines and ships `writeragent_debug.log`, then
dashboard/ERP integration for device management.

---

## Repository layout

```text
.
├── gateway/
│   ├── app.py               FastAPI proxy, auth, admin routes
│   ├── packager.py          Builds CellScribe.oxt from WriterAgent.oxt
│   ├── store.py             SQLite device registry and request log
│   ├── admin.html           Single-page admin dashboard
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── .env.example         Template; copy to .env and fill in
├── provision/
│   └── provision_pc.py      Writes writeragent.json for a Linux PC
├── vendor/
│   └── WriterAgent.oxt      Pinned, tested extension package
└── README.md
```

### Tested extension package

`vendor/WriterAgent.oxt`

- Release tag 0.8.84 (the package itself reports 0.8.85)
- SHA-256 `445e495556b1e67e20b5777eb96c7b6e1cb082a5bd871b4f9f6b46f5f99bf73c`
- Tested on LibreOffice 24.8.7, Ubuntu 24.10

The upstream release asset was replaced on 2026-11-01, after the 2026-09-19 release date, so keep this
copy and test updates before a wider rollout.

---

## Upstream extension notes

CellScribe uses [WriterAgent](https://github.com/KeithCu/writeragent) rather than building its own
extension. Verified on 2026-11-02: one headless `=PROMPT()` call in Calc through
`mistral-small-latest` returned the expected text with no cell error.

- Licence: GPL-3.0-or-later. Source-sharing rules apply only when distributing a modified version.
- Missing features should be contributed upstream rather than forked.
- **Why not LibreThinker:** it supports Writer only, and requests made with your own API key send the
  key and document text through `api.librethinker.com`.
- Leave WriterAgent's MCP server and public tunnel off. Both are off by default, and the MCP server has
  no authentication.

### Open items carried over from the pilot

- Security review before use with confidential documents. So far: a keyword scan of the package and a
  headless startup check with no key set, which showed no remote connections.
- Confirm company policy allows sending document content to Mistral.
- The extension can download extra tools such as the Harper grammar checker from GitHub. Not yet
  tested.
- The debug log shows `_geometric_open_job timed out after 30.0s` in headless runs. Check whether it
  also appears with a normal window (`writeragent_debug.log` in the profile's `user/config`).

---

## Licence

GPL-3.0-or-later. The full text is in [LICENSE](LICENSE).

The bundled WriterAgent extension is also GPL-3.0-or-later, and CellScribe's own files carry matching
`SPDX-License-Identifier: GPL-3.0-or-later` headers. Source-sharing rules apply only when distributing
a modified version — running it internally does not trigger them.

The licence choice matches the bundled extension. If your organisation needs different terms for the
gateway and provisioning code, replace `LICENSE` and the SPDX headers accordingly.
