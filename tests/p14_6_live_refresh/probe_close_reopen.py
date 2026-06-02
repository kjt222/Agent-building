"""Refresh mechanism candidate 3: close current tab + re-open via REST API.

Hypothesis: closing the canvas tab destroys the plugin's in-memory state
for that file; re-opening triggers a fresh disk read. No stateful toggle,
no half-broken buffer trap.

Single attempt — no verify-then-retry loop. That's the model's job.
"""

from __future__ import annotations

import ctypes
import json
import re
import shutil
import ssl
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import lzstring
import win32gui
import win32ui
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import keyring  # noqa: E402

from agent.credentials import SERVICE_NAME  # noqa: E402

VAULT = Path(r"D:\D\scientific research vault")
RESULTS = Path(__file__).resolve().parents[2] / "tests" / "results" / "p14_6_live_refresh"
REST = "https://127.0.0.1:27124"
KEY_REF = "obsidian.local_rest_api.scientific_research_vault"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def rest(path: str, method: str = "GET", body: bytes = b"") -> tuple[int, str]:
    key = keyring.get_password(SERVICE_NAME, KEY_REF)
    req = urllib.request.Request(
        REST + path, method=method, data=body if body else None,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def find_target() -> Path:
    for p in VAULT.rglob("A Comparative Evaluation*.md"):
        s = str(p)
        if ".agent_bak_" in s or ".bak" in p.name or ".backup" in p.name:
            continue
        return p
    raise FileNotFoundError


def decode_drawing(text: str):
    m = re.search(r"```compressed-json\s*\n([\s\S]+?)\n```", text)
    raw = re.sub(r"\s+", "", m.group(1))
    return json.loads(lzstring.LZString().decompressFromBase64(raw)), m.span(1)


def encode_drawing(data: dict) -> str:
    return lzstring.LZString().compressToBase64(json.dumps(data, separators=(",", ":")))


def write_probe(target: Path, label: str) -> tuple[str, float, float]:
    text = target.read_text(encoding="utf-8")
    data, (s, e) = decode_drawing(text)
    xs, ys = [], []
    for el in data["elements"]:
        if el.get("isDeleted"):
            continue
        x, y = el.get("x"), el.get("y")
        w, h = el.get("width", 0), el.get("height", 0)
        if x is None or y is None:
            continue
        xs.extend([x, x + w])
        ys.extend([y, y + h])
    cx = (min(xs) + max(xs)) / 2 if xs else 0.0
    cy = (min(ys) + max(ys)) / 2 if ys else 0.0
    eid = uuid.uuid4().hex[:16]
    seed = int(uuid.uuid4().int % 2_000_000_000)
    data["elements"].append({
        "id": eid, "type": "text", "x": cx, "y": cy,
        "width": 900.0, "height": 96.0, "angle": 0,
        "strokeColor": "#e03131", "backgroundColor": "#fff3bf",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "seed": seed, "version": 1, "versionNonce": seed + 1,
        "isDeleted": False, "boundElements": None,
        "updated": int(time.time() * 1000), "link": None, "locked": False,
        "text": label, "fontSize": 72, "fontFamily": 1,
        "textAlign": "left", "verticalAlign": "top", "baseline": 60,
        "containerId": None, "originalText": label, "lineHeight": 1.25,
    })
    target.write_text(text[:s] + encode_drawing(data) + text[e:], encoding="utf-8")
    return eid, cx, cy


def screenshot(hwnd: int, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = max(1, r - l), max(1, b - t)
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bm = win32ui.CreateBitmap()
    bm.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bm)
    ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)
    info = bm.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bm.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    img.save(str(out))
    win32gui.DeleteObject(bm.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)


def find_hwnd() -> int | None:
    import win32process
    import psutil
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd) or ""
        if "Comparative Evaluation" not in t:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() == "obsidian.exe":
                found.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def main() -> int:
    target = find_target()
    bak_run = target.with_suffix(target.suffix + ".bak_p14_6_close_reopen_run")
    shutil.copy2(target, bak_run)
    print(f"per-run backup: {bak_run.name}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = RESULTS / f"close_reopen_{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    hwnd = find_hwnd()
    print(f"hwnd: {hwnd}")
    screenshot(hwnd, out / "0_baseline.png")

    # Sanity: query active note state before any change
    code, body = rest("/active/")
    try:
        active_path = json.loads(body).get("path", "?")
        active_size = json.loads(body).get("stat", {}).get("size", "?")
    except Exception:
        active_path, active_size = "?", "?"
    print(f"\n[before] active note path: {active_path}")
    print(f"[before] active note size: {active_size}")

    # 1. Write probe
    label = f"LIVE_REFRESH_PROBE_{stamp}"
    probe_id, px, py = write_probe(target, label)
    print(f"\n[write] probe '{label}' at canvas ({px:.0f}, {py:.0f})  id={probe_id}")
    file_size = target.stat().st_size
    print(f"        file size after write: {file_size} bytes")

    # 2. Make sure target IS the active tab (so close hits the right tab)
    rel = target.relative_to(VAULT).as_posix()
    code, body = rest(f"/open/{urllib.parse.quote(rel)}", "POST")
    print(f"\n[open] POST /open -> HTTP {code}")
    time.sleep(0.8)

    # 3. CLOSE the active tab
    code, body = rest("/commands/workspace:close/", "POST")
    print(f"\n[close] POST /commands/workspace:close/ -> HTTP {code}  body={body[:120]!r}")
    time.sleep(0.8)
    screenshot(hwnd, out / "1_after_close.png")

    # 4. RE-OPEN the file (Obsidian sees .md with excalidraw-plugin frontmatter →
    #    defaults to Excalidraw view; plugin reads file from disk fresh)
    code, body = rest(f"/open/{urllib.parse.quote(rel)}", "POST")
    print(f"\n[open#2] POST /open -> HTTP {code}")
    time.sleep(2.5)  # let plugin parse + render
    screenshot(hwnd, out / "2_after_reopen.png")

    # 5. Verify file integrity — Obsidian SHOULD NOT have clobbered it
    after_size = target.stat().st_size
    text = target.read_text(encoding="utf-8")
    data, _ = decode_drawing(text)
    probes_in_file = [e for e in data["elements"]
                      if "PROBE" in (e.get("text") or "").upper()]
    print(f"\n[verify] file size after close+reopen: {after_size} bytes "
          f"(before write: {bak_run.stat().st_size}, after write: {file_size})")
    print(f"[verify] probe elements in file now: {len(probes_in_file)}")
    if probes_in_file:
        print(f"         {probes_in_file[0].get('text')!r} at "
              f"({probes_in_file[0].get('x'):.0f}, {probes_in_file[0].get('y'):.0f})")

    # 6. REST API view of the file content
    code, body = rest(f"/vault/{urllib.parse.quote(rel)}")
    if code == 200:
        print(f"\n[rest] GET /vault/... -> {code}  {len(body)} chars")
        # Quick PROBE check in REST-fetched content
        print(f"       contains 'PROBE'? {('PROBE' in body)}")

    print(f"\n[done] Screenshots: {out}")
    print(f"       Probe LEFT IN PLACE — visually inspect Obsidian canvas now.")
    print(f"       To restore baseline: copy /Y "
          f'"{target.with_suffix(target.suffix + ".bak_p14_6_probe_baseline")}" '
          f'"{target}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
