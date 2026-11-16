"""Background job, run every minute by launchd (installed by install.sh).

Free: no API and no Claude. Each run it:
1. picks up new follow-ups ("call me", "I'll let you know") from your texts,
2. answers any command you texted yourself ("list", "done 2", "help"),
3. every 30 minutes, checks your added plans for reschedules or cancellations,
4. at 12:30 PM and 8:30 PM, texts you the open follow-ups if there are new ones.
It only ever texts you, and never touches your calendar.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from imessage_plans import changes, followups, notify, selfchat  # noqa: E402
from imessage_plans.client import HelperDB, HelperError  # noqa: E402
from imessage_plans.state import STATE_DIR  # noqa: E402

FILE = os.path.join(STATE_DIR, "background.json")
DIGEST_TIMES = [(12, 30), (20, 30)]
CHANGE_EVERY = timedelta(minutes=30)


def _load():
    try:
        with open(FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, FILE)


def run(db, notify_fn, now=None):
    now = now or datetime.now().astimezone()
    st = _load()
    out = {"at": now.isoformat(timespec="seconds")}
    self_ident = notify.get_self_handle()
    last = datetime.fromisoformat(st["last_run"]) if st.get("last_run") else now - timedelta(minutes=35)
    msgs = db.fetch(min(last, now - timedelta(minutes=5)) - timedelta(minutes=2))
    out["new_followups"] = len(followups.scan(msgs, self_ident, now))
    if self_ident:
        out["commands"] = selfchat.handle_new(msgs, self_ident, notify_fn, now)
    if not st.get("last_change_check") or now - datetime.fromisoformat(st["last_change_check"]) >= CHANGE_EVERY:
        out["changes"] = changes.check(db, notify_fn, now)["alerts"]
        st["last_change_check"] = now.isoformat(timespec="seconds")
    for h, m in DIGEST_TIMES:
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = slot.strftime("%Y-%m-%d %H:%M")
        if slot <= now < slot + timedelta(hours=2) and st.get("last_digest") != key:
            st["last_digest"] = key
            items = followups.open_items(now)
            fresh = [i for i in items if i["at"] > st.get("last_digest_at", "")]
            if fresh and notify_fn:
                notify_fn(followups.render_list(items))
                out["digest_sent"] = len(items)
            st["last_digest_at"] = now.isoformat(timespec="minutes")
    st["last_run"] = now.isoformat(timespec="seconds")
    _save(st)
    return out


if __name__ == "__main__":
    db = HelperDB()
    try:
        result = run(db, notify.text_me)
    except HelperError as e:
        result = {"error": str(e)}
    finally:
        db.close()
    if result.get("commands") or result.get("changes") or result.get("digest_sent") or result.get("error"):
        print(json.dumps(result), flush=True)
