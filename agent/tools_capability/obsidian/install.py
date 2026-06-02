"""One-time install helper for Obsidian Local REST API plugin.

Used to:
  1. Copy the Local REST API plugin files into a vault's
     .obsidian/plugins/ directory (if not already installed)
  2. After the user enables the plugin in Obsidian, read the auto-
     generated API key from data.json and stash it in keyring under
     the standard ref so the runtime tools can find it.

Run from CLI:
    .venv/Scripts/python.exe -m agent.tools_capability.obsidian.install --vault "D:\\path\\to\\vault"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import keyring

from agent.credentials import SERVICE_NAME
from agent.tools_capability.obsidian.rest_client import keyring_ref_for_vault


def import_key_from_vault(vault_root: Path) -> str:
    """Read the plugin's data.json, stash the api key in keyring.

    Returns the keyring ref string so the caller can confirm where to
    look later.
    """
    data_json = (
        vault_root / ".obsidian" / "plugins"
        / "obsidian-local-rest-api" / "data.json"
    )
    if not data_json.exists():
        raise FileNotFoundError(
            f"Local REST API plugin data.json not found at {data_json}. "
            "Open Obsidian, install + enable the plugin, then re-run."
        )
    data = json.loads(data_json.read_text(encoding="utf-8"))
    api_key = data.get("apiKey")
    if not api_key:
        raise RuntimeError(
            f"apiKey not in {data_json}; enable the plugin and let it "
            "auto-generate a key, then re-run."
        )
    ref = keyring_ref_for_vault(str(vault_root))
    keyring.set_password(SERVICE_NAME, ref, api_key)
    return ref


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Obsidian REST API install helper")
    parser.add_argument("--vault", required=True, help="Absolute path to vault root")
    args = parser.parse_args(argv)
    vault = Path(args.vault).expanduser().resolve()
    if not (vault / ".obsidian").exists():
        print(f"ERROR: {vault} is not an Obsidian vault (no .obsidian dir)",
              file=sys.stderr)
        return 2
    ref = import_key_from_vault(vault)
    print(f"OK — API key stored in keyring under ref:\n  {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
