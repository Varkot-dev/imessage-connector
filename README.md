# iMessage + Ed connector

A local, read-only connector for Claude that:

1. **Finds plans in your texts** and proposes them as Google Calendar events.
2. **Texts you when a plan changes.** If someone writes "can we do 6 instead" or
   "something came up" in the chat for a plan you added, you get a text. Free, local,
   and it never touches your calendar.
3. **Tracks follow-ups.** "Call me", "lmk", "I'll let you know": loose ends get texted to
   you twice a day as a numbered list.
4. **Lets you reply by text.** Text yourself "done 2", "snooze 1", "list", or "help" and it
   answers within a minute.
5. **Catches you up on Ed Discussion** for the classes you choose.

It runs on your Mac because that's where your messages are stored. It's a small
Python process that sits idle until Claude calls it, so you won't notice it.

## What it can and can't do

- **Only the helper can read Messages.** A small separate program, **iMessage Helper.app**,
  is the one thing you give Full Disk Access. It runs `service.py` in the background,
  which opens `chat.db` read-only (SQLite read-only mode plus `query_only`) and answers
  questions over a private socket that only your user account can use. The Claude app
  and Terminal never get Full Disk Access.
- **The helper can't be borrowed.** Before starting, the launcher checks the SHA-256 of
  `service.py` and `imessage_plans/db.py` against the values baked in at install. If
  anything edits them (including Claude), it refuses to run until you re-run `install.sh`.
  It takes no arguments and runs Python in isolated mode with no add-on packages.
- **Sends only to you.** The only sending code is `text_me`, which can only text **your**
  number from `config.json`; the tool has no recipient field.
- **Ed is read-only.** Only GET requests are made to Ed.
- **No extra cost by default.** Plan finding runs through the Claude you are chatting with,
  never a paid API, unless you set `PLANS_USE_HAIKU_API=1`.
- **Calendar is ask-first and create-only.** Claude checks for duplicates, asks before adding
  events, and never edits or deletes events because of a text.
- **Optional chat limits.** Add `"exclude_chats": ["Mom", "+15025551234"]` or
  `"include_only": [...]` to `config.json` to hide chats from the helper entirely.

## Change alerts

When you add a plan, Claude registers it with `register_plan` and `record_plan_decision`
(including the calendar event id). The background job (every 30 minutes) then reads only those plans' conversations and looks for
reschedule or cancel language with plain keyword matching. No API and no Claude usage.
Each message triggers at most one text. Past plans are ignored.

To tune it, edit `CHANGE_PHRASES` in `imessage_plans/changes.py`. Logs go to
`~/.imessage-plans/background.log`.

## Follow-ups and texting back

`background.py` runs every minute (installed by `install.sh`). It is free: no API and no
Claude. It looks for people asking you to get back to them ("call me", "can I call you
later?", "lmk") in one-on-one chats, and for your own promises ("ill call tmmr", "I'll let
you know") in any chat. At 12:30 PM and 8:30 PM it texts you the open list if anything new
came in. Items expire after 4 days.

You can reply in your self-chat (the conversation with your own number):

| Text | What happens |
|---|---|
| `list` | Sends the open follow-ups, numbered |
| `done 2`, `done 1 3`, `done all` | Clears those items |
| `snooze 2` | Hides it until tomorrow |
| `ok` | Dismisses a change alert |
| `help` | Shows these commands |

Anything else you text yourself is ignored, so notes-to-self are safe. Commands only change
the connector's own files in `~/.imessage-plans`. They never text anyone else and never
touch your calendar. Phrases live in `imessage_plans/followups.py` if you want to tune them.

## Setup (about 5 minutes)

First copy `config.example.json` to `config.json` and put your own phone number (or Apple ID
email) in `self_handle`. That is the only number the connector can ever text.


Double-click **setup.command** in this folder. It will:
1. Install everything and build the helper (needs Xcode command line tools; if it asks,
   run `xcode-select --install` and double-click again).
2. Ask you to paste your Ed token (make one at https://edstem.org/us/settings/api-tokens).
   It's saved in the macOS Keychain, not a file.
3. Open Full Disk Access settings and show you **iMessage Helper.app** in Finder. Drag it into
   the list and turn it on. Add nothing else.
4. Check that the helper can read Messages.

Then quit the Claude app fully (Cmd+Q) and reopen it.

The first time Claude texts you, macOS asks whether Claude can control Messages. Click OK.
The first time the Ed token is read, click **Always Allow** on the Keychain prompt.

To test the filter on your real texts (no Full Disk Access needed for Terminal):
```bash
.venv/bin/python test_run.py --days 14
```
It writes a report to `reports/` showing which messages the filter kept and dropped.

If you edit `service.py` or `imessage_plans/db.py`, re-run `bash install.sh` so the helper
accepts the new code.

## Using it

Just ask Claude in the desktop app:
- "Check my texts for plans" (it proposes events, then adds the ones you approve)
- "What did Sam say about Friday?"
- "Which Ed courses do I have?" then "Track my two CS classes on Ed"
- "Catch me up on Ed" (announcements and staff posts first, then new threads)
- "Text me a summary" (sends only to your own number)

## Files

| File | What it does |
|---|---|
| `server.py` | The MCP server and its tools (asks the helper for messages) |
| `service.py` | The Messages helper, the only code that reads chat.db |
| `launcher/imessage-helper.c` | The hash-checking launcher you give Full Disk Access |
| `imessage_plans/client.py` | How the connector talks to the helper |
| `imessage_plans/db.py` | Read-only chat.db reader: decodes `attributedBody`, contacts, reactions, skips unsent messages and short codes |
| `imessage_plans/prefilter.py` | The keyword and time filter with context windows |
| `imessage_plans/extract.py` | Optional paid Haiku mode (off by default) |
| `imessage_plans/state.py` | Dedup memory for plans, and `register_plan` for filter-only mode |
| `imessage_plans/changes.py` | Free keyword check for reschedules and cancellations |
| `imessage_plans/followups.py` | Free keyword follow-up tracker |
| `imessage_plans/selfchat.py` | Answers commands you text yourself |
| `background.py` | The every-minute background job (commands, follow-ups, change check, digests) |
| `check_changes.py` | Runs only the change check once, for testing |
| `imessage_plans/ed.py` | Read-only Ed Discussion client |
| `imessage_plans/notify.py` | Text-to-self, the only sending code |
| `test_run.py` | Test mode report |

## Known limits

- **Undocumented formats.** Apple doesn't document the Messages database, so a macOS
  update could change it. If texts suddenly come back empty, the decoder probably needs
  an update.
- **Ed API.** Ed's API is unofficial (it's the same one the web app uses). Endpoints follow
  the open-source `edapi` package.
- **iCloud.** Messages that were offloaded to iCloud and aren't on the Mac can't be read.
- **Lid closed.** Checks only run while the Mac is awake and the Claude app is open.
- **Not airtight.** The hash check stops edits to the helper's own files, but someone with full control of your user account could still change Python itself. It is a strong fence, not a vault.

## Prior art this borrows from

- `rfaile313/iMessageWatcher`: texts to calendar with a local model; borrowed ROWID-style
  dedup and sending surrounding context, not single messages. That project adds events
  with no confirmation; this one asks first.
- `anipotts/imessage-mcp`, `cyberpapiii/imessage-max`, `wolfiesch/imessage-mcp`: read-only
  connection settings and `attributedBody` decoding.
- `jachimo` chat.db reference: date units, tapback codes, unsent messages, short codes.
- `smartspot2/edapi`: Ed API endpoints and auth.
