#!/usr/bin/env bash
# Libre Thinker Agent - one-shot PC setup.
#
# Standalone:   bash setup-pc.sh http://<gateway-ip>:8080 lta_xxxxx [model]
# From gateway: curl -fsSL -H "Authorization: Bearer lta_xxxxx" http://<gateway-ip>:8080/setup.sh | bash
#
# What it does:
#   1. Finds the LibreOffice on this PC (apt/deb, /opt official build, Flatpak, Snap).
#   2. Makes sure LibreOffice can run Python extensions. If not, repairs it:
#      apt-get update (switching an end-of-life Ubuntu to old-releases), then
#      installs python3-uno matching the exact LibreOffice version.
#   3. Installs the Libre Thinker Agent extension and writes its config.
set -u   # no pipefail: "cmd | grep -q" must not fail on SIGPIPE

GATEWAY_DEFAULT=__GATEWAY__   # filled in when served by the gateway
TOKEN_DEFAULT=__TOKEN__
MODEL_DEFAULT=__MODEL__

main() {
unfilled() { case "$1" in __*__) return 0 ;; *) return 1 ;; esac; }
GATEWAY=${1:-$(unfilled "$GATEWAY_DEFAULT" && echo "${LTA_GATEWAY:-}" || echo "$GATEWAY_DEFAULT")}
TOKEN=${2:-$(unfilled "$TOKEN_DEFAULT" && echo "${LTA_TOKEN:-}" || echo "$TOKEN_DEFAULT")}
MODEL=${3:-$(unfilled "$MODEL_DEFAULT" && echo "${LTA_MODEL:-}" || echo "$MODEL_DEFAULT")}

EXT_ID=org.extension.writeragent
FLATPAK_ID=org.libreoffice.LibreOffice

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERR\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { local a=""; if [ -r /dev/tty ]; then read -r -p "$1" a </dev/tty || true; fi; printf '%s' "$a"; }

# ---------------------------------------------------------------- inputs
[ -n "$GATEWAY" ] || GATEWAY=$(ask "Gateway URL (e.g. http://192.168.1.10:8080): ")
[ -n "$TOKEN" ]   || TOKEN=$(ask "Device token (lta_...): ")
GATEWAY=${GATEWAY%/}
[ -n "$GATEWAY" ] && [ -n "$TOKEN" ] || die "Usage: bash setup-pc.sh <gateway-url> <device-token> [model]"
command -v curl >/dev/null || die "curl is required: sudo apt install curl"

# ---------------------------------------------------------------- clean environment
# conda/pyenv on PATH breaks LibreOffice's embedded Python (std::bad_alloc).
clean_path=$(printf '%s' "${PATH:-}" | tr ':' '\n' \
  | grep -v -E '/(ana|mini)conda[0-9]*/|/(mini|mamba)forge[0-9]*/|/condabin(/|$)|/\.pyenv/' \
  | paste -sd: - || true)
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin${clean_path:+:$clean_path}"
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PYTHON_EXE VIRTUAL_ENV || true
export DEBIAN_FRONTEND=noninteractive

SUDO=""
if [ "$(id -u)" != 0 ]; then
  command -v sudo >/dev/null && SUDO="sudo"
fi
# stdin may be this very script (curl | bash): never let apt or debconf read it.
apt_get() { $SUDO env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o DPkg::Lock::Timeout=600 -o Dpkg::Options::=--force-confold "$@" </dev/null; }

# ---------------------------------------------------------------- gateway checks
say "Checking the gateway at $GATEWAY"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$GATEWAY/healthz" || true)
[ "$code" = 200 ] || die "Gateway not reachable ($GATEWAY/healthz -> ${code:-no answer}). Check the IP, that Docker is running, and the firewall (port 8080)."
models_json=$(curl -s --max-time 15 -H "Authorization: Bearer $TOKEN" "$GATEWAY/v1/models" || true)
case "$models_json" in
  *'"data"'*) ok "Gateway reachable, token accepted" ;;
  *) die "Token rejected by the gateway (revoked or mistyped). Create/copy the device token again in the dashboard." ;;
esac
if [ -z "$MODEL" ]; then
  # The gateway's own setup script names the preferred model for this device.
  MODEL=$(curl -s --max-time 15 -H "Authorization: Bearer $TOKEN" "$GATEWAY/setup.sh" \
    | sed -n -E "s/^MODEL(_DEFAULT)?='?([^' ]+)'?.*/\2/p" | grep -v '^__' | head -n1 || true)
  [ -n "$MODEL" ] || MODEL=$(printf '%s' "$models_json" | grep -o '"id": *"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/')
fi
[ -n "$MODEL" ] || die "Could not determine a model for this device."
ok "Model: $MODEL"

# ---------------------------------------------------------------- LibreOffice discovery
os_id=""; os_like=""; os_rel=""; os_name=""; ubu_rel=""
if [ -r /etc/os-release ]; then
  os_id=$(. /etc/os-release; printf '%s' "${ID:-}")
  os_like=$(. /etc/os-release; printf '%s' "${ID_LIKE:-}")
  os_rel=$(. /etc/os-release; printf '%s' "${VERSION_CODENAME:-}")
  os_name=$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-$ID}")
  # Ubuntu itself, or a distribution built on it (its UBUNTU_CODENAME, e.g. noble).
  ubu_rel=$(. /etc/os-release; printf '%s' "${UBUNTU_CODENAME:-}")
  [ -z "$ubu_rel" ] && [ "$os_id" = ubuntu ] && ubu_rel=$os_rel
fi
case " $os_id $os_like " in
  *" debian "*|*" ubuntu "*) PKG=apt ;;
  *" fedora "*|*" rhel "*) PKG=dnf ;;
  *" suse "*|*" opensuse "*) PKG=zypper ;;
  *) PKG=other ;;
esac

has_loader() { [ -e "$1/pythonloader.unorc" ] && [ -e "$1/libpythonloaderlo.so" ]; }

native_dir() {  # program dir of the distro LibreOffice, if installed
  local d
  if command -v unopkg >/dev/null; then
    d=$(dirname "$(readlink -f "$(command -v unopkg)")")
    case "$d" in /snap/*) ;; *) [ -x "$d/soffice.bin" ] && { echo "$d"; return; } ;; esac
  fi
  for d in /usr/lib/libreoffice/program /usr/lib64/libreoffice/program; do
    [ -x "$d/soffice.bin" ] && { echo "$d"; return; }
  done
}
opt_dir() {  # newest official TDF build under /opt
  local d
  for d in $(ls -d /opt/libreoffice*/program 2>/dev/null | sort -rV); do
    [ -x "$d/soffice.bin" ] && { echo "$d"; return; }
  done
}
has_flatpak() { command -v flatpak >/dev/null && flatpak info "$FLATPAK_ID" >/dev/null 2>&1; }
has_snap()    { command -v snap >/dev/null && snap list libreoffice >/dev/null 2>&1; }

NATIVE=$(native_dir || true)
OPT=$(opt_dir || true)
found=""
[ -n "$NATIVE" ] && found="$found system($NATIVE)"
[ -n "$OPT" ] && found="$found official-build($OPT)"
has_flatpak && found="$found flatpak"
has_snap && found="$found snap"
say "LibreOffice installations found:${found:- none}"

# ---------------------------------------------------------------- apt repair helpers
apt_update() {
  local out
  say "Refreshing package lists (you may be asked for your password)"
  out=$(apt_get update 2>&1); local rc=$?
  if [ "$os_id" = ubuntu ] && printf '%s' "$out" | grep -qE "(no longer|does not) have a Release file|404 +Not Found.*ubuntu\.com"; then
    if [ "${LTA_USE_OLD_RELEASES:-0}" != 1 ]; then
      warn "Ubuntu '$os_rel' is end-of-life: its package servers are gone, so packages cannot be downloaded."
      warn "Not changing apt sources. Use a supported Ubuntu (e.g. 24.04 LTS), or re-run with LTA_USE_OLD_RELEASES=1 to allow it."
    elif curl -fsI --max-time 10 "http://old-releases.ubuntu.com/ubuntu/dists/$os_rel/Release" >/dev/null 2>&1; then
      warn "Ubuntu '$os_rel' is end-of-life; pointing apt at old-releases.ubuntu.com (backups: *.lta-bak)"
      local f
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
        [ -f "$f" ] || continue
        grep -qE '(archive|security)\.ubuntu\.com' "$f" || continue
        $SUDO cp -p "$f" "$f.lta-bak"
        $SUDO sed -i -E 's#https?://([a-z]{2}\.)?(archive|security)\.ubuntu\.com/ubuntu/?#http://old-releases.ubuntu.com/ubuntu/#g' "$f"
      done
      out=$(apt_get update 2>&1); rc=$?
    fi
  fi
  [ $rc = 0 ] || warn "apt-get update reported problems (continuing):$(printf '\n%s' "$out" | grep -E '^(E|W):' | head -n5)"
  return 0
}

pkg_ver() { dpkg-query -W -f='${db:Status-Abbrev}${Version}' "$1" 2>/dev/null | sed -n 's/^ii *//p'; }
candidates() { apt-cache madison "$1" 2>/dev/null | awk -F'|' '{gsub(/ /,"",$2); print $2}'; }

# ---------------------------------------------------------------- apt: make the system LibreOffice complete
# The agent needs, from one consistent LibreOffice build: version 24.8+, Writer,
# Calc and the matching python3-uno. The desktop look packages keep it from
# looking dated. On Ubuntu, LibreOffice older than 24.8 is upgraded from the
# official LibreOffice PPA (ppa:libreoffice/ppa); LTA_NO_UPGRADE=1 turns that off.
lo_num() { printf '%s' "${1#*:}" | awk -F'[.-]' '$1 ~ /^[0-9]+$/ {printf "%d", $1*100+$2}'; }
cand_ver() { apt-cache policy "$1" 2>/dev/null | awk '/Candidate:/{print $2}' | grep -v '(none)'; }
need_gnome() { case "${XDG_CURRENT_DESKTOP:-}" in *GNOME*|*Unity*|*ubuntu*|"") return 0 ;; esac; return 1; }
CORE_PKGS="libreoffice-core libreoffice-writer libreoffice-calc python3-uno"
look_pkgs() { echo libreoffice-gtk3 libreoffice-style-yaru; need_gnome && echo libreoffice-gnome; }

apt_complete() {   # everything the agent needs is installed and from one build
  local core n p
  core=$(pkg_ver libreoffice-core); [ -n "$core" ] || return 1
  n=$(lo_num "$core"); [ -n "$n" ] && [ "$n" -ge 2408 ] || return 1
  for p in libreoffice-writer libreoffice-calc; do [ -n "$(pkg_ver "$p")" ] || return 1; done
  [ "$(pkg_ver python3-uno)" = "$core" ] || return 1
  [ -n "$(native_dir)" ] && has_loader "$(native_dir)"
}
look_missing() {   # look packages not installed (only names apt cannot find are skipped)
  local p out=""
  for p in $(look_pkgs); do [ -n "$(pkg_ver "$p")" ] || out="$out $p"; done
  printf '%s' "$out"
}

PPA_URL=https://ppa.launchpadcontent.net/libreoffice/ppa/ubuntu
ppa_ok() { [ -n "$(lo_num "$(cand_ver libreoffice-core)")" ] && [ "$(lo_num "$(cand_ver libreoffice-core)")" -ge 2408 ]; }

# Add ppa:libreoffice/ppa for the Ubuntu base release. add-apt-repository is
# tried first; on derivatives it can pick the distro's own codename (which the
# PPA does not have), so fall back to writing the source for $ubu_rel directly.
add_lo_ppa() {
  command -v add-apt-repository >/dev/null || apt_get install -y software-properties-common >/dev/null 2>&1
  if command -v add-apt-repository >/dev/null; then
    $SUDO add-apt-repository -y ppa:libreoffice/ppa </dev/null >/dev/null 2>&1
    apt_update
    ppa_ok && return 0
  fi
  say "Adding the LibreOffice PPA for Ubuntu base '$ubu_rel' directly"
  # Drop entries add-apt-repository made for a codename the PPA does not have.
  local f fp key
  for f in /etc/apt/sources.list.d/libreoffice-ubuntu-ppa-*; do
    [ -f "$f" ] && ! grep -q "$ubu_rel" "$f" && $SUDO rm -f "$f"
  done
  fp=$(curl -fsSL --max-time 20 "https://api.launchpad.net/1.0/~libreoffice/+archive/ubuntu/ppa" \
        | grep -o '"signing_key_fingerprint": *"[0-9A-Fa-f]*"' | grep -o '[0-9A-Fa-f]\{16,\}' | head -n1)
  [ -n "$fp" ] || { warn "Could not look up the PPA signing key (internet/proxy?)"; return 1; }
  key=$(curl -fsSL --max-time 20 "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x$fp") \
    && printf '%s' "$key" | grep -q 'BEGIN PGP PUBLIC KEY' \
    || { warn "Could not download the PPA signing key"; return 1; }
  $SUDO mkdir -p /etc/apt/keyrings
  printf '%s\n' "$key" | $SUDO tee /etc/apt/keyrings/libreoffice-ppa.asc >/dev/null
  printf 'Types: deb\nURIs: %s\nSuites: %s\nComponents: main\nSigned-By: /etc/apt/keyrings/libreoffice-ppa.asc\n' \
    "$PPA_URL" "$ubu_rel" | $SUDO tee /etc/apt/sources.list.d/libreoffice-ppa-lta.sources >/dev/null
  apt_update
  ppa_ok
}

apt_setup_lo() {
  [ -n "$SUDO" ] || [ "$(id -u)" = 0 ] || { warn "sudo is not available; cannot install LibreOffice parts"; return 1; }
  apt_update
  local core n cand cn pkgs="" p
  core=$(pkg_ver libreoffice-core); n=$(lo_num "$core")
  if [ -z "$n" ] || [ "$n" -lt 2408 ]; then
    cand=$(cand_ver libreoffice-core); cn=$(lo_num "$cand")
    if [ -z "$cn" ] || [ "$cn" -lt 2408 ]; then
      if [ "${LTA_NO_UPGRADE:-0}" = 1 ] || [ -z "$ubu_rel" ]; then
        TOO_OLD=${core:-${cand:-none}}; return 1
      fi
      say "LibreOffice ${core:+${core#*:} }is older than 24.8 (or missing); adding the official LibreOffice PPA"
      add_lo_ppa
      cand=$(cand_ver libreoffice-core); cn=$(lo_num "$cand")
      if [ -z "$cn" ] || [ "$cn" -lt 2408 ]; then
        warn "No LibreOffice 24.8+ is available for $os_name (Ubuntu base '$ubu_rel'), even from the PPA."
        TOO_OLD=${core:-${cand:-none}}; return 1
      fi
    fi
    # Upgrade every installed LibreOffice package together, plus what the agent needs.
    for p in $(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 'libreoffice*' 2>/dev/null | awk '$1=="ii"{print $2}') \
             $CORE_PKGS $(look_pkgs); do
      [ -n "$(cand_ver "$p")" ] && pkgs="$pkgs $p"
    done
    say "Installing LibreOffice ${cand#*:} with Writer, Calc and Python support (this can take a few minutes)"
  else
    # LibreOffice is new enough: add only what is missing, at the same version.
    for p in $CORE_PKGS $(look_pkgs); do
      [ "$p" = libreoffice-core ] && continue
      if [ "$p" = python3-uno ]; then [ "$(pkg_ver python3-uno)" = "$core" ] && continue
      else [ -n "$(pkg_ver "$p")" ] && continue; fi
      [ -n "$(cand_ver "$p")" ] || continue
      if candidates "$p" | grep -qxF "$core"; then pkgs="$pkgs $p=$core"; else pkgs="$pkgs $p"; fi
    done
    [ -n "$pkgs" ] || return 0
    say "Installing missing LibreOffice parts:$(printf '%s' "$pkgs" | sed 's/=[^ ]*//g')"
  fi
  # shellcheck disable=SC2086
  out=$(apt_get install -y --allow-downgrades $(printf '%s\n' $pkgs | sort -u) 2>&1); rc=$?
  if [ $rc != 0 ] && ! apt_complete; then
    # Big LibreOffice upgrades can stop halfway because apt orders them badly.
    # Let apt finish what it started, then try once more.
    say "Finishing the LibreOffice installation (second pass)"
    apt_get -f install -y >/dev/null 2>&1
    # shellcheck disable=SC2086
    out=$(apt_get install -y --allow-downgrades $(printf '%s\n' $pkgs | sort -u) 2>&1); rc=$?
  fi
  if [ $rc != 0 ] && ! apt_complete; then
    # apt's exit code is not trusted: an unrelated broken package (e.g. a
    # half-installed mysql-server) makes it fail even when LibreOffice is fine.
    printf '%s\n' "$out" | grep -E '^(E:|dpkg: error| [a-z0-9.+-]+$)' | head -n6 | sed 's/^/    apt: /' >&2
  fi
  NATIVE=$(native_dir || true)
  apt_complete || return 1
  return 0
}

repair_native() {   # Fedora/openSUSE: just add the Python component
  [ -n "$SUDO" ] || [ "$(id -u)" = 0 ] || { warn "sudo not available; cannot repair automatically"; return 1; }
  case "$PKG" in
    dnf) $SUDO dnf install -y libreoffice-pyuno </dev/null ;;
    zypper) $SUDO zypper --non-interactive install libreoffice-pyuno </dev/null ;;
    *) return 1 ;;
  esac
  NATIVE=$(native_dir || true)
}

native_ok() { [ -n "$NATIVE" ] && has_loader "$NATIVE"; }

# ---------------------------------------------------------------- choose target
MODE=""
TOO_OLD=""
if [ "$PKG" = apt ]; then
  if apt_complete && [ -z "$(look_missing)" ]; then
    MODE=native
  else
    if apt_setup_lo; then
      MODE=native; ok "LibreOffice $(pkg_ver libreoffice-core | sed 's/^[0-9]*://; s/-.*//') is ready (Writer, Calc, Python)"
      m=$(look_missing); [ -z "$m" ] || warn "Could not install the look packages:$m (LibreOffice may look dated)"
    elif [ -z "$TOO_OLD" ]; then
      core=$(pkg_ver libreoffice-core)
      warn "Could not complete the system LibreOffice (core: ${core:-none}, python3-uno: $(pkg_ver python3-uno || true))."
      if [ -n "$core" ] && [ "$(pkg_ver python3-uno)" != "$core" ]; then
        warn "No python3-uno for LibreOffice ${core#*:} in this PC's package sources (was it installed from a PPA or a .deb that is gone?)."
      fi
    fi
  fi
elif [ -n "$NATIVE" ]; then
  native_ok || repair_native
  native_ok && MODE=native
fi
[ -z "$MODE" ] && [ -n "$OPT" ] && has_loader "$OPT" && MODE=opt
[ -z "$MODE" ] && has_flatpak && MODE=flatpak
[ -z "$MODE" ] && has_snap && MODE=snap
if [ -n "$TOO_OLD" ] && [ -z "$MODE" ]; then
  echo >&2
  warn "This PC's LibreOffice (${TOO_OLD#*:}) is too old: the agent needs 24.8 or newer. System: ${os_name:-unknown}"
  [ -n "$ubu_rel" ] || [ "${LTA_NO_UPGRADE:-0}" = 1 ] || warn "This system is not Ubuntu-based, so the script cannot upgrade LibreOffice itself. Install LibreOffice 24.8+ (e.g. the Flatpak), then re-run."
  if [ "${LTA_NO_UPGRADE:-0}" = 1 ]; then
    warn "Automatic upgrade is off (LTA_NO_UPGRADE=1). Re-run without it, or upgrade by hand:"
    echo "    sudo add-apt-repository -y ppa:libreoffice/ppa" >&2
    echo "    sudo apt-get update && sudo apt-get install -y libreoffice python3-uno" >&2
  fi
  die "LibreOffice too old."
fi
[ -n "$MODE" ] || die "No usable LibreOffice with Python support. On Ubuntu run: sudo apt-get update && sudo apt-get install libreoffice python3-uno   then re-run this script."

case "$MODE" in
  native|opt) PROFILE_ROOT="$HOME/.config/libreoffice"; WORK="${TMPDIR:-/tmp}" ;;
  flatpak)    PROFILE_ROOT="$HOME/.var/app/$FLATPAK_ID/config/libreoffice"; WORK="$HOME/.var/app/$FLATPAK_ID/cache" ;;
  snap)       PROFILE_ROOT="$HOME/snap/libreoffice/current/.config/libreoffice"; WORK="$HOME" ;;
esac
ok "Using LibreOffice: $MODE"

uno() {
  case "$MODE" in
    native)  "$NATIVE/unopkg" "$@" ;;
    opt)     "$OPT/unopkg" "$@" ;;
    flatpak) flatpak run --command=unopkg "$FLATPAK_ID" "$@" ;;
    snap)    return 1 ;;
  esac
}

# ---------------------------------------------------------------- LibreOffice must be closed
lo_running() { pgrep -x soffice.bin >/dev/null 2>&1 || pgrep -f 'program/soffice' >/dev/null 2>&1; }
while lo_running; do
  if [ -r /dev/tty ]; then
    ans=$(ask "LibreOffice is open. Save your work, close ALL LibreOffice windows, then press Enter (or type 'kill' to force-close): ")
    [ "$ans" = kill ] && { pkill -x soffice.bin 2>/dev/null; pkill -f 'program/soffice' 2>/dev/null; sleep 2; }
  else
    die "Close LibreOffice first, then re-run."
  fi
done

# ---------------------------------------------------------------- download + install
mkdir -p "$WORK"
TMPD=$(mktemp -d "$WORK/lta-setup.XXXXXX") || die "Cannot create a temp directory in $WORK"
trap 'rm -rf "$TMPD"' EXIT
OXT="$TMPD/LibreThinkerAgent.oxt"
say "Downloading the Libre Thinker Agent extension"
# Let bash write the file, not curl: a Snap curl is sandboxed and cannot write
# to /tmp or hidden folders, but it can always write to its stdout.
curl -fsSL --max-time 120 -H "Authorization: Bearer $TOKEN" "$GATEWAY/setup/LibreThinkerAgent.oxt" > "$OXT" \
  || die "Download failed from $GATEWAY/setup/LibreThinkerAgent.oxt"
[ "$(head -c 2 "$OXT" 2>/dev/null)" = PK ] || die "Downloaded extension is not valid (got $(wc -c < "$OXT") bytes)."

install_oxt() {
  uno remove "$EXT_ID" >/dev/null 2>&1 || true
  uno add -f "$OXT" 2>&1
}

if [ "$MODE" = snap ]; then
  # The Snap cannot be driven from outside; let LibreOffice's own Extension Manager install it.
  cp "$OXT" "$HOME/LibreThinkerAgent.oxt"
  warn "Snap LibreOffice: an 'Extension Installation' dialog will open. Click OK / Accept, then CLOSE LibreOffice."
  snap run libreoffice "$HOME/LibreThinkerAgent.oxt" >/dev/null 2>&1 || true
else
  say "Installing the extension"
  out=$(install_oxt); rc=$?
  if [ $rc != 0 ] && printf '%s' "$out" | grep -q "lock file"; then
    warn "Removing a stale LibreOffice lock"; rm -f "$PROFILE_ROOT"/*/.lock; out=$(install_oxt); rc=$?
  fi
  if [ $rc != 0 ] && printf '%s' "$out" | grep -qE "loader\.Python|cannot be instantiated|bad_alloc"; then
    if [ "$MODE" = native ]; then
      warn "LibreOffice's Python loader failed to start; repairing and retrying"
      repair_native; out=$(install_oxt); rc=$?
    fi
    if [ $rc != 0 ]; then
      # A broken earlier registration in the profile can keep failing; reset only the extension cache.
      for d in "$PROFILE_ROOT"/*/user/uno_packages "$PROFILE_ROOT"/*/user/extensions; do
        [ -d "$d" ] && { warn "Resetting extension cache $d (backup: $d.lta-bak)"; rm -rf "$d.lta-bak"; mv "$d" "$d.lta-bak"; }
      done
      out=$(install_oxt); rc=$?
    fi
  fi
  if [ $rc != 0 ]; then
    printf '%s\n' "$out" >&2
    echo >&2
    echo "Diagnostics:" >&2
    echo "  OS: $os_name ($os_id $os_rel, Ubuntu base: ${ubu_rel:-none})   mode: $MODE   program: ${NATIVE:-$OPT}" >&2
    [ "$PKG" = apt ] && echo "  libreoffice-core: $(pkg_ver libreoffice-core)   python3-uno: $(pkg_ver python3-uno)" >&2
    die "Extension install failed. Send the text above to the admin."
  fi
  # unopkg can leave its profile lock behind; nothing else is running, so clear it.
  lo_running || rm -f "$PROFILE_ROOT"/*/.lock 2>/dev/null
  listed=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do   # unopkg's helper process takes a moment to exit
    list_out=$(uno list 2>&1 || true)
    case "$list_out" in *"$EXT_ID"*) listed=1; break ;; esac
    sleep 1; lo_running || rm -f "$PROFILE_ROOT"/*/.lock 2>/dev/null
  done
  if [ $listed = 1 ]; then ok "Extension installed and registered"; else warn "Extension installed (registration check timed out; it will load when LibreOffice starts)"; fi
  lo_running || rm -f "$PROFILE_ROOT"/*/.lock 2>/dev/null
fi

# ---------------------------------------------------------------- config
profile=$(ls -dt "$PROFILE_ROOT"/*/user 2>/dev/null | head -n1 || true)
[ -n "$profile" ] || profile="$PROFILE_ROOT/4/user"
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
  elif command -v python3 >/dev/null; then
    new=$(NEW="$new" python3 -I -c '
import json, os, sys
try:
    old = json.loads("\n".join(l for l in open(sys.argv[1]) if not l.lstrip().startswith("//")))
    if not isinstance(old, dict): old = {}
except Exception:
    old = {}
n = json.loads(os.environ["NEW"])
keys = dict(old.get("api_keys_by_endpoint") or {}); keys.update(n.pop("api_keys_by_endpoint"))
old.update(n); old["api_keys_by_endpoint"] = keys
print(json.dumps(old, indent=1))' "$cfg" 2>/dev/null || printf '%s' "$new")
  fi
fi
( umask 077; printf '%s\n' "$new" > "$cfg" ); chmod 600 "$cfg"
ok "Configured $cfg"

echo
printf '\033[1;32mDone.\033[0m Open LibreOffice'
case "$MODE" in flatpak) printf ' (Flatpak)' ;; snap) printf ' (Snap)' ;; esac
echo ' and try =PROMPT("hello") in Calc, or the Libre Thinker Agent sidebar in Writer.'

  # Another LibreOffice (Snap/Flatpak) keeps its own extensions: if the menu opens
  # that one, the agent seems missing. Say so plainly.
  if [ "$MODE" = native ] || [ "$MODE" = opt ]; then
    others=""
    has_snap && others="$others Snap"
    has_flatpak && others="$others Flatpak"
    if [ -n "$others" ]; then
      echo
      warn "This PC also has a${others} LibreOffice (e.g. from the App Center). It does NOT have the agent."
      warn "The agent is in the system LibreOffice. Open it with: ${NATIVE:-$OPT}/soffice --calc"
      has_snap && warn "To make the menu always open the right one, remove the other:  sudo snap remove libreoffice"
      has_flatpak && warn "To make the menu always open the right one, remove the other:  flatpak uninstall $FLATPAK_ID"
    fi
  fi

  # Without the desktop integration package, the system LibreOffice uses its
  # plain built-in look, which looks dated. Suggest the packages; do not install unasked.
  if [ "$MODE" = native ] && [ "$PKG" = apt ]; then
    missing=""
    for p in libreoffice-gtk3 libreoffice-style-yaru; do
      [ -n "$(pkg_ver "$p")" ] || missing="$missing $p"
    done
    case "${XDG_CURRENT_DESKTOP:-}" in *GNOME*|*Unity*|"") [ -n "$(pkg_ver libreoffice-gnome)" ] || missing="$missing libreoffice-gnome" ;; esac
    if [ -n "$missing" ]; then
      echo
      warn "LibreOffice will look dated: its desktop look-and-feel packages are not installed. For the modern look run:"
      echo "    sudo apt-get install -y$missing" >&2
    fi
  fi
}

main "$@"
