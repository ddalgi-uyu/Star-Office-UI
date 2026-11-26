#!/usr/bin/env python3
"""Star Office presence watcher (X11 / GNOME).

Polls the foreground window with `xprop`, maps the active app to a Star Office
state + a short bubble label, and writes it to the local state.json that the
office reads (host "main agent") or that office-agent-push.py forwards (guest).

Only the state word + a short "which app" label are written — never window
contents. Set SHOW_TITLE=1 to also include a trimmed window title (more detail,
less private).

Usage:
    python3 presence_watch.py
Env:
    STAR_OFFICE_STATE_FILE   where to write state.json (default: ./state.json)
    PRESENCE_INTERVAL        poll seconds (default: 4)
    SHOW_TITLE               "1" to append a short window title
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

STATE_FILE = os.environ.get(
    "STAR_OFFICE_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"),
)
INTERVAL = float(os.environ.get("PRESENCE_INTERVAL", "4"))
SHOW_TITLE = os.environ.get("SHOW_TITLE", "0") in {"1", "true", "yes"}

# Match on a substring of WM_CLASS (lowercased) -> (state, bubble label).
# First match wins; order matters (specific before generic).
APP_MAP = [
    (("isaac", "omniverse", "kit", "nvidia-omniverse"), "executing", "🤖 Isaac Sim"),
    (("blender",), "writing", "🎨 Blender"),
    (("unreal", "unrealeditor"), "executing", "🎮 Unreal"),
    (("code", "cursor", "vscodium", "codium"), "writing", "💻 Coding"),
    (("jetbrains", "pycharm", "idea", "clion"), "writing", "💻 Coding"),
    (("firefox", "chrome", "chromium", "brave", "edge"), "researching", "🔎 Browsing"),
    (("gnome-terminal", "konsole", "kitty", "alacritty", "xterm", "wezterm", "tilix"),
     "executing", "⌨️ Terminal"),
    (("obsidian", "typora", "logseq"), "writing", "📝 Notes"),
    (("slack", "discord", "telegram", "zoom", "teams"), "researching", "💬 Comms"),
    (("nautilus", "files", "dolphin"), "idle", "📁 Files"),
]


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return ""


def active_window():
    root = sh(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    m = re.search(r"0x[0-9a-fA-F]+", root)
    if not m:
        return None, None
    wid = m.group(0)
    if wid == "0x0":
        return None, None
    cls_raw = sh(["xprop", "-id", wid, "WM_CLASS"])
    classes = re.findall(r'"([^"]*)"', cls_raw)
    wm_class = " ".join(classes).lower()
    name_raw = sh(["xprop", "-id", wid, "_NET_WM_NAME"])
    nm = re.search(r'"(.*)"', name_raw)
    title = nm.group(1) if nm else ""
    return wm_class, title


def map_app(wm_class, title):
    if not wm_class:
        return "idle", "🛋 Away", ""
    for keys, state, label in APP_MAP:
        if any(k in wm_class for k in keys):
            return state, label, keys[0]
    # Unknown app: still show it as actively in-app, using a cleaned class name.
    tokens = [t for t in re.split(r"[\s._-]+", wm_class) if t]
    name = tokens[-1].capitalize() if tokens else "App"
    return "executing", f"🖥 {name}", name


def write_state(state, detail, app=""):
    data = {
        "state": state,
        "detail": detail,
        "app": app,
        "progress": 0,
        "updated_at": datetime.now().isoformat(),
    }
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def main():
    print(f"Presence watcher → {STATE_FILE} (every {INTERVAL}s, SHOW_TITLE={SHOW_TITLE})")
    last = None
    try:
        while True:
            wm_class, title = active_window()
            state, label, app = map_app(wm_class, title)
            detail = label
            if SHOW_TITLE and title:
                detail = f"{label} — {title[:40]}"
            if detail != last:
                write_state(state, detail, app)
                print(f"[{datetime.now():%H:%M:%S}] {state:10} {detail}  (app={app})")
                last = detail
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\n👋 presence watcher stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
