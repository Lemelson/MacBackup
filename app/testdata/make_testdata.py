#!/usr/bin/env python3
"""Generate fake MacBackup engine files for testing the app (no real backups, no personal data).

Usage: make_testdata.py [scenario] [en|ru]
  scenario: idle | running | verifying | stalled | interrupted | failed | due | empty | noaccess | nodisk
Writes testdata/data/{config.json,state,logs} and testdata/disk/Backup/MacBackup/{index.json,README.md}.
"""
import json, os, random, shutil, sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
VOLUME = os.path.join(ROOT, "disk", "Backup")
DEST = os.path.join(VOLUME, "MacBackup")
scenario = sys.argv[1] if len(sys.argv) > 1 else "idle"
lang = sys.argv[2] if len(sys.argv) > 2 else "en"
RU = lang == "ru"
def T(en, ru): return ru if RU else en
now = datetime.now().astimezone()
random.seed(7)

def iso(d): return d.isoformat(timespec="seconds") if d else None
def rid(d): return d.strftime("%Y-%m-%dT%H-%M-%S")

shutil.rmtree(DATA, ignore_errors=True)
shutil.rmtree(os.path.join(ROOT, "disk"), ignore_errors=True)
for d in (os.path.join(DATA, "state"), os.path.join(DATA, "logs")):
    os.makedirs(d, exist_ok=True)
if scenario not in ("nodisk", "interrupted"):
    os.makedirs(DEST, exist_ok=True)

with open(os.path.join(DATA, "config.json"), "w") as f:
    json.dump({"language": lang, "disk_volume": VOLUME, "dest_folder": "MacBackup", "due_days": 7,
               "reminder_grace_days": 3, "engine_only_key": "kept by the app"}, f, indent=2)

UNITS = [  # layer, key name, title, source, GB, compression
    ("keys", "ssh", "SSH keys", "~/.ssh", 0.0002, 1.1),
    ("keys", "keychains", "Keychains", "~/Library/Keychains", 0.05, 1.3),
    ("keys", "gnupg", "GnuPG", "~/.gnupg", 0.001, 1.6),
    ("ai", "claude-code", "Claude Code", "~/.claude", 2.4, 5.8),
    ("ai", "codex", "Codex", "~/.codex", 1.1, 6.3),
    ("ai", "claude-projects/my-app", "claude-projects/my-app", "~/.claude/projects/my-app", 0.6, 4.9),
    ("projects", "my-website", "my-website", "~/Projects/my-website", 3.3, 3.7),
    ("projects", "photo-tools", "photo-tools", "~/Projects/photo-tools", 0.8, 4.1),
    ("projects", "budget-tracker", "budget-tracker", "~/Projects/budget-tracker", 1.9, 3.2),
    ("projects", "video-notes", "video-notes", "~/Projects/video-notes", 14.2, 1.4),
    ("projects", "home-automation", "home-automation", "~/Projects/home-automation", 0.03, 5.0),
    ("projects", "recipe-app", "recipe-app", "~/Projects/recipe-app", 0.4, 3.9),
    ("projects", "chat-bot", "chat-bot", "~/Projects/chat-bot", 2.7, 2.1),
    ("projects", "long-name", "an-unusually-long-project-name-to-check-the-table-layout", "~/Projects/long", 0.12, 2.8),
    ("system", "preferences", "App preferences", "~/Library/Preferences", 0.3, 4.4),
    ("system", "homebrew", "Homebrew package list", "/opt/homebrew", 0.001, 3.0),
    ("system", "launchagents", "LaunchAgents", "~/Library/LaunchAgents", 0.0001, 2.5),
    ("system", "app-support", "Application Support", "~/Library/Application Support", 38.0, 1.9),
    ("media", "photos", "Photos", "~/Pictures", 61.0, 1.0),
    ("media", "documents", "Documents", "~/Documents", 9.4, 1.0),
    ("media", "desktop", "Desktop", "~/Desktop", 4.6, 1.0),
]
GB = 1_000_000_000
last_ok = now - (timedelta(days=9) if scenario == "due" else timedelta(days=2, hours=1, minutes=20))

units = {}
for layer, name, title, src, gb, ratio in UNITS:
    key = f"{layer}/{name}"
    raw = int(gb * GB)
    created = last_ok - timedelta(days=random.choice([0, 0, 0, 2, 5, 9]), minutes=random.randint(0, 50))
    bad, never = name == "chat-bot", name == "desktop"
    units[key] = {
        "layer": layer, "title": title, "source": src,
        "archive": f"Media/{title}" if layer == "media" else f"archives/{layer}/{name}.tar.zst",
        "files": random.randint(10, 90000), "bytes_raw": raw,
        "bytes_archive": raw if layer == "media" else int(raw / ratio),
        "archive_sha256": "%064x" % random.getrandbits(256),
        "created_at": iso(created),
        "verified_at": None if never else iso(created + timedelta(minutes=3)),
        "verify_ok": None if never else (not bad),
        "seconds": round(raw / 150_000_000, 1), "skipped_large_files": 3 if name == "photos" else 0,
    }

if scenario != "empty":
    index = {"version": 1, "updated_at": iso(last_ok), "units": units}
    targets = [os.path.join(DATA, "state", "index.json")]
    if os.path.isdir(DEST): targets.append(os.path.join(DEST, "index.json"))
    for p in targets:
        with open(p, "w") as f: json.dump(index, f, ensure_ascii=False, indent=1)
    if os.path.isdir(DEST):
        with open(os.path.join(DEST, "README.md"), "w") as f:
            f.write("# MacBackup (test data)\n\nThis README is fake. The real one is written by the engine.\n")

events = []
runs = [  # start, mode, outcome, seconds, done, skipped, failed, raw, archive
    (last_ok - timedelta(days=21), "manual", "done", 3710, 21, 0, 0, 140 * GB, 92 * GB),
    (last_ok - timedelta(days=14), "auto", "done", 640, 6, 15, 0, 21 * GB, 9 * GB),
    (last_ok - timedelta(days=7), "auto", "fail", 410, 4, 15, 1, 6 * GB, 2.1 * GB),
    (last_ok - timedelta(days=3), "manual", "cancel", 95, 2, 3, 0, 1.2 * GB, 0.4 * GB),
    (last_ok - timedelta(minutes=12), "manual", "done", 720, 7, 13, 1, 18.3 * GB, 7.9 * GB),
]
if scenario == "failed":
    runs.append((now - timedelta(hours=3), "auto", "fail", 33, 0, 0, 0, 0, 0))
if scenario == "empty":
    runs = []
for start, mode, outcome, secs, done, skipped, failed, raw, arch in runs:
    r = rid(start)
    end = start + timedelta(seconds=secs)
    events.append({"ts": iso(start), "run_id": r, "event": "run_start", "mode": mode})
    log = [f"{start:%Y-%m-%d %H:%M:%S} " + T(f"Starting backup ({mode})", f"Начинаю копию ({'вручную' if mode == 'manual' else 'авто'})"),
           f"{start:%Y-%m-%d %H:%M:%S} " + T("Backup disk connected, 417 GB free", "Диск подключён, свободно 417 ГБ")]
    for k in list(units)[:done]:
        events.append({"ts": iso(start + timedelta(seconds=5)), "run_id": r, "event": "unit_done", "unit": k,
                       "bytes_raw": units[k]["bytes_raw"], "bytes_archive": units[k]["bytes_archive"], "seconds": 4.2})
        log.append(f"{start:%H:%M:%S} " + T("Packed", "Упаковал") + f" {k}: {units[k]['bytes_raw'] / GB:.1f} GB → {units[k]['bytes_archive'] / GB:.1f} GB")
    if failed:
        err = T("No access to file Library/Messages/chat.db (Operation not permitted)",
                "Нет доступа к файлу Library/Messages/chat.db (Operation not permitted)") if outcome == "done" \
            else T("The backup disk was disconnected while writing (Input/output error)",
                   "Диск отключился во время записи (Input/output error)")
        events.append({"ts": iso(end), "run_id": r, "event": "unit_fail", "unit": "projects/chat-bot", "error": err})
        log.append(f"{end:%H:%M:%S} ERROR projects/chat-bot: {err}")
    ev = {"done": "run_done", "fail": "run_fail", "cancel": "run_cancel"}[outcome]
    e = {"ts": iso(end), "run_id": r, "event": ev, "mode": mode, "units_done": done, "units_skipped_unchanged": skipped,
         "units_failed": failed, "bytes_raw": raw, "bytes_archive": arch, "seconds": secs}
    if outcome == "fail":
        e["error"] = T("Not enough space on the backup disk", "Не хватает места на диске для копий") if not raw \
            else T("The backup disk was disconnected during the backup", "Диск отключили во время копии")
    events.append(e)
    log.append(f"{end:%H:%M:%S} " + T("Finished", "Конец") + f": {ev}")
    log += [f"{end:%H:%M:%S} " + T(f"detail {i}: sample log line to check scrolling", f"подробность {i}: строка журнала для проверки прокрутки") for i in range(60)]
    with open(os.path.join(DATA, "logs", f"run-{r}.log"), "w") as f:
        f.write("\n".join(log) + "\n")

if scenario == "noaccess" and runs:
    events.append({"ts": iso(now), "run_id": rid(runs[-1][0]), "event": "warn",
                   "message": T("No access to ~/Library/Mail (Operation not permitted)", "Нет доступа к ~/Library/Mail (Operation not permitted)")})

with open(os.path.join(DATA, "logs", "runs.jsonl"), "w") as f:
    for i, e in enumerate(events):
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
        if i == 3: f.write("{this line is broken on purpose and must be skipped\n")

st = {"state": "idle", "run_id": rid(runs[-1][0]) if runs else None, "mode": "manual",
      "started_at": None, "updated_at": iso(now), "finished_at": iso(last_ok) if runs else None,
      "disk_mounted": True, "disk_free_bytes": 417 * GB, "phase": None, "current_unit": None,
      "units_total": 0, "units_done": 0, "units_skipped_unchanged": 0, "units_failed": 0,
      "bytes_total": 0, "bytes_done": 0, "speed_bps": 0.0, "eta_seconds": None,
      "last_success_at": iso(last_ok) if runs else None, "message": "", "pid": None}
if scenario in ("running", "verifying", "stalled", "interrupted"):
    st.update({"state": "verifying" if scenario == "verifying" else "running", "run_id": rid(now - timedelta(minutes=9)),
               "started_at": iso(now - timedelta(minutes=9)), "phase": "verify" if scenario == "verifying" else "archive",
               "current_unit": "projects/my-website", "units_total": 21, "units_done": 9,
               "units_skipped_unchanged": 4, "bytes_total": 150 * GB, "bytes_done": 91.5 * GB,
               "speed_bps": 172_000_000.0, "eta_seconds": 340, "finished_at": None,
               "message": T("Compressing project my-website", "Сжимаю проект my-website"), "pid": 1})  # pid 1 is always alive
    if scenario == "stalled":
        st["updated_at"] = iso(now - timedelta(minutes=40)); st["pid"] = 999999
    if scenario == "interrupted":
        st.update({"state": "interrupted", "pid": None, "disk_mounted": False,
                   "pending_since": iso(now - timedelta(hours=2, minutes=5))})
if scenario == "failed":
    st["state"] = "failed"
    st["message"] = T("Not enough space on the backup disk", "Не хватает места на диске для копий")
if scenario != "empty":
    with open(os.path.join(DATA, "state", "status.json"), "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
print(f"testdata scenario '{scenario}' ({lang}) written to {ROOT}")
