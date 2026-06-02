"""Win32 helper to find the Obsidian window for a vault + capture it.

Used by smoke runners to take an after-run screenshot of what the user
would actually see, independent of any PIL approximation.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

import win32gui
import win32process
import win32ui
from PIL import Image

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

_PW_RENDERFULLCONTENT = 0x00000002


def find_obsidian_window(*, title_substring: str = "") -> int | None:
    """Return the hwnd of a visible obsidian.exe top-level window whose
    title contains ``title_substring`` (case-insensitive). When the
    substring is empty, the first visible Obsidian window wins.
    """
    found: list[int] = []
    needle = title_substring.lower()

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = (win32gui.GetWindowText(hwnd) or "")
        if needle and needle not in title.lower():
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


def capture_window(hwnd: int, out_path: Path) -> bool:
    """Capture window contents into out_path. Uses PrintWindow with
    PW_RENDERFULLCONTENT so backgrounded / overlapped windows still
    yield true content (no focus theft). Returns True on a non-blank
    capture, False if PrintWindow returned 0 / the bitmap is solid
    black (indicates window was minimized).
    """
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
    rc = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(),
                                          _PW_RENDERFULLCONTENT)
    info = bm.GetInfo()
    img = Image.frombuffer(
        "RGB",
        (info["bmWidth"], info["bmHeight"]),
        bm.GetBitmapBits(True),
        "raw",
        "BGRX",
        0,
        1,
    )
    img.save(str(out_path))
    win32gui.DeleteObject(bm.GetHandle())
    mem_dc.DeleteDC()
    src_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    # Tiny window or all-black usually means minimized / off-screen.
    if w < 400 or h < 400 or rc != 1:
        return False
    # Sanity sample: check if any of the four corners is non-black.
    for px in (img.getpixel((0, 0)),
               img.getpixel((w - 1, 0)),
               img.getpixel((0, h - 1)),
               img.getpixel((w - 1, h - 1))):
        if px != (0, 0, 0):
            return True
    return False
