"""Live-refresh probe v2 — uses Obsidian Local REST API plugin.

Sequence:
    1. Write a bright canary probe element into the target canvas
    2. Bring Obsidian to foreground via obsidian:// URI (no focus theft
       beyond what user expects when calling the plugin)
    3. POST /commands/obsidian-excalidraw-plugin:toggle-excalidraw-view/
       — flips canvas → markdown
    4. Brief wait
    5. POST same command — flips back markdown → canvas, which forces
       the plugin to re-read the .md file from disk and rebuild canvas
    6. Screenshot via PrintWindow + ask user to confirm probe visible
    7. Restore baseline from per-run backup

This is the candidate implementation for `obsidian.refresh_note`.
If this works without focus-stealing keyboard injection, the meta-tier
can rely on it.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import lzstring
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image
try:
    import psutil
except ImportError:
    psutil = None
import keyring

# Allow running this script from any cwd by ensuring repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.credentials import SERVICE_NAME  # noqa: E402

VAULT = Path(r"D:\D\scientific research vault")
RESULTS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "results"
    / "p14_6_live_refresh"
)
REST_BASE = "https://127.0.0.1:27124"
REST_KEY_REF = "obsidian.local_rest_api.scientific_research_vault"
TOGGLE_CMD = "obsidian-excalidraw-plugin:toggle-excalidraw-view"

_PW_RENDERFULLCONTENT = 0x00000002

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def find_target() -> Path:
    for p in VAULT.rglob("A Comparative Evaluation*.md"):
        s = str(p)
        if ".agent_bak_" in s or ".bak" in p.name or ".backup" in p.name:
            continue
        return p
    raise FileNotFoundError("target canvas not found")


def find_obsidian_hwnd(title_hint: str) -> int | None:
    found: list[int] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if title_hint.lower() not in title.lower():
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w = max(1, right - left)
    h = max(1, bottom - top)
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bm = win32ui.CreateBitmap()
    bm.CreateCompatibleBitmap(src_dc, w, h)
    mem_dc.SelectObject(bm)
    ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(),
                                     _PW_RENDERFULLCONTENT)
    info = bm.GetInfo()
    bits = bm.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bits, "raw", "BGRX", 0, 1)
    img.save(str(out_path))
    win32gui.DeleteObject(bm.GetHandle())
    mem_dc.DeleteDC()
    src_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)


def decode_drawing(text: str):
    import re
    m = re.search(r"```compressed-json\s*\n([\s\S]+?)\n```", text)
    if not m:
        raise RuntimeError("no compressed-json fence")
    raw = m.group(1)
    fence_clean = re.sub(r"\s+", "", raw)
    decoded = lzstring.LZString().decompressFromBase64(fence_clean)
    if not decoded:
        raise RuntimeError("lz-string returned empty")
    return json.loads(decoded), m.span(1)


def encode_drawing(data: dict) -> str:
    return lzstring.LZString().compressToBase64(
        json.dumps(data, separators=(",", ":"))
    )


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
    return (
        (min(xs) + max(xs)) / 2 if xs else 0.0,
        (min(ys) + max(ys)) / 2 if ys else 0.0,
    )


def make_probe(label: str, x: float, y: float) -> dict:
    eid = uuid.uuid4().hex[:16]
    seed = int(uuid.uuid4().int % 2_000_000_000)
    return {
        "id": eid, "type": "text", "x": x, "y": y,
        "width": 900.0, "height": 96.0, "angle": 0,
        "strokeColor": "#e03131", "backgroundColor": "#fff3bf",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100, "groupIds": [],
        "frameId": None, "roundness": None, "seed": seed,
        "version": 1, "versionNonce": seed + 1, "isDeleted": False,
        "boundElements": None, "updated": int(time.time() * 1000),
        "link": None, "locked": False,
        "text": label, "fontSize": 72, "fontFamily": 1,
        "textAlign": "left", "verticalAlign": "top", "baseline": 60,
        "containerId": None, "originalText": label, "lineHeight": 1.25,
    }


def write_probe(target: Path, label: str) -> tuple[str, float, float]:
    text = target.read_text(encoding="utf-8")
    data, (s, e) = decode_drawing(text)
    cx, cy = element_bbox_center(data)
    probe = make_probe(label, x=cx, y=cy)
    data["elements"].append(probe)
    new_text = text[:s] + encode_drawing(data) + text[e:]
    target.write_text(new_text, encoding="utf-8")
    return probe["id"], cx, cy


def rest_post_command(cmd_id: str, key: str) -> tuple[int, str]:
    url = f"{REST_BASE}/commands/{urllib.parse.quote(cmd_id, safe=':-')}/"
    req = urllib.request.Request(
        url, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Length": "0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            return r.status, r.read().decode("utf-8", errors="replace")[:300]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return e.code, body


def rest_open_file(rel_path: str, key: str) -> tuple[int, str]:
    """Ask Obsidian to open the file (sets it as the active tab so the
    toggle command targets the right view)."""
    url = f"{REST_BASE}/open/{urllib.parse.quote(rel_path)}"
    req = urllib.request.Request(
        url, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Length": "0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            return r.status, r.read().decode("utf-8", errors="replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:300]


def main() -> int:
    target = find_target()
    print(f"target: {target}")
    bak = target.with_suffix(target.suffix + ".bak_p14_6_probe_run")
    shutil.copy2(target, bak)
    print(f"per-run backup: {bak.name}")

    key = keyring.get_password(SERVICE_NAME, REST_KEY_REF)
    if not key:
        print(f"ERROR: API key not in keyring under '{REST_KEY_REF}'")
        return 2

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = RESULTS_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    hwnd = find_obsidian_hwnd("A Comparative Evaluation")
    if hwnd is None:
        print("ERROR: Obsidian window not found")
        return 2
    print(f"Obsidian hwnd: {hwnd}")
    screenshot_window(hwnd, out_dir / "0_baseline.png")

    # write probe
    label = f"LIVE_REFRESH_PROBE_{stamp}"
    print(f"\n[write] probe label: {label!r}")
    probe_id, px, py = write_probe(target, label)
    print(f"        probe at canvas ({px:.0f}, {py:.0f})  id={probe_id}")

    # ensure file is active tab — POST /open/<rel-path>
    rel = target.relative_to(VAULT).as_posix()
    print(f"\n[open] POST /open/{rel[:60]}...")
    code, body = rest_open_file(rel, key)
    print(f"        -> HTTP {code}  body={body[:120]}")
    time.sleep(1.0)

    # toggle off (canvas → markdown)
    print(f"\n[toggle 1] POST /commands/{TOGGLE_CMD}/")
    code, body = rest_post_command(TOGGLE_CMD, key)
    print(f"        -> HTTP {code}  body={body[:120]}")
    time.sleep(1.0)
    screenshot_window(hwnd, out_dir / "1_after_toggle_to_markdown.png")

    # toggle on (markdown → canvas — forces re-read of file)
    print(f"\n[toggle 2] POST /commands/{TOGGLE_CMD}/")
    code, body = rest_post_command(TOGGLE_CMD, key)
    print(f"        -> HTTP {code}  body={body[:120]}")
    time.sleep(1.5)
    screenshot_window(hwnd, out_dir / "2_after_toggle_back_to_excalidraw.png")

    print(
        f"\n[result] Probe LEFT IN PLACE for visual inspection.\n"
        f"         Screenshots: {out_dir}\n"
        f"         Look for red 72pt {label!r} near canvas coords ({px:.0f}, {py:.0f}).\n"
        f"         To restore baseline: copy /Y \"{bak}\" \"{target}\""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
