"""Excalidraw L2 oracle (P14.2.2).

Probes that catch the failure modes observed in P13.2.x smokes:

  1. Every element with a `customData.latex_source` should be **renderable**
     in *some* path — either the Obsidian-katex path (latex_source is enough)
     or the matplotlib SVG path (fileId → files{}[fileId].dataURL non-empty).
     Both empty = the model wrote a string but no rendering will happen.

  2. Multi-element LaTeX insertions must be **grouped**. P13.2.3 round 12 left
     15 elements ungrouped so the red frame floated free of its contents.
     "Grouped" = all latex elements share a non-empty groupId, OR all latex
     elements live inside the same frame element (frameId references a
     frame). If neither holds → group_state = "ungrouped".

  3. Pairwise overlap > 80% between two non-frame elements indicates a
     visual mess (text on top of text, image on top of image). Frames are
     allowed to overlap their children. Report findings, don't fail (the
     oracle warns rather than fails so a slightly-overlapping artifact still
     completes the smoke).

  4. Orphan fileId — element references files{}[fileId] that doesn't exist.

Output verdict:
  - "fail"  if any latex element is unrenderable (rule 1 violated)
  - "warn"  if grouping missing OR overlap detected (rules 2/3)
  - "pass"  otherwise
  - "unknown" if scene won't parse at all

`findings` are written in the imperative voice ("group all latex elements
into one frame"), suitable for direct injection into the agent's next
iteration prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..excalidraw_io import load_excalidraw
from ..oracle import OracleReport, register_oracle


_OVERLAP_THRESHOLD = 0.80


def _bbox(el: dict) -> tuple[float, float, float, float] | None:
    x, y = el.get("x"), el.get("y")
    w, h = el.get("width"), el.get("height")
    if any(v is None for v in (x, y, w, h)):
        return None
    try:
        return float(x), float(y), float(x) + float(w), float(y) + float(h)
    except (TypeError, ValueError):
        return None


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    iw = max(0.0, inter_x2 - inter_x1)
    ih = max(0.0, inter_y2 - inter_y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    # "Overlap fraction" of the smaller element (not classical IoU) — catches
    # the "text fully covered by image" case which classical IoU dilutes.
    area_a = max(0.0, (ax2 - ax1)) * max(0.0, (ay2 - ay1))
    area_b = max(0.0, (bx2 - bx1)) * max(0.0, (by2 - by1))
    smaller = min(area_a, area_b)
    if smaller <= 0:
        return 0.0
    return inter / smaller


def _check_renderability(scene: dict, findings: list[str], evidence: dict) -> bool:
    """Rule 1. Returns True if all latex elements can render in some path."""
    files = scene.get("files") or {}
    elements = scene.get("elements") or []
    latex_count = 0
    unrenderable: list[str] = []
    for el in elements:
        if el.get("type") != "image":
            continue
        if el.get("isDeleted"):
            continue
        fid = el.get("fileId")
        file_entry = files.get(fid) if isinstance(fid, str) else None
        dataurl = (file_entry or {}).get("dataURL") or ""
        has_svg = (
            dataurl.startswith("data:image/svg+xml;base64,") and len(dataurl) > 500
        )
        ls = (el.get("customData") or {}).get("latex_source")
        has_latex_source = isinstance(ls, str) and bool(ls.strip())
        if has_latex_source:
            latex_count += 1
        # 2026-06-09: LaTeX rendering is now BAKED INTO write_elements (it
        # always materializes an SVG dataURL). The old katex escape hatch —
        # "image+fileId with empty dataURL is fine because the plugin renders
        # latex_source" — was the source of the user's real broken-image
        # boxes (the plugin katex did NOT reliably fill the dataURL in their
        # vault). So an image element that declares a fileId MUST carry a
        # real SVG dataURL; latex_source no longer excuses an empty one.
        if isinstance(fid, str) and not has_svg:
            why = (
                "AND no customData.latex_source"
                if not has_latex_source
                else "(customData.latex_source alone no longer counts — the "
                "write tool bakes the SVG)"
            )
            unrenderable.append(
                f"element {el.get('id')!r}: image type with fileId={fid!r} but "
                f"files['{fid}'].dataURL is empty or not SVG (len={len(dataurl)}) "
                f"{why} — renders as a broken-image box in Obsidian"
            )
    evidence["latex_count"] = latex_count
    evidence["unrenderable_count"] = len(unrenderable)
    if unrenderable:
        findings.extend(unrenderable)
        findings.append(
            "fix: give the image element a `latex` field and let "
            "obsidian_write_excalidraw_elements render it (it populates "
            "files[fileId].dataURL with a valid SVG base64 automatically)."
        )
        return False
    return True


def _check_grouping(scene: dict, findings: list[str], evidence: dict) -> bool:
    """Rule 2. Returns True if every latex element is part of *some* group
    (or frame). The earlier "all latex must share ONE group" rule was too
    strict: when a canvas accumulates multiple panels across sessions,
    each panel can be self-contained (its own group) without sharing a
    common group across panels. The actual failure mode P13.2.3 round 12
    surfaced was "the red frame floated free of its contents" — i.e. some
    latex elements had no group at all. So flag only orphans, not the
    multi-panel case."""
    elements = scene.get("elements") or []
    latex_els = [
        el for el in elements
        if isinstance((el.get("customData") or {}).get("latex_source"), str)
    ]
    if len(latex_els) <= 1:
        evidence["group_state"] = "n/a"
        return True

    frame_ids_in_scene = {
        f.get("id") for f in elements if f.get("type") == "frame"
    }

    orphans = []
    for el in latex_els:
        gids = el.get("groupIds") or []
        fid = el.get("frameId")
        in_some_group = bool(gids)
        in_existing_frame = isinstance(fid, str) and fid in frame_ids_in_scene
        if not in_some_group and not in_existing_frame:
            orphans.append(el.get("id"))

    if not orphans:
        evidence["group_state"] = "all-grouped"
        return True

    evidence["group_state"] = "some-orphans"
    findings.append(
        f"grouping: {len(orphans)}/{len(latex_els)} latex elements are "
        f"orphans (no groupIds, no frameId): {orphans[:5]}. Each latex "
        f"element should belong to SOME group or frame so dragging one "
        f"piece moves the related elements together."
    )
    return False


def _check_overlap(scene: dict, findings: list[str], evidence: dict) -> bool:
    """Rule 3. Pairwise overlap > 80% on non-frame elements."""
    elements = scene.get("elements") or []
    candidates = []
    for el in elements:
        if el.get("type") == "frame":
            continue
        b = _bbox(el)
        if b is None:
            continue
        candidates.append((el.get("id"), b))

    overlaps: list[str] = []
    for i, (id_a, ba) in enumerate(candidates):
        for j in range(i + 1, len(candidates)):
            id_b, bb = candidates[j]
            ov = _iou(ba, bb)
            if ov >= _OVERLAP_THRESHOLD:
                overlaps.append(
                    f"overlap: {id_a!r} ↔ {id_b!r} cover {ov:.0%} of the "
                    f"smaller element"
                )
    evidence["overlap_violations"] = len(overlaps)
    if overlaps:
        findings.extend(overlaps[:10])  # cap noise
        findings.append(
            "fix: respread elements; if two latex images are at identical "
            "coordinates the user can only see one."
        )
        return False
    return True


def _check_orphan_fileids(scene: dict, findings: list[str], evidence: dict) -> bool:
    files = scene.get("files") or {}
    elements = scene.get("elements") or []
    orphans = []
    for el in elements:
        fid = el.get("fileId")
        if isinstance(fid, str) and fid and fid not in files:
            orphans.append(
                f"element {el.get('id')!r} references fileId={fid!r} but "
                f"files[{fid!r}] is missing"
            )
    evidence["orphan_fileids"] = len(orphans)
    if orphans:
        findings.extend(orphans)
        return False
    return True


class ExcalidrawOracle:
    name = "excalidraw"

    def check(
        self,
        artifact_paths: Iterable[Path],
        task_spec: dict[str, Any] | None = None,
    ) -> OracleReport:
        paths = [Path(p) for p in artifact_paths]
        if not paths:
            return OracleReport(
                oracle=self.name,
                verdict="unknown",
                findings=["no artifact paths provided"],
            )

        # Pick the first parseable scene. Multi-file scenes accumulate
        # findings.
        per_file_evidence: dict[str, dict] = {}
        all_findings: list[str] = []
        any_fail = False
        any_warn = False
        any_parseable = False

        for p in paths:
            scene, err, kind = load_excalidraw(p)
            file_ev: dict[str, Any] = {"path": str(p), "kind": kind}
            if scene is None:
                file_ev["parse_error"] = err
                per_file_evidence[str(p)] = file_ev
                continue
            any_parseable = True
            file_ev["element_count"] = len(scene.get("elements") or [])
            file_ev["file_count"] = len(scene.get("files") or {})

            rules_findings: list[str] = []
            ok_render = _check_renderability(scene, rules_findings, file_ev)
            ok_orphan = _check_orphan_fileids(scene, rules_findings, file_ev)
            ok_group = _check_grouping(scene, rules_findings, file_ev)
            ok_overlap = _check_overlap(scene, rules_findings, file_ev)

            if not (ok_render and ok_orphan):
                any_fail = True
            if not (ok_group and ok_overlap):
                any_warn = True

            if rules_findings:
                all_findings.append(f"[{Path(p).name}]")
                all_findings.extend(rules_findings)
            per_file_evidence[str(p)] = file_ev

        if not any_parseable:
            return OracleReport(
                oracle=self.name,
                verdict="unknown",
                findings=["no excalidraw artifact could be parsed"],
                evidence={"files": per_file_evidence},
            )

        if any_fail:
            verdict = "fail"
        elif any_warn:
            verdict = "warn"
        else:
            verdict = "pass"

        return OracleReport(
            oracle=self.name,
            verdict=verdict,
            findings=all_findings,
            evidence={"files": per_file_evidence},
        )


register_oracle("excalidraw", ExcalidrawOracle())
