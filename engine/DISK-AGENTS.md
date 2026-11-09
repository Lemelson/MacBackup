# MacBackup — notes for AI agents

Read this before restoring from this drive, or before debugging/changing MacBackup.
Explain results to the owner in plain words; never print secret values (Keychain export, `.env`, keys).

## Where things are

| What | Path |
|---|---|
| Engine (one file, Python 3.9+ stdlib + `zstd` CLI) | on the Mac: `MacBackup.app/Contents/Resources/engine/macbackup.py`; copy here: `tools/macbackup.py` |
| Settings | `~/Library/Application Support/MacBackup/config.json` |
| Live status (updated every second while running) | `~/Library/Application Support/MacBackup/state/status.json` |
| Index | `index.json` here (mirror: `…/MacBackup/state/index.json` on the Mac) |
| Event log (JSON lines, all runs) | `logs/runs.jsonl` here and on the Mac in `…/MacBackup/logs/` |
| Per-run log | `logs/run-<run_id>.log` |
| Unfinished run marker | `…/MacBackup/state/pending.json` (`since`) |
| Scheduler | `~/Library/LaunchAgents/com.macbackup.auto.plist` → `MacBackup --auto` → engine `auto` |

## Commands

```bash
E="python3 <path>/macbackup.py"
$E plan                     # scan only: sizes per part, what changed
$E run                      # exit 0 ok · 1 errors · 2 no disk · 3 already running · 4 cancelled · 5 paused (disk gone)
$E run --only projects/foo  # one part (prefix)
$E run --force --only ai/   # rebuild even if unchanged
$E verify --all             # re-verify every archive
$E auto                     # scheduler logic
$E cancel
```

## Model

- **Part (unit)** = `archives/<layer>/<id>.tar.zst` + manifest `<id>.files.tsv`
  (`path, size, mtime_ns, sha256, kind`; kind f=file, s=SQLite snapshot, l=symlink, g=generated).
  Layers: `keys`, `ai`, `projects`, `system`, `media` (`media` = plain uncompressed folder copies, path relative to the disk root).
- Parts are rebuilt from the home folder on every run, so new folders are picked up automatically.
- Paths in archives are relative to `~` → restore is always `tar -xf X -C ~`.
- **Incremental**: fingerprint over (path, kind, size, mtime_ns, link target) + engine version + zstd args.
  Same fingerprint + archive present + last verification ok → skipped. Bump `ENGINE_VERSION` to rebuild everything.
- **Excluded**: dependency/build/cache directory names (`EXCLUDE_DIR_NAMES`), videos > `video_skip_mb`,
  `.gguf/.safetensors` > 200 MB, `.f32` render buffers (list: `inventory/skipped-big-media.tsv`).
- **SQLite** files are snapshotted with the backup API (gives up after 20 s if locked, then copies raw).
- **Verification**: decompress, hash every member, compare with the manifest, compare the archive's SHA-256 with
  the one computed while writing, then spot-check up to 25 unchanged files against the originals.
- Writes go to `*.part` and are renamed only after success. Leftovers are removed at the start of the next run.
- **Disk disconnected / access denied mid-run** → `run_interrupted` event, status `interrupted`, `pending.json`
  stays; the scheduler resumes on the next mount. No notification. Reminders only start
  `reminder_grace_days` after a backup became due or an unfinished run began, at most once per `reminder_every_hours`.

## Reading the logs

`runs.jsonl` events: `run_start`, `unit_start`, `unit_done` (bytes_raw, bytes_archive, seconds, note), `unit_skip`,
`unit_fail` (error), `verify_ok` / `verify_fail`, `warn` (unreadable files; "No access" = grant Full Disk Access to
MacBackup.app), `copy_done` (mirror folders), `run_done` / `run_fail` / `run_cancel` / `run_interrupted` with totals, `info`.

```bash
python3 -c "import json;i=json.load(open('index.json'));u=i['units'];print(i.get('last_success_at'),len(u),sum(not v.get('verify_ok') for v in u.values()),'failed')"
tail -5 logs/runs.jsonl
```

When you change something by hand, append an `info` line to `runs.jsonl` saying what and why, so the next agent can follow.
