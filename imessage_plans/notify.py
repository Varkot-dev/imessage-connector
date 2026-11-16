"""Text-to-self. The ONLY code in this project that sends anything.

Guardrails:
- The recipient is never a parameter. It is read from config.json in the project
  folder (or ~/.imessage-plans/config.json), "self_handle", which only you edit. If it is missing, nothing is sent.
- The message is passed to AppleScript as an argument, not pasted into the script,
  so message text can't change what the script does.
- Every text starts with "[Claude] " so you can tell them apart, and so the plan
  finder ignores them instead of reading its own notifications.
- Rate limited (default 10 per hour) and length capped.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time

from .state import STATE_DIR

PROJECT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
SEND_LOG = os.path.join(STATE_DIR, "send_log.json")
PREFIX = "[Claude] "
MAX_CHARS = 1200
MAX_PER_HOUR = 10

APPLESCRIPT = """
on run argv
    set targetHandle to item 1 of argv
    set theText to item 2 of argv
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        try
            set target to participant targetHandle of svc
        on error
            set target to buddy targetHandle of svc
        end try
        send theText to target
    end tell
end run
"""


def _load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def get_self_handle() -> str | None:
    """Your own number, from config.json in the project folder (or ~/.imessage-plans/config.json)."""
    handle = (_load_json(PROJECT_CONFIG, {}).get("self_handle")
              or _load_json(CONFIG_FILE, {}).get("self_handle") or "").strip()
    if re.fullmatch(r"\+?\d{10,15}", re.sub(r"[\s\-().]", "", handle)):
        return re.sub(r"[\s\-().]", "", handle)
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", handle):
        return handle
    return None


def text_me(message: str) -> dict:
    handle = get_self_handle()
    if not handle:
        return {"ok": False, "error": f'No valid "self_handle" in {CONFIG_FILE}. Add your own phone number or Apple ID email there.'}
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "Empty message."}
    now = time.time()
    log = [t for t in _load_json(SEND_LOG, []) if now - t < 3600]
    if len(log) >= MAX_PER_HOUR:
        return {"ok": False, "error": f"Rate limit: already sent {MAX_PER_HOUR} texts in the last hour."}
    body = PREFIX + (message if len(message) <= MAX_CHARS else message[:MAX_CHARS - 3] + "...")
    try:
        r = subprocess.run(["osascript", "-e", APPLESCRIPT, handle, body],
                           capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return {"ok": False, "error": "osascript not found (this only works on a Mac)."}
    if r.returncode != 0:
        return {"ok": False, "error": "Messages refused the send. Allow Claude to control Messages in "
                "System Settings > Privacy & Security > Automation. Details: " + r.stderr.strip()[:300]}
    log.append(now)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SEND_LOG, "w") as f:
        json.dump(log, f)
    return {"ok": True, "sent_to": "you", "characters": len(body)}
