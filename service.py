"""Messages helper: the ONLY program that can read chat.db.

launchd starts it through the small `bin/imessage-helper` launcher, which is the one
thing you give Full Disk Access. The launcher checks the SHA-256 of this file and of
imessage_plans/db.py before running them, so if anything edits this code, it refuses
to start until you reinstall.

It answers read-only questions over a private socket (~/.imessage-plans/messages.sock,
readable only by your user account). The Claude connector connects to it instead of
opening chat.db itself, so the Claude app never needs Full Disk Access.

Requests are one JSON line each:
  {"op": "ping"}
  {"op": "fetch", "since": ISO, "until": ISO|null, "chat_id": int|null, "limit": int}
  {"op": "recent_chats", "since": ISO}
  {"op": "image", "attachment_id": int}   -> JPEG (base64), resized, images only
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # the launcher runs Python in isolated mode, which skips this

from imessage_plans.db import MessagesDB  # noqa: E402

STATE_DIR = os.path.expanduser("~/.imessage-plans")
SOCKET = os.path.join(STATE_DIR, "messages.sock")
CONFIG = os.path.join(HERE, "config.json")
MAX_LIMIT = 50000
MAX_REQUEST = 10_000


def load_config() -> dict:
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def chat_allowed(name: str, cfg: dict) -> bool:
    """Optional exclude_chats / include_only in config.json, matched on chat name or number."""
    n = (name or "").lower()
    include = [x.lower() for x in cfg.get("include_only", []) if x]
    exclude = [x.lower() for x in cfg.get("exclude_chats", []) if x]
    if include and not any(x in n for x in include):
        return False
    return not any(x in n for x in exclude)


def _msg(m) -> dict:
    return {"rowid": m.rowid, "guid": m.guid, "chat_id": m.chat_id, "chat_name": m.chat_name,
            "is_group": m.is_group, "sender": m.sender, "is_from_me": m.is_from_me,
            "sent_at": m.sent_at.isoformat(timespec="seconds"), "text": m.text, "kind": m.kind,
            "chat_identifier": m.extra.get("chat_identifier")}


def handle(req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"ok": True}
    cfg = load_config()
    try:
        db = MessagesDB()
    except (PermissionError, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}
    try:
        if op == "fetch":
            since = datetime.fromisoformat(req["since"])
            until = datetime.fromisoformat(req["until"]) if req.get("until") else None
            chat_id = req.get("chat_id")
            limit = max(1, min(int(req.get("limit", 20000)), MAX_LIMIT))
            msgs = db.fetch(since, until, chat_id=int(chat_id) if chat_id is not None else None, limit=limit)
            return {"ok": True, "messages": [_msg(m) for m in msgs if chat_allowed(m.chat_name, cfg)]}
        if op == "recent_chats":
            chats = db.recent_chats(datetime.fromisoformat(req["since"]))
            return {"ok": True, "chats": [c for c in chats if chat_allowed(c["name"], cfg)]}
        if op == "image":
            path = db.image_path(int(req["attachment_id"]))
            if not path:
                return {"ok": False, "error": "No image with that attachment_id (or the file is not on this Mac)."}
            return {"ok": True, "jpeg_base64": _to_jpeg_b64(path)}
        return {"ok": False, "error": f"unknown op {op!r}"}
    finally:
        db.close()


MAX_IMAGE_BYTES = 4_000_000


def _to_jpeg_b64(path: str) -> str:
    """Convert (HEIC, PNG, ...) to a resized JPEG with macOS's built-in sips."""
    tmp = tempfile.mkdtemp(dir=STATE_DIR)
    out = os.path.join(tmp, "img.jpg")
    try:
        subprocess.run(["/usr/bin/sips", "-s", "format", "jpeg", "-Z", "1568", path, "--out", out],
                       capture_output=True, timeout=30, check=True)
        if os.path.getsize(out) > MAX_IMAGE_BYTES:
            raise ValueError("Image too large after resizing.")
        with open(out, "rb") as f:
            return base64.b64encode(f.read()).decode()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(MAX_REQUEST)
        try:
            resp = handle(json.loads(line))
        except Exception as e:  # never crash the helper on a bad request
            resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.wfile.write((json.dumps(resp) + "\n").encode())


def main() -> None:
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    old = os.umask(0o177)  # socket created as owner-only
    server = socketserver.ThreadingUnixStreamServer(SOCKET, Handler)
    os.umask(old)
    os.chmod(SOCKET, 0o600)
    print(f"{datetime.now():%Y-%m-%d %H:%M} messages helper listening", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
