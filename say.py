#!/usr/bin/env python3
"""Star Office — make your character say something.

Pushes a custom speech line that your character will speak in the room. The
message sticks until you change it (office-agent-push.py keeps it; it never
clears it). Run with no text (or an empty string) to go back to the default
status chatter.

Usage:
    python3 say.py "shipping the demo, back in 20!"
    python3 say.py ""          # clear custom speech

Config (env):
    OFFICE_URL   office backend (default http://127.0.0.1:19000)
    AGENT_ID     who speaks (default: read office-agent-state.json, else "star")
    JOIN_KEY     guest key (not needed for host/main 'star')
"""

import json
import os
import sys

OFFICE_URL = os.environ.get("OFFICE_URL", "http://127.0.0.1:19000").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_CACHE = os.path.join(HERE, "office-agent-state.json")
STATE_FILE = os.environ.get("STAR_OFFICE_STATE_FILE", os.path.join(HERE, "state.json"))


def resolve_agent():
    aid = os.environ.get("AGENT_ID", "")
    key = os.environ.get("JOIN_KEY", "")
    if not aid and os.path.exists(STATE_CACHE):
        try:
            with open(STATE_CACHE, encoding="utf-8") as f:
                d = json.load(f)
            aid = d.get("agentId") or ""
            key = key or d.get("joinKey") or ""
        except Exception:
            pass
    return (aid or "star"), key


def current_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return (json.load(f).get("state") or "idle")
    except Exception:
        return "idle"


def main():
    import requests
    message = sys.argv[1] if len(sys.argv) > 1 else ""
    aid, key = resolve_agent()
    payload = {"agentId": aid, "state": current_state(), "message": message}
    if key:
        payload["joinKey"] = key
    try:
        r = requests.post(f"{OFFICE_URL}/agent-push", json=payload, timeout=10)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and data.get("ok"):
            print(f'💬 {aid} says: "{message}"' if message else f"💬 {aid} cleared custom speech")
        else:
            print(f"⚠️  failed ({r.status_code}): {data.get('msg', r.text)}")
            sys.exit(1)
    except Exception as e:
        print(f"⚠️  error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
