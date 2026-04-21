"""Migration utilities for Agent storage.

Migrates existing JSON conversations to SQLite database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .database import Database, get_database


def migrate_conversations(
    json_dir: Path,
    db: Optional[Database] = None,
    verbose: bool = True
) -> dict:
    """Migrate conversations from JSON files to SQLite.

    Args:
        json_dir: Directory containing conversation JSON files and index.json
        db: Database instance (uses default if None)
        verbose: Print progress messages

    Returns:
        dict with migration statistics
    """
    if db is None:
        db = get_database()

    stats = {
        "total": 0,
        "migrated": 0,
        "skipped": 0,
        "errors": [],
    }

    index_path = json_dir / "index.json"
    if not index_path.exists():
        if verbose:
            print(f"No index.json found in {json_dir}")
        return stats

    # Load index
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:
        stats["errors"].append(f"Failed to load index.json: {e}")
        return stats

    conversations = index.get("conversations", [])
    stats["total"] = len(conversations)

    for conv_meta in conversations:
        conv_id = conv_meta.get("id")
        if not conv_id:
            continue

        # Check if already migrated
        existing = db.get_conversation(conv_id)
        if existing:
            stats["skipped"] += 1
            if verbose:
                print(f"  Skipped (exists): {conv_id}")
            continue

        # Load full conversation
        conv_path = json_dir / f"{conv_id}.json"
        if not conv_path.exists():
            stats["errors"].append(f"Missing file: {conv_path}")
            continue

        try:
            conv = json.loads(conv_path.read_text(encoding="utf-8"))

            # Create conversation
            db.create_conversation(
                conv_id=conv_id,
                title=conv.get("title", "New Conversation"),
                profile=conv.get("profile")
            )

            # Manually set timestamps
            db.conn.execute(
                """
                UPDATE conversations
                SET created_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (conv.get("created_at"), conv.get("updated_at"), conv_id)
            )

            # Add messages
            messages = conv.get("messages", [])
            for msg in messages:
                db.add_message(
                    conv_id=conv_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    model=msg.get("model"),
                    sources=msg.get("sources")
                )

            stats["migrated"] += 1
            if verbose:
                print(f"  Migrated: {conv_id} ({len(messages)} messages)")

        except Exception as e:
            stats["errors"].append(f"Error migrating {conv_id}: {e}")

    return stats


def migrate_all(config_dir: Optional[Path] = None, verbose: bool = True) -> dict:
    """Run all migrations.

    Args:
        config_dir: Config directory (default: ~/.agent or config/)
        verbose: Print progress messages

    Returns:
        dict with all migration statistics
    """
    if config_dir is None:
        # Try default locations
        home_config = Path.home() / ".agent"
        local_config = Path("config")

        if home_config.exists():
            config_dir = home_config
        elif local_config.exists():
            config_dir = local_config
        else:
            return {"error": "No config directory found"}

    results = {}

    # Migrate conversations
    conv_dir = config_dir / "conversations"
    if conv_dir.exists():
        if verbose:
            print(f"\nMigrating conversations from {conv_dir}...")
        results["conversations"] = migrate_conversations(conv_dir, verbose=verbose)
    else:
        if verbose:
            print(f"No conversations directory found at {conv_dir}")
        results["conversations"] = {"total": 0, "migrated": 0}

    return results


if __name__ == "__main__":
    import sys

    verbose = "--quiet" not in sys.argv
    results = migrate_all(verbose=verbose)

    print("\n=== Migration Results ===")
    for category, stats in results.items():
        if isinstance(stats, dict) and "total" in stats:
            print(f"\n{category}:")
            print(f"  Total: {stats['total']}")
            print(f"  Migrated: {stats.get('migrated', 0)}")
            print(f"  Skipped: {stats.get('skipped', 0)}")
            if stats.get("errors"):
                print(f"  Errors: {len(stats['errors'])}")
                for err in stats["errors"][:5]:
                    print(f"    - {err}")
