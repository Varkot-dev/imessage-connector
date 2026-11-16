"""Change detection tests with a fake message source. Run: python -m pytest tests -q"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imessage_plans import changes, state  # noqa: E402
from imessage_plans.db import Message  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(state, "STATE_FILE", str(tmp_path / "state.json"))


class FakeDB:
    def __init__(self, msgs):
        self.msgs = msgs

    def fetch(self, since, chat_id=None, **_):
        return [m for m in self.msgs if m.sent_at >= since and (chat_id is None or m.chat_id == chat_id)]


def msg(rowid, text, mins_ago, chat_id=7, sender="Dan", now=None):
    now = now or datetime.now().astimezone()
    return Message(rowid, f"g{rowid}", chat_id, "Dan", False, sender, sender == "me",
                   now - timedelta(minutes=mins_ago), text)


@pytest.mark.parametrize("text", [
    "can we do 6 instead", "something came up sorry", "can we push it to thurs",
    "cant make it anymore", "rain check?", "actually can we do 7pm", "are we still on for tmrw",
])
def test_detects_changes(text):
    assert changes.looks_like_change(text), text


@pytest.mark.parametrize("text", ["bet", "see you there", "lol", "McClellan?", "[Claude] Possible change"])
def test_ignores_normal(text):
    assert not changes.looks_like_change(text), text


def _added_plan(now):
    start = (now + timedelta(days=2)).replace(microsecond=0).isoformat()
    pid = state.register_plan("Dinner with Dan", start, "Dan", chat_id=7)["plan_id"]
    state.record_decision(pid, "added", "evt123")
    return pid


def test_alerts_once_and_only_for_added(monkeypatch):
    now = datetime.now().astimezone()
    _added_plan(now)
    state.register_plan("Hang with Raghav", (now + timedelta(days=3)).date().isoformat(), "Raghav", chat_id=9)
    later = now + timedelta(minutes=10)
    db = FakeDB([msg(1, "bet", -2, now=now), msg(2, "can we do 6 instead", -5, now=now),
                 msg(3, "can we push it", -6, chat_id=9, sender="Raghav", now=now)])
    sent = []
    out = changes.check(db, lambda b: sent.append(b) or {"ok": True}, now=later)
    assert len(out["alerts"]) == 1 and out["alerts"][0]["calendar_event_id"] == "evt123"
    assert len(sent) == 1 and "6 instead" in sent[0] and "not been changed" in sent[0]
    out2 = changes.check(db, lambda b: sent.append(b), now=later + timedelta(minutes=30))
    assert out2["alerts"] == [] and len(sent) == 1


def test_skips_past_plans():
    now = datetime.now().astimezone()
    pid = state.register_plan("Old thing", (now - timedelta(days=2)).isoformat(), "Dan", chat_id=7)["plan_id"]
    state.record_decision(pid, "added")
    out = changes.check(FakeDB([msg(1, "cancel", 1, now=now)]), None, now=now)
    assert out["alerts"] == []


def test_register_plan_dedups():
    a = state.register_plan("Dinner with Dan", "2026-11-18T17:20:00", "Dan", chat_id=7)
    b = state.register_plan("dinner w dan", "2026-11-18T17:20:00", "Dan")
    assert a["plan_id"] == b["plan_id"] and b["previous_decision"] == "proposed"
