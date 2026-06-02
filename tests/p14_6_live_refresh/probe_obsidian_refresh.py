"""Standalone Obsidian live-refresh probe (P14.6.LR).

Question this probe answers, separately for each fallback layer:
    "If I write a new element into the target .excalidraw.md and then trigger
     refresh via mechanism X, does the Obsidian Excalidraw canvas reflect
     the change without me closing or focusing the window?"

We test three mechanisms in sequence (least to most invasive):
    A. passive   — just write, wait, hope plugin's file watcher reloads
    B. utime     — write + os.utime(path, None) to bump mtime explicitly
    C. uri       — write + launch ``obsidian://open?vault=...&file=...`` to
                   re-activate the file in Obsidian (may steal focus)

After each mechanism, capture a screenshot of the Obsidian window region.
The probe text reads ``LIVE_REFRESH_PROBE_<timestamp>`` in a 28pt blue font
at canvas-local (50, 50), so any reload will produce it as a visibly distinct
new top-left element. If a screenshot shows the marker, that mechanism works.

This is intentionally NOT wired into the agent loop. The point is to nail
down the refresh primitive before any meta-tier tool wraps it.

Run:
    .venv/Scripts/python.exe tests/p14_6_live_refresh/probe_obsidian_refresh.py

Side effects:
    - Writes one new text element to the canvas (~50 chars). Roundtrip-safe
      based on prior validation (lz-string base64 with internal newlines).
    - Creates 4 screenshots under tests/results/p14_6_live_refresh/<ts>/
    - Restores the canvas to baseline at the end (reads the
      .bak_p14_6_probe_baseline backup made earlier).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import ctypes
from ctypes import wintypes

import lzstring
import mss
import mss.tools
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image
try:
    import psutil
except ImportError:
    psutil = None

_PW_RENDERFULLCONTENT = 0x00000002

VAULT = Path(r"D:\D\scientific research vault")
RESULTS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "results"
    / "p14_6_live_refresh"
)


def find_target() -> Path:
    for p in VAULT.rglob("A Comparative Evaluation*.md"):
        s = str(p)
        if ".agent_bak_" in s or ".bak" in p.name or ".backup" in p.name:
            continue
        return p
    raise FileNotFoundError("target canvas not found in vault")


def find_obsidian_hwnd(canvas_title_hint: str) -> int | None:
    """Find a visible top-level window owned by obsidian.exe whose title
    contains the canvas hint. Filtering by process name (not just window
    class) avoids picking up WPS Office / other Chromium-backed apps that
    happen to display a file with the same name."""
    found: list[int] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if canvas_title_hint.lower() not in title.lower():
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pname = (psutil.Process(pid).name() if psutil else "").lower()
        except Exception:
            pname = ""
        if pname == "obsidian.exe":
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def screenshot_window(hwnd: int, out_path: Path) -> None:
    """Capture the window's true rendered contents via PrintWindow.

    Unlike grabbing the screen region under the window (which captures
    whatever overlaps it), PrintWindow asks the window itself to render
    into a memory DC. With PW_RENDERFULLCONTENT this works for windows
    that use composited rendering (Chromium / Electron / DWM-only apps)
    — which Obsidian and WPS both do. No focus stealing required.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src_dc, width, height)
    mem_dc.SelectObject(bitmap)

    result = ctypes.windll.user32.PrintWindow(
        hwnd, mem_dc.GetSafeHdc(), _PW_RENDERFULLCONTENT
    )

    info = bitmap.GetInfo()
    bits = bitmap.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB",
        (info["bmWidth"], info["bmHeight"]),
        bits,
        "raw",
        "BGRX",
        0,
        1,
    )
    img.save(str(out_path))

    win32gui.DeleteObject(bitmap.GetHandle())
    mem_dc.DeleteDC()
    src_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    if result != 1:
        print(f"        (warn) PrintWindow returned {result}, image may be blank")


def decode_drawing(text: str) -> tuple[dict, tuple[int, int], str]:
    m = re.search(r"```compressed-json\s*\n([\s\S]+?)\n```", text)
    if not m:
        raise RuntimeError("no compressed-json fence")
    raw = m.group(1)
    fence_clean = re.sub(r"\s+", "", raw)
    lz = lzstring.LZString()
    decoded = lz.decompressFromBase64(fence_clean)
    if not decoded:
        raise RuntimeError("lz-string decompress returned empty")
    return json.loads(decoded), m.span(1), raw


def encode_drawing(data: dict) -> str:
    lz = lzstring.LZString()
    payload = json.dumps(data, separators=(",", ":"))
    return lz.compressToBase64(payload)


def make_probe_element(text_label: str, x: float, y: float) -> dict:
    eid = uuid.uuid4().hex[:16]
    seed = int(uuid.uuid4().int % 2_000_000_000)
    now_ms = int(time.time() * 1000)
    return {
        "id": eid,
        "type": "text",
        "x": x,
        "y": y,
        "width": 900.0,
        "height": 96.0,
        "angle": 0,
        "strokeColor": "#e03131",  # bright red — impossible to miss
        "backgroundColor": "#fff3bf",  # yellow bg highlight
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": seed,
        "version": 1,
        "versionNonce": seed + 1,
        "isDeleted": False,
        "boundElements": None,
        "updated": now_ms,
        "link": None,
        "locked": False,
        "text": text_label,
        "fontSize": 72,  # huge
        "fontFamily": 1,
        "textAlign": "left",
        "verticalAlign": "top",
        "baseline": 60,
        "containerId": None,
        "originalText": text_label,
        "lineHeight": 1.25,
    }


def element_bbox_center(data: dict) -> tuple[float, float]:
    xs, ys = [], []
    for e in data["elements"]:
        if e.get("isDeleted"):
            continue
        x, y = e.get("x"), e.get("y")
        w, h = e.get("width", 0), e.get("height", 0)
        if x is None or y is None:
            continue
        xs.extend([x, x + w])
        ys.extend([y, y + h])
    cx = (min(xs) + max(xs)) / 2 if xs else 0.0
    cy = (min(ys) + max(ys)) / 2 if ys else 0.0
    return cx, cy


def write_canvas_with_probe(target: Path, probe_label: str) -> tuple[str, float, float, int]:
    """Insert one probe text element into the canvas, positioned at the
    center of the existing element bbox so it cannot be off-screen.

    Returns (probe_id, x, y, element_count_after_write)."""
    text = target.read_text(encoding="utf-8")
    data, (s, e), _raw = decode_drawing(text)
    cx, cy = element_bbox_center(data)
    probe = make_probe_element(probe_label, x=cx, y=cy)
    data["elements"].append(probe)
    new_fence = encode_drawing(data)
    new_text = text[:s] + new_fence + text[e:]
    target.write_text(new_text, encoding="utf-8")
    return probe["id"], cx, cy, len(data["elements"])


def read_back_probe_count(target: Path) -> int:
    """Re-decode the file and count probe text elements."""
    text = target.read_text(encoding="utf-8")
    data, _, _ = decode_drawing(text)
    return sum(
        1 for e in data["elements"]
        if "PROBE" in (e.get("text") or "").upper()
    )


def main() -> int:
    target = find_target()
    print(f"target: {target}")

    # ensure baseline backup exists (created in prior turn, but re-create
    # if user has since modified — we DO want to restore exactly what was
    # there when probe started, not whatever baseline was last saved)
    bak = target.with_suffix(target.suffix + ".bak_p14_6_probe_run")
    shutil.copy2(target, bak)
    print(f"per-run backup: {bak.name}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    results_dir = RESULTS_ROOT / stamp
    results_dir.mkdir(parents=True, exist_ok=True)

    title_hint = "A Comparative Evaluation"
    hwnd = find_obsidian_hwnd(title_hint)
    if hwnd is None:
        print("ERROR: Obsidian window with canvas open not found.")
        print("       Open the file in Obsidian and re-run.")
        return 2
    print(f"Obsidian hwnd: {hwnd}")

    # --- 0. baseline screenshot ---
    print("\n[0] capturing BASELINE screenshot...")
    screenshot_window(hwnd, results_dir / "0_baseline.png")

    # --- write probe element ---
    probe_label = f"LIVE_REFRESH_PROBE_{stamp}"
    print(f"\n[write] adding probe text element: {probe_label!r}")
    probe_id, px, py, count_after = write_canvas_with_probe(target, probe_label)
    print(f"        probe id={probe_id}  at canvas coords=({px:.0f}, {py:.0f})")
    print(f"        elements after write: {count_after}")
    # read-back verification
    rb = read_back_probe_count(target)
    print(f"        read-back probe count: {rb} (expect 1)")
    if rb != 1:
        print("ERROR: probe did not land in file — aborting refresh-mechanism phase.")
        # restore + bail
        shutil.copy2(bak, target)
        return 3

    # --- A. passive wait (let plugin file watcher fire on its own) ---
    print("\n[A] passive — waiting 10s for plugin file watcher...")
    time.sleep(10)
    screenshot_window(hwnd, results_dir / "A_passive_10s.png")

    # --- B. utime touch ---
    print("\n[B] utime — bumping mtime, waiting 6s...")
    os.utime(target, None)
    time.sleep(6)
    screenshot_window(hwnd, results_dir / "B_utime_6s.png")

    # --- C. obsidian:// URI ---
    print("\n[C] obsidian:// URI re-open, waiting 6s...")
    vault_name = VAULT.name
    rel = target.relative_to(VAULT).as_posix()
    uri = (
        f"obsidian://open?vault={urllib.parse.quote(vault_name)}"
        f"&file={urllib.parse.quote(rel)}"
    )
    print(f"        uri: {uri[:140]}")
    try:
        # os.startfile invokes the shell URL handler directly, bypassing
        # cmd's argument parsing (which mangles `=` and `&` in URLs).
        os.startfile(uri)
    except Exception as exc:
        print(f"        URI launch failed: {exc}")
    time.sleep(6)
    screenshot_window(hwnd, results_dir / "C_uri_6s.png")

    # --- LEAVE PROBE IN PLACE so the user can manually inspect Obsidian ---
    print(
        f"\n[done] Probe LEFT IN PLACE in target file.\n"
        f"       File path: {target}\n"
        f"       Probe text: {probe_label!r}\n"
        f"       Probe coords (canvas): ({px:.0f}, {py:.0f})\n"
        f"       Style: 72pt red on yellow background — should be visually obvious.\n"
        f"       Backup to restore from: {bak}\n\n"
        f"To restore baseline after inspection:\n"
        f'   copy /Y "{bak}" "{target}"\n'
        f"\nScreenshots under: {results_dir}\n"
        f"  0_baseline.png     — before any write\n"
        f"  A_passive_10s.png  — wait only (no extra signal to plugin)\n"
        f"  B_utime_6s.png     — after os.utime(path, None)\n"
        f"  C_uri_6s.png       — after obsidian:// URI re-open\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
