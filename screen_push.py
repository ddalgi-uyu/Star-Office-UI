#!/usr/bin/env python3
"""Star Office — active-window screen pusher (X11 / GNOME).

Grabs a small thumbnail of the CURRENTLY FOCUSED window (not the whole desktop)
with ffmpeg, downscales it, and POSTs it to the office's /agent-screen endpoint
every ~1.5s so each person's character can show a live "monitor" in the room.

Only the focused window is captured — other windows, notifications and the rest
of your desktop are never sent.

Config (env or edit below):
    OFFICE_URL     office backend (default http://127.0.0.1:19000)
    AGENT_ID       who to attach the screen to (default "star" = host/main agent)
    JOIN_KEY       required for guests (not for the host/main agent)
    SCREEN_INTERVAL   seconds between frames (default 1.5)
    SCREEN_WIDTH   downscaled thumbnail width in px (default 320)

Guests: run office-agent-push.py first (it writes office-agent-state.json with
your agentId); this script will auto-read that, so you usually only set OFFICE_URL.
"""

import base64
import json
import os
import re
import subprocess
import sys
import time

OFFICE_URL = os.environ.get("OFFICE_URL", "http://127.0.0.1:19000").rstrip("/")
AGENT_ID = os.environ.get("AGENT_ID", "")
JOIN_KEY = os.environ.get("JOIN_KEY", "")
INTERVAL = float(os.environ.get("SCREEN_INTERVAL", "1.5"))
OUT_WIDTH = int(os.environ.get("SCREEN_WIDTH", "320"))
DISPLAY = os.environ.get("DISPLAY", ":0")

STATE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "office-agent-state.json")
TMP_JPG = os.path.join(os.environ.get("TMPDIR", "/tmp"), "star-office-screen.jpg")


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def resolve_agent():
    """Pick agentId/joinKey: explicit env > office-agent-state.json > host 'star'."""
    aid, key = AGENT_ID, JOIN_KEY
    if not aid and os.path.exists(STATE_CACHE):
        try:
            with open(STATE_CACHE, encoding="utf-8") as f:
                d = json.load(f)
            aid = d.get("agentId") or ""
            key = key or d.get("joinKey") or ""
        except Exception:
            pass
    if not aid:
        aid = "star"  # host / main agent — backend allows screen without joinKey
    return aid, key


def root_size():
    info = sh(["xwininfo", "-root"])
    w = re.search(r"Width:\s*(\d+)", info)
    h = re.search(r"Height:\s*(\d+)", info)
    return (int(w.group(1)), int(h.group(1))) if (w and h) else (1920, 1080)


def active_geom(root_w, root_h):
    root = sh(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    m = re.search(r"0x[0-9a-fA-F]+", root)
    if not m or m.group(0) == "0x0":
        return None
    info = sh(["xwininfo", "-id", m.group(0)])
    def g(pat):
        mm = re.search(pat, info)
        return int(mm.group(1)) if mm else None
    x = g(r"Absolute upper-left X:\s*(-?\d+)")
    y = g(r"Absolute upper-left Y:\s*(-?\d+)")
    w = g(r"Width:\s*(\d+)")
    h = g(r"Height:\s*(\d+)")
    if None in (x, y, w, h):
        return None
    # Clamp to the visible screen so ffmpeg never grabs out of bounds.
    x = max(0, min(x, root_w - 2))
    y = max(0, min(y, root_h - 2))
    w = max(2, min(w, root_w - x))
    h = max(2, min(h, root_h - y))
    w -= w % 2
    h -= h % 2
    return x, y, w, h


def capture(geom):
    x, y, w, h = geom
    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-f", "x11grab", "-video_size", f"{w}x{h}", "-i", f"{DISPLAY}+{x},{y}",
        "-frames:v", "1", "-vf", f"scale={OUT_WIDTH}:-2", "-q:v", "7",
        "-y", TMP_JPG,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=8)
    except Exception:
        return None
    if not os.path.exists(TMP_JPG) or os.path.getsize(TMP_JPG) == 0:
        return None
    with open(TMP_JPG, "rb") as f:
        return f.read()


def push(aid, key, jpg):
    import requests
    payload = {"agentId": aid, "image": base64.b64encode(jpg).decode("ascii")}
    if key:
        payload["joinKey"] = key
    r = requests.post(f"{OFFICE_URL}/agent-screen", json=payload, timeout=10)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


def main():
    aid, key = resolve_agent()
    root_w, root_h = root_size()
    print(f"Screen pusher → {OFFICE_URL}/agent-screen  agent={aid}  "
          f"(joinKey={'set' if key else 'none/host'})  every {INTERVAL}s, width={OUT_WIDTH}px")
    print(f"Screen {root_w}x{root_h} on DISPLAY={DISPLAY}. Ctrl+C to stop.")
    try:
        while True:
            geom = active_geom(root_w, root_h)
            if geom:
                jpg = capture(geom)
                if jpg:
                    try:
                        code, resp = push(aid, key, jpg)
                        if code != 200 or not resp.get("ok"):
                            print(f"⚠️  push {code}: {resp.get('msg', '')}")
                            if code in (403, 404):
                                print("   (bad agentId/joinKey or not joined — stopping)")
                                sys.exit(1)
                    except Exception as e:
                        print(f"⚠️  push error: {e}")
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\n👋 screen pusher stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
