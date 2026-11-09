#!/bin/bash
# Build a self-contained zstd (universal arm64 + x86_64, links only macOS's libSystem).
#
# Why: macOS's built-in `tar` reads .tar.zst only by running an external `zstd` program,
# and a fresh Mac has none. MacBackup puts this binary on the backup disk (tools/zstd),
# so restoring needs nothing installed:  PATH="/Volumes/<Disk>/MacBackup/tools:$PATH" tar -xf X -C ~
#
# Source: the official zstd release tarball, fetched and checksum-verified by Homebrew.
# Usage: scripts/build-portable-zstd.sh [output-path]   (default: app/build/zstd)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-app/build/zstd}"

brew fetch --build-from-source zstd >/dev/null
TARBALL="$(brew --cache --build-from-source zstd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -xzf "$TARBALL" -C "$WORK"
SRC="$(find "$WORK" -maxdepth 1 -type d -name 'zstd-*' | head -1)"

make -C "$SRC/programs" -j"$(sysctl -n hw.ncpu)" zstd-release \
  HAVE_LZMA=0 HAVE_LZ4=0 HAVE_ZLIB=0 HAVE_THREAD=1 \
  MOREFLAGS="-arch arm64 -arch x86_64 -mmacosx-version-min=11.0" >"$WORK/build.log" 2>&1 \
  || { tail -20 "$WORK/build.log" >&2; exit 1; }

BIN="$SRC/programs/zstd"
# Refuse a binary that needs anything beyond the system library (it would not run on a fresh Mac).
if otool -L "$BIN" | tail -n +2 | grep -v "/usr/lib/libSystem.B.dylib" | grep -q "dylib"; then
  echo "error: $BIN links non-system libraries:" >&2; otool -L "$BIN" >&2; exit 1
fi
mkdir -p "$(dirname "$OUT")"
cp "$BIN" "$OUT"
codesign -s - --force "$OUT"
echo "Done: $OUT ($("$OUT" --version | sed 's/^\*\** *//; s/ *\**$//'), $(lipo -archs "$OUT"))"
