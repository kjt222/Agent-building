"""Per-domain L2 oracles. Importing any submodule registers it.

For runtime auto-registration, import this package — it eagerly loads all
known oracles so `get_oracle("excalidraw")` works without an extra import in
caller code.
"""

from . import excalidraw, klayout, office, sentaurus  # noqa: F401  (registry)
