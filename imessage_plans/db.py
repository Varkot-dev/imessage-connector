"""Read-only access to the macOS Messages database (chat.db).

Safety:
- The database is opened with SQLite's read-only URI mode plus PRAGMA query_only,
  so no statement in this process can write to it.
- There is no code anywhere in this project that sends messages.

Gotchas handled (learned from existing open-source iMessage readers):
- On macOS Ventura and later, message.text is often NULL and the real text lives
  in message.attributedBody (an Apple "typedstream" blob). We decode it.
- message.date is nanoseconds since 2001-01-01 UTC on modern macOS (seconds on
  very old databases).
- Tapbacks/reactions are separate rows (associated_message_type 2000-2006). We
  keep them as short context lines, because a "Loved" or "Liked" on a plan is
  often the confirmation.
- Unsent messages (date_retracted) are skipped. Edited messages use the latest text.
- Messages you sent have handle_id 0; direction comes from is_from_me.
"""

from __future__ import annotations

import glob
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

CHAT_DB = os.path.expanduser(os.environ.get("IMESSAGE_DB_PATH", "~/Library/Messages/chat.db"))
ADDRESSBOOK_GLOB = os.path.expanduser(
    "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
)
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

REACTION_NAMES = {
    2000: "loved", 2001: "liked", 2002: "disliked", 2003: "laughed at",
    2004: "emphasized", 2005: "questioned", 2006: "reacted to",
}


@dataclass
class Message:
    rowid: int
    guid: str
    chat_id: int
    chat_name: str
    is_group: bool
    sender: str  # "me" or a contact name / number
    is_from_me: bool
    sent_at: datetime  # timezone-aware, local time
    text: str
    kind: str = "message"  # "message" or "reaction"
    reply_to_guid: str | None = None
    service: str | None = None
    extra: dict = field(default_factory=dict)

    def line(self) -> str:
        ts = self.sent_at.strftime("%a %Y-%m-%d %I:%M %p")
        return f"[{ts}] {self.sender}: {self.text}"


# ---------------------------------------------------------------- decoding

def _read_typedstream_int(buf: bytes, i: int) -> tuple[int, int]:
    """Apple typedstream integer encoding: 0x81 -> int16 LE follows, 0x82 -> int32 LE."""
    b = buf[i]
    if b == 0x81:
        return int.from_bytes(buf[i + 1:i + 3], "little"), i + 3
    if b == 0x82:
        return int.from_bytes(buf[i + 1:i + 5], "little"), i + 5
    return b, i + 1


def _decode_manual(blob: bytes) -> str | None:
    """Heuristic decoder used by many open-source projects: find the NSString
    payload and read its length-prefixed UTF-8 bytes."""
    for marker in (b"NSString", b"NSMutableString"):
        idx = blob.find(marker)
        if idx == -1:
            continue
        j = idx + len(marker)
        # After the class name comes a version byte and some type markers, then
        # 0x2B ('+') which introduces the string bytes.
        plus = blob.find(b"\x2b", j, j + 12)
        if plus == -1:
            continue
        try:
            length, start = _read_typedstream_int(blob, plus + 1)
            raw = blob[start:start + length]
            return raw.decode("utf-8", errors="replace")
        except Exception:
            continue
    return None


def _decode_with_library(blob: bytes) -> str | None:
    try:
        import typedstream  # pytypedstream, optional
    except ImportError:
        return None
    try:
        obj = typedstream.unarchive_from_data(blob)
    except Exception:
        return None

    def find_str(o, depth=0):
        if depth > 6 or o is None:
            return None
        if isinstance(o, str):
            return o if o.strip() else None
        if isinstance(o, (bytes, bytearray)):
            return None
        for attr in ("value", "contents", "elements"):
            sub = getattr(o, attr, None)
            if sub is None or sub is o:
                continue
            items = sub if isinstance(sub, (list, tuple)) else [sub]
            for it in items:
                s = find_str(it, depth + 1)
                if s:
                    return s
        return None

    return find_str(obj)


def decode_attributed_body(blob: bytes | None) -> str | None:
    if not blob:
        return None
    text = _decode_with_library(blob) or _decode_manual(blob)
    if text:
        # Object replacement chars mark attachments; strip them.
        text = text.replace("￼", "").strip()
    return text or None


def apple_time_to_local(value: int | None) -> datetime | None:
    if not value:
        return None
    seconds = value / 1e9 if value > 1e12 else value
    return (APPLE_EPOCH + timedelta(seconds=seconds)).astimezone()


def local_to_apple_ns(dt: datetime) -> int:
    return int((dt.astimezone(timezone.utc) - APPLE_EPOCH).total_seconds() * 1e9)


# ---------------------------------------------------------------- contacts

def _normalize_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    return digits[-10:] if len(digits) >= 10 else digits


def load_contacts() -> dict[str, str]:
    """Map normalized phone numbers / lowercase emails to contact names using the
    local AddressBook databases (read-only). Returns {} if unavailable."""
    names: dict[str, str] = {}
    for path in glob.glob(ADDRESSBOOK_GLOB):
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            con.execute("PRAGMA query_only = ON")
            cur = con.cursor()
            people = {
                pk: " ".join(p for p in (first, last) if p) or (org or "")
                for pk, first, last, org in cur.execute(
                    "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD"
                )
            }
            for owner, number in cur.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                if owner in people and number and people[owner]:
                    names[_normalize_phone(number)] = people[owner]
            for owner, addr in cur.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                if owner in people and addr and people[owner]:
                    names[addr.lower()] = people[owner]
            con.close()
        except sqlite3.Error:
            continue
    return names


def resolve_handle(handle: str | None, contacts: dict[str, str]) -> str:
    if not handle:
        return "unknown"
    if "@" in handle:
        return contacts.get(handle.lower(), handle)
    return contacts.get(_normalize_phone(handle), handle)


def is_short_code(handle: str | None) -> bool:
    """Automated senders (banks, 2FA codes, promos) use 5-6 digit short codes."""
    if not handle or "@" in handle:
        return False
    digits = re.sub(r"\D", "", handle)
    return 0 < len(digits) <= 6


# ---------------------------------------------------------------- database

class MessagesDB:
    def __init__(self, path: str = CHAT_DB):
        self.path = path
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Is this a Mac with Messages set up?"
            )
        try:
            self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            self.con.execute("PRAGMA query_only = ON")
            self.con.execute("SELECT 1 FROM message LIMIT 1")
        except sqlite3.OperationalError as e:
            raise PermissionError(
                "Could not open chat.db. Give the app running this server (the Claude "
                "desktop app, or Terminal when testing) Full Disk Access in System "
                "Settings > Privacy & Security > Full Disk Access, then fully quit and "
                f"reopen it. SQLite said: {e}"
            ) from e
        self.contacts = load_contacts()
        self._chat_cache: dict[int, tuple[str, bool]] = {}
        self._ident: dict[int, str | None] = {}
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(message)")}
        self.has_retracted = "date_retracted" in cols
        self.has_reply = "reply_to_guid" in cols

    def close(self):
        self.con.close()

    def chat_label(self, chat_id: int) -> tuple[str, bool]:
        if chat_id in self._chat_cache:
            return self._chat_cache[chat_id]
        row = self.con.execute(
            "SELECT display_name, chat_identifier FROM chat WHERE ROWID = ?", (chat_id,)
        ).fetchone()
        members = [
            r[0] for r in self.con.execute(
                "SELECT h.id FROM chat_handle_join chj JOIN handle h ON h.ROWID = chj.handle_id "
                "WHERE chj.chat_id = ?", (chat_id,)
            )
        ]
        is_group = len(members) > 1
        self._ident[chat_id] = row[1] if row else None
        display = row[0] if row and row[0] else None
        if not display:
            names = [resolve_handle(m, self.contacts) for m in members]
            display = ", ".join(names) if names else (row[1] if row else f"chat {chat_id}")
        self._chat_cache[chat_id] = (display, is_group)
        return self._chat_cache[chat_id]

    def fetch(self, since: datetime, until: datetime | None = None,
              chat_id: int | None = None, include_short_codes: bool = False,
              limit: int = 20000) -> list[Message]:
        """All messages and reactions since a time, oldest first."""
        where = ["m.date >= ?"]
        params: list = [local_to_apple_ns(since)]
        if until:
            where.append("m.date <= ?")
            params.append(local_to_apple_ns(until))
        if chat_id is not None:
            where.append("cmj.chat_id = ?")
            params.append(chat_id)
        if self.has_retracted:
            where.append("(m.date_retracted IS NULL OR m.date_retracted = 0)")
        reply_col = "m.reply_to_guid" if self.has_reply else "NULL"
        sql = f"""
            SELECT m.ROWID, m.guid, cmj.chat_id, m.is_from_me, m.date, m.text,
                   m.attributedBody, m.associated_message_type, m.associated_message_guid,
                   h.id, m.service, {reply_col}
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE {' AND '.join(where)}
            ORDER BY m.date ASC
            LIMIT ?
        """
        params.append(limit)
        rows = self.con.execute(sql, params).fetchall()
        guid_text: dict[str, str] = {}
        out: list[Message] = []
        for (rowid, guid, cid, from_me, date, text, body, assoc_type, assoc_guid,
             handle, service, reply_to) in rows:
            if not from_me and not include_short_codes and is_short_code(handle):
                continue
            body_text = text if text and text.strip() and text.strip() != "￼" else None
            body_text = body_text or decode_attributed_body(body)
            sender = "me" if from_me else resolve_handle(handle, self.contacts)
            chat_name, is_group = self.chat_label(cid)
            sent_at = apple_time_to_local(date)
            assoc_type = assoc_type or 0
            if 2000 <= assoc_type <= 2006:
                target_guid = (assoc_guid or "").split("/")[-1]
                target = guid_text.get(target_guid, "a message")
                verb = REACTION_NAMES.get(assoc_type, "reacted to")
                snippet = target if len(target) < 80 else target[:77] + "..."
                out.append(Message(rowid, guid, cid, chat_name, is_group, sender, bool(from_me),
                                   sent_at, f'({verb} "{snippet}")', kind="reaction",
                                   service=service, extra={"chat_identifier": self._ident.get(cid)}))
                continue
            if assoc_type < 0 or assoc_type >= 3000:
                continue  # reaction removals and other system rows
            if not body_text:
                continue  # attachments with no text, system events
            if body_text.startswith("[Claude] "):
                continue  # our own text-to-self notifications
            guid_text[guid] = body_text
            out.append(Message(rowid, guid, cid, chat_name, is_group, sender, bool(from_me),
                               sent_at, body_text, reply_to_guid=reply_to, service=service,
                               extra={"chat_identifier": self._ident.get(cid)}))
        return out

    def recent_chats(self, since: datetime) -> list[dict]:
        rows = self.con.execute(
            """SELECT cmj.chat_id, COUNT(*), MAX(m.date)
               FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
               WHERE m.date >= ? AND m.associated_message_type = 0
               GROUP BY cmj.chat_id ORDER BY MAX(m.date) DESC""",
            (local_to_apple_ns(since),),
        ).fetchall()
        result = []
        for cid, count, last in rows:
            name, is_group = self.chat_label(cid)
            result.append({
                "chat_id": cid, "name": name, "is_group": is_group,
                "messages": count,
                "last_message_at": apple_time_to_local(last).isoformat(timespec="minutes"),
            })
        return result
