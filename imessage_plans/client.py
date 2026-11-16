"""How the connector asks the Messages helper for messages (it can't open chat.db itself)."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime

from .db import Message

SOCKET = os.path.expanduser("~/.imessage-plans/messages.sock")

NOT_RUNNING = ("The Messages helper isn't running. Run install.sh, then make sure "
               "'iMessage Helper' has Full Disk Access (see README step 3).")


class HelperError(RuntimeError):
    pass


def _request(payload: dict, timeout: float = 30) -> dict:
    if not os.path.exists(SOCKET):
        raise HelperError(NOT_RUNNING)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET)
        s.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    except (ConnectionRefusedError, FileNotFoundError):
        raise HelperError(NOT_RUNNING)
    finally:
        s.close()
    resp = json.loads(buf or b"{}")
    if not resp.get("ok"):
        raise HelperError(resp.get("error", "Messages helper returned no answer."))
    return resp


def _to_message(d: dict) -> Message:
    return Message(rowid=d["rowid"], guid=d.get("guid", ""), chat_id=d["chat_id"], chat_name=d["chat_name"],
                   is_group=d["is_group"], sender=d["sender"], is_from_me=d["is_from_me"],
                   sent_at=datetime.fromisoformat(d["sent_at"]), text=d["text"], kind=d.get("kind", "message"))


class HelperDB:
    """Same interface the connector used with MessagesDB, but over the helper's socket."""

    def fetch(self, since: datetime, until: datetime | None = None, chat_id: int | None = None,
              limit: int = 20000, **_) -> list[Message]:
        resp = _request({"op": "fetch", "since": since.isoformat(), "until": until.isoformat() if until else None,
                         "chat_id": chat_id, "limit": limit})
        return [_to_message(d) for d in resp["messages"]]

    def recent_chats(self, since: datetime) -> list[dict]:
        return _request({"op": "recent_chats", "since": since.isoformat()})["chats"]

    def close(self):
        pass


def ping() -> bool:
    try:
        _request({"op": "ping"}, timeout=3)
        return True
    except (HelperError, OSError, ValueError):
        return False
