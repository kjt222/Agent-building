from __future__ import annotations

from pathlib import Path

import yaml

from .config_loader import get_config_dir


DEFAULT_APP_CONFIG = {
    "active_profile": "research",
    "profiles": {
        "research": {
            "rag_db_path": "data/research/rag.sqlite",
            "logs_dir": "logs/research",
            "lexicon_files": [
                "lexicons/global.yaml",
                "lexicons/research.yaml",
                "lexicons/global.txt",
                "lexicons/research.txt",
            ],
            "cloud_send": "raw",
            "allow_raw_on_confirm": True,
            "conflict_confirm": False,
            "vector_store_content": "raw",
        },
        "sensitive": {
            "rag_db_path": "data/sensitive/rag.sqlite",
            "logs_dir": "logs/sensitive",
            "lexicon_files": [
                "lexicons/global.yaml",
                "lexicons/sensitive.yaml",
                "lexicons/global.txt",
                "lexicons/sensitive.txt",
            ],
            "cloud_send": "masked",
            "allow_raw_on_confirm": True,
            "conflict_confirm": False,
            "vector_store_content": "raw",
        },
    },
    "knowledge_bases": [],
    "active_kbs": [],
}

DEFAULT_GLOBAL_YAML = {
    "sensitive": [],
    "whitelist": [],
    "patterns": [
        {"name": "id_card", "regex": r"\b\d{17}[\dXx]\b"},
        {"name": "phone", "regex": r"\b1\d{10}\b"},
        {"name": "email", "regex": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"},
        {"name": "bank_card", "regex": r"\b\d{12,19}\b"},
        {"name": "tax_id", "regex": r"\b[0-9A-Z]{15,20}\b"},
        {"name": "invoice_no", "regex": r"\b\d{8,20}\b"},
    ],
}

DEFAULT_PROFILE_YAML = {"sensitive": [], "whitelist": [], "patterns": []}

DEFAULT_TXT = "# One term per line.\n"

DEFAULT_OFFICE_CONFIG = {
    "backend": "com",
    "visible": False,
    "display_alerts": False,
    "prefer": "wps",
    "apps": {
        "word": {
            "heading_styles": ["Heading {level}"],
            "wps_progids": ["Wps.Application", "KWps.Application", "Kwps.Application"],
            "office_progids": ["Word.Application"],
        },
        "excel": {
            "wps_progids": ["Et.Application", "KET.Application"],
            "office_progids": ["Excel.Application"],
        },
    },
}


def init_app(config_dir: str | None = None, force: bool = False, active_profile: str | None = None) -> list[str]:
    config_path = get_config_dir(config_dir)
    config_path.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    app_yaml = config_path / "app.yaml"
    if force or not app_yaml.exists():
        config = DEFAULT_APP_CONFIG.copy()
        if active_profile:
            config["active_profile"] = active_profile
        with app_yaml.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=False)
        created.append(str(app_yaml))

    office_yaml = config_path / "office.yaml"
    if force or not office_yaml.exists():
        with office_yaml.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(DEFAULT_OFFICE_CONFIG, handle, sort_keys=False, allow_unicode=False)
        created.append(str(office_yaml))

    root = config_path.parent
    for rel in [
        "data/research",
        "data/sensitive",
        "logs/research",
        "logs/sensitive",
        "lexicons",
    ]:
        (root / rel).mkdir(parents=True, exist_ok=True)

    lexicon_files = {
        root / "lexicons/global.yaml": DEFAULT_GLOBAL_YAML,
        root / "lexicons/research.yaml": DEFAULT_PROFILE_YAML,
        root / "lexicons/sensitive.yaml": DEFAULT_PROFILE_YAML,
    }
    for path, payload in lexicon_files.items():
        if force or not path.exists():
            with path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)
            created.append(str(path))

    for filename in ["global.txt", "research.txt", "sensitive.txt"]:
        path = root / "lexicons" / filename
        if force or not path.exists():
            path.write_text(DEFAULT_TXT, encoding="utf-8")
            created.append(str(path))

    return created
