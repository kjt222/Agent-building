"""Low-level Obsidian Local REST API client.

Wraps the HTTPS-with-self-signed-cert + bearer-token setup so the
individual tools don't repeat it. The plugin's documentation lives at
https://coddingtonbear.github.io/obsidian-local-rest-api/ ; the relevant
endpoints used here:

  GET  /active/                     — JSON of active note (use for state
                                      sanity checks)
  GET  /vault/<path>                — file contents (may return in-memory
                                      buffer, not always disk truth — check
                                      the prior reference memory note)
  POST /open/<path>                 — focus or open a file as active tab
  GET  /commands/                   — list every registered command
  POST /commands/<id>/              — execute a command by id

The plugin auto-generates an API key + a self-signed TLS cert on first
run, stored in `<vault>/.obsidian/plugins/obsidian-local-rest-api/data.json`.
We mirror the key into keyring on install so subsequent runs don't need
to re-read the vault config file.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

import keyring

from agent.credentials import SERVICE_NAME

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 27124  # HTTPS; insecure port 27123 disabled by default

# The keyring ref pattern includes the vault name so a user with multiple
# vaults can have one key per vault. Helper below derives it from a Path.
KEYRING_REF_PREFIX = "obsidian.local_rest_api"


def keyring_ref_for_vault(vault_path: str) -> str:
    """Build the keyring ref for a vault's Local REST API key."""
    from pathlib import Path

    safe = Path(vault_path).name.replace(" ", "_")
    return f"{KEYRING_REF_PREFIX}.{safe}"


@dataclass
class RestResponse:
    status: int
    body: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ObsidianRestClient:
    """Thin wrapper. One instance per vault."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        keyring_ref: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            if not keyring_ref:
                raise ValueError("must supply api_key or keyring_ref")
            api_key = keyring.get_password(SERVICE_NAME, keyring_ref)
            if not api_key:
                raise RuntimeError(
                    f"Obsidian REST API key not found in keyring under "
                    f"ref {keyring_ref!r}. Install the Local REST API "
                    f"plugin in the vault, then re-run install setup."
                )
        self._api_key = api_key
        self._base_url = f"https://{host}:{port}"
        self._timeout = timeout
        self._ssl_ctx = ssl.create_default_context()
        # Plugin uses a self-signed cert; can't verify chain. The risk is
        # localhost-only — a MITM would need to be on the loopback iface.
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    # ---- core HTTP ----

    def _request(self, path: str, method: str = "GET",
                 body: bytes = b"",
                 extra_headers: dict[str, str] | None = None) -> RestResponse:
        url = self._base_url + path
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Length": str(len(body)),
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(
            url, method=method, data=body if body else None, headers=headers
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self._timeout, context=self._ssl_ctx
            ) as r:
                return RestResponse(r.status, r.read().decode("utf-8",
                                                              errors="replace"))
        except urllib.error.HTTPError as e:
            return RestResponse(
                e.code, e.read().decode("utf-8", errors="replace")
            )

    # ---- typed wrappers ----

    def active_note(self) -> dict[str, Any]:
        """GET /active/ — JSON body of currently-active note."""
        r = self._request("/active/", extra_headers={
            "Accept": "application/vnd.olrapi.note+json",
        })
        if not r.ok:
            raise RuntimeError(f"GET /active failed: {r.status} {r.body[:200]}")
        return json.loads(r.body) if r.body else {}

    def open_file(self, vault_relative_path: str) -> None:
        """POST /open/<path> — make this file the active tab.

        Path is vault-relative, forward slashes. URL-encoded internally.
        """
        encoded = urllib.parse.quote(vault_relative_path)
        r = self._request(f"/open/{encoded}", method="POST")
        if not r.ok:
            raise RuntimeError(
                f"POST /open/{vault_relative_path!r} failed: "
                f"{r.status} {r.body[:200]}"
            )

    def execute_command(self, command_id: str) -> None:
        """POST /commands/<id>/ — fire an Obsidian command by id.

        Listing of available command ids: ``GET /commands/``. The plugin
        documentation calls these the same id strings shown in
        Settings → Hotkeys.
        """
        encoded = urllib.parse.quote(command_id, safe=":-")
        r = self._request(f"/commands/{encoded}/", method="POST")
        if not r.ok:
            raise RuntimeError(
                f"POST /commands/{command_id} failed: "
                f"{r.status} {r.body[:200]}"
            )

    def list_commands(self) -> list[dict[str, str]]:
        """GET /commands/ — list registered Obsidian commands."""
        r = self._request("/commands/")
        if not r.ok:
            raise RuntimeError(f"GET /commands/ failed: {r.status}")
        data = json.loads(r.body) if r.body else {}
        return list(data.get("commands", []))
