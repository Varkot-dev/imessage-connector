"""Ed reader tests with a fake API (no network)."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from imessage_plans import ed  # noqa: E402


@pytest.fixture
def fake_ed(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "ED_STATE_FILE", str(tmp_path / "ed_state.json"))
    monkeypatch.setattr(ed, "STATE_DIR", str(tmp_path))
    now = datetime.now(timezone.utc)
    iso = lambda h: (now - timedelta(hours=h)).isoformat()  # noqa: E731
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "user":
            return {"courses": [
                {"course": {"id": 1, "code": "ESE 2180", "name": "Devices", "status": "active",
                            "session": "Fall", "year": "2026"}, "role": {"role": "student"}},
                {"course": {"id": 2, "code": "OLD 101", "name": "Old", "status": "archived"}},
            ]}
        if path == "courses/1/threads":
            return {"users": [{"id": 9, "name": "Prof K", "course_role": "admin"},
                              {"id": 5, "name": "Sam", "course_role": "student"}],
                    "threads": [
                        {"number": 40, "title": "HW5 due date moved", "type": "announcement", "user_id": 9,
                         "created_at": iso(2), "updated_at": iso(2), "document": "HW5 is now due Friday."},
                        {"number": 39, "title": "Q about lab 3", "type": "question", "user_id": 5,
                         "created_at": iso(30), "updated_at": iso(3), "document": "How do I...",
                         "is_answered": True, "is_staff_answered": True},
                        {"number": 10, "title": "Ancient", "type": "post", "user_id": 5,
                         "created_at": iso(500), "updated_at": iso(500), "document": "old"},
                    ]}
        if path == "courses/1/threads/39":
            return {"users": [{"id": 9, "name": "Prof K", "course_role": "admin"}],
                    "thread": {"title": "Q about lab 3", "user_id": 5, "document": "How do I...",
                               "answers": [{"user_id": 9, "document": "Use the 10x probe.",
                                            "comments": [{"user_id": 5, "document": "thanks"}]}],
                               "comments": []}}
        raise AssertionError(path)

    monkeypatch.setattr(ed, "_get", fake_get)
    return calls


def test_courses_and_tracking(fake_ed):
    courses = ed.list_courses()
    assert [c["code"] for c in courses] == ["ESE 2180"]   # archived hidden
    ed.set_tracked([1])
    assert ed.list_courses()[0]["tracked"] is True


def test_updates_order_and_window(fake_ed):
    ed.set_tracked([1])
    out = ed.updates(hours=24)
    threads = out["courses"][0]["threads"]
    assert [t["number"] for t in threads] == [40, 39]      # announcement first, old thread excluded
    assert threads[0]["by_staff"] and threads[0]["is_new"]
    assert threads[1]["is_new"] is False                   # old thread with new activity
    assert out["courses"][0]["course"] == "ESE 2180"


def test_updates_requires_courses(fake_ed):
    assert "error" in ed.updates()


def test_thread_marks_staff(fake_ed):
    t = ed.get_thread(1, 39)
    assert t["answers"][0]["by"] == "Prof K (staff)"
    assert t["answers"][1]["depth"] == 1


def test_only_get_requests():
    src = open(ed.__file__).read()
    assert 'method="GET"' in src and "POST" not in src.replace("post, edit", "")
