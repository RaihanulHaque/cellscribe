# Setting up office PCs

Close LibreOffice on the PC first. Every route ends in the same state: the extension installed and
`writeragent.json` pointing at the gateway with that PC's token.

### Option A: install one file and paste a token (no command line)

The gateway serves one extension file, identical for every PC and containing no secret:
`http://<gateway-ip>:8080/setup/LibreThinkerAgent.oxt`. The dashboard links it under each new token.

1. Double-click `LibreThinkerAgent.oxt` (or **Tools → Extension Manager → Add**) and restart LibreOffice.
2. Open **Tools → Libre Thinker Agent: Enter access token…** and paste the PC's token.

The extension checks the token with the gateway (a wrong or revoked token is rejected with a
message), picks the default model from those the PC may use, and writes `writeragent.json`.

For scripted rollouts the same action accepts the token directly
(`org.librethinkeragent.config:SetToken` with a `Token` property), skipping the dialog.

### Option B: one command (Linux)

The dashboard shows this command next to each new token:

```bash
curl -fsSL -H "Authorization: Bearer lta_..." http://<gateway-ip>:8080/setup.sh | bash
```

The script is generated for that device. It downloads `LibreThinkerAgent.oxt`, installs it with `unopkg`
(clearing conda from `PATH` and a stale lock if needed), and writes `writeragent.json`, merging with
`jq` when available and otherwise backing up and replacing the file. It writes the gateway's
default model; add `?model=<id>` to the URL to choose another. Set `LIBRETHINKERAGENT_PUBLIC_URL` if PCs
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
| `--install-extension` | off | Also run `unopkg add vendor/WriterAgent.oxt`. This is the **unpatched** package; prefer installing the gateway's `LibreThinkerAgent.oxt`. |
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
flatpak run --command=unopkg org.libreoffice.LibreOffice add LibreThinkerAgent.oxt

python3 provision/provision_pc.py \
  --gateway http://<gateway-ip>:8080 \
  --token lta_... \
  --profile ~/.var/app/org.libreoffice.LibreOffice/config/libreoffice/4/user
```

### Updating PCs

When the gateway is rebuilt with a new `LibreThinkerAgent.oxt`, reinstall it on each PC with LibreOffice
closed (`unopkg add -f LibreThinkerAgent.oxt`, or Option A again). The token and settings are kept.

### Upgrading a gateway that ran under an earlier name

Earlier builds were called CellScribe, Libre Agent and Libre Thinker. On an existing server:

1. **Settings.** Rename `CELLSCRIBE_*` or `LIBRETHINKER_*` to `LIBRETHINKERAGENT_*` in `gateway/.env`.
   The old names are still read as a fallback (the new names win), so this can wait.
2. **Data.** The compose volume is now `gateway_librethinkeragent-data` (it was
   `gateway_librethinker-data`, and `gateway_cellscribe-data` before that). Copy the data across once,
   with the old container stopped:

   ```bash
   docker rm -f librethinker-gateway        # or cellscribe-gateway
   cd gateway && docker compose create --build
   docker run --rm -v gateway_librethinker-data:/old:ro -v gateway_librethinkeragent-data:/new      python:3.12-slim sh -c 'cp -a /old/. /new/'
   docker compose up -d
   ```

   The gateway renames a copied `librethinker.db` or `cellscribe.db` to `librethinkeragent.db` on
   start, so devices, tokens and request history carry over. Keep the old volume until you have
   checked the dashboard.
3. **PCs.** Reinstall `LibreThinkerAgent.oxt` on each PC. Existing device tokens (`cs_…`, `lt_…`) keep
   working; new tokens start with `lta_`. Setup scripts generated before the rename point at a
   download URL that no longer exists, so generate a new one from the dashboard.

## LibreOffice version and Python on the PC

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
PATH=/usr/bin:/bin unopkg add -f LibreThinkerAgent.oxt
```

Start LibreOffice from the application menu rather than a conda-activated terminal. If conda is
only used interactively, `conda config --set auto_activate_base false` avoids the problem entirely.
