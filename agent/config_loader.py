from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def get_config_dir(explicit_dir: str | None = None) -> Path:
    if explicit_dir:
        return Path(explicit_dir)
    return Path(__file__).resolve().parents[1] / "config"


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a mapping: {path}")
    return data


def save_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=False)


def load_models_config(explicit_dir: str | None = None) -> Dict[str, Any]:
    config_dir = get_config_dir(explicit_dir)
    return load_yaml(config_dir / "models.yaml")


def load_policy_config(explicit_dir: str | None = None) -> Dict[str, Any]:
    config_dir = get_config_dir(explicit_dir)
    return load_yaml(config_dir / "policy.yaml")


def load_rag_config(explicit_dir: str | None = None) -> Dict[str, Any]:
    config_dir = get_config_dir(explicit_dir)
    return load_yaml(config_dir / "rag.yaml")


def load_app_config(explicit_dir: str | None = None) -> Dict[str, Any]:
    config_dir = get_config_dir(explicit_dir)
    return load_yaml(config_dir / "app.yaml")


def load_office_config(explicit_dir: str | None = None) -> Dict[str, Any]:
    config_dir = get_config_dir(explicit_dir)
    return load_yaml(config_dir / "office.yaml")


def load_behavior_config(explicit_dir: str | None = None) -> Dict[str, Any]:
    config_dir = get_config_dir(explicit_dir)
    path = config_dir / "behavior.yaml"
    if not path.exists():
        return {}
    return load_yaml(path)
