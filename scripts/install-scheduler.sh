#!/bin/bash
# Install the MacBackup scheduler: runs `MacBackup --auto` hourly and whenever a disk is mounted.
# The engine decides itself whether a backup is due, so most runs exit in a fraction of a second.
set -euo pipefail
APP="${1:-}"
for c in "$APP" "$HOME/Applications/MacBackup.app" "/Applications/MacBackup.app"; do
  if [ -n "$c" ] && [ -x "$c/Contents/MacOS/MacBackup" ]; then APP="$c"; break; fi
done
[ -x "$APP/Contents/MacOS/MacBackup" ] || { echo "MacBackup.app not found"; exit 1; }
LABEL=com.macbackup.auto
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Application Support/MacBackup/logs"
mkdir -p "$LOGDIR" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/MacBackup</string><string>--auto</string></array>
  <key>StartInterval</key><integer>3600</integer>
  <key>StartOnMount</key><true/>
  <key>RunAtLoad</key><false/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGDIR/launchd.out</string>
  <key>StandardErrorPath</key><string>$LOGDIR/launchd.err</string>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Scheduler installed: $PLIST -> $APP"
