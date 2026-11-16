#!/bin/bash
# Setup: Python environment, the Messages helper (the only thing with Full Disk
# Access), its background job, and the Claude desktop app config.
# Safe to re-run; re-run it after any change to service.py or imessage_plans/db.py.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
LABEL="com.imessage-plans.helper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STATE="$HOME/.imessage-plans"

# 1. Python
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 "$HOME/miniconda3/bin/python3"; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
[ -z "$PY" ] && { echo "Need Python 3.10 or newer (python.org or miniconda)."; exit 1; }
echo "Using $PY ($("$PY" --version))"
"$PY" -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
echo "Dependencies installed."

# 2. Build the launcher with the current file hashes baked in
if ! command -v cc >/dev/null 2>&1; then
  echo "A C compiler is needed. Run: xcode-select --install   then run this again."; exit 1
fi
REALPY="$("$DIR/.venv/bin/python" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
h() { shasum -a 256 "$1" | cut -d' ' -f1; }
APP="$DIR/iMessage Helper.app"
rm -rf "$DIR/bin" "$APP"
mkdir -p "$APP/Contents/MacOS"
cc -O2 -o "$APP/Contents/MacOS/imessage-helper" "$DIR/launcher/imessage-helper.c" \
  -DPYTHON_PATH="\"$REALPY\"" -DPROJECT_DIR="\"$DIR\"" \
  -DHASH_SERVICE="\"$(h "$DIR/service.py")\"" \
  -DHASH_INIT="\"$(h "$DIR/imessage_plans/__init__.py")\"" \
  -DHASH_DB="\"$(h "$DIR/imessage_plans/db.py")\""
cat > "$APP/Contents/Info.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.imessage-plans.helper</string>
  <key>CFBundleName</key><string>iMessage Helper</string>
  <key>CFBundleExecutable</key><string>imessage-helper</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSBackgroundOnly</key><true/>
</dict></plist>
PLISTEOF
# Ad-hoc sign the whole app so macOS has a stable identity to attach Full Disk Access to.
codesign --force --sign - --identifier com.imessage-plans.helper "$APP"
echo "Built iMessage Helper.app."

# 3. Background job (starts at login, restarts if it stops)
mkdir -p "$STATE" "$HOME/Library/LaunchAgents"
chmod 700 "$STATE"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/imessage-helper</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$STATE/helper.log</string>
  <key>StandardErrorPath</key><string>$STATE/helper.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Background helper installed."

# 3b. Plan change check (every 30 minutes, free, texts only you, only when a plan changes)
CLABEL="com.imessage-plans.changes"
CPLIST="$HOME/Library/LaunchAgents/$CLABEL.plist"
cat > "$CPLIST" <<EOF2
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$CLABEL</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>$DIR/check_changes.py</string>
  </array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$STATE/changes.log</string>
  <key>StandardErrorPath</key><string>$STATE/changes.log</string>
</dict></plist>
EOF2
launchctl bootout "gui/$(id -u)/$CLABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$CPLIST"
echo "Plan change check installed (every 30 minutes)."

# 4. Claude desktop config (backup first)
mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] && cp "$CONFIG" "$CONFIG.backup-$(date +%Y%m%d-%H%M%S)"
"$DIR/.venv/bin/python" - "$CONFIG" "$DIR" <<'EOF'
import json, os, sys
config_path, d = sys.argv[1], sys.argv[2]
cfg = {}
if os.path.exists(config_path):
    with open(config_path) as f:
        cfg = json.load(f)
cfg.setdefault("mcpServers", {})["imessage-and-ed"] = {
    "command": os.path.join(d, ".venv", "bin", "python"),
    "args": [os.path.join(d, "server.py")],
}
with open(config_path, "w") as f:
    json.dump(cfg, f, indent=2)
print("Added 'imessage-and-ed' to the Claude desktop config.")
EOF

echo
echo "Last step: give Full Disk Access to ONLY this app:"
echo "  $APP"
echo "(drag it from Finder into System Settings > Privacy & Security > Full Disk Access)"
echo "Then run:  launchctl kickstart -k gui/$(id -u)/$LABEL"
