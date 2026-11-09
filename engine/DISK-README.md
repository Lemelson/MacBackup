# MacBackup — backup of a Mac's work data

This drive holds everything that mattered on the Mac it came from: keys and server access, AI-tool
history (Codex, Claude Code, …), every project with its git history, and system settings —
so the work can be restored on any other Mac.

The archives are not password-protected; protect the drive itself (APFS encrypted).

## What's here

| Path | Contents |
|---|---|
| `archives/keys/` | SSH keys, Keychain files, optional Keychain export (`keychain-export`), all `.env`/key/token files gathered from projects (`secret-files`) |
| `archives/ai/` | Codex (sessions per month, settings, skills, memory, images), Claude Code (chats per project, skills, memory, settings), Claude/Codex apps, other AI tools |
| `archives/projects/` | One archive per project folder: code, git history, `.env`, databases. Without `node_modules`, venvs, build output, caches, and videos > 20 MB |
| `archives/system/` | Dotfiles, `~/.config`, `~/.local`, login items, app preferences, fonts, browser profiles, Voice Memos, app data, list of apps |
| `inventory/` | Readable without unpacking: `apps.tsv`, `brew.txt`, `launchagents.tsv`, `git-repos.tsv` (unpushed work), `keychain-item-names.tsv`, `skipped-big-media.tsv` |
| `index.json` | Table of contents: every part, its size, when it was written, whether it passed verification |
| `logs/` | `runs.jsonl` (every event of every run) and `run-*.log` (one human-readable log per run) |
| `tools/macbackup.py` | The engine itself |

Every archive has a `*.files.tsv` manifest: path, size, change time, SHA-256 of each file.
Which archive contains a file: `grep -l "file-name" archives/*/*.files.tsv archives/*/*/*.files.tsv`

## Restore on a new Mac

Each archive unpacks **into the home folder** with the `tar` built into macOS — nothing to install:

```bash
tar -xf /Volumes/<Disk>/MacBackup/archives/projects/my-app.tar.zst -C ~
```

Suggested order (or ask Claude Code / Codex: "restore my Mac from this drive using its README"):

1. **Keys**
   ```bash
   for a in /Volumes/<Disk>/MacBackup/archives/keys/*.tar.zst; do tar -xf "$a" -C ~; done
   chmod 700 ~/.ssh; chmod 600 ~/.ssh/*
   ```
   Exported Keychain passwords (if enabled) land in `~/MacBackup-restore/keychain-work-items.json`.
   The whole login Keychain is `~/Library/Keychains/login.keychain-db`: Keychain Access → File → Import;
   its password is the **login password of the old Mac**.
2. **AI tools** — `archives/ai/*`, then install the apps and sign in again.
3. **Projects** — `archives/projects/*` (one at a time is fine). Reinstall dependencies
   (`npm install`, `python3 -m venv .venv && pip install -r requirements.txt`, …).
4. **System** — `archives/system/*`. Reinstall apps from `inventory/apps.tsv` and `inventory/brew.txt`.
5. **Everything at once** on an empty Mac:
   ```bash
   cd /Volumes/<Disk>/MacBackup && find archives -name '*.tar.zst' -print0 | xargs -0 -n1 tar -C ~ -xf
   ```

## How the copy is kept fresh

- The MacBackup app ("Back up now", progress, contents, history) and a scheduler that checks hourly and on
  every disk mount; it backs up when the last backup is older than the configured interval.
- Later runs only rewrite parts that changed.
- Every archive is decompressed after writing and every file re-hashed; up to 25 files per part are compared
  with the originals.
- Unplugging the disk mid-run is safe: the run pauses and continues next time.
- Parts whose source was deleted on the Mac stay here (`source_missing: true` in `index.json`).
