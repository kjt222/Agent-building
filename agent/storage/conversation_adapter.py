"""Conversation adapter for backward compatibility.

Provides the same interface as the old ConversationManager
but uses the new SQLite Database internally.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .database import Database, get_database
from .migration import migrate_conversations


class ConversationManagerV2:
    """SQLite-backed conversation manager.

    Drop-in replacement for the old JSON-based ConversationManager.
    Automatically migrates existing JSON conversations on first use.
    """

    def __init__(self, base_dir: Path, db: Optional[Database] = None):
        """Initialize the conversation manager.

        Args:
            base_dir: Base config directory (for migration compatibility)
            db: Database instance (uses singleton if None)
        """
        self.base_dir = base_dir
        self.db = db or get_database()

        # Auto-migrate existing JSON conversations
        self._migrate_if_needed()

    def _migrate_if_needed(self):
        """Migrate JSON conversations if they exist and haven't been migrated."""
        json_dir = self.base_dir / "conversations"
        if not json_dir.exists():
            return

        # Check if already migrated by looking for a marker
        marker_file = json_dir / ".migrated_to_sqlite"
        if marker_file.exists():
            return

        # Run migration
        try:
            stats = migrate_conversations(json_dir, self.db, verbose=False)
            if stats.get("migrated", 0) > 0:
                # Create marker file
                marker_file.write_text(
                    f"Migrated {stats['migrated']} conversations on "
                    f"{datetime.now().isoformat()}"
                )
        except Exception as e:
            # Log but don't fail - we can still use the new system
            print(f"Warning: Migration failed: {e}")

    def create(self, profile: str) -> str:
        """Create a new conversation.

        Args:
            profile: Profile name

        Returns:
            Conversation ID
        """
        conv_id = f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.db.create_conversation(conv_id, title="New Conversation", profile=profile)
        return conv_id

    def get(self, conv_id: str) -> Optional[dict]:
        """Get a conversation by ID.

        Args:
            conv_id: Conversation ID

        Returns:
            Conversation dict or None if not found
        """
        return self.db.get_conversation(conv_id)

    def list_all(self) -> list[dict]:
        """List all conversations with messages.

        Returns:
            List of conversation metadata dicts
        """
        convs = self.db.list_conversations()
        # Format to match old API
        result = []
        for c in convs:
            result.append({
                "id": c["id"],
                "title": c["title"],
                "created_at": c["created_at"],
                "updated_at": c["updated_at"],
                "message_count": c["message_count"],
                "preview": (c.get("preview") or "")[:50],
            })
        return result

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        model: str = None,
        sources: list = None
    ) -> bool:
        """Add a message to a conversation.

        Args:
            conv_id: Conversation ID
            role: Message role ('user' or 'assistant')
            content: Message content
            model: Model name (optional)
            sources: Source references (optional)

        Returns:
            True if successful, False if conversation not found
        """
        # Check if conversation exists
        conv = self.db.get_conversation(conv_id)
        if not conv:
            return False

        self.db.add_message(conv_id, role, content, model, sources)
        return True

    def add_activity_trace(self, conv_id: str, request_id: str, **trace) -> bool:
        """Persist an AgentLoop activity trace for a conversation turn."""
        conv = self.db.get_conversation(conv_id)
        if not conv:
            return False
        self.db.add_activity_trace(conv_id, request_id, **trace)
        return True

    def list_activity_traces(self, conv_id: str) -> list[dict]:
        """List persisted AgentLoop traces for a conversation."""
        if not self.db.get_conversation(conv_id):
            return []
        return self.db.list_activity_traces(conv_id)

    def get_activity_trace(self, conv_id: str, request_id: str) -> Optional[dict]:
        """Fetch one persisted AgentLoop trace."""
        return self.db.get_activity_trace(conv_id, request_id)

    def delete(self, conv_id: str) -> bool:
        """Delete a conversation.

        Args:
            conv_id: Conversation ID

        Returns:
            True if deleted, False if not found
        """
        return self.db.delete_conversation(conv_id)

    # New features not in old API

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """Search messages across all conversations.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching messages with conversation context
        """
        return self.db.search_messages(query, limit)


# Alias for compatibility
ConversationManager = ConversationManagerV2
