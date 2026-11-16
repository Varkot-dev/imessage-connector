"""Follow-up tracker. Free and local: plain keyword matching, no API, no Claude.

Catches two kinds of loose ends in your texts:
- someone asking you to get back to them ("call me", "lmk", "can I call you later")
- you promising something ("ill call tmmr", "I'll let you know", "will send it")

Open items are texted to you as a numbered list. Reply "done 2", "snooze 1",
"list", or "help" (see selfchat.py). Items expire on their own after a few days.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta

from .state import STATE_DIR

FILE = os.path.join(STATE_DIR, "followups.json")
EXPIRE_DAYS = 4

THEM_ASK = [
    "call me", "can you call", "can u call", "can i call you", "can i call u", "call you later",
    "call u later", "ft me", "facetime me", "lmk", "let me know", "text me back", "text me when",
    "get back to me", "hmu when", "can we talk", "need to talk", "wanna talk", "talk later",
    "did you see my", "did u see my", "send me", "can you send", "can u send",
]
ME_PROMISE = [
    "i'll call", "ill call", "i will call", "i'll let you know", "ill let you know", "ill let u know",
    "i'll let u know", "will lyk", "i'll lyk", "ill lyk", "i'll text", "ill text", "i'll send", "ill send",
    "i'll check", "ill check", "i'll get back", "ill get back", "i'll ask", "ill ask", "i'll look into",
]


def _rx(phrases):
    return re.compile("|".join(r"(?<![a-z])" + re.escape(p).replace(r"\ ", r"\s+") + r"(?![a-z])"
                               for p in phrases), re.IGNORECASE)


_THEM, _ME = _rx(THEM_ASK), _rx(ME_PROMISE)


def classify(text: str, from_me: bool) -> str | None:
    t = (text or "").replace("’", "'")
    if from_me:
        return "promise" if _ME.search(t) else None
    return "ask" if _THEM.search(t) else None


def _load() -> dict:
    try:
        with open(FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": {}, "last_scan": None, "numbering": {}}


def _save(d: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, FILE)


def scan(messages, self_ident: str | None = None, now: datetime | None = None) -> list[dict]:
    """Add new follow-ups from messages. Returns only the newly added items."""
    now = now or datetime.now().astimezone()
    d = _load()
    new = []
    for m in messages:
        if m.kind != "message" or (self_ident and _is_self(m, self_ident)):
            continue
        kind = classify(m.text, m.is_from_me)
        if not kind or (kind == "ask" and m.is_group):
            continue
        key = str(m.rowid)
        if key in d["items"]:
            continue
        quote = m.text.strip().replace("\n", " ")
        item = {
            "id": key, "kind": kind, "chat": m.chat_name, "from": m.sender,
            "text": quote if len(quote) <= 90 else quote[:87] + "...",
            "at": m.sent_at.isoformat(timespec="minutes"), "status": "open", "snooze_until": None,
        }
        d["items"][key] = item
        new.append(item)
    d["last_scan"] = now.isoformat(timespec="seconds")
    _save(d)
    return new


def _is_self(m, self_ident: str) -> bool:
    ident = (m.extra or {}).get("chat_identifier") or ""
    return _norm(ident) == _norm(self_ident)


def _norm(h: str) -> str:
    if "@" in (h or ""):
        return h.lower()
    digits = re.sub(r"\D", "", h or "")
    return digits[-10:]


def open_items(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now().astimezone()
    d = _load()
    out = []
    for it in d["items"].values():
        if it["status"] != "open":
            continue
        if datetime.fromisoformat(it["at"]) < now - timedelta(days=EXPIRE_DAYS):
            continue
        if it.get("snooze_until") and datetime.fromisoformat(it["snooze_until"]) > now:
            continue
        out.append(it)
    return sorted(out, key=lambda i: i["at"])


def render_list(items: list[dict]) -> str:
    """Numbered list, and remembers which number means which item for replies."""
    d = _load()
    d["numbering"] = {str(i + 1): it["id"] for i, it in enumerate(items)}
    _save(d)
    if not items:
        return "No open follow-ups."
    lines = ["Follow-ups:"]
    for i, it in enumerate(items, 1):
        if it["kind"] == "ask":
            lines.append(f'{i}. {it["from"]}: "{it["text"]}"')
        else:
            lines.append(f'{i}. You told {it["chat"]}: "{it["text"]}"')
    lines.append('Reply "done 1", "snooze 2", "done all", or "help".')
    return "\n".join(lines)


def set_status(numbers: list[str], status: str, now: datetime | None = None) -> list[str]:
    now = now or datetime.now().astimezone()
    d = _load()
    changed = []
    targets = list(d["numbering"].keys()) if numbers == ["all"] else numbers
    for n in targets:
        iid = d["numbering"].get(n)
        it = d["items"].get(iid) if iid else None
        if not it:
            continue
        if status == "snoozed":
            it["snooze_until"] = (now + timedelta(days=1)).isoformat(timespec="minutes")
        else:
            it["status"] = status
        changed.append(n)
    _save(d)
    return changed
