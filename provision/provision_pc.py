#!/usr/bin/env python3
# CellScribe PC provisioning
# SPDX-License-Identifier: GPL-3.0-or-later
"""Point a Linux LibreOffice profile at the CellScribe gateway.

Writes the gateway endpoint, the chosen model, and the device token into
WriterAgent's own config file. The Mistral key is never written to the PC.

Usage:
    python3 provision_pc.py --gateway http://10.0.0.5:8080 --token cs_xxx \\
        [--model mistral-small-latest] [--user "$USER"] [--install-extension]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any


def find_profile(user: str) -> str:
    """Locate the LibreOffice user profile directory for *user*."""
    home = os.path.expanduser(f"~{user}") if user != os.environ.get("USER") else os.path.expanduser("~")
    candidates = sorted(
        glob.glob(os.path.join(home, ".config", "libreoffice", "*", "user")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(
            f"No LibreOffice profile found under {home}/.config/libreoffice.\n"
            "Start LibreOffice once as this user, then re-run."
        )
    return candidates[0]


def load_config(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    # The file may carry a leading "//" comment header; drop comment lines.
    cleaned = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}")
    return data if isinstance(data, dict) else {}


def install_extension() -> None:
    oxt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor", "WriterAgent.oxt")
    oxt = os.path.abspath(oxt)
    if not os.path.exists(oxt):
        raise SystemExit(f"Extension package not found: {oxt}")
    print(f"Installing {oxt}")
    result = subprocess.run(["unopkg", "add", oxt], check=False)
    if result.returncode != 0:
        raise SystemExit("unopkg add failed. Close LibreOffice and retry.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True, help="Gateway base URL, e.g. http://10.0.0.5:8080")
    parser.add_argument("--token", required=True, help="Per-device token from the dashboard")
    parser.add_argument("--model", default="mistral-small-latest")
    parser.add_argument("--user", default=os.environ.get("USER", ""), help="Linux user to configure")
    parser.add_argument("--profile", default="", help="Override the LibreOffice profile path")
    parser.add_argument("--install-extension", action="store_true", help="Also run unopkg add")
    parser.add_argument("--dry-run", action="store_true", help="Print the result without writing")
    args = parser.parse_args()

    endpoint = args.gateway.rstrip("/")
    # WriterAgent appends /v1 itself; store the bare base URL.
    if endpoint.endswith("/v1"):
        endpoint = endpoint[: -len("/v1")]

    if args.install_extension:
        install_extension()

    profile = args.profile or find_profile(args.user)
    config_dir = os.path.join(profile, "config")
    config_path = os.path.join(config_dir, "writeragent.json")
    os.makedirs(config_dir, exist_ok=True)

    config = load_config(config_path)

    keys = config.get("api_keys_by_endpoint")
    if not isinstance(keys, dict):
        keys = {}
    keys[endpoint] = args.token

    config["endpoint"] = endpoint
    config["text_model"] = args.model
    config["model"] = args.model
    config["api_keys_by_endpoint"] = keys
    config.setdefault("enable_agent_log", True)
    config.setdefault("request_timeout", 300)

    if args.dry_run:
        print(json.dumps({"path": config_path, "config": config}, indent=2))
        return

    if os.path.exists(config_path):
        backup = f"{config_path}.bak.{int(time.time())}"
        shutil.copy2(config_path, backup)
        print(f"Backed up existing config to {backup}")

    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=4)
        handle.write("\n")
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass

    print(f"Configured {config_path}")
    print(f"  endpoint : {endpoint}")
    print(f"  model    : {args.model}")
    print("WriterAgent picks this up within a few seconds; no restart needed.")


if __name__ == "__main__":
    sys.exit(main())
