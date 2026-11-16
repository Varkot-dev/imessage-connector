"""Follow-up tracker and self-chat command tests. Run: python -m pytest tests -q"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imessage_plans import followups, selfchat, state  # noqa: E402
from imessage_plans.db import Message  # noqa: E402

SELF = "+15555550123"


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(followups, "FILE", str(tmp_path / "f.json"))
    monkeypatch.setattr(selfchat, "FILE", str(tmp_path / "s.json"))


def msg(rowid, text, mins_ago=1, sender="Praveen", chat="Praveen", group=False, ident="+15551112222", now=None):
    now = now or datetime.now().astimezone()
    return Message(rowid, f"g{rowid}", rowid, chat, group, sender, sender == "me",
                   now - timedelta(minutes=mins_ago), text, extra={"chat_identifier": ident})


@pytest.mark.parametrize("text,from_me,kind", [
    ("Call me ra, need to talk about interview", False, "ask"),
    ("Can I call you later?", False, "ask"),
    ("lmk what u think", False, "ask"),
    ("ill call tmmr", True, "promise"),
    ("I’ll let you know tonight", True, "promise"),
    ("lol that was crazy", False, None),
    ("lmafo", False, None),
    ("let me know", True, None),
])
def test_classify(text, from_me, kind):
    assert followups.classify(text, from_me) == kind


def test_scan_list_done_and_commands():
    now = datetime.now().astimezone()
    msgs = [msg(1, "Call me when you can", sender="Dad", chat="Dad"),
            msg(2, "ill call tmmr before class", sender="me", chat="Dad"),
            msg(3, "lmk", chat="twins", group=True),           # asks in groups are skipped
            msg(4, "call me", sender="me", chat="me", ident=SELF)]  # self chat skipped
    assert len(followups.scan(msgs, SELF, now)) == 2
    assert len(followups.scan(msgs, SELF, now)) == 0           # no duplicates
    text = selfchat.respond("list")
    assert "1. Dad" in text and "2. You told Dad" in text
    assert selfchat.respond("done 1") == "Marked done: 1."
    assert len(followups.open_items()) == 1
    assert selfchat.respond("snooze 2").startswith("Snoozed")
    assert followups.open_items() == []
    assert selfchat.respond("remember to buy milk") is None   # notes are ignored
    assert "Commands" in selfchat.respond("help")


def test_selfchat_answers_once_even_if_duplicated():
    now = datetime.now().astimezone()
    sent = []
    cmds = [msg(10, "help", sender="me", chat="me", ident=SELF, now=now),
            msg(11, "help", sender="me", chat="me", ident=SELF, now=now),  # same text echoed
            msg(12, "help", sender="Dan", chat="Dan", now=now),            # not self chat
            msg(13, "list", mins_ago=120, sender="me", chat="me", ident=SELF, now=now)]  # too old
    out = selfchat.handle_new(cmds, SELF, lambda b: sent.append(b), now)
    assert len(out) == 1 and len(sent) == 1
    assert selfchat.handle_new(cmds, SELF, lambda b: sent.append(b), now) == []
