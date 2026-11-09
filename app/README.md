# MacBackup app

A native macOS window + menu bar item for the MacBackup engine (`../engine/macbackup.py`).
The app never copies files itself: it shows the engine's status and starts it.
UI in English or Russian (switch in Settings ⌘, or the globe menu in the main window).

## Build
    ./build.sh            # builds, bundles ../engine/macbackup.py, installs ~/Applications/MacBackup.app
`BUNDLE_ID=…` overrides the bundle identifier (default `app.macbackup.MacBackup`). Ad-hoc signed;
every rebuild changes the signature, so macOS may ask again for Full Disk Access / notifications.

## Command-line modes
- `MacBackup --auto` — no window or menu bar item: runs `macbackup.py auto` as a child process (file access
  is attributed to MacBackup.app, which gets Full Disk Access once), waits, returns its exit code,
  appends output to `<data>/logs/auto.log`. Meant for launchd (hourly and on disk mount).
- `MacBackup --notify "<title>" "<body>"` — posts a notification as the app and exits. The engine calls it
  (the app passes its own path to the engine in `MACBACKUP_APP`).
- Manual backups from the window also run as a child of the app. Closing the window keeps the app in the
  menu bar; quitting during a backup asks first.

## Where data lives
`~/Library/Application Support/MacBackup/` (override: `MACBACKUP_DATA`):
`config.json`, `state/status.json`, `state/index.json` (mirror), `logs/runs.jsonl`, `logs/run-<id>.log`,
`logs/app-launch.log`, `logs/auto.log`. On the backup disk: `<disk_volume>/<dest_folder>/index.json` and `README.md`.
Python: `MACBACKUP_PYTHON`, else the first of /opt/homebrew/bin, /usr/local/bin, /usr/bin `python3`.
Engine: `MACBACKUP_ENGINE`, else `MacBackup.app/Contents/Resources/engine/macbackup.py`.

`config.json` (app reads and writes, engine reads; unknown keys are preserved):
`{"language": "en"|"ru", "disk_volume": "/Volumes/Backup", "dest_folder": "MacBackup", "due_days": 7, "reminder_grace_days": 3}`.

## Engine contract (what the app reads)
- `status.json`: `state` (idle | running | verifying | done | failed | cancelled | interrupted), `run_id`, `mode`,
  `started_at`, `updated_at`, `finished_at`, `pending_since`, `disk_mounted`, `disk_free_bytes`,
  `phase` (scan | archive | verify | copy | finish), `current_unit`, `units_total/done/skipped_unchanged/failed`,
  `bytes_total/done`, `speed_bps`, `eta_seconds`, `last_success_at`, `message`, `pid`.
  running/verifying with a dead pid or `updated_at` older than 2 min is shown as interrupted.
- `index.json`: `{version, updated_at, units: {"<layer>/<name>": {layer (keys|ai|projects|system|media), title,
  source, archive, files, bytes_raw, bytes_archive, archive_sha256, created_at, verified_at, verify_ok, seconds,
  skipped_large_files}}}`. Media `archive` is a plain folder relative to the disk root (shown uncompressed).
- `runs.jsonl`: one event per line `{ts, run_id, event, unit?, error?, message?, mode?}`; totals on
  run_done / run_fail / run_cancel (`units_done` = changed only, `units_skipped_unchanged`, `units_failed`,
  `bytes_raw`, `bytes_archive`, `seconds`). A `warn` mentioning "No access"/"Нет доступа" in the newest run shows
  the Full Disk Access banner. Broken lines are skipped. Dates are ISO 8601 (no zone = local time).
- Engine exit code 3 = already running.

## Test mode (fake data, no real backups)
    python3 testdata/make_testdata.py running en   # idle|running|verifying|stalled|interrupted|failed|due|empty|noaccess|nodisk, en|ru
    MACBACKUP_TESTDATA=$PWD/testdata ~/Applications/MacBackup.app/Contents/MacOS/MacBackup --test [--tab=history] [--settings]
    testdata/shot.sh running ../docs/images/app-running.png en   # window-only screenshot (APPEARANCE=light, HIDE_BADGE=1)
Test mode reads `testdata/data` and runs `testdata/fake_engine.py` (pretends for ~30 s).
`MACBACKUP_APPEARANCE=light|dark` forces the look.
