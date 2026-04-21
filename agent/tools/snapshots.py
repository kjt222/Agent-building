from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: str
    file_path: Path
    created_at: str
    note: Optional[str] = None


def _snapshot_dir(target: Path, root: Optional[Path]) -> Path:
    base = root if root is not None else target.parent
    return base / ".snapshots" / target.name


def _manifest_path(snapshot_dir: Path) -> Path:
    return snapshot_dir / "manifest.json"


def _load_manifest(snapshot_dir: Path) -> Dict[str, Any]:
    manifest_path = _manifest_path(snapshot_dir)
    if not manifest_path.exists():
        return {"source": "", "snapshots": []}
    with manifest_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return {"source": "", "snapshots": []}
    if "snapshots" not in data or not isinstance(data["snapshots"], list):
        data["snapshots"] = []
    return data


def _save_manifest(snapshot_dir: Path, data: Dict[str, Any]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(snapshot_dir)
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=True, indent=2)


def create_snapshot(target: Path, root: Optional[Path] = None, note: Optional[str] = None) -> SnapshotInfo:
    if not target.exists():
        raise FileNotFoundError(target)
    snapshot_dir = _snapshot_dir(target, root)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"{timestamp}-{uuid4().hex[:8]}"
    snapshot_name = f"{snapshot_id}{target.suffix}"
    snapshot_path = snapshot_dir / snapshot_name
    shutil.copy2(target, snapshot_path)
    manifest = _load_manifest(snapshot_dir)
    manifest["source"] = str(target)
    manifest["snapshots"].append(
        {"id": snapshot_id, "file": snapshot_name, "created_at": timestamp, "note": note}
    )
    _save_manifest(snapshot_dir, manifest)
    return SnapshotInfo(snapshot_id=snapshot_id, file_path=snapshot_path, created_at=timestamp, note=note)


def list_snapshots(target: Path, root: Optional[Path] = None) -> List[SnapshotInfo]:
    snapshot_dir = _snapshot_dir(target, root)
    manifest = _load_manifest(snapshot_dir)
    entries = []
    for entry in manifest.get("snapshots", []):
        snapshot_name = entry.get("file")
        if not snapshot_name:
            continue
        snapshot_path = snapshot_dir / snapshot_name
        entries.append(
            SnapshotInfo(
                snapshot_id=str(entry.get("id", "")),
                file_path=snapshot_path,
                created_at=str(entry.get("created_at", "")),
                note=entry.get("note"),
            )
        )
    return sorted(entries, key=lambda item: item.created_at)


def restore_snapshot(target: Path, snapshot_id: str, root: Optional[Path] = None) -> Path:
    snapshot_path = Path(snapshot_id)
    if not snapshot_path.exists():
        snapshot_dir = _snapshot_dir(target, root)
        manifest = _load_manifest(snapshot_dir)
        match = None
        for entry in manifest.get("snapshots", []):
            if entry.get("id") == snapshot_id:
                match = entry
                break
        if match is None:
            raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")
        snapshot_path = snapshot_dir / match.get("file", "")
    if not snapshot_path.exists():
        raise FileNotFoundError(snapshot_path)
    shutil.copy2(snapshot_path, target)
    return target
