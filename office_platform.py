#!/usr/bin/env python3
"""Cross-platform helpers for Star Office presence + screen capture.

Backends:
  - Linux / X11 : xprop + xwininfo (window), ffmpeg x11grab (capture)
  - Windows      : ctypes/user32 (window), Pillow ImageGrab (capture) — no ffmpeg needed
  - macOS        : osascript (frontmost app), Pillow ImageGrab (capture, full screen)

Public API:
  get_active_window() -> (app, title, geom)   # app: lowercase id-ish string; geom=(x,y,w,h) or None
  grab_jpeg(geom, out_width=320, quality=70)  -> bytes | None
  screen_size() -> (w, h)
"""

import os
import re
import io
import platform
import subprocess
import tempfile

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"
IS_LINUX = SYSTEM == "Linux"
DISPLAY = os.environ.get("DISPLAY", ":0")

# Align GetWindowRect (logical) with ImageGrab (physical) on high-DPI Windows.
if IS_WINDOWS:
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


# ========================= Active window =========================

def get_active_window():
    """Return (app, title, geom). app is a lowercase identifier (exe/WM_CLASS),
    geom=(x,y,w,h) or None. Never raises."""
    try:
        if IS_WINDOWS:
            return _win_active_window()
        if IS_MAC:
            return _mac_active_window()
        return _x11_active_window()
    except Exception:
        return "", "", None


def _x11_active_window():
    root = _sh(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    m = re.search(r"0x[0-9a-fA-F]+", root)
    if not m or m.group(0) == "0x0":
        return "", "", None
    wid = m.group(0)
    info = _sh(["xwininfo", "-id", wid])

    def g(pat):
        mm = re.search(pat, info)
        return int(mm.group(1)) if mm else None

    x, y = g(r"Absolute upper-left X:\s*(-?\d+)"), g(r"Absolute upper-left Y:\s*(-?\d+)")
    w, h = g(r"Width:\s*(\d+)"), g(r"Height:\s*(\d+)")

    classes = re.findall(r'"([^"]*)"', _sh(["xprop", "-id", wid, "WM_CLASS"]))
    app = " ".join(classes).lower().strip()
    nm = re.search(r'"(.*)"', _sh(["xprop", "-id", wid, "_NET_WM_NAME"]))
    title = nm.group(1) if nm else ""

    if None in (x, y, w, h):
        return app, title, None
    rw, rh = screen_size()
    x = max(0, min(x, rw - 2))
    y = max(0, min(y, rh - 2))
    w = max(2, min(w, rw - x))
    h = max(2, min(h, rh - y))
    w -= w % 2
    h -= h % 2
    return app, title, (x, y, w, h)


def _win_active_window():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", None

    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x, y = rect.left, rect.top
    w, h = rect.right - rect.left, rect.bottom - rect.top

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = _win_proc_name(pid.value, kernel32)

    if w <= 0 or h <= 0:
        return app, title, None
    return app, title, (x, y, w, h)


def _win_proc_name(pid, kernel32):
    import ctypes
    from ctypes import wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            base = os.path.basename(buf.value)
            return re.sub(r"\.exe$", "", base, flags=re.I).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def _mac_active_window():
    app = ""
    try:
        app = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=5).stdout.strip().lower()
    except Exception:
        pass
    w, h = screen_size()
    return app, "", (0, 0, w, h)  # macOS: capture full screen (no simple per-window grab)


# ========================= Screen size =========================

def screen_size():
    if IS_WINDOWS:
        try:
            import ctypes
            u = ctypes.windll.user32
            w = u.GetSystemMetrics(78) or u.GetSystemMetrics(0)  # SM_CXVIRTUALSCREEN / SM_CXSCREEN
            h = u.GetSystemMetrics(79) or u.GetSystemMetrics(1)  # SM_CYVIRTUALSCREEN / SM_CYSCREEN
            return int(w), int(h)
        except Exception:
            return (1920, 1080)
    if IS_LINUX:
        info = _sh(["xwininfo", "-root"])
        mw, mh = re.search(r"Width:\s*(\d+)", info), re.search(r"Height:\s*(\d+)", info)
        return (int(mw.group(1)), int(mh.group(1))) if (mw and mh) else (1920, 1080)
    try:
        from PIL import ImageGrab
        return ImageGrab.grab().size
    except Exception:
        return (1920, 1080)


# ========================= Capture =========================

def grab_jpeg(geom, out_width=320, quality=70):
    """Return JPEG bytes of the screen region geom=(x,y,w,h), or None."""
    if not geom:
        return None
    if IS_LINUX:
        return _ffmpeg_grab(geom, out_width, quality)
    return _pil_grab(geom, out_width, quality)


def _ffmpeg_grab(geom, out_width, quality):
    x, y, w, h = geom
    out = os.path.join(tempfile.gettempdir(), "star-office-screen.jpg")
    qv = max(2, min(31, int(31 - (quality / 100.0) * 29)))  # map 0..100 -> ffmpeg 31..2
    cmd = [
        "ffmpeg", "-loglevel", "error", "-f", "x11grab",
        "-video_size", f"{w}x{h}", "-i", f"{DISPLAY}+{x},{y}",
        "-frames:v", "1", "-vf", f"scale={out_width}:-2", "-q:v", str(qv),
        "-y", out,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=8)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            with open(out, "rb") as f:
                return f.read()
    except Exception:
        pass
    return None


def _pil_grab(geom, out_width, quality):
    try:
        from PIL import ImageGrab
    except Exception:
        return None
    x, y, w, h = geom
    try:
        kwargs = {"bbox": (x, y, x + w, y + h)}
        if IS_WINDOWS:
            kwargs["all_screens"] = True
        img = ImageGrab.grab(**kwargs)
        if out_width and img.width > out_width:
            ratio = out_width / float(img.width)
            img = img.resize((out_width, max(1, int(img.height * ratio))))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return None
