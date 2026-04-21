from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

import yaml

from .config_loader import get_config_dir, load_app_config


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    rag_db_path: Path
    logs_dir: Path
    lexicon_files: list[Path]
    cloud_send: str
    allow_raw_on_confirm: bool
    conflict_confirm: bool
    vector_store_content: str


def _base_dir(config_dir: str | None) -> Path:
    return get_config_dir(config_dir).parent


def _resolve_path(path: str | Path, base_dir: Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    return base_dir / value


def _default_lexicon_files(profile_name: str, base_dir: Path) -> list[Path]:
    return [
        _resolve_path("lexicons/global.yaml", base_dir),
        _resolve_path(f"lexicons/{profile_name}.yaml", base_dir),
        _resolve_path("lexicons/global.txt", base_dir),
        _resolve_path(f"lexicons/{profile_name}.txt", base_dir),
    ]


def resolve_profile(
    config_dir: str | None = None, profile_override: str | None = None
) -> ProfileConfig:
    app_config = load_app_config(config_dir)
    base_dir = _base_dir(config_dir)
    active = profile_override or app_config.get("active_profile")
    if not active:
        raise ValueError("active_profile is not set in app.yaml")
    profiles = app_config.get("profiles", {})
    if active not in profiles:
        raise KeyError(f"profile not found: {active}")
    profile_data = profiles[active]

    rag_db_path = _resolve_path(
        profile_data.get("rag_db_path", f"data/{active}/rag.sqlite"), base_dir
    )
    logs_dir = _resolve_path(profile_data.get("logs_dir", f"logs/{active}"), base_dir)
    lexicon_files = [
        _resolve_path(path, base_dir) for path in profile_data.get("lexicon_files", [])
    ] or _default_lexicon_files(active, base_dir)

    return ProfileConfig(
        name=active,
        rag_db_path=rag_db_path,
        logs_dir=logs_dir,
        lexicon_files=lexicon_files,
        cloud_send=str(profile_data.get("cloud_send", "raw")),
        allow_raw_on_confirm=bool(profile_data.get("allow_raw_on_confirm", True)),
        conflict_confirm=bool(profile_data.get("conflict_confirm", False)),
        vector_store_content=str(profile_data.get("vector_store_content", "raw")),
    )


def update_active_profile(config_dir: str | None, profile_name: str) -> None:
    config_path = get_config_dir(config_dir) / "app.yaml"
    config = load_app_config(config_dir)
    profiles = config.get("profiles", {})
    if profile_name not in profiles:
        raise KeyError(f"profile not found: {profile_name}")
    config["active_profile"] = profile_name
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=False)
