"""Change detection for plans already on the calendar. Free and local: plain
keyword matching, no API and no Claude usage.

For every plan you added (decision "added") whose time has not passed, it reads
new messages in that plan's conversation and looks for reschedule or cancel
language ("can we do 6 instead", "something came up", "push it to thurs").
When it finds one, it texts you (only you, through notify.text_me) and remembers
the message so you are never alerted twice for it.

It never edits or deletes calendar events. It only tells you.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import state

CHANGE_PHRASES = [
    "reschedule", "resched", "postpone", "cancel", "cancelled", "canceled", "rain check", "raincheck",
    "push it", "push back", "pushback", "move it", "move to", "moved to", "instead",
    "can't make", "cant make", "can't come", "cant come", "can't do", "cant do", "not gonna make",
    "won't make", "wont make", "something came up", "smth came up", "change of plans",
    "different time", "different day", "another time", "another day", "later instead",
    "earlier instead", "still on", "are we still", "we still on", "no longer", "gotta bail",
    "have to bail", "bail on", "next week instead", "how about", "what about", "rather do",
]
TIME_RX = (r"\b\d{1,2}(:\d{2})?\s?(am|pm|a|p)\b|\b\d{1,2}:\d{2}\b|\b(mon|tue|tues|wed|weds|thu|thur|thurs|"
           r"fri|sat|sun)(day)?\b|\b(tmrw|tmr|tomorrow|tonight|today)\b")
SOFT_RX = r"\b(can we|could we|can u|can you|actually|wait|lets do|let's do|do)\b"

_HARD = re.compile("|".join(r"\b" + re.escape(p).replace(r"\ ", r"\s+") + r"\b" for p in CHANGE_PHRASES),
                   re.IGNORECASE)
_TIME = re.compile(TIME_RX, re.IGNORECASE)
_SOFT = re.compile(SOFT_RX, re.IGNORECASE)


def looks_like_change(text: str) -> bool:
    """A clear change phrase, or a new time/day said in a 'can we / actually' way."""
    if not text or text.startswith("[Claude]"):
        return False
    return bool(_HARD.search(text)) or bool(_TIME.search(text) and _SOFT.search(text))


def _start_dt(rec: dict, now: datetime) -> datetime | None:
    s = rec.get("start") or ""
    try:
        dt = datetime.fromisoformat(s if len(s) > 10 else s + "T23:59:00")
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=now.tzinfo)


def _when(dt: datetime | None, raw: str) -> str:
    if not dt:
        return raw
    if len(raw) <= 10:
        return dt.strftime("%a %b %-d")
    return dt.strftime("%a %b %-d, %-I:%M %p")


def check(db, notify_fn=None, now: datetime | None = None) -> dict:
    """Scan conversations of upcoming added plans. Texts you (via notify_fn) once per
    newly found change message. Returns what it found."""
    now = now or datetime.now().astimezone()
    data = state._load()
    alerts = []
    for rec in data["plans"].values():
        if rec.get("decision") != "added" or not rec.get("chat_id"):
            continue
        start = _start_dt(rec, now)
        if start and start < now - timedelta(hours=1):
            continue
        since_raw = rec.get("changes_checked_at") or rec.get("decided_at") or rec.get("first_seen")
        since = datetime.fromisoformat(since_raw) if since_raw else now - timedelta(hours=48)
        if since.tzinfo is None:
            since = since.replace(tzinfo=now.tzinfo)
        seen = set(rec.get("alerted_rowids", []))
        msgs = db.fetch(since - timedelta(minutes=5), chat_id=rec["chat_id"])
        hits = [m for m in msgs if m.kind == "message" and m.rowid not in seen and looks_like_change(m.text)]
        rec["changes_checked_at"] = now.isoformat(timespec="seconds")
        if not hits:
            continue
        rec["alerted_rowids"] = sorted(seen | {m.rowid for m in hits})
        quote = hits[-1].text.strip().replace("\n", " ")
        quote = quote if len(quote) <= 120 else quote[:117] + "..."
        who = hits[-1].sender
        alerts.append({
            "plan_id": rec["plan_id"], "title": rec.get("title"), "start": rec.get("start"),
            "calendar_event_id": rec.get("calendar_event_id"), "from": who, "message": quote,
            "text": (f'Possible change to "{rec.get("title")}" ({_when(start, rec.get("start", ""))}). '
                     f'{who}: "{quote}". Your calendar has not been changed.'),
        })
    state._save(data)
    sent = None
    if alerts and notify_fn:
        body = alerts[0]["text"] if len(alerts) == 1 else "Possible plan changes:\n" + "\n".join(
            "- " + a["text"] for a in alerts)
        sent = notify_fn(body)
    return {"checked_at": now.isoformat(timespec="seconds"), "alerts": alerts, "texted": sent}
