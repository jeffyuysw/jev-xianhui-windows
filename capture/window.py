"""Locate the chat window and grab a bitmap of it.

Uses ctypes against user32/gdi32 - no extra dependency for the window part. The
bitmap is produced with PrintWindow(PW_RENDERFULLCONTENT), which works for
windows that draw through hardware compositing, and needs no full-desktop
screen capture permission.

Robustness notes (these matter - an unresponsive window must never freeze us):
  * EnumWindows / SendMessageTimeout are reached through a hard deadline. If a
    window does not answer, we skip it instead of blocking the caller.
  * Results are cached briefly so a 1 Hz status poll does not re-enumerate the
    whole desktop every tick.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time
from dataclasses import dataclass

import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

# A hung window would otherwise stall us forever. Windows sends WM_NULL with
# this timeout to test responsiveness.
SMTO_ABORTIFHUNG = 0x0002
SMTO_BLOCK = 0x0001
_HUNG_TIMEOUT_MS = 120

# Short cache so the 1 Hz UI poll does not enumerate the desktop repeatedly.
_CACHE_TTL_S = 0.8
_cache_lock = threading.Lock()
_cached: tuple[float, tuple[str, ...], "WindowInfo | None"] | None = None


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int
    process: str = ""


def _process_name(hwnd: int) -> str:
    """Executable name owning this window, e.g. 'Weixin.exe'. '' on failure."""
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not handle:
        return ""
    try:
        size = wt.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)
        ):
            return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _is_visible_top_level(hwnd: int) -> bool:
    if not user32.IsWindowVisible(hwnd):
        return False
    # Skip owned/tool windows (tooltips, IME shells).
    ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
    if ex & 0x00000080:  # WS_EX_TOOLWINDOW
        return False
    return True


def _is_responsive(hwnd: int) -> bool:
    """False when the window is hung, so we never block on its properties."""
    result = ctypes.c_ulong(0)
    ok = user32.SendMessageTimeoutW(
        hwnd,
        0x0000,  # WM_NULL
        0,
        0,
        SMTO_ABORTIFHUNG | SMTO_BLOCK,
        _HUNG_TIMEOUT_MS,
        ctypes.byref(result),
    )
    return bool(ok)


def _client_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    """Client area bounds in screen coordinates (no title bar/borders)."""
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, w, h


def _enumerate(titles: list[str], processes: list[str] | None = None) -> "WindowInfo | None":
    results: list[WindowInfo] = []
    procs = {p.lower() for p in (processes or []) if p}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _enum(hwnd, _lparam):
        try:
            if not _is_visible_top_level(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            proc = _process_name(hwnd)
            # A window qualifies when its process matches, or (when no process
            # filter is given) its title contains a keyword. Matching the process
            # matters: WeChat's own title is just "微信", but a browser tab that
            # happens to mention 微信 in its page title also matches on title, and
            # being bigger it used to win - we then OCR'd a web page.
            by_proc = bool(procs) and proc in procs
            keys = [t for t in titles if t]
            by_title = bool(keys) and any(k in title for k in keys)
            if not (by_proc or by_title):
                return True
            # Only inspect windows that answer; a hung one would stall us.
            if not _is_responsive(hwnd):
                return True
            left, top, w, h = _client_rect_screen(hwnd)
            if w < 200 or h < 200:  # too small to be a chat window
                return True
            results.append(WindowInfo(hwnd, title, left, top, w, h, proc))
        except Exception:
            pass  # a single bad window must not abort the scan
        return True

    user32.EnumWindows(_enum, 0)
    if not results:
        return None
    # Prefer a genuine process match (it cannot be a browser tab); among equals
    # take the largest, which is the window the user is actually looking at.
    if procs:
        exact = [wi for wi in results if wi.process in procs]
        if exact:
            results = exact
    return max(results, key=lambda wi: wi.width * wi.height)


def find_target_window(
    titles: list[str], processes: list[str] | None = None, *, use_cache: bool = True
) -> "WindowInfo | None":
    """Return the largest visible window matching a process or title keyword.

    Process names take priority over title substrings, which keeps us off
    unrelated windows (e.g. a browser tab whose title mentions 微信).
    Results are cached for a fraction of a second (see _CACHE_TTL_S).
    """
    global _cached
    key = (tuple(titles), tuple(processes or ()))
    now = time.monotonic()

    if use_cache:
        with _cache_lock:
            if _cached is not None:
                ts, cached_key, cached_val = _cached
                if cached_key == key and (now - ts) < _CACHE_TTL_S:
                    return cached_val

    value = _enumerate(titles, processes)

    with _cache_lock:
        _cached = (time.monotonic(), key, value)
    return value


def invalidate_window_cache() -> None:
    """Force the next find_target_window() to re-enumerate."""
    global _cached
    with _cache_lock:
        _cached = None


def capture_window(win: WindowInfo) -> np.ndarray | None:
    """Grab the client area as an RGB numpy array (h, w, 3). Never touches disk."""
    hwnd = win.hwnd
    w, h = win.width, win.height
    if w <= 0 or h <= 0:
        return None

    hdc_window = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    try:
        hbitmap = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
        try:
            gdi32.SelectObject(hdc_mem, hbitmap)
            ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
            if not ok:
                user32.PrintWindow(hwnd, hdc_mem, 0)

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wt.DWORD),
                    ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long),
                    ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h  # negative = top-down rows
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0  # BI_RGB

            buf = ctypes.create_string_buffer(w * h * 4)
            got = gdi32.GetDIBits(
                hdc_mem, hbitmap, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS
            )
            if not got:
                return None
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
            return arr[:, :, 2::-1].copy()  # BGRA -> RGB
        finally:
            gdi32.DeleteObject(hbitmap)
    finally:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_window)


def client_origin(hwnd: int) -> tuple[int, int]:
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y
