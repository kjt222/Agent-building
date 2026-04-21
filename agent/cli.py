from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from .config_loader import (
    load_app_config,
    load_models_config,
    load_office_config,
    load_policy_config,
    load_rag_config,
)
from .behavior import resolve_behavior, build_llm_kwargs
from .desktop import run_desktop
from .init_setup import init_app
from .logging_utils import log_event
from .models import default_registry
from .office import ExcelComEditor, OfficeComError, WordComEditor
from .planner import (
    extract_docx_preview,
    extract_xlsx_preview,
    generate_docx_plan,
    generate_xlsx_plan,
    validate_docx_plan,
    validate_xlsx_plan,
    write_plan,
)
from .policy import PolicyEngine
from .privacy import load_lexicons, mask_text
from .profile import resolve_profile, update_active_profile
from .rag import RagService, SqliteVectorStore, answer_question
from .rag.service import RagConfig
from .rag.watcher import watch_path
from .tools import XlsxEditor, apply_docx_ops, create_snapshot, list_snapshots, restore_snapshot

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


def cmd_models(args: argparse.Namespace) -> int:
    models_config = load_models_config(args.config_dir)
    profile = resolve_profile(args.config_dir, args.profile)
    if "profiles" in models_config:
        profile_cfg = models_config.get("profiles", {}).get(profile.name, {})
    else:
        profile_cfg = models_config
    llm_active = profile_cfg.get("llm", {}).get("active", "unset")
    emb_active = profile_cfg.get("embedding", {}).get("active", "unset")
    print(f"profile={profile.name}")
    print(f"llm.active={llm_active}")
    print(f"embedding.active={emb_active}")
    return 0


def cmd_policy_check(args: argparse.Namespace) -> int:
    policy_config = load_policy_config(args.config_dir)
    engine = PolicyEngine(policy_config)
    decision = engine.check(args.action)
    print(f"action={args.action}")
    print(f"decision={decision.status}")
    if decision.rule:
        print(f"rule={decision.rule}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    models_config = load_models_config(args.config_dir)
    registry = default_registry()

    if "profiles" in models_config:
        profiles = models_config.get("profiles", {})
        for profile_name, profile_cfg in profiles.items():
            for section in ("llm", "embedding"):
                if section not in profile_cfg:
                    print(f"missing section: {profile_name}.{section}", file=sys.stderr)
                    return 1
                active = profile_cfg[section].get("active")
                providers = profile_cfg[section].get("providers", {})
                if not active:
                    print(f"missing active provider: {profile_name}.{section}", file=sys.stderr)
                    return 1
                if active not in providers:
                    print(f"missing provider config: {profile_name}.{section}.{active}", file=sys.stderr)
                    return 1
                try:
                    provider_type = providers[active].get("type", active)
                    registry.create(provider_type, providers[active], provider_id=active)
                except KeyError:
                    print(f"unknown provider: {profile_name}.{active}", file=sys.stderr)
                    return 1
    else:
        for section in ("llm", "embedding"):
            if section not in models_config:
                print(f"missing section: {section}", file=sys.stderr)
                return 1
            active = models_config[section].get("active")
            providers = models_config[section].get("providers", {})
            if not active:
                print(f"missing active provider: {section}", file=sys.stderr)
                return 1
            if active not in providers:
                print(f"missing provider config: {section}.{active}", file=sys.stderr)
                return 1
            try:
                provider_type = providers[active].get("type", active)
                registry.create(provider_type, providers[active], provider_id=active)
            except KeyError:
                print(f"unknown provider: {active}", file=sys.stderr)
                return 1
    try:
        app_config = load_app_config(args.config_dir)
        active = app_config.get("active_profile")
        profiles = app_config.get("profiles", {})
        if not active or active not in profiles:
            print("invalid active_profile in app.yaml", file=sys.stderr)
            return 1
    except FileNotFoundError:
        print("missing app.yaml", file=sys.stderr)
        return 1
    print("config ok")
    return 0


def _build_rag_config(config_dir: str | None, profile_name: str | None) -> RagConfig:
    rag_config = load_rag_config(config_dir)
    index_cfg = rag_config.get("index", {})
    profile = resolve_profile(config_dir, profile_name)
    db_path = profile.rag_db_path

    return RagConfig(
        db_path=db_path,
        chunk_size=int(index_cfg.get("chunk_size", 800)),
        chunk_overlap=int(index_cfg.get("chunk_overlap", 150)),
        top_k=int(index_cfg.get("top_k", 6)),
        score_threshold=float(index_cfg.get("score_threshold", 0.2)),
        max_context_chars=int(index_cfg.get("max_context_chars", 8000)),
        extensions=tuple(index_cfg.get("extensions", [".txt", ".pdf", ".docx", ".xlsx"])),
    )


def _build_rag_service(config_dir: str | None, profile_name: str | None) -> RagService:
    models_config = load_models_config(config_dir)
    registry = default_registry()
    embedder = registry.create_from_config(models_config, "embedding", profile=profile_name)
    config = _build_rag_config(config_dir, profile_name)
    store = SqliteVectorStore(config.db_path)
    return RagService(embedder=embedder, store=store, config=config)


def cmd_rag_index(args: argparse.Namespace) -> int:
    profile = resolve_profile(args.config_dir, args.profile)
    service = _build_rag_service(args.config_dir, profile.name)
    target = Path(args.path)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    indexed = service.index_path(target, force=args.force)
    print(f"indexed_files={indexed}")
    log_event(
        profile.logs_dir,
        {
            "action": "rag_index",
            "profile": profile.name,
            "path": str(target),
            "indexed_files": indexed,
            "status": "ok",
        },
    )
    return 0


def cmd_rag_query(args: argparse.Namespace) -> int:
    profile = resolve_profile(args.config_dir, args.profile)
    service = _build_rag_service(args.config_dir, profile.name)
    results = service.query(args.text, top_k=args.top_k)
    for idx, item in enumerate(results, start=1):
        snippet = item.text[:200].replace("\n", " ")
        source = item.metadata.get("source_path", "")
        print(f"{idx}. score={item.score:.3f} source={source}")
        print(f"   {snippet}")
    if not results:
        print("no results")
    log_event(
        profile.logs_dir,
        {
            "action": "rag_query",
            "profile": profile.name,
            "results": len(results),
            "status": "ok",
        },
    )
    return 0


def cmd_rag_ask(args: argparse.Namespace) -> int:
    models_config = load_models_config(args.config_dir)
    registry = default_registry()

    profile = resolve_profile(args.config_dir, args.profile)
    llm = registry.create_from_config(models_config, "llm", profile=profile.name)
    behavior = resolve_behavior(args.config_dir, profile.name, llm.provider, llm.model)
    llm_kwargs = build_llm_kwargs(behavior)
    service = _build_rag_service(args.config_dir, profile.name)
    rag_config = service.config
    results = service.query(args.question, top_k=args.top_k)
    mask_fn = None
    send_mode = "raw"
    if profile.cloud_send == "masked":
        if args.confirm_raw and profile.allow_raw_on_confirm:
            send_mode = "raw"
        else:
            lexicon = load_lexicons(profile.lexicon_files)
            mask_fn = lambda text: mask_text(text, lexicon)
            send_mode = "masked"
    answer = answer_question(
        llm=llm,
        question=args.question,
        results=results,
        max_context_chars=rag_config.max_context_chars,
        allow_empty=args.allow_empty,
        mask_fn=mask_fn,
        llm_kwargs=llm_kwargs,
    )
    if not results:
        print("no results")
    print(answer)
    log_event(
        profile.logs_dir,
        {
            "action": "rag_ask",
            "profile": profile.name,
            "results": len(results),
            "send_mode": send_mode,
            "status": "ok",
        },
    )
    return 0


def cmd_rag_watch(args: argparse.Namespace) -> int:
    profile = resolve_profile(args.config_dir, args.profile)
    service = _build_rag_service(args.config_dir, profile.name)
    target = Path(args.path)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    observer = watch_path(service, target)
    log_event(
        profile.logs_dir,
        {"action": "rag_watch", "profile": profile.name, "path": str(target), "status": "started"},
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    log_event(
        profile.logs_dir,
        {"action": "rag_watch", "profile": profile.name, "path": str(target), "status": "stopped"},
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    created = init_app(args.config_dir, force=args.force, active_profile=args.profile)
    if created:
        print("created:")
        for path in created:
            print(f"- {path}")
    else:
        print("already initialized")
    return 0


def cmd_profile_list(args: argparse.Namespace) -> int:
    app_config = load_app_config(args.config_dir)
    active = app_config.get("active_profile", "unset")
    profiles = sorted(app_config.get("profiles", {}).keys())
    print(f"active={active}")
    for name in profiles:
        print(f"- {name}")
    return 0


def cmd_profile_set(args: argparse.Namespace) -> int:
    update_active_profile(args.config_dir, args.name)
    print(f"active_profile={args.name}")
    return 0


def _load_plan(path: str) -> dict:
    plan_path = Path(path)
    if not plan_path.exists():
        raise FileNotFoundError(plan_path)
    if plan_path.suffix.lower() == ".json":
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("plan file must be a mapping")
    return data


def _resolve_plan_file(plan_path: Path, file_value: str) -> Path:
    file_path = Path(file_value)
    if not file_path.is_absolute():
        return plan_path.parent / file_path
    return file_path


def _policy_check(
    engine: PolicyEngine, actions: list[tuple[str, str]], confirm: bool
) -> int:
    denied = []
    needs_confirm = []
    for action, detail in actions:
        decision = engine.check(action)
        if decision.status == "deny":
            denied.append((action, detail, decision.rule))
        elif decision.needs_confirmation:
            needs_confirm.append((action, detail, decision.rule))
    if denied:
        print("policy denied:", file=sys.stderr)
        for action, detail, rule in denied:
            suffix = f" (rule={rule})" if rule else ""
            print(f"- {action} {detail}{suffix}", file=sys.stderr)
        return 1
    if needs_confirm and not confirm:
        print("confirmation required:", file=sys.stderr)
        for action, detail, rule in needs_confirm:
            suffix = f" (rule={rule})" if rule else ""
            print(f"- {action} {detail}{suffix}", file=sys.stderr)
        return 2
    return 0


def _resolve_mask(profile, confirm_raw: bool):
    if profile.cloud_send != "masked":
        return None, "raw"
    if confirm_raw and profile.allow_raw_on_confirm:
        return None, "raw"
    lexicon = load_lexicons(profile.lexicon_files)
    return lambda text: mask_text(text, lexicon), "masked"


def _apply_docx_plan(
    args: argparse.Namespace,
    plan: dict,
    plan_path: Path,
    action_name: str = "docx_apply",
) -> int:
    file_value = plan.get("file")
    if not file_value:
        print("plan missing file", file=sys.stderr)
        return 1
    ops = plan.get("ops", [])
    if not isinstance(ops, list) or not ops:
        print("plan ops must be a non-empty list", file=sys.stderr)
        return 1
    target = _resolve_plan_file(plan_path, str(file_value))
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    policy_config = load_policy_config(args.config_dir)
    engine = PolicyEngine(policy_config)
    status = _policy_check(engine, [("tool.docx_edit", str(target))], args.confirm)
    if status:
        return status
    snapshot = create_snapshot(target, note=action_name)
    result = apply_docx_ops(target, ops)
    profile = resolve_profile(args.config_dir, args.profile)
    log_event(
        profile.logs_dir,
        {
            "action": action_name,
            "profile": profile.name,
            "file": str(target),
            "snapshot": snapshot.snapshot_id,
            "replacements": result.replacements,
            "appended": result.appended,
            "headings": result.headings,
            "cross_run_merges": result.cross_run_merges,
            "status": "ok",
        },
    )
    print(f"replacements={result.replacements}")
    print(f"appended={result.appended}")
    print(f"headings={result.headings}")
    if result.cross_run_merges:
        print(f"cross_run_merges={result.cross_run_merges}")
    return 0


def cmd_docx_apply(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = _load_plan(args.plan)
    validate_docx_plan(plan)
    return _apply_docx_plan(args, plan, plan_path, action_name="docx_apply")


def _apply_xlsx_plan(
    args: argparse.Namespace,
    plan: dict,
    plan_path: Path,
    action_name: str = "xlsx_apply",
) -> int:
    file_value = plan.get("file")
    if not file_value:
        print("plan missing file", file=sys.stderr)
        return 1
    ops = plan.get("ops", [])
    if not isinstance(ops, list) or not ops:
        print("plan ops must be a non-empty list", file=sys.stderr)
        return 1
    target = _resolve_plan_file(plan_path, str(file_value))
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    editor = XlsxEditor(target)
    default_sheet = plan.get("sheet")
    planned = editor.classify_ops(ops, default_sheet=default_sheet)
    policy_config = load_policy_config(args.config_dir)
    engine = PolicyEngine(policy_config)
    action_pairs = [(entry.action, entry.detail) for entry in planned]
    status = _policy_check(engine, action_pairs, args.confirm)
    if status:
        return status
    snapshot = create_snapshot(target, note=action_name)
    result = editor.apply_ops(ops, default_sheet=default_sheet)
    editor.save()
    profile = resolve_profile(args.config_dir, args.profile)
    log_event(
        profile.logs_dir,
        {
            "action": action_name,
            "profile": profile.name,
            "file": str(target),
            "snapshot": snapshot.snapshot_id,
            "set_cells": result.set_cells,
            "formula_cells": result.formula_cells,
            "inserted_columns": result.inserted_columns,
            "deleted_columns": result.deleted_columns,
            "inserted_rows": result.inserted_rows,
            "deleted_rows": result.deleted_rows,
            "filters_set": result.filters_set,
            "sorted_ranges": result.sorted_ranges,
            "status": "ok",
        },
    )
    print(f"set_cells={result.set_cells}")
    print(f"formula_cells={result.formula_cells}")
    print(f"inserted_columns={result.inserted_columns}")
    print(f"deleted_columns={result.deleted_columns}")
    print(f"inserted_rows={result.inserted_rows}")
    print(f"deleted_rows={result.deleted_rows}")
    print(f"filters_set={result.filters_set}")
    print(f"sorted_ranges={result.sorted_ranges}")
    return 0


def cmd_xlsx_apply(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = _load_plan(args.plan)
    validate_xlsx_plan(plan)
    return _apply_xlsx_plan(args, plan, plan_path, action_name="xlsx_apply")


def _default_plan_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".plan.yaml")


def cmd_docx_plan(args: argparse.Namespace) -> int:
    target = Path(args.file)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    preview = extract_docx_preview(target, max_paragraphs=args.max_paragraphs, max_chars=args.max_chars)
    profile = resolve_profile(args.config_dir, args.profile)
    mask_fn, send_mode = _resolve_mask(profile, args.confirm_raw)
    if mask_fn:
        preview = mask_fn(preview)
    models_config = load_models_config(args.config_dir)
    llm = default_registry().create_from_config(models_config, "llm", profile=profile.name)
    plan = generate_docx_plan(llm, args.instruction, preview, target)
    output_path = Path(args.out) if args.out else _default_plan_path(target)
    write_plan(plan, output_path)
    log_event(
        profile.logs_dir,
        {
            "action": "docx_plan",
            "profile": profile.name,
            "file": str(target),
            "output": str(output_path),
            "send_mode": send_mode,
            "status": "ok",
        },
    )
    print(f"plan_written={output_path}")
    return 0


def cmd_docx_auto(args: argparse.Namespace) -> int:
    target = Path(args.file)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    preview = extract_docx_preview(target, max_paragraphs=args.max_paragraphs, max_chars=args.max_chars)
    profile = resolve_profile(args.config_dir, args.profile)
    mask_fn, send_mode = _resolve_mask(profile, args.confirm_raw)
    if mask_fn:
        preview = mask_fn(preview)
    models_config = load_models_config(args.config_dir)
    llm = default_registry().create_from_config(models_config, "llm", profile=profile.name)
    plan = generate_docx_plan(llm, args.instruction, preview, target)
    output_path = Path(args.out) if args.out else _default_plan_path(target)
    write_plan(plan, output_path)
    log_event(
        profile.logs_dir,
        {
            "action": "docx_auto_plan",
            "profile": profile.name,
            "file": str(target),
            "output": str(output_path),
            "send_mode": send_mode,
            "status": "ok",
        },
    )
    return _apply_docx_plan(args, plan, output_path, action_name="docx_auto")


def cmd_xlsx_plan(args: argparse.Namespace) -> int:
    target = Path(args.file)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    preview = extract_xlsx_preview(
        target,
        max_rows=args.max_rows,
        max_cols=args.max_cols,
        max_sheets=args.max_sheets,
        max_chars=args.max_chars,
    )
    profile = resolve_profile(args.config_dir, args.profile)
    mask_fn, send_mode = _resolve_mask(profile, args.confirm_raw)
    if mask_fn:
        preview = mask_fn(preview)
    models_config = load_models_config(args.config_dir)
    llm = default_registry().create_from_config(models_config, "llm", profile=profile.name)
    plan = generate_xlsx_plan(llm, args.instruction, preview, target)
    output_path = Path(args.out) if args.out else _default_plan_path(target)
    write_plan(plan, output_path)
    log_event(
        profile.logs_dir,
        {
            "action": "xlsx_plan",
            "profile": profile.name,
            "file": str(target),
            "output": str(output_path),
            "send_mode": send_mode,
            "status": "ok",
        },
    )
    print(f"plan_written={output_path}")
    return 0


def cmd_xlsx_auto(args: argparse.Namespace) -> int:
    target = Path(args.file)
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    preview = extract_xlsx_preview(
        target,
        max_rows=args.max_rows,
        max_cols=args.max_cols,
        max_sheets=args.max_sheets,
        max_chars=args.max_chars,
    )
    profile = resolve_profile(args.config_dir, args.profile)
    mask_fn, send_mode = _resolve_mask(profile, args.confirm_raw)
    if mask_fn:
        preview = mask_fn(preview)
    models_config = load_models_config(args.config_dir)
    llm = default_registry().create_from_config(models_config, "llm", profile=profile.name)
    plan = generate_xlsx_plan(llm, args.instruction, preview, target)
    output_path = Path(args.out) if args.out else _default_plan_path(target)
    write_plan(plan, output_path)
    log_event(
        profile.logs_dir,
        {
            "action": "xlsx_auto_plan",
            "profile": profile.name,
            "file": str(target),
            "output": str(output_path),
            "send_mode": send_mode,
            "status": "ok",
        },
    )
    return _apply_xlsx_plan(args, plan, output_path, action_name="xlsx_auto")


def cmd_office_word_apply(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = _load_plan(args.plan)
    validate_docx_plan(plan)
    file_value = plan.get("file")
    if not file_value:
        print("plan missing file", file=sys.stderr)
        return 1
    target = _resolve_plan_file(plan_path, str(file_value))
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    policy_config = load_policy_config(args.config_dir)
    engine = PolicyEngine(policy_config)
    status = _policy_check(engine, [("tool.ui_word_edit", str(target))], args.confirm)
    if status:
        return status
    snapshot = create_snapshot(target, note="office_word_apply")
    office_config = load_office_config(args.config_dir)
    if office_config.get("backend") != "com":
        print("office backend is not COM", file=sys.stderr)
        return 1
    editor = None
    try:
        editor = WordComEditor(target, office_config)
        result = editor.apply_ops(plan.get("ops", []))
    except OfficeComError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if editor:
            editor.close(save=True)
    profile = resolve_profile(args.config_dir, args.profile)
    log_event(
        profile.logs_dir,
        {
            "action": "office_word_apply",
            "profile": profile.name,
            "file": str(target),
            "snapshot": snapshot.snapshot_id,
            "replacements": result.replacements,
            "appended": result.appended,
            "headings": result.headings,
            "status": "ok",
        },
    )
    print(f"replacements={result.replacements}")
    print(f"appended={result.appended}")
    print(f"headings={result.headings}")
    return 0


def cmd_office_excel_apply(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = _load_plan(args.plan)
    validate_xlsx_plan(plan)
    file_value = plan.get("file")
    if not file_value:
        print("plan missing file", file=sys.stderr)
        return 1
    target = _resolve_plan_file(plan_path, str(file_value))
    if not target.exists():
        print(f"path not found: {target}", file=sys.stderr)
        return 1
    editor = XlsxEditor(target)
    default_sheet = plan.get("sheet")
    planned = editor.classify_ops(plan.get("ops", []), default_sheet=default_sheet)
    policy_config = load_policy_config(args.config_dir)
    engine = PolicyEngine(policy_config)
    action_pairs = [(entry.action, entry.detail) for entry in planned]
    status = _policy_check(engine, action_pairs, args.confirm)
    if status:
        return status
    snapshot = create_snapshot(target, note="office_excel_apply")
    office_config = load_office_config(args.config_dir)
    if office_config.get("backend") != "com":
        print("office backend is not COM", file=sys.stderr)
        return 1
    com_editor = None
    try:
        com_editor = ExcelComEditor(target, office_config)
        result = com_editor.apply_ops(plan.get("ops", []), default_sheet=default_sheet)
    except OfficeComError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if com_editor:
            com_editor.close(save=True)
    profile = resolve_profile(args.config_dir, args.profile)
    log_event(
        profile.logs_dir,
        {
            "action": "office_excel_apply",
            "profile": profile.name,
            "file": str(target),
            "snapshot": snapshot.snapshot_id,
            "set_cells": result.set_cells,
            "formula_cells": result.formula_cells,
            "inserted_columns": result.inserted_columns,
            "deleted_columns": result.deleted_columns,
            "inserted_rows": result.inserted_rows,
            "deleted_rows": result.deleted_rows,
            "filters_set": result.filters_set,
            "sorted_ranges": result.sorted_ranges,
            "status": "ok",
        },
    )
    print(f"set_cells={result.set_cells}")
    print(f"formula_cells={result.formula_cells}")
    print(f"inserted_columns={result.inserted_columns}")
    print(f"deleted_columns={result.deleted_columns}")
    print(f"inserted_rows={result.inserted_rows}")
    print(f"deleted_rows={result.deleted_rows}")
    print(f"filters_set={result.filters_set}")
    print(f"sorted_ranges={result.sorted_ranges}")
    return 0


def cmd_snapshot_list(args: argparse.Namespace) -> int:
    target = Path(args.file)
    snapshots = list_snapshots(target)
    if not snapshots:
        print("no snapshots")
        return 0
    for entry in snapshots:
        note = f" note={entry.note}" if entry.note else ""
        print(f"{entry.snapshot_id} {entry.created_at} {entry.file_path}{note}")
    return 0


def cmd_snapshot_restore(args: argparse.Namespace) -> int:
    target = Path(args.file)
    policy_config = load_policy_config(args.config_dir)
    engine = PolicyEngine(policy_config)
    status = _policy_check(engine, [("tool.fs_overwrite", str(target))], args.confirm)
    if status:
        return status
    restored = restore_snapshot(target, args.id)
    profile = resolve_profile(args.config_dir, args.profile)
    log_event(
        profile.logs_dir,
        {
            "action": "snapshot_restore",
            "profile": profile.name,
            "file": str(target),
            "snapshot": args.id,
            "status": "ok",
        },
    )
    print(f"restored={restored}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui import run

    run(host=args.host, port=args.port, config_dir=args.config_dir)
    return 0


def cmd_desktop(args: argparse.Namespace) -> int:
    run_desktop(host=args.host, port=args.port, config_dir=args.config_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent CLI")
    parser.add_argument(
        "--config-dir",
        help="Optional config directory (default: ./config)",
        default=None,
    )
    parser.add_argument("--profile", help="Override active profile", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    models_parser = subparsers.add_parser("models", help="Show active model providers")
    models_parser.set_defaults(func=cmd_models)

    policy_parser = subparsers.add_parser("policy", help="Policy operations")
    policy_sub = policy_parser.add_subparsers(dest="policy_cmd", required=True)
    policy_check = policy_sub.add_parser("check", help="Check a policy decision")
    policy_check.add_argument("--action", required=True, help="Action id to check")
    policy_check.set_defaults(func=cmd_policy_check)

    validate_parser = subparsers.add_parser("validate", help="Validate config")
    validate_parser.set_defaults(func=cmd_validate)

    init_parser = subparsers.add_parser("init", help="Initialize config and lexicons")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    init_parser.set_defaults(func=cmd_init)

    profile_parser = subparsers.add_parser("profile", help="Profile operations")
    profile_sub = profile_parser.add_subparsers(dest="profile_cmd", required=True)
    profile_list = profile_sub.add_parser("list", help="List profiles")
    profile_list.set_defaults(func=cmd_profile_list)
    profile_set = profile_sub.add_parser("set", help="Set active profile")
    profile_set.add_argument("--name", required=True, help="Profile name")
    profile_set.set_defaults(func=cmd_profile_set)

    rag_parser = subparsers.add_parser("rag", help="RAG operations")
    rag_sub = rag_parser.add_subparsers(dest="rag_cmd", required=True)
    rag_index = rag_sub.add_parser("index", help="Index files into the RAG store")
    rag_index.add_argument("--path", required=True, help="File or folder path to index")
    rag_index.add_argument("--force", action="store_true", help="Force reindex")
    rag_index.set_defaults(func=cmd_rag_index)

    rag_query = rag_sub.add_parser("query", help="Query the RAG store")
    rag_query.add_argument("--text", required=True, help="Query text")
    rag_query.add_argument("--top-k", type=int, default=None, help="Override top-k")
    rag_query.set_defaults(func=cmd_rag_query)

    rag_ask = rag_sub.add_parser("ask", help="Ask with RAG context")
    rag_ask.add_argument("--question", required=True, help="Question to ask")
    rag_ask.add_argument("--top-k", type=int, default=None, help="Override top-k")
    rag_ask.add_argument(
        "--confirm-raw",
        action="store_true",
        help="Allow sending raw context when profile is masked",
    )
    rag_ask.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow LLM answers even when no context is found",
    )
    rag_ask.set_defaults(func=cmd_rag_ask)

    rag_watch = rag_sub.add_parser("watch", help="Watch a path and index changes")
    rag_watch.add_argument("--path", required=True, help="File or folder path to watch")
    rag_watch.set_defaults(func=cmd_rag_watch)

    docx_parser = subparsers.add_parser("docx", help="Docx operations")
    docx_sub = docx_parser.add_subparsers(dest="docx_cmd", required=True)
    docx_plan = docx_sub.add_parser("plan", help="Generate docx edit plan")
    docx_plan.add_argument("--file", required=True, help="Target docx file")
    docx_plan.add_argument("--instruction", required=True, help="Edit instruction")
    docx_plan.add_argument("--out", default=None, help="Output plan file path")
    docx_plan.add_argument(
        "--confirm-raw",
        action="store_true",
        help="Allow sending raw preview when profile is masked",
    )
    docx_plan.add_argument("--max-paragraphs", type=int, default=200, help="Preview paragraphs")
    docx_plan.add_argument("--max-chars", type=int, default=12000, help="Preview max chars")
    docx_plan.set_defaults(func=cmd_docx_plan)

    docx_auto = docx_sub.add_parser("auto", help="Plan and apply docx edits")
    docx_auto.add_argument("--file", required=True, help="Target docx file")
    docx_auto.add_argument("--instruction", required=True, help="Edit instruction")
    docx_auto.add_argument("--out", default=None, help="Output plan file path")
    docx_auto.add_argument(
        "--confirm-raw",
        action="store_true",
        help="Allow sending raw preview when profile is masked",
    )
    docx_auto.add_argument("--max-paragraphs", type=int, default=200, help="Preview paragraphs")
    docx_auto.add_argument("--max-chars", type=int, default=12000, help="Preview max chars")
    docx_auto.add_argument(
        "--confirm",
        action="store_true",
        help="Allow operations requiring confirmation",
    )
    docx_auto.set_defaults(func=cmd_docx_auto)

    docx_apply = docx_sub.add_parser("apply", help="Apply docx edit plan")
    docx_apply.add_argument("--plan", required=True, help="Plan file (.yaml/.json)")
    docx_apply.add_argument(
        "--confirm",
        action="store_true",
        help="Allow operations requiring confirmation",
    )
    docx_apply.set_defaults(func=cmd_docx_apply)

    xlsx_parser = subparsers.add_parser("xlsx", help="Xlsx operations")
    xlsx_sub = xlsx_parser.add_subparsers(dest="xlsx_cmd", required=True)
    xlsx_plan = xlsx_sub.add_parser("plan", help="Generate xlsx edit plan")
    xlsx_plan.add_argument("--file", required=True, help="Target xlsx file")
    xlsx_plan.add_argument("--instruction", required=True, help="Edit instruction")
    xlsx_plan.add_argument("--out", default=None, help="Output plan file path")
    xlsx_plan.add_argument(
        "--confirm-raw",
        action="store_true",
        help="Allow sending raw preview when profile is masked",
    )
    xlsx_plan.add_argument("--max-rows", type=int, default=20, help="Preview rows per sheet")
    xlsx_plan.add_argument("--max-cols", type=int, default=10, help="Preview columns per sheet")
    xlsx_plan.add_argument("--max-sheets", type=int, default=3, help="Preview sheets")
    xlsx_plan.add_argument("--max-chars", type=int, default=12000, help="Preview max chars")
    xlsx_plan.set_defaults(func=cmd_xlsx_plan)

    xlsx_auto = xlsx_sub.add_parser("auto", help="Plan and apply xlsx edits")
    xlsx_auto.add_argument("--file", required=True, help="Target xlsx file")
    xlsx_auto.add_argument("--instruction", required=True, help="Edit instruction")
    xlsx_auto.add_argument("--out", default=None, help="Output plan file path")
    xlsx_auto.add_argument(
        "--confirm-raw",
        action="store_true",
        help="Allow sending raw preview when profile is masked",
    )
    xlsx_auto.add_argument("--max-rows", type=int, default=20, help="Preview rows per sheet")
    xlsx_auto.add_argument("--max-cols", type=int, default=10, help="Preview columns per sheet")
    xlsx_auto.add_argument("--max-sheets", type=int, default=3, help="Preview sheets")
    xlsx_auto.add_argument("--max-chars", type=int, default=12000, help="Preview max chars")
    xlsx_auto.add_argument(
        "--confirm",
        action="store_true",
        help="Allow operations requiring confirmation",
    )
    xlsx_auto.set_defaults(func=cmd_xlsx_auto)

    xlsx_apply = xlsx_sub.add_parser("apply", help="Apply xlsx edit plan")
    xlsx_apply.add_argument("--plan", required=True, help="Plan file (.yaml/.json)")
    xlsx_apply.add_argument(
        "--confirm",
        action="store_true",
        help="Allow operations requiring confirmation",
    )
    xlsx_apply.set_defaults(func=cmd_xlsx_apply)

    snapshot_parser = subparsers.add_parser("snapshot", help="Snapshot operations")
    snapshot_sub = snapshot_parser.add_subparsers(dest="snapshot_cmd", required=True)
    snapshot_list = snapshot_sub.add_parser("list", help="List snapshots for a file")
    snapshot_list.add_argument("--file", required=True, help="Target file")
    snapshot_list.set_defaults(func=cmd_snapshot_list)
    snapshot_restore = snapshot_sub.add_parser("restore", help="Restore a snapshot")
    snapshot_restore.add_argument("--file", required=True, help="Target file")
    snapshot_restore.add_argument("--id", required=True, help="Snapshot id or path")
    snapshot_restore.add_argument(
        "--confirm",
        action="store_true",
        help="Allow operations requiring confirmation",
    )
    snapshot_restore.set_defaults(func=cmd_snapshot_restore)

    ui_parser = subparsers.add_parser("ui", help="Launch local configuration UI")
    ui_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    ui_parser.add_argument("--port", type=int, default=8686, help="Bind port")
    ui_parser.set_defaults(func=cmd_ui)

    desktop_parser = subparsers.add_parser("desktop", help="Launch desktop UI shell")
    desktop_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    desktop_parser.add_argument("--port", type=int, default=8686, help="Bind port")
    desktop_parser.set_defaults(func=cmd_desktop)

    office_parser = subparsers.add_parser("office", help="Office/WPS automation")
    office_sub = office_parser.add_subparsers(dest="office_cmd", required=True)
    office_word = office_sub.add_parser("word", help="Word/WPS automation")
    office_word_sub = office_word.add_subparsers(dest="word_cmd", required=True)
    office_word_apply = office_word_sub.add_parser("apply", help="Apply plan via COM")
    office_word_apply.add_argument("--plan", required=True, help="Plan file (.yaml/.json)")
    office_word_apply.add_argument(
        "--confirm",
        action="store_true",
        help="Allow operations requiring confirmation",
    )
    office_word_apply.set_defaults(func=cmd_office_word_apply)

    office_excel = office_sub.add_parser("excel", help="Excel/WPS automation")
    office_excel_sub = office_excel.add_subparsers(dest="excel_cmd", required=True)
    office_excel_apply = office_excel_sub.add_parser("apply", help="Apply plan via COM")
    office_excel_apply.add_argument("--plan", required=True, help="Plan file (.yaml/.json)")
    office_excel_apply.add_argument(
        "--confirm",
        action="store_true",
        help="Allow operations requiring confirmation",
    )
    office_excel_apply.set_defaults(func=cmd_office_excel_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    if load_dotenv:
        load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
