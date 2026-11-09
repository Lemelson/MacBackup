#!/bin/bash
# Usage: shot.sh <scenario> <out.png> [en|ru] [extra app args...]
# Generates test data, launches the installed app in test mode, captures only its window, quits it.
# Env: APPEARANCE=light|dark (default dark), HIDE_BADGE=1 hides the "Test mode" badge.
# Only touches MacBackup processes started with --test; never --auto/--notify or the user's own window.
set -e
cd "$(dirname "$0")/.."
SC=$1; OUT=$2; LANG_=${3:-en}; shift $(( $# < 3 ? $# : 3 ))
kill_test() { for p in $(pgrep -f "MacBackup.app/Contents/MacOS/MacBackup --test" || true); do kill "$p" 2>/dev/null || true; done; }
kill_test; sleep 0.5
python3 testdata/make_testdata.py "$SC" "$LANG_" >/dev/null
MACBACKUP_TESTDATA="$PWD/testdata" MACBACKUP_APPEARANCE="${APPEARANCE:-dark}" MACBACKUP_HIDE_TEST_BADGE="${HIDE_BADGE:-}" \
  "$HOME/Applications/MacBackup.app/Contents/MacOS/MacBackup" --test "$@" >/dev/null 2>&1 &
PID=$!
sleep 4
WID=$(swift testdata/winid.swift "$PID")
mkdir -p "$(dirname "$OUT")"
screencapture -x -o -l "$WID" "$OUT"
[ -n "${KEEP:-}" ] || kill "$PID"
echo "$OUT"
