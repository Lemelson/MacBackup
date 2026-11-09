#!/bin/bash
# Rebuild MacBackup and reinstall it to ~/Applications/MacBackup.app (engine bundled inside).
set -euo pipefail
cd "$(dirname "$0")"

APP="$HOME/Applications/MacBackup.app"
ENGINE="../engine/macbackup.py"
BUNDLE_ID="${BUNDLE_ID:-app.macbackup.MacBackup}"   # override if you fork

swift build -c release --arch arm64
BIN="$(swift build -c release --arch arm64 --show-bin-path)/MacBackup"

if [ ! -f build/AppIcon.icns ]; then
  mkdir -p build
  rm -rf build/AppIcon.iconset
  swift make_icon.swift build/AppIcon.iconset
  iconutil -c icns build/AppIcon.iconset -o build/AppIcon.icns
fi

# Quit the windowed app gracefully (it asks first if a backup is running).
# Headless `--auto` / `--notify` runs are left alone: the running binary keeps working after the bundle is replaced.
if pgrep -f "MacBackup.app/Contents/MacOS/MacBackup$" >/dev/null; then
  osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
  sleep 1
fi

mkdir -p "$HOME/Applications"
STAGE="$(mktemp -d)/MacBackup.app"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources/engine" "$STAGE/Contents/Resources/en.lproj" "$STAGE/Contents/Resources/ru.lproj"
cp "$BIN" "$STAGE/Contents/MacOS/MacBackup"
cp build/AppIcon.icns "$STAGE/Contents/Resources/AppIcon.icns"
if [ -f "$ENGINE" ]; then
  cp "$ENGINE" "$STAGE/Contents/Resources/engine/macbackup.py"
else
  echo "warning: $ENGINE not found, the app will report a missing engine" >&2
fi
# Self-contained zstd: the engine copies it to the disk (tools/zstd) so a fresh Mac can restore.
[ -x build/zstd ] || ../scripts/build-portable-zstd.sh "$PWD/build/zstd"
cp build/zstd "$STAGE/Contents/Resources/engine/zstd"
for l in en ru; do printf '"CFBundleDisplayName" = "MacBackup";\n"CFBundleName" = "MacBackup";\n' > "$STAGE/Contents/Resources/$l.lproj/InfoPlist.strings"; done

cat > "$STAGE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>__BUNDLE_ID__</string>
  <key>CFBundleName</key><string>MacBackup</string>
  <key>CFBundleDisplayName</key><string>MacBackup</string>
  <key>CFBundleExecutable</key><string>MacBackup</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.1</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleLocalizations</key><array><string>en</string><string>ru</string></array>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Backs up your Mac to an external disk</string>
</dict>
</plist>
PLIST

sed -i '' "s/__BUNDLE_ID__/$BUNDLE_ID/" "$STAGE/Contents/Info.plist"
codesign -s - --force --deep "$STAGE"
rm -rf "$APP"
mv "$STAGE" "$APP"
echo "Done: $APP"
