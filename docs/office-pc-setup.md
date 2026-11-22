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

The script is `gateway/setup-pc.sh` with that device's gateway URL, token and model filled in. The same
file also runs standalone (copy it to the PC, e.g. by USB):

```bash
bash setup-pc.sh http://<gateway-ip>:8080 lta_... [model]
```

Close LibreOffice before running it (it offers to force-close it). Steps:

1. Checks the gateway and the token.
2. On Ubuntu and Ubuntu-based systems (Linux Mint, Zorin, Pop!_OS, elementary), makes the system
   LibreOffice complete: version 24.8 or newer, Writer, Calc, the `python3-uno` that matches LibreOffice
   exactly (without it: `com.sun.star.loader.Python cannot be instantiated`), and the desktop look
   packages (`libreoffice-gtk3`, `libreoffice-style-yaru`, and `libreoffice-gnome` on GNOME).
   - LibreOffice older than 24.8 is upgraded from the official LibreOffice PPA. On derivatives whose
     codename the PPA does not know (Linux Mint), it adds the PPA for the Ubuntu base release
     (`UBUNTU_CODENAME`) as `/etc/apt/sources.list.d/libreoffice-ppa-lta.sources`.
     `LTA_NO_UPGRADE=1` turns the upgrade off.
   - Large upgrades can stop halfway because of apt's ordering; the script runs `apt-get -f install`
     and a second pass. Success is judged by the installed packages, not apt's exit code, so an
     unrelated broken package (for example a half-installed `mysql-server`) does not stop it.
   - LibreOffice is never downgraded. Apt sources are only changed for the PPA, and on an end-of-life
     Ubuntu only with `LTA_USE_OLD_RELEASES=1` (switching to `old-releases.ubuntu.com`, with
     `*.lta-bak` backups).
   - Other systems: uses an `/opt` official build, Flatpak or Snap LibreOffice if present (Snap through
     LibreOffice's own Extension Manager dialog).
3. Downloads `LibreThinkerAgent.oxt` (written by the shell, so a Snap `curl` works too), installs it
   with `unopkg` (clearing conda from `PATH` and a stale lock if needed), and writes
   `writeragent.json`, merging with the existing file when possible.
4. Warns when a second LibreOffice (App Center Snap or Flatpak) is installed. That copy does not have
   the agent; if the menu opens it, the agent seems missing. Remove it (`sudo snap remove libreoffice`).

Running it on a PC that is already complete takes a few seconds and needs no password; an upgrade
from LibreOffice 24.2 takes about 10 minutes. It is safe to re-run. It writes the gateway's
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

## LibreOffice version and Python on the PC

### LibreOffice 24.8 is mandatory

WriterAgent declares `LibreOffice-minimal-version 24.8`. On an older version, for example
**24.2, the default on Ubuntu 24.04 LTS**, installation fails with:

```text
ERROR: Exception occurred: unsatisfied dependencies at ./desktop/source/deployment/registry/package/dp_package.cxx:614
ERROR: unopkg failed.
```

Check each PC with `libreoffice --version`. `setup.sh` (Option B) upgrades it automatically on Ubuntu
and Ubuntu-based systems. To do it by hand on Ubuntu 24.04:

```bash
sudo add-apt-repository ppa:libreoffice/ppa
sudo apt update
sudo apt install libreoffice python3-uno libreoffice-gtk3 libreoffice-gnome libreoffice-style-yaru
```

If apt stops halfway with `dependency problems - leaving unconfigured`, run `sudo apt -f install`
and the install command again.

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
