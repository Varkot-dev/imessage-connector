"""Remembers which plans were already shown, added, or skipped, so the same plan is
never proposed twice. Stored in ~/.imessage-plans/state.json (never in chat.db)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime

STATE_DIR = os.path.expanduser("~/.imessage-plans")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
STOPWORDS = {"with", "and", "the", "a", "at", "for", "to", "of", "w", "me"}


def _load() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_check": None, "plans": {}}


def _save(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOPWORDS}


def plan_id(plan: dict) -> str:
    key = f"{plan.get('conversation','')}|{plan.get('start','')[:10]}|{' '.join(sorted(_tokens(plan.get('title',''))))}"
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def find_match(state: dict, plan: dict) -> dict | None:
    """Same conversation, same day, and overlapping title words counts as the same plan."""
    day = plan.get("start", "")[:10]
    toks = _tokens(plan.get("title", ""))
    for rec in state["plans"].values():
        if rec.get("conversation") != plan.get("conversation") or rec.get("start", "")[:10] != day:
            continue
        other = _tokens(rec.get("title", ""))
        if not toks or not other or len(toks & other) / min(len(toks), len(other)) >= 0.5:
            return rec
    return None


def annotate_and_record(plans: list[dict]) -> list[dict]:
    """Attach plan_id and previous decision to each plan, and record new ones as 'proposed'."""
    state = _load()
    out = []
    for p in plans:
        match = find_match(state, p)
        if match:
            p["plan_id"] = match["plan_id"]
            p["previous_decision"] = match.get("decision")
            # A later message can change a plan the user already saw.
            p["changed_since_last_seen"] = (
                match.get("start") != p.get("start") or match.get("status") != p.get("status")
            )
        else:
            p["plan_id"] = plan_id(p)
            p["previous_decision"] = None
            p["changed_since_last_seen"] = False
            state["plans"][p["plan_id"]] = {
                "plan_id": p["plan_id"], "title": p.get("title"), "start": p.get("start"),
                "status": p.get("status"), "conversation": p.get("conversation"),
                "decision": "proposed", "first_seen": datetime.now().isoformat(timespec="seconds"),
            }
        out.append(p)
    _save(state)
    return out


def register_plan(title: str, start: str, conversation: str, chat_id: int | None = None,
                  status: str = "confirmed") -> dict:
    """Filter-only mode: the chat Claude reads snippets and registers each plan it shows
    the user, so it gets a plan_id for record_plan_decision and change tracking."""
    p = {"title": title, "start": start, "conversation": conversation, "status": status}
    state = _load()
    match = find_match(state, p)
    if match:
        if chat_id and not match.get("chat_id"):
            match["chat_id"] = chat_id
        _save(state)
        return {"plan_id": match["plan_id"], "previous_decision": match.get("decision"),
                "changed_since_last_seen": match.get("start") != start or match.get("status") != status}
    pid = plan_id(p)
    state["plans"][pid] = {**p, "plan_id": pid, "chat_id": chat_id, "decision": "proposed",
                           "first_seen": datetime.now().astimezone().isoformat(timespec="seconds")}
    _save(state)
    return {"plan_id": pid, "previous_decision": None, "changed_since_last_seen": False}


def record_decision(pid: str, decision: str, calendar_event_id: str | None = None) -> dict:
    state = _load()
    rec = state["plans"].get(pid)
    if not rec:
        return {"ok": False, "error": f"Unknown plan_id {pid}"}
    rec["decision"] = decision
    rec["decided_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if calendar_event_id:
        rec["calendar_event_id"] = calendar_event_id
    _save(state)
    return {"ok": True, "plan": rec}


def get_last_check() -> datetime | None:
    v = _load().get("last_check")
    return datetime.fromisoformat(v) if v else None


def set_last_check(dt: datetime) -> None:
    state = _load()
    state["last_check"] = dt.isoformat(timespec="seconds")
    _save(state)
