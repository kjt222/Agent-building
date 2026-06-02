"""Obsidian capability-tier tools (P14.6).

Three primitives the agent needs to drive an open Obsidian instance via
the Local REST API plugin:

  - ``read_excalidraw_canvas``     decode a .excalidraw.md, return structured
                                   JSON of elements + links + frontmatter
  - ``write_excalidraw_elements``  append / replace elements in the canvas's
                                   ## Drawing compressed-json fence
  - ``refresh_note``               force the Excalidraw plugin to drop its
                                   in-memory cache and re-read from disk
                                   (sequence: /open → workspace:close →
                                   /open; PROVEN-WORKING per
                                   tests/p14_6_live_refresh/probe_close_reopen.py)

The shared low-level REST helpers live in ``rest_client.py`` so the same
auth + SSL setup is reused.
"""

from agent.tools_capability.obsidian.rest_client import ObsidianRestClient

__all__ = ["ObsidianRestClient"]
