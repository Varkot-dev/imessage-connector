"""Local, free pre-filter: keep only conversation snippets that might contain a plan.

Design rule: it is fine to keep too much (Haiku throws out non-plans), but dropping
a real plan is the failure we care about. So the signal lists are broad, and every
hit pulls in the messages around it so short replies like "bet" or a tapback stay
attached to the plan they confirm.

To teach it new words, add one word or phrase per line to
~/.imessage-plans/extra_keywords.txt (no code changes needed).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import timedelta

from .db import Message

EXTRA_KEYWORDS_FILE = os.path.expanduser("~/.imessage-plans/extra_keywords.txt")

# When/time words, including common texting spellings and typos.
TIME_WORDS = r"""
today tdy 2day tonight tonite 2nite tn tmrw tmr tmrrw tmrw tmw tom tomorrow tomorow tommorow
tommorrow 2morrow 2moro morning mornin afternoon evening night nite later l8r weekend wknd
mon monday tue tues tuesday wed weds wednesday thu thur thurs thursday fri friday sat saturday
sun sunday jan january feb february mar march apr april may jun june jul july aug august
sep sept september oct october nov november dec december noon midnight oclock asap soon
""".split()

TIME_PHRASES = [
    "this week", "next week", "this weekend", "next weekend", "end of the week", "o'clock",
    "in an hour", "in a bit", "in a few", "after class", "after work", "before class",
    "after practice", "same time", "same place", "same spot", "right after", "day after",
    "next mon", "next tue", "next wed", "next thu", "next fri", "next sat", "next sun",
]

# Things people make plans to do.
ACTIVITY_WORDS = r"""
dinner din lunch breakfast brunch coffee boba food eat eating drinks drink bar party
pregame darty function game games gym lift workout run hike study studying library lib
meet meeting call ft facetime zoom hang hangout chill movie movies concert show flight
airport pickup ride uber lyft drive practice class lecture recitation section oh interview
appointment appt reservation res rez bday birthday event tickets tix trip visit dentist
doctor haircut shopping groceries church service rehearsal tournament match
""".split()

ACTIVITY_PHRASES = [
    "office hours", "pick up", "pick me up", "pick you up", "drop off", "move in", "move out",
]

# Invitation / scheduling / change language.
PLAN_WORDS = r"""
wanna wna tryna down free around available avail lets let's plan plans planning
reschedule resched postpone cancel cancelled canceled omw otw coming come slide link linkup
meetup hmu
""".split()

PLAN_PHRASES = [
    "want to", "u down", "you down", "r u free", "are you free", "you free", "u free",
    "should we", "can we", "could we", "we should", "pull up", "come thru", "come through",
    "hop on", "see you", "see u", "see ya", "cya", "meet up", "meet you", "meet u",
    "push it", "push back", "move it", "can't make", "cant make", "can't come", "cant come",
    "rain check", "raincheck", "still on", "are we still", "we still", "what time", "wat time",
    "what day", "when are", "when r", "when do", "when should", "where at", "where are we",
    "save me a", "grab a", "grab food", "get food", "count me in", "i'm in", "im in",
]

TIME_REGEXES = [
    r"\b\d{1,2}(:\d{2})?\s?(am|pm|a\.m\.|p\.m\.|a|p)\b",  # 7pm, 7:30 pm, 7p
    r"\b\d{1,2}:\d{2}\b",                                  # 7:30
    r"@\s?\d{1,2}\b",                                     # @ 7
    r"\bat\s\d{1,2}(:\d{2})?\b",                           # at 7
    r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b",                    # 11/23
    r"\b\d{1,2}(st|nd|rd|th)\b",                           # the 23rd
    r"\b\d{1,2}\s?-\s?\d{1,2}\s?(am|pm)?\b",               # 5-7
    r"\b(\d+|a|an|one|two|few)\s?(min|mins|minutes|hr|hrs|hours)\b",
]


def _load_extra() -> list[str]:
    try:
        with open(EXTRA_KEYWORDS_FILE) as f:
            return [ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        return []


def _build_regex() -> re.Pattern:
    words = set(TIME_WORDS + ACTIVITY_WORDS + PLAN_WORDS)
    phrases = TIME_PHRASES + ACTIVITY_PHRASES + PLAN_PHRASES + _load_extra()
    parts = [r"\b(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")\b"]
    parts += [r"\b" + re.escape(p).replace(r"\ ", r"\s+") + r"\b" if p[0].isalnum()
              else re.escape(p) for p in phrases]
    parts += TIME_REGEXES
    return re.compile("|".join(parts), re.IGNORECASE)


@dataclass
class Snippet:
    chat_id: int
    chat_name: str
    is_group: bool
    messages: list[Message]
    hit_rowids: set[int] = field(default_factory=set)

    def render(self) -> str:
        kind = "group chat" if self.is_group else "1:1 chat"
        lines = [f'Conversation: "{self.chat_name}" ({kind}, chat_id {self.chat_id})']
        for m in self.messages:
            lines.append(m.line())
        return "\n".join(lines)


@dataclass
class FilterResult:
    snippets: list[Snippet]
    kept_rowids: set[int]
    total_messages: int

    @property
    def kept_count(self) -> int:
        return len(self.kept_rowids)


def run_filter(messages: list[Message], before: int = 6, after: int = 10,
               before_window: timedelta = timedelta(hours=8),
               after_window: timedelta = timedelta(hours=36),
               disabled: bool = False) -> FilterResult:
    """Group messages by chat, find signal hits, and keep a context window around each."""
    rx = _build_regex()
    by_chat: dict[int, list[Message]] = {}
    for m in messages:
        by_chat.setdefault(m.chat_id, []).append(m)

    snippets: list[Snippet] = []
    kept: set[int] = set()
    for chat_id, msgs in by_chat.items():
        hits = [i for i, m in enumerate(msgs) if disabled or (m.kind == "message" and rx.search(m.text))]
        if not hits:
            continue
        # Build index ranges around each hit, then merge overlaps.
        ranges: list[list[int]] = []
        for i in hits:
            lo = i
            while lo > 0 and i - lo < before and msgs[i].sent_at - msgs[lo - 1].sent_at <= before_window:
                lo -= 1
            hi = i
            while hi < len(msgs) - 1 and hi - i < after and msgs[hi + 1].sent_at - msgs[i].sent_at <= after_window:
                hi += 1
            if ranges and lo <= ranges[-1][1] + 1:
                ranges[-1][1] = max(ranges[-1][1], hi)
            else:
                ranges.append([lo, hi])
        hit_ids = {msgs[i].rowid for i in hits}
        for lo, hi in ranges:
            chunk = msgs[lo:hi + 1]
            kept.update(m.rowid for m in chunk)
            snippets.append(Snippet(chat_id, chunk[0].chat_name, chunk[0].is_group, chunk,
                                    {m.rowid for m in chunk if m.rowid in hit_ids}))
    snippets.sort(key=lambda s: s.messages[0].sent_at)
    return FilterResult(snippets, kept, len(messages))
