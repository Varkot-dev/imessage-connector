"""Replies you text to yourself. Free and local.

The connector only listens in your own self-chat (the conversation with the number in
config.json), and only to short commands. Anything else you text yourself is ignored,
so notes-to-self are safe. Commands only change the connector's own state files: they
never send to anyone else and never touch your calendar.

Commands: list, done 1, done 1 3, done all, snooze 2, ok, help
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta

from . import followups
from .state import STATE_DIR

FILE = os.path.join(STATE_DIR, "selfchat.json")
HELP = ('Commands: "list" (open follow-ups), "done 1" or "done 1 3" or "done all", '
        '"snooze 2" (hide until tomorrow), "ok" (dismiss a change alert), "help".')
CMD = re.compile(r"^\s*(list|ls|help|\?|ok|okay|got it|(done|snooze)\s+((\d+[\s,]*)+|all))\s*[.!]?\s*$",
                 re.IGNORECASE)


def _load() -> dict:
    try:
        with open(FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"handled_rowids": [], "handled_keys": []}


def _save(d: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    d["handled_rowids"] = d["handled_rowids"][-500:]
    d["handled_keys"] = d["handled_keys"][-500:]
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, FILE)


def respond(text: str) -> str | None:
    m = CMD.match(text or "")
    if not m:
        return None
    word = m.group(1).lower().split()[0]
    if word in ("help", "?"):
        return HELP
    if word in ("list", "ls"):
        return followups.render_list(followups.open_items())
    if word in ("ok", "okay", "got"):
        return "Got it."
    nums = ["all"] if m.group(3).lower() == "all" else re.findall(r"\d+", m.group(3))
    status = "done" if word == "done" else "snoozed"
    changed = followups.set_status(nums, status)
    if not changed:
        return 'No match. Reply "list" to see current numbers.'
    verb = "Marked done" if status == "done" else "Snoozed until tomorrow"
    return f"{verb}: {', '.join(changed)}."


def handle_new(messages, self_ident: str, notify_fn, now: datetime | None = None) -> list[dict]:
    """Answer new commands in the self-chat. Messages sent from your phone to your own
    number can show up twice on the Mac (sent and received), so each is answered once."""
    now = now or datetime.now().astimezone()
    d = _load()
    done_rows, done_keys = set(d["handled_rowids"]), set(d["handled_keys"])
    out = []
    for m in messages:
        if m.kind != "message" or not followups._is_self(m, self_ident) or m.rowid in done_rows:
            continue
        if m.sent_at < now - timedelta(minutes=30):
            done_rows.add(m.rowid)
            continue  # never answer old texts (first run, or the Mac was asleep)
        key = f"{m.text.strip().lower()}|{m.sent_at.strftime('%Y%m%d%H%M')}"
        done_rows.add(m.rowid)
        if key in done_keys:
            continue
        reply = respond(m.text)
        if reply is None:
            continue
        done_keys.add(key)
        sent = notify_fn(reply) if notify_fn else None
        out.append({"command": m.text.strip(), "reply": reply, "sent": sent})
    d["handled_rowids"], d["handled_keys"] = sorted(done_rows), sorted(done_keys)
    _save(d)
    return out
