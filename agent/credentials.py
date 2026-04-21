from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


SERVICE_NAME = "agent-building"


@dataclass(frozen=True)
class KeyStatus:
    source: str
    present: bool
    masked: str
    env_name: Optional[str] = None
    key_ref: Optional[str] = None


def _load_keyring():
    try:
        import keyring  # type: ignore
    except ImportError as exc:
        raise RuntimeError("keyring not installed. Install `keyring`.") from exc
    return keyring


def _mask(value: Optional[str]) -> str:
    if not value:
        return "missing"
    tail = value[-4:] if len(value) >= 4 else value
    return f"****{tail}"


def resolve_api_key(
    api_key_env: Optional[str] = None,
    api_key_ref: Optional[str] = None,
    prefer_env: bool = True,
) -> Optional[str]:
    env_value = os.getenv(api_key_env) if api_key_env else None
    if prefer_env and env_value:
        return env_value
    if api_key_ref:
        keyring = _load_keyring()
        stored = keyring.get_password(SERVICE_NAME, api_key_ref)
        if stored:
            return stored
    if not prefer_env and env_value:
        return env_value
    return None


def store_api_key(api_key_ref: str, value: str) -> None:
    keyring = _load_keyring()
    keyring.set_password(SERVICE_NAME, api_key_ref, value)


def delete_api_key(api_key_ref: str) -> None:
    keyring = _load_keyring()
    try:
        keyring.delete_password(SERVICE_NAME, api_key_ref)
    except keyring.errors.PasswordDeleteError:
        return


def describe_key(api_key_env: Optional[str], api_key_ref: Optional[str]) -> KeyStatus:
    env_value = os.getenv(api_key_env) if api_key_env else None
    if env_value:
        return KeyStatus(
            source="env",
            present=True,
            masked=_mask(env_value),
            env_name=api_key_env,
            key_ref=api_key_ref,
        )
    if api_key_ref:
        try:
            keyring = _load_keyring()
            stored = keyring.get_password(SERVICE_NAME, api_key_ref)
        except Exception:
            stored = None
        if stored:
            return KeyStatus(
                source="keyring",
                present=True,
                masked=_mask(stored),
                env_name=api_key_env,
                key_ref=api_key_ref,
            )
    return KeyStatus(
        source="missing",
        present=False,
        masked="missing",
        env_name=api_key_env,
        key_ref=api_key_ref,
    )
