"""Tests against a fake chat.db with the same schema as the real one.
Run: python -m pytest tests -q"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from imessage_plans import db as dbmod  # noqa: E402
from imessage_plans import state  # noqa: E402
from imessage_plans.db import MessagesDB, decode_attributed_body, local_to_apple_ns  # noqa: E402
from imessage_plans.prefilter import run_filter  # noqa: E402


def typedstream_blob(text: str) -> bytes:
    """Same byte layout Messages uses for attributedBody."""
    b = text.encode()
    ln = bytes([len(b)]) if len(b) < 128 else b"\x81" + len(b).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
            b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + ln + b +
            b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x86\x86")


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(state, "STATE_FILE", str(tmp_path / "state" / "state.json"))
    monkeypatch.setattr(dbmod, "ADDRESSBOOK_GLOB", str(tmp_path / "none" / "*.abcddb"))
    path = tmp_path / "chat.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB,
            handle_id INTEGER, date INTEGER, is_from_me INTEGER, associated_message_type INTEGER DEFAULT 0,
            associated_message_guid TEXT, service TEXT, date_retracted INTEGER DEFAULT 0, reply_to_guid TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        INSERT INTO handle VALUES (1, '+15025551234'), (2, '+12155550000'), (3, '56789'), (4, '+12155559999');
        INSERT INTO chat VALUES (1, 'c1', '+15025551234', NULL), (2, 'c2', 'grp', 'ESE squad'), (3, 'c3', '56789', NULL);
        INSERT INTO chat_handle_join VALUES (1, 1), (2, 2), (2, 4), (3, 3);
    """)
    now = datetime.now().astimezone()
    rows = [
        # (chat, handle, from_me, minutes_ago, text, blob_text, assoc_type, assoc_guid, retracted)
        (1, 1, 0, 300, "how was the exam", None, 0, None, 0),
        (1, 1, 0, 290, None, "u tryna get dinner tmrw at 7?", 0, None, 0),  # text only in attributedBody
        (1, 0, 1, 285, "bet", None, 0, None, 0),
        (1, 0, 1, 200, "lol that video was crazy", None, 0, None, 0),
        (2, 2, 0, 120, "lab report is due friday", None, 0, None, 0),
        (2, 4, 0, 118, "wanna study at the lib after class", None, 0, None, 0),
        (2, 0, 1, 117, None, None, 2000, "p:0/m6", 0),  # "me" loved the study message
        (2, 2, 0, 60, "meet at 8pm then", None, 0, None, 1),  # unsent, must be skipped
        (3, 3, 0, 30, "Your verification code is 123456 expires tonight", None, 0, None, 0),  # short code
        (1, 1, 0, 10, "ok", None, 0, None, 0),
    ]
    for i, (chat, h, me, ago, text, blob, at, ag, ret) in enumerate(rows, start=1):
        d = local_to_apple_ns(now - timedelta(minutes=ago))
        con.execute(
            "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, date, is_from_me, "
            "associated_message_type, associated_message_guid, service, date_retracted) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (i, f"m{i}", text, typedstream_blob(blob) if blob else None, h, d, me, at, ag, "iMessage",
             local_to_apple_ns(now) if ret else 0),
        )
        con.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, i))
    con.commit()
    con.close()
    return str(path)


def test_decoder_long_and_unicode():
    assert decode_attributed_body(typedstream_blob("café ☕ fri 7pm")) == "café ☕ fri 7pm"
    long = "x" * 300 + " dinner"
    assert decode_attributed_body(typedstream_blob(long)) == long
    assert decode_attributed_body(None) is None


def test_reads_attributed_body_reactions_and_skips(fake_db):
    db = MessagesDB(fake_db)
    msgs = db.fetch(datetime.now().astimezone() - timedelta(days=1))
    texts = [m.text for m in msgs]
    assert "u tryna get dinner tmrw at 7?" in texts           # decoded from attributedBody
    assert any(t.startswith('(loved "wanna study') for t in texts)  # reaction kept as context
    assert "meet at 8pm then" not in texts                     # unsent skipped
    assert not any("verification code" in t for t in texts)    # short code skipped
    assert {m.chat_name for m in msgs if m.chat_id == 2} == {"ESE squad"}


def test_database_is_read_only(fake_db):
    db = MessagesDB(fake_db)
    with pytest.raises(sqlite3.OperationalError):
        db.con.execute("DELETE FROM message")
    with pytest.raises(sqlite3.OperationalError):
        db.con.execute("INSERT INTO message (ROWID) VALUES (999)")


def test_filter_keeps_plans_with_confirmations(fake_db):
    db = MessagesDB(fake_db)
    msgs = db.fetch(datetime.now().astimezone() - timedelta(days=1))
    res = run_filter(msgs)
    kept_text = [m.text for s in res.snippets for m in s.messages]
    assert "u tryna get dinner tmrw at 7?" in kept_text
    assert "bet" in kept_text                                    # confirmation pulled in as context
    assert any(t.startswith("(loved") for t in kept_text)       # tapback confirmation kept


@pytest.mark.parametrize("text", [
    "u down to hop on later", "pull up rn", "wanna grab food", "dinner @ 7", "7p?",
    "can we push it to thurs", "free tmr?", "cant make it anymore", "see u at 5:30",
    "lets meet up 11/23", "practice moved to the 3rd", "ft in 20 mins",
])
def test_filter_catches_slang(text):
    from imessage_plans.prefilter import _build_regex
    assert _build_regex().search(text), text


@pytest.mark.parametrize("text", ["lol that video was crazy", "how was the exam", "ok", "true"])
def test_filter_skips_chatter(text):
    from imessage_plans.prefilter import _build_regex
    assert not _build_regex().search(text), text


def test_extra_keywords_file(tmp_path, monkeypatch):
    from imessage_plans import prefilter
    f = tmp_path / "extra.txt"
    f.write_text("# comment\nskrrt over\n")
    monkeypatch.setattr(prefilter, "EXTRA_KEYWORDS_FILE", str(f))
    assert prefilter._build_regex().search("ima skrrt over there")


def test_state_dedup(fake_db):
    p = {"title": "Dinner with Sam", "start": "2026-11-18T19:00:00", "status": "confirmed",
         "conversation": "Sam"}
    first = state.annotate_and_record([dict(p)])[0]
    assert first["previous_decision"] is None
    state.record_decision(first["plan_id"], "added", "evt123")
    again = state.annotate_and_record([dict(p, title="Dinner w/ Sam")])[0]
    assert again["plan_id"] == first["plan_id"] and again["previous_decision"] == "added"
    assert again["changed_since_last_seen"] is False
    moved = state.annotate_and_record([dict(p, start="2026-11-18T20:00:00")])[0]
    assert moved["changed_since_last_seen"] is True


def test_server_filter_only_mode(fake_db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import server
    monkeypatch.setattr(server, "_haiku_enabled", lambda: False)
    monkeypatch.setattr(server, "_db", lambda: MessagesDB(fake_db))
    out = server.find_plans(hours=24)
    assert out["mode"] == "filter_only"
    assert any("dinner tmrw" in s for s in out["snippets_text"])


def test_server_with_fake_haiku(fake_db, monkeypatch):
    import server
    monkeypatch.setattr(server, "_haiku_enabled", lambda: True)
    monkeypatch.setattr(server, "_db", lambda: MessagesDB(fake_db))
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT19:00:00")
    fake = [{"title": "Dinner", "start": tomorrow, "time_known": True, "status": "confirmed",
             "confidence": 0.9, "evidence": "dinner tmrw at 7 / bet", "conversation": "+15025551234"},
            {"title": "Old thing", "start": "2020-01-01", "time_known": False, "status": "confirmed",
             "confidence": 0.9, "evidence": "x", "conversation": "+15025551234"}]
    monkeypatch.setattr(server, "extract_plans", lambda snips, now: (fake, {"calls": 1}))
    out = server.find_plans(hours=24)
    assert [p["title"] for p in out["plans"]] == ["Dinner"]     # past plan dropped
    out2 = server.find_plans(hours=24)
    assert [p["title"] for p in out2["plans"]] == ["Dinner"]    # still proposed, not decided
    server.record_plan_decision(out["plans"][0]["plan_id"], "added", "evt1")
    out3 = server.find_plans(hours=24)
    assert out3["plans"] == []                                   # never proposed twice


def test_sending_code_only_in_notify():
    bad = ("osascript", "applescript", "tell application", "nsapplescript")
    for dirpath, _, files in os.walk(ROOT):
        if "tests" in dirpath or ".venv" in dirpath:
            continue
        for f in files:
            if f.endswith(".py") and f != "notify.py":
                src = open(os.path.join(dirpath, f)).read().lower()
                assert not any(b in src for b in bad), f


def test_text_me_has_no_recipient_parameter():
    import inspect
    import server
    from imessage_plans import notify
    assert list(inspect.signature(notify.text_me).parameters) == ["message"]
    assert list(inspect.signature(server.text_me).parameters) == ["message"]


def test_text_me_guardrails(tmp_path, monkeypatch):
    from imessage_plans import notify
    monkeypatch.setattr(notify, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(notify, "PROJECT_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setattr(notify, "SEND_LOG", str(tmp_path / "log.json"))
    monkeypatch.setattr(notify, "STATE_DIR", str(tmp_path))
    assert notify.text_me("hi")["ok"] is False                 # no config, nothing sent
    (tmp_path / "config.json").write_text('{"self_handle": "not a number"}')
    assert notify.text_me("hi")["ok"] is False                 # invalid handle refused
    (tmp_path / "config.json").write_text('{"self_handle": "+1 (502) 555-0100"}')
    sent = []
    class R:
        returncode = 0
        stderr = ""
    def fake_run(args, **kw):
        sent.append(args)
        return R()
    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    for _ in range(10):
        assert notify.text_me('evil" & do shell script "rm"')["ok"] is True
    assert notify.text_me("one too many")["ok"] is False      # rate limit
    args = sent[0]
    assert args[3] == "+15025550100"                           # recipient from config only
    assert args[4].startswith("[Claude] ")                    # text passed as an argument
    assert 'evil"' not in args[2]                              # never pasted into the script


def test_own_notifications_ignored(fake_db):
    con = sqlite3.connect(fake_db)
    con.execute("INSERT INTO message (ROWID, guid, text, handle_id, date, is_from_me, associated_message_type) "
                "VALUES (99, 'm99', '[Claude] 2 new plans: dinner tmrw at 7', 0, ?, 1, 0)",
                (local_to_apple_ns(datetime.now().astimezone()),))
    con.execute("INSERT INTO chat_message_join VALUES (1, 99)")
    con.commit()
    con.close()
    msgs = MessagesDB(fake_db).fetch(datetime.now().astimezone() - timedelta(days=1))
    assert not any(m.text.startswith("[Claude]") for m in msgs)


def test_api_is_opt_in(monkeypatch):
    import server
    monkeypatch.setattr(server, "get_api_key", lambda: "k")
    monkeypatch.delenv("PLANS_USE_HAIKU_API", raising=False)
    assert server._haiku_enabled() is False          # a stray key alone never costs money
    monkeypatch.setenv("PLANS_USE_HAIKU_API", "1")
    assert server._haiku_enabled() is True


def test_helper_roundtrip(fake_db, tmp_path, monkeypatch):
    """Connector -> socket -> helper -> chat.db, the way it runs on the Mac."""
    import threading
    import service
    from imessage_plans import client
    sock = str(tmp_path / "h" / "messages.sock")
    monkeypatch.setattr(service, "STATE_DIR", str(tmp_path / "h"))
    monkeypatch.setattr(service, "SOCKET", sock)
    monkeypatch.setattr(service, "MessagesDB", lambda: MessagesDB(fake_db))
    monkeypatch.setattr(service, "CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(client, "SOCKET", sock)
    assert client.ping() is False                               # not running yet
    t = threading.Thread(target=service.main, daemon=True)
    t.start()
    for _ in range(50):
        if client.ping():
            break
        import time; time.sleep(0.05)
    assert oct(os.stat(sock).st_mode & 0o777) == "0o600"        # owner-only socket
    db = client.HelperDB()
    msgs = db.fetch(datetime.now().astimezone() - timedelta(days=1))
    assert "u tryna get dinner tmrw at 7?" in [m.text for m in msgs]
    assert {c["name"] for c in db.recent_chats(datetime.now().astimezone() - timedelta(days=1))} >= {"ESE squad"}
    (tmp_path / "cfg.json").write_text('{"exclude_chats": ["ese squad"]}')
    assert "ESE squad" not in {m.chat_name for m in db.fetch(datetime.now().astimezone() - timedelta(days=1))}
    with pytest.raises(client.HelperError):
        client._request({"op": "drop_tables"})                  # unknown ops refused
