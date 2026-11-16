"""Test mode: run the pipeline over your recent texts and write a report you can review.

    python test_run.py --days 14            # filter only (free), nothing leaves your Mac
    python test_run.py --days 14 --use-api  # also run Haiku through a paid API key

The report shows which messages the filter kept (>> marks the trigger), which it
dropped, and which plans Haiku found. It never changes the connector's saved state
and never touches your calendar. Reports are saved in ./reports (git-ignored).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from imessage_plans.client import HelperDB  # noqa: E402
from imessage_plans.db import MessagesDB  # noqa: E402
from imessage_plans.extract import MODEL, extract_plans, get_api_key  # noqa: E402
from imessage_plans.prefilter import run_filter  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--no-llm", action="store_true", help="filter only (the default)")
    ap.add_argument("--use-api", action="store_true", help="also run Haiku through your paid API key")
    ap.add_argument("--no-filter", action="store_true", help="skip the keyword filter")
    ap.add_argument("--db", default=None, help="read a chat.db copy directly (default: ask the Messages helper)")
    args = ap.parse_args()

    now = datetime.now().astimezone()
    since = now - timedelta(days=args.days)
    db = MessagesDB(args.db) if args.db else HelperDB()  # Terminal needs no Full Disk Access
    messages = db.fetch(since)
    db.close()
    result = run_filter(messages, disabled=args.no_filter)

    plans, usage, err = [], None, None
    if args.use_api and not args.no_llm:
        if not get_api_key():
            err = "No API key found, so Haiku was skipped. See README step 4."
        else:
            try:
                plans, usage = extract_plans(result.snippets, now)
            except Exception as e:  # report, don't crash, so the filter output is still saved
                err = f"Haiku call failed: {e}"

    out = [f"# Plan finder test run, {now:%a %b %d %Y %I:%M %p}", ""]
    out.append(f"- Window: last {args.days:g} days (since {since:%a %b %d %I:%M %p})")
    out.append(f"- Messages scanned: {result.total_messages}")
    pct = (100 * result.kept_count / result.total_messages) if result.total_messages else 0
    out.append(f"- Kept by filter: {result.kept_count} ({pct:.0f}%) in {len(result.snippets)} snippets"
               + (" (filter disabled)" if args.no_filter else ""))
    if usage:
        out.append(f"- Haiku ({MODEL}): {usage['calls']} calls, {usage['input_tokens']} input tokens, "
                   f"{usage['output_tokens']} output tokens")
    if err:
        out.append(f"- Note: {err}")

    out += ["", "## Plans found", ""]
    if args.no_llm or not args.use_api:
        out.append("(Filter only. To find plans for free, ask Claude in the desktop app: \"check my texts for plans\".)")
    elif not plans:
        out.append("(none)")
    for p in sorted(plans, key=lambda p: p.get("start", "")):
        when = p.get("start", "?") + ("" if p.get("time_known") else " (no time agreed)")
        out.append(f"- **{p.get('title')}**: {when} | {p.get('status')} | "
                   f"confidence {p.get('confidence')} | {p.get('conversation')}")
        out.append(f"  - evidence: \"{p.get('evidence')}\"")
        if p.get("location"):
            out.append(f"  - location: {p['location']}")
        if p.get("notes"):
            out.append(f"  - notes: {p['notes']}")

    out += ["", "## What the filter kept", ""]
    for s in result.snippets:
        out.append(f"### {s.chat_name}" + (" (group)" if s.is_group else ""))
        out.append("```")
        for m in s.messages:
            out.append((">> " if m.rowid in s.hit_rowids else "   ") + m.line())
        out.append("```")

    out += ["", "## What the filter dropped", "",
            "Skim this for plans the filter missed. Add any missing words to "
            "~/.imessage-plans/extra_keywords.txt (one per line) and run again.", ""]
    dropped: dict[str, list[str]] = {}
    for m in messages:
        if m.rowid not in result.kept_rowids:
            dropped.setdefault(m.chat_name, []).append(m.line())
    for chat, lines in dropped.items():
        out.append(f"### {chat} ({len(lines)} dropped)")
        out.append("```")
        out.extend(lines)
        out.append("```")

    os.makedirs("reports", exist_ok=True)
    path = os.path.join("reports", f"test-{now:%Y%m%d-%H%M}.md")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out[:12]))
    print(f"\nFull report: {os.path.abspath(path)}")


if __name__ == "__main__":
    main()
