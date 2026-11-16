"""Read-only Ed Discussion reader.

Uses your personal Ed API token (create one at https://edstem.org/us/settings/api-tokens).
Only GET requests are made, so it can never post, edit, or delete anything on Ed.
Endpoints follow the open-source `edapi` package (Ed has no official public docs):
  GET /api/user                        -> your enrolled courses
  GET /api/courses/<id>/threads        -> threads, newest first
  GET /api/courses/<id>/threads/<num>  -> one thread with answers and comments
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .state import STATE_DIR

ED_BASE = os.environ.get("ED_API_BASE", "https://us.edstem.org/api/")
KEYCHAIN_SERVICE = "ed-api-token"
ED_STATE_FILE = os.path.join(STATE_DIR, "ed_state.json")
STAFF_ROLES = {"staff", "admin", "mentor", "tutor"}


def get_token() -> str | None:
    tok = os.environ.get("ED_API_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _get(path: str, params: dict | None = None) -> dict:
    token = get_token()
    if not token:
        raise RuntimeError("No Ed API token found (ED_API_TOKEN or Keychain item 'ed-api-token').")
    url = urllib.parse.urljoin(ED_BASE, path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Ed rejected the token. It may have expired; make a new one in Ed settings.") from e
        raise


def _load_state() -> dict:
    try:
        with open(ED_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tracked_course_ids": [], "last_check": None}


def _save_state(s: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ED_STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clip(text: str | None, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def list_courses(active_only: bool = True) -> list[dict]:
    data = _get("user")
    tracked = set(_load_state().get("tracked_course_ids", []))
    out = []
    for entry in data.get("courses", []):
        c = entry.get("course", {})
        if active_only and c.get("status") not in (None, "active"):
            continue
        out.append({
            "course_id": c.get("id"), "code": c.get("code"), "name": c.get("name"),
            "session": f"{c.get('session', '')} {c.get('year', '')}".strip(),
            "my_role": (entry.get("role") or {}).get("role"),
            "tracked": c.get("id") in tracked,
        })
    return out


def set_tracked(course_ids: list[int]) -> dict:
    s = _load_state()
    s["tracked_course_ids"] = sorted(set(int(c) for c in course_ids))
    _save_state(s)
    return {"ok": True, "tracked_course_ids": s["tracked_course_ids"]}


def _course_lookup() -> dict[int, str]:
    return {c["course_id"]: (c["code"] or c["name"] or str(c["course_id"])) for c in list_courses(False)}


def updates(course_ids: list[int] | None = None, hours: int | None = None,
            max_threads: int = 60, mark_checked: bool = True) -> dict:
    """New or active threads since the last check (or the last N hours)."""
    s = _load_state()
    now = datetime.now(timezone.utc)
    last = _parse_time(s.get("last_check"))
    since = now - timedelta(hours=hours) if hours else (last or now - timedelta(days=3))
    ids = course_ids or s.get("tracked_course_ids") or []
    if not ids:
        return {"error": "No courses chosen. Call ed_list_courses, then ed_track_courses with the ones you want."}
    names = _course_lookup()
    result = {"since": since.astimezone().isoformat(timespec="minutes"), "courses": []}
    for cid in ids:
        threads, offset = [], 0
        while len(threads) < max_threads:
            page = _get(f"courses/{cid}/threads", {"limit": 50, "offset": offset, "sort": "new"})
            batch = page.get("threads", [])
            staff = {u["id"] for u in page.get("users", []) if (u.get("course_role") or "").lower() in STAFF_ROLES}
            if not batch:
                break
            stop = False
            for t in batch:
                created = _parse_time(t.get("created_at"))
                updated = _parse_time(t.get("updated_at")) or created
                if updated and updated < since and not t.get("is_pinned"):
                    stop = True  # "new" sort is newest first, so older pages are all old
                    continue
                is_new = bool(created and created >= since)
                threads.append({
                    "number": t.get("number"), "title": t.get("title"),
                    "type": t.get("type"), "category": t.get("category"),
                    "is_new": is_new, "is_pinned": t.get("is_pinned"),
                    "by_staff": t.get("user_id") in staff,
                    "answered": t.get("is_answered"), "staff_answered": t.get("is_staff_answered"),
                    "replies": t.get("reply_count"), "unread_replies": t.get("new_reply_count"),
                    "created_at": created.astimezone().isoformat(timespec="minutes") if created else None,
                    "preview": _clip(t.get("document"), 500 if t.get("type") == "announcement" else 220),
                })
            if stop or len(batch) < 50:
                break
            offset += 50
        # Announcements and staff posts first, then new threads, then other activity.
        threads.sort(key=lambda x: (x["type"] != "announcement", not x["by_staff"], not x["is_new"]))
        result["courses"].append({"course_id": cid, "course": names.get(cid, str(cid)),
                                  "threads": threads[:max_threads]})
    if mark_checked and not hours:
        s["last_check"] = now.isoformat(timespec="seconds")
        _save_state(s)
    return result


def get_thread(course_id: int, number: int) -> dict:
    data = _get(f"courses/{course_id}/threads/{number}")
    t = data.get("thread", {})
    users = {u["id"]: u for u in data.get("users", [])}

    def who(uid):
        u = users.get(uid) or {}
        role = (u.get("course_role") or "").lower()
        return f"{u.get('name', 'Anonymous')}{' (staff)' if role in STAFF_ROLES else ''}"

    def flatten(items, depth=0):
        out = []
        for c in items or []:
            out.append({"by": who(c.get("user_id")), "depth": depth, "endorsed": c.get("is_endorsed"),
                        "at": c.get("created_at"), "text": _clip(c.get("document"), 1500)})
            out.extend(flatten(c.get("comments"), depth + 1))
        return out

    return {
        "title": t.get("title"), "type": t.get("type"), "category": t.get("category"),
        "by": who(t.get("user_id")), "created_at": t.get("created_at"),
        "text": _clip(t.get("document"), 4000),
        "answers": flatten(t.get("answers")), "comments": flatten(t.get("comments")),
    }
