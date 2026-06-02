"""ArtifactContext: progressive disclosure for non-code artifacts (P11.3).

Public surface:
- ``ArtifactKind`` / ``ArtifactRecord`` / ``ArtifactManifest`` (types)
- ``WordArtifactManifest`` (P11.3.1)
- ``ArtifactRegistry`` + ``get_registry`` / ``reset_registry`` / ``serialize_for_context``
- Convenience: ``register_word_artifact``
"""

from agent.core.artifact_context.types import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
)
from agent.core.artifact_context.excel_manifest import ExcelArtifactManifest
from agent.core.artifact_context.word_manifest import WordArtifactManifest
from agent.core.artifact_context.registry import (
    ArtifactRegistry,
    conversation_id_from_ctx,
    get_registry,
    register_excel_artifact_from_read,
    register_excel_artifact_from_runtime,
    register_word_artifact,
    reset_all_registries,
    reset_registry,
    serialize_for_context,
)

__all__ = [
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactRecord",
    "ExcelArtifactManifest",
    "WordArtifactManifest",
    "ArtifactRegistry",
    "conversation_id_from_ctx",
    "get_registry",
    "register_excel_artifact_from_read",
    "register_excel_artifact_from_runtime",
    "register_word_artifact",
    "reset_all_registries",
    "reset_registry",
    "serialize_for_context",
]
