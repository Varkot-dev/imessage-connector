#!/bin/bash
# Double-click this file to set everything up.
cd "$(dirname "$0")"
DIR="$(pwd)"
echo "=== Step 1 of 4: installing ==="
bash install.sh || { echo "Install failed. Send Claude the error above."; read -r -p "Press Enter to close"; exit 1; }

echo
echo "=== Step 2 of 4: Ed token ==="
if security find-generic-password -s ed-api-token >/dev/null 2>&1; then
  echo "Ed token already saved. Skipping."
else
  echo "Make a token at https://edstem.org/us/settings/api-tokens"
  echo "Paste it below and press Enter. It will NOT show as you paste; that's normal."
  echo "(To skip Ed for now, just press Enter twice.)"
  security add-generic-password -U -s ed-api-token -a "$USER" -w && echo "Saved to Keychain."
fi
echo
echo "=== Step 3 of 4: Full Disk Access for the helper app only ==="
echo "Finder will highlight 'iMessage Helper' and System Settings will open."
echo "Drag 'iMessage Helper' from Finder into the Full Disk Access list and turn it ON."
echo "Do NOT add Claude; it doesn't need it."
open -R "$DIR/iMessage Helper.app"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
read -r -p "Press Enter here once 'iMessage Helper' is in the list and ON... "
echo
echo "=== Step 4 of 4: checking ==="
launchctl kickstart -k "gui/$(id -u)/com.imessage-plans.helper"
sleep 3
if .venv/bin/python -c "import sys; sys.path.insert(0,'.'); from imessage_plans.client import HelperDB; from datetime import datetime, timedelta; m=HelperDB().fetch(datetime.now().astimezone()-timedelta(days=1)); print(f'Helper works: read {len(m)} messages from the last day.')"; then
  echo
  echo "All set. Quit the Claude app fully (Cmd+Q) and reopen it."
else
  echo "Helper isn't reading Messages yet. Log: ~/.imessage-plans/helper.log"
  tail -5 ~/.imessage-plans/helper.log 2>/dev/null
fi
read -r -p "Press Enter to close"
