#!/usr/bin/env python3
"""MacBackup engine: incremental, verified, compressed backup of a Mac's work data
(keys, AI-tool history, projects, settings) to an external drive.

Every archive is a tar compressed with zstd whose paths are relative to the home
folder, so any archive restores with:  tar -xf <archive>.tar.zst -C ~
(macOS's built-in tar reads .tar.zst; nothing to install on the new Mac).

Commands:
  plan              scan everything, print what would be archived (writes nothing)
  run [--mode M]    back up changed parts, verify each archive byte by byte
  verify [--all]    re-verify archives already on the disk
  auto              what the scheduler runs: back up if due/unfinished and the disk is
                    connected, otherwise remind (rarely)
  cancel            ask a running engine to stop
  status            print the live status JSON

Settings: ~/Library/Application Support/MacBackup/config.json (see config.example.json).
Requires Python 3.9+ (stdlib only) and the `zstd` command (brew install zstd).
"""
from __future__ import annotations

import argparse
import datetime as dt
import filecmp
import hashlib
import io
import json
import os
import random
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_VERSION = 1          # part of every fingerprint: bump to rebuild all archives
HOME = Path.home()
DATA = Path(os.environ.get("MACBACKUP_DATA", HOME / "Library/Application Support/MacBackup"))
STATE = DATA / "state"
LOGS = DATA / "logs"
CONFIG_FILE = DATA / "config.json"
STATUS_FILE = STATE / "status.json"
LOCK_FILE = STATE / "lock"
CANCEL_FILE = STATE / "cancel"
PENDING_FILE = STATE / "pending.json"
INDEX_MIRROR = STATE / "index.json"
ENGINE_DIR = Path(__file__).resolve().parent

DEFAULTS = {
    "language": "en",
    "disk_volume": "/Volumes/Backup",
    "dest_folder": "MacBackup",
    "due_days": 7,                 # back up when the last finished backup is this old
    "reminder_grace_days": 3,      # then wait this long for the disk before reminding
    "reminder_every_hours": 24,
    "home_skip": [],               # extra top-level home entries never backed up
    "documents_skip": [],          # Documents/<name> entries never backed up
    "split_dirs": [],              # folders (relative to ~) archived per child, e.g. "Documents/Codex"
    "system_folders": [],          # home folders treated as "system" instead of "projects", e.g. "Backups"
    "exclude_paths": [],           # paths relative to ~ never backed up
    "mirror_folders": [],          # [{"id","title","source","dest"}] copied as plain folders (no compression)
    "secret_extra_paths": [],      # extra files/folders for keys/secret-files (relative to ~ or absolute)
    "keychain_export_regex": "",   # login-keychain generic-password services exported WITH values ("" = off)
    "app_data_folders": ["Obsidian", "Transmission"],  # ~/Library/Application Support/<name>
    "video_skip_mb": 20,
    "app_path": "",                # MacBackup.app for notifications (auto-detected when empty)
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


CFG = load_config()
DISK = Path(os.environ.get("MACBACKUP_DISK_ROOT", CFG["disk_volume"]))
DEST = DISK / CFG["dest_folder"]
ZSTD = shutil.which("zstd") or next((p for p in ("/opt/homebrew/bin/zstd", "/usr/local/bin/zstd") if os.path.exists(p)), "zstd")
# Chosen by measurement (see README "Why zstd -9"): within 1-2% of level 19's ratio at 15-30x the speed.
ZSTD_LEVEL_ARGS = ["-9", "--long=27"]
MIN_FREE_BYTES = 5 * 1024**3

# ---- what is never copied --------------------------------------------------
EXCLUDE_DIR_NAMES = {
    "node_modules", ".venv", "venv", "__pycache__", ".next", ".nuxt", ".turbo",
    ".parcel-cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target",
    "DerivedData", ".gradle", ".build", "build", "dist", ".cache", "Pods",
    ".svelte-kit", ".angular", ".expo", "bower_components", ".tox",
    "Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
    "ShaderCache", "GrShaderCache", "CacheStorage", "Crashpad", "blob_storage",
    "component_crx_cache", "optimization_guide_model_store",
}
EXCLUDE_FILE_NAMES = {".DS_Store"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mts", ".m2ts", ".mxf", ".prores"}
VIDEO_SKIP_BYTES = int(CFG["video_skip_mb"]) * 1024**2
MODEL_EXT = {".gguf", ".safetensors"}
MODEL_SKIP_BYTES = 200 * 1024**2
INTERMEDIATE_EXT = {".f32"}   # raw render buffers
HOME_SKIP = {
    "Library", "Downloads", "Movies", "Pictures", "Music", "Applications", "Public",
    ".Trash", ".cache", ".cargo", ".rustup", ".gradle", ".android", ".npm", ".pnpm-store",
    ".yarn", ".bun", ".docker", ".ollama", ".lmstudio", ".m2", ".nvm", ".pyenv", ".conda",
    ".cocoapods", ".swiftpm", ".vscode", ".vscode-shared", ".openjfx", "nltk_data",
    ".antigravity", ".cursor-server", ".zcompdump",
    ".CFUserTextEncoding", ".lesshst", ".viminfo", ".python_history", ".node_repl_history",
} | set(CFG["home_skip"])
HOME_SPECIAL = {".codex", ".claude", ".claude.json", ".gemini", ".ssh", ".local", "Documents",
                ".claude-science", ".grok", ".agents", ".cursor", ".copilot", ".codexbar", ".hyperframes"} \
    | set(CFG["system_folders"])
SPLIT_DIRS = {HOME / p for p in CFG["split_dirs"]}
GLOBAL_EXCLUDE_REL = {p.rstrip("/") for p in CFG["exclude_paths"]} | \
    {str(Path(m["source"]).expanduser().relative_to(HOME)) for m in CFG["mirror_folders"]
     if str(Path(m["source"]).expanduser()).startswith(str(HOME) + "/")}

SECRET_NAME_RE = re.compile(
    r"(^\.env($|\.)|\.env$|^\.envrc$|\.pem$|\.key$|\.p12$|\.p8$|^credentials.*\.json$|"
    r"^client_secret.*\.json$|^secrets?\.(json|env|ya?ml|toml)$|^secret_key.*|^\.flask_secret_key$|"
    r"^auth\.json$|^\.netrc$|^\.npmrc$|^\.pypirc$|_ed25519$|_rsa$)", re.I)
SECRET_DIR_NAMES = {".secrets", "2fa-backup-codes"}
SECRET_DEFAULT_PATHS = [".codex/auth.json", ".config/gh", ".kaggle", ".aws", ".config/gcloud", ".netrc"]
KEYCHAIN_EXPORT_RE = re.compile(CFG["keychain_export_regex"], re.I) if CFG["keychain_export_regex"] else None
KEYCHAIN_EXPORT_SKIP_RE = re.compile(r"(Safe Storage|^com\.apple|^Adobe|AirPort|iCloud|WiFi)", re.I)

# ---- texts (status, titles, notifications) in English and Russian ---------
TEXTS = {
    "en": {
        "start": "Starting", "scan": "Looking for changes since last time", "scan_unit": "Checking: {t}",
        "compress": "Compressing: {t}", "verify": "Verifying byte by byte: {t}", "verify_one": "Verifying: {t}",
        "mirror": "Copying folder: {t}", "no_disk": "The backup disk is not connected",
        "done": "Done: {d} updated, {s} unchanged, {f} errors. Wrote {w} in {m} min. Whole backup on disk: {a}",
        "done_short": "Backup updated: {d} parts changed, {f} errors. Whole backup: {a}",
        "errors": "Backup finished with {f} errors. Open MacBackup to see which parts.",
        "cancelled": "Stopped. Finished parts are kept; the next run continues from here.",
        "interrupted": "Paused — the backup disk was disconnected. It will continue when the disk is connected again.",
        "failed": "Backup stopped: {e}", "verified": "Checked {n} archives, {b} with problems",
        "remind": "Your last backup was {d}. Connect the backup disk — the backup starts by itself.",
        "remind_pending": "A backup started {d} is still unfinished. Connect the backup disk to finish it.",
        "never": "never made", "days_ago": "{n} days ago",
        "low_space": "Not enough space on the backup disk for {u}",
        # unit titles
        "t_ssh": "SSH keys and settings", "t_keychains": "Keychain files", "t_kc_export": "Saved service passwords (Keychain export)",
        "t_secret_files": "All .env files, keys and tokens", "t_codex_month": "Codex: sessions {m}",
        "t_codex_files": "Codex: session files", "t_codex_archived": "Codex: archived sessions",
        "t_codex_images": "Codex: generated images", "t_codex_core": "Codex: settings, skills, memory, history",
        "t_claude_project": "Claude Code: {n}", "t_claude_files": "Claude Code: loose project files",
        "t_claude_core": "Claude Code: skills, memory, settings", "t_claude_app": "Claude app: chats and workspaces",
        "t_codex_app": "Codex app", "t_gemini": "Gemini / Antigravity", "t_other_ai": "Other AI tools (Grok, Cursor, agents…)",
        "t_speech": "Speech recognition models (FluidAudio)", "t_home_files": "Loose files in the home folder",
        "t_docs": "Documents/{n}", "t_docs_files": "Documents (loose files)", "t_split_files": "{n} (loose files)",
        "t_dotfiles": "Hidden settings in the home folder", "t_local": "~/.local: own commands and services",
        "t_libsettings": "Login items, app preferences, fonts", "t_sysfolder": "~/{n}",
        "t_macbackup": "MacBackup settings and logs", "t_browser": "Browser {n}: bookmarks, passwords, profiles",
        "t_appdata": "App data: {n}", "t_voice": "Voice Memos", "t_inventory": "List of apps, login items, repositories",
    },
    "ru": {
        "start": "Начинаю", "scan": "Смотрю, что изменилось с прошлого раза", "scan_unit": "Смотрю: {t}",
        "compress": "Сжимаю: {t}", "verify": "Проверяю побайтово: {t}", "verify_one": "Проверяю: {t}",
        "mirror": "Копирую папку: {t}", "no_disk": "Диск для копии не подключён",
        "done": "Готово: обновлено {d}, без изменений {s}, ошибок {f}. Записано {w} за {m} мин. Вся копия на диске: {a}",
        "done_short": "Копия обновлена: изменилось {d}, ошибок {f}. Вся копия: {a}",
        "errors": "Копия закончилась с ошибками ({f}). Откройте MacBackup, чтобы увидеть какие.",
        "cancelled": "Остановлено. Готовые части сохранены, в следующий раз продолжу с этого места.",
        "interrupted": "Пауза — диск отключили. Копия продолжится, когда диск снова подключат.",
        "failed": "Копия остановилась: {e}", "verified": "Проверено {n} архивов, с ошибками {b}",
        "remind": "Последняя копия: {d}. Подключите диск для копии — копия начнётся сама.",
        "remind_pending": "Копия, начатая {d}, ещё не закончена. Подключите диск, чтобы её доделать.",
        "never": "ещё не было", "days_ago": "{n} дн. назад",
        "low_space": "На диске не хватает места для {u}",
        "t_ssh": "SSH-ключи и настройки", "t_keychains": "Связка ключей (файлы)", "t_kc_export": "Пароли сервисов из связки ключей",
        "t_secret_files": "Все .env, ключи и токены", "t_codex_month": "Codex: сессии {m}",
        "t_codex_files": "Codex: файлы сессий", "t_codex_archived": "Codex: архив сессий",
        "t_codex_images": "Codex: картинки", "t_codex_core": "Codex: настройки, скиллы, память, история",
        "t_claude_project": "Claude Code: {n}", "t_claude_files": "Claude Code: файлы projects",
        "t_claude_core": "Claude Code: скиллы, память, настройки", "t_claude_app": "Приложение Claude: чаты, рабочие папки",
        "t_codex_app": "Приложение Codex", "t_gemini": "Gemini / Antigravity", "t_other_ai": "Другие ИИ-инструменты (Grok, Cursor, agents…)",
        "t_speech": "Модели распознавания речи (FluidAudio)", "t_home_files": "Файлы в корне домашней папки",
        "t_docs": "Документы/{n}", "t_docs_files": "Документы (файлы в корне)", "t_split_files": "{n} (файлы)",
        "t_dotfiles": "Скрытые настройки в домашней папке", "t_local": "~/.local: свои команды и сервисы",
        "t_libsettings": "Автозапуски, настройки программ, шрифты", "t_sysfolder": "~/{n}",
        "t_macbackup": "Настройки и журналы MacBackup", "t_browser": "Браузер {n}: закладки, пароли, профили",
        "t_appdata": "Данные программ: {n}", "t_voice": "Диктофон", "t_inventory": "Список программ, автозапусков, репозиториев",
    },
}


def T(key: str, **kw) -> str:
    lang = load_config().get("language", "en")
    s = TEXTS.get(lang, TEXTS["en"]).get(key) or TEXTS["en"][key]
    return s.format(**kw) if kw else s


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def human(n: float) -> str:
    ru = load_config().get("language") == "ru"
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ") if ru else ("B", "KB", "MB", "GB", "TB")
    for i, unit in enumerate(units):
        if abs(n) < 1024 or i == len(units) - 1:
            return f"{n:.1f} {unit}" if i else f"{int(n)} {unit}"
        n /= 1024
    return str(n)


def disk_ok() -> bool:
    """Mounted AND readable. macOS privacy (Removable Volumes / Full Disk Access) can deny
    access to a mounted disk; that is treated like a disconnected disk: pause, resume later."""
    try:
        if not (DISK.is_dir() and os.path.ismount(DISK)):
            return False
        os.listdir(DISK)
        return True
    except OSError:
        return False


class DiskGone(Exception):
    pass


class Cancelled(Exception):
    pass


# ---- logging ----------------------------------------------------------------
class Log:
    def __init__(self, run_id: str):
        self.run_id = run_id
        LOGS.mkdir(parents=True, exist_ok=True)
        self.text = open(LOGS / f"run-{run_id}.log", "a", encoding="utf-8")
        self.lock = threading.Lock()

    def event(self, event: str, **kw):
        rec = {"ts": now_iso(), "run_id": self.run_id, "event": event, **kw}
        line = json.dumps(rec, ensure_ascii=False)
        with self.lock:
            with open(LOGS / "runs.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def say(self, msg: str):
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        with self.lock:
            self.text.write(line + "\n")
            self.text.flush()
        print(line, flush=True)

    def close(self):
        self.text.close()
        sync_logs_to_disk()


def sync_logs_to_disk():
    """The disk keeps a copy of all logs, so a new Mac (or agent) can read the history."""
    if not disk_ok() or not DEST.is_dir():
        return
    try:
        (DEST / "logs").mkdir(exist_ok=True)
        for p in LOGS.glob("*"):
            q = DEST / "logs" / p.name
            if p.is_file() and (not q.exists() or q.stat().st_size != p.stat().st_size or q.stat().st_mtime < p.stat().st_mtime):
                shutil.copy2(p, q)
    except OSError:
        pass


# ---- status -----------------------------------------------------------------
class Status:
    def __init__(self, run_id: str, mode: str, state: str):
        self.d = {
            "state": state, "run_id": run_id, "mode": mode, "started_at": now_iso(),
            "updated_at": now_iso(), "finished_at": None, "disk_mounted": disk_ok(),
            "disk_free_bytes": disk_free(), "phase": "scan", "current_unit": None,
            "units_total": 0, "units_done": 0, "units_skipped_unchanged": 0, "units_failed": 0,
            "bytes_total": 0, "bytes_done": 0, "speed_bps": 0.0, "eta_seconds": None,
            "last_success_at": last_success_at(), "pending_since": pending_since(),
            "message": T("start"), "pid": os.getpid(),
        }
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def set(self, **kw):
        with self.lock:
            self.d.update(kw)

    def add_bytes(self, n: int):
        with self.lock:
            self.d["bytes_done"] += n

    def _write(self):
        with self.lock:
            d = dict(self.d)
        el = max(0.001, time.time() - self.t0)
        if d["phase"] in ("archive", "verify") and d["bytes_done"]:
            d["speed_bps"] = round(d["bytes_done"] / el, 1)
            left = max(0, d["bytes_total"] - d["bytes_done"])
            d["eta_seconds"] = int(left / d["speed_bps"]) if d["speed_bps"] else None
        d["updated_at"] = now_iso()
        d["disk_mounted"] = disk_ok()
        d["disk_free_bytes"] = disk_free()
        write_json_atomic(STATUS_FILE, d)

    def _loop(self):
        while not self.stop.wait(1.0):
            self._write()

    def finish(self, state: str, message: str):
        self.set(state=state, message=message, finished_at=now_iso(), phase="finish",
                 current_unit=None, last_success_at=last_success_at(), pending_since=pending_since())
        self.stop.set()
        self.thread.join(timeout=3)
        self._write()


def write_json_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def disk_free() -> int:
    try:
        return shutil.disk_usage(DISK).free if disk_ok() else 0
    except OSError:
        return 0


def load_index() -> dict:
    for p in ((DEST / "index.json") if disk_ok() else None, INDEX_MIRROR):
        if p is None:
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {"version": 1, "updated_at": None, "units": {}, "runs": []}


def save_index(idx: dict):
    idx["updated_at"] = now_iso()
    write_json_atomic(INDEX_MIRROR, idx)
    if not disk_ok():
        raise DiskGone()
    write_json_atomic(DEST / "index.json", idx)


def last_success_at():
    return load_index().get("last_success_at")


def pending_since():
    try:
        return json.loads(PENDING_FILE.read_text()).get("since")
    except (OSError, ValueError):
        return None


# ---- units ------------------------------------------------------------------
@dataclass
class Unit:
    id: str
    layer: str
    title: str
    items: list            # absolute Paths (files or dirs); arcname = path relative to HOME
    exclude_rel: set = field(default_factory=set)
    extra_exclude_names: set = field(default_factory=set)
    keep_names: set = field(default_factory=set)
    media_rule: bool = True
    generated: dict | None = None
    entries: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    bytes_raw: int = 0
    fingerprint: str = ""

    @property
    def archive_rel(self) -> str:
        return f"archives/{self.id}.tar.zst"


def slug(s: str) -> str:
    s = s.strip().replace("/", "__")
    s = re.sub(r"[\s]+", "_", s)
    s = re.sub(r"[^\w.\-]+", "-", s, flags=re.UNICODE)
    return s.strip("-_") or "unnamed"


def rel(p: Path) -> str:
    return str(p.relative_to(HOME))


def add_split_or_single(add, p: Path, uid: str, title: str, layer: str = "projects"):
    """A folder becomes one unit, or one unit per child when listed in split_dirs."""
    if p in SPLIT_DIRS:
        loose = []
        for q in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if q.is_dir() and not q.is_symlink():
                add(Unit(f"{uid}/{slug(q.name)}", layer, f"{title}/{q.name}", [q]))
            else:
                loose.append(q)
        if loose:
            add(Unit(f"{uid}/_files", layer, T("t_split_files", n=title), loose))
    else:
        add(Unit(uid, layer, title, [p]))


def build_units() -> list[Unit]:
    U: list[Unit] = []
    add = U.append
    L = HOME / "Library"
    AS = L / "Application Support"

    # keys ----------------------------------------------------------------
    add(Unit("keys/ssh", "keys", T("t_ssh"), [HOME / ".ssh"], media_rule=False))
    add(Unit("keys/keychains", "keys", T("t_keychains"), [L / "Keychains"], media_rule=False))

    # ai ------------------------------------------------------------------
    codex = HOME / ".codex"
    sess = codex / "sessions"
    if sess.is_dir():
        for y in sorted(p for p in sess.iterdir() if p.is_dir()):
            for m in sorted(p for p in y.iterdir() if p.is_dir()):
                add(Unit(f"ai/codex-sessions-{y.name}-{m.name}", "ai", T("t_codex_month", m=f"{y.name}-{m.name}"), [m], media_rule=False))
            loose = [p for p in y.iterdir() if p.is_file()]
            if loose:
                add(Unit(f"ai/codex-sessions-{y.name}-files", "ai", T("t_codex_files"), loose, media_rule=False))
        loose = [p for p in sess.iterdir() if p.is_file()]
        if loose:
            add(Unit("ai/codex-sessions-files", "ai", T("t_codex_files"), loose, media_rule=False))
    for sub, key in (("archived_sessions", "t_codex_archived"), ("generated_images", "t_codex_images")):
        if (codex / sub).exists():
            add(Unit(f"ai/codex-{slug(sub)}", "ai", T(key), [codex / sub], media_rule=False))
    if codex.is_dir():
        add(Unit("ai/codex-core", "ai", T("t_codex_core"), [codex],
                 exclude_rel={rel(sess), rel(codex / "archived_sessions"), rel(codex / "generated_images"),
                              rel(codex / ".tmp"), rel(codex / "cache")}, media_rule=False))
    claude = HOME / ".claude"
    cproj = claude / "projects"
    if cproj.is_dir():
        for p in sorted(cproj.iterdir()):
            if p.is_dir():
                add(Unit(f"ai/claude-projects/{slug(p.name)}", "ai", T("t_claude_project", n=p.name), [p], media_rule=False))
        loose = [p for p in cproj.iterdir() if not p.is_dir()]
        if loose:
            add(Unit("ai/claude-projects/_files", "ai", T("t_claude_files"), loose, media_rule=False))
    if claude.is_dir():
        items = [claude] + [p for p in HOME.glob(".claude.json*") if p.is_file()]
        add(Unit("ai/claude-core", "ai", T("t_claude_core"), items,
                 exclude_rel={rel(cproj), rel(claude / "cache"), rel(claude / "telemetry"),
                              rel(claude / "shell-snapshots"), rel(claude / "statsig"),
                              rel(claude / "worktrees")}, media_rule=False))
    if (AS / "Claude").is_dir():
        c = AS / "Claude"
        add(Unit("ai/claude-app", "ai", T("t_claude_app"), [c],
                 exclude_rel={rel(c / "vm_bundles"), rel(c / "claude-code"), rel(c / "claude-code-vm")}))
    if (AS / "Codex").is_dir():
        add(Unit("ai/codex-app", "ai", T("t_codex_app"), [AS / "Codex"]))
    g = HOME / ".gemini"
    if g.is_dir():
        add(Unit("ai/gemini-antigravity", "ai", T("t_gemini"), [g], exclude_rel={rel(g / "antigravity-browser-profile")}))
    others = [HOME / n for n in (".grok", ".agents", ".cursor", ".copilot", ".codexbar", ".hyperframes", ".claude-science")
              if (HOME / n).exists()]
    if others:
        add(Unit("ai/other-ai-tools", "ai", T("t_other_ai"), others,
                 extra_exclude_names={"conda", "miniconda", "miniconda3", "envs", "pkgs"}))
    if (AS / "FluidAudio").is_dir():
        add(Unit("ai/fluidaudio-models", "ai", T("t_speech"), [AS / "FluidAudio"], media_rule=False))

    # projects ----------------------------------------------------------
    loose_home, dot_items = [], []
    for p in sorted(HOME.iterdir(), key=lambda x: x.name.lower()):
        n = p.name
        if n in HOME_SKIP or n in HOME_SPECIAL or n.startswith(".zcompdump") or rel(p) in GLOBAL_EXCLUDE_REL:
            continue
        if p.is_symlink() or p.is_file():
            (dot_items if n.startswith(".") else loose_home).append(p)
            continue
        if not p.is_dir():
            continue
        if n.startswith("."):
            dot_items.append(p)
            continue
        add_split_or_single(add, p, f"projects/{slug(n)}", n)
    if loose_home:
        add(Unit("projects/_home-files", "projects", T("t_home_files"), loose_home))
    docs = HOME / "Documents"
    if docs.is_dir():
        dloose = []
        for p in sorted(docs.iterdir(), key=lambda x: x.name.lower()):
            if p.name in CFG["documents_skip"] or rel(p) in GLOBAL_EXCLUDE_REL:
                continue
            if p.is_dir() and not p.is_symlink():
                add_split_or_single(add, p, f"projects/Documents/{slug(p.name)}", T("t_docs", n=p.name))
            else:
                dloose.append(p)
        if dloose:
            add(Unit("projects/Documents/_files", "projects", T("t_docs_files"), dloose))

    # system ------------------------------------------------------------
    if dot_items:
        add(Unit("system/dotfiles", "system", T("t_dotfiles"), dot_items, media_rule=False))
    if (HOME / ".local").is_dir():
        add(Unit("system/local", "system", T("t_local"), [HOME / ".local"],
                 exclude_rel={".local/share/uv", ".local/share/pipx", ".local/state"}))
    smalls = [L / "LaunchAgents", L / "Preferences", L / "Fonts", L / "Services", L / "Scripts",
              L / "KeyBindings", L / "Spelling"]
    add(Unit("system/library-settings", "system", T("t_libsettings"), [p for p in smalls if p.exists()], media_rule=False))
    for name in CFG["system_folders"]:
        if (HOME / name).is_dir():
            add(Unit(f"system/{slug(name)}", "system", T("t_sysfolder", n=name), [HOME / name], media_rule=False))
    add(Unit("system/macbackup-data", "system", T("t_macbackup"), [DATA], exclude_rel={rel(STATE)} if str(DATA).startswith(str(HOME)) else set(),
             media_rule=False))
    browsers = [("helium", AS / "net.imput.helium"), ("chrome", AS / "Google" / "Chrome"), ("arc", AS / "Arc"),
                ("brave", AS / "BraveSoftware"), ("edge", AS / "Microsoft Edge"), ("comet", AS / "Comet"),
                ("firefox", AS / "Firefox"), ("safari", L / "Safari")]
    for name, p in browsers:
        if p.is_dir():
            add(Unit(f"system/browser-{name}", "system", T("t_browser", n=name), [p],
                     extra_exclude_names={"Service Worker", "IndexedDB", "WebStorage", "File System",
                                          "Extensions", "Safe Browsing", "OnDeviceHeadSuggestModel",
                                          "ZxcvbnData", "hyphen-data", "Subresource Filter",
                                          "MEIPreload", "TrustTokenKeyCommitments", "WasmTtsEngine", "cache2"}))
    appdata = [AS / n for n in CFG["app_data_folders"] if (AS / n).exists()]
    if appdata:
        add(Unit("system/app-data", "system", T("t_appdata", n=", ".join(p.name for p in appdata)), appdata,
                 extra_exclude_names={"Models", "models", "Caches", "Media", "Metadata", "Logs"}))
    vm = L / "Group Containers" / "group.com.apple.VoiceMemos.shared"
    if vm.is_dir():
        add(Unit("system/voice-memos", "system", T("t_voice"), [vm], media_rule=False))
    for u in U:
        u.exclude_rel |= GLOBAL_EXCLUDE_REL
    return U


# ---- scanning ----------------------------------------------------------------
@dataclass
class Entry:
    path: str
    arc: str
    kind: str     # f, d, l, g
    size: int
    mtime_ns: int
    mode: int
    link: str = ""


def scan_unit(u: Unit, secret_hits: list | None):
    excl_names = (EXCLUDE_DIR_NAMES | u.extra_exclude_names) - u.keep_names
    excl_rel = {e.rstrip("/") for e in u.exclude_rel}
    entries, skipped, errors = [], [], []

    def add_file(p: str, st: os.stat_result):
        arc = os.path.relpath(p, HOME)
        name = os.path.basename(p)
        if name in EXCLUDE_FILE_NAMES or name.endswith("-shm"):
            # SQLite "-shm" is shared memory, rebuilt on open; reading the database (our own
            # snapshot) touches it, which would make the part look changed on every run.
            return
        if stat.S_ISLNK(st.st_mode):
            try:
                entries.append(Entry(p, arc, "l", 0, st.st_mtime_ns, st.st_mode, os.readlink(p)))
            except OSError as e:
                errors.append(f"{arc}: {e}")
            return
        if not stat.S_ISREG(st.st_mode):
            return
        ext = os.path.splitext(name)[1].lower()
        if u.media_rule and (ext in INTERMEDIATE_EXT or (ext in VIDEO_EXT and st.st_size > VIDEO_SKIP_BYTES) or
                             (ext in MODEL_EXT and st.st_size > MODEL_SKIP_BYTES)):
            skipped.append((arc, st.st_size))
            return
        entries.append(Entry(p, arc, "f", st.st_size, st.st_mtime_ns, st.st_mode))
        if secret_hits is not None and SECRET_NAME_RE.search(name) and st.st_size < 5 * 1024**2:
            secret_hits.append(p)

    for item in u.items:
        item = Path(item)
        try:
            st = os.lstat(item)
        except OSError as e:
            errors.append(f"{item}: {e}")
            continue
        if not stat.S_ISDIR(st.st_mode):
            add_file(str(item), st)
            continue
        for root, dirs, files in os.walk(item, onerror=lambda e: errors.append(f"{os.path.relpath(e.filename or '', HOME)}: {e.strerror}")):
            rroot = os.path.relpath(root, HOME)
            keep = []
            for d in dirs:
                full = os.path.join(root, d)
                rd = os.path.relpath(full, HOME)
                if d in excl_names or rd in excl_rel:
                    continue
                if os.path.islink(full):
                    try:
                        add_file(full, os.lstat(full))
                    except OSError as e:
                        errors.append(f"{rd}: {e}")
                    continue
                keep.append(d)
            dirs[:] = sorted(keep)
            if secret_hits is not None and os.path.basename(root) in SECRET_DIR_NAMES:
                secret_hits.append(root)
            try:
                dst = os.lstat(root)
                entries.append(Entry(root, rroot, "d", 0, dst.st_mtime_ns, dst.st_mode))
            except OSError:
                pass
            for f in sorted(files):
                full = os.path.join(root, f)
                if os.path.relpath(full, HOME) in excl_rel:
                    continue
                try:
                    add_file(full, os.lstat(full))
                except OSError as e:
                    errors.append(f"{os.path.relpath(full, HOME)}: {e.strerror}")
    if u.generated:
        for arc, data in sorted(u.generated.items()):
            entries.append(Entry("", arc, "g", len(data), 0, 0o600))
    u.entries, u.skipped, u.errors = entries, skipped, errors
    u.bytes_raw = sum(e.size for e in entries)
    h = hashlib.sha256(f"v{ENGINE_VERSION}|{' '.join(ZSTD_LEVEL_ARGS)}\n".encode())
    for e in entries:
        if e.kind == "g":
            h.update(f"{e.arc}\0g\0{hashlib.sha256(u.generated[e.arc]).hexdigest()}\n".encode())
        else:
            h.update(f"{e.arc}\0{e.kind}\0{e.size}\0{e.mtime_ns}\0{e.link}\n".encode())
    u.fingerprint = h.hexdigest()


# ---- archiving ---------------------------------------------------------------
class HashingReader:
    """Reads exactly `size` bytes of a file for tar, hashing them; pads if the file shrank."""

    def __init__(self, f, size, status: Status | None):
        self.f, self.left, self.status = f, size, status
        self.h = hashlib.sha256()
        self.short = False

    def read(self, n=-1):
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left)
        b = self.f.read(n)
        if len(b) < n:
            self.short = True
            b += b"\0" * (n - len(b))
        self.left -= len(b)
        self.h.update(b)
        if self.status:
            self.status.add_bytes(len(b))
        return b


def is_sqlite(path: str) -> bool:
    if not path.endswith((".sqlite", ".sqlite3", ".db")):
        return False
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def sqlite_snapshot(src: str, dst: Path) -> bool:
    """Consistent copy of a live SQLite database; gives up after 20 s if its app holds a lock."""
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=5)
        d = sqlite3.connect(dst)
        state = {"deadline": time.time() + 20, "remaining": None}

        def progress(status, remaining, total):
            # Give up only when no pages moved for 20 s (locked by its app), not on big databases.
            if state["remaining"] is None or remaining < state["remaining"]:
                state["remaining"] = remaining
                state["deadline"] = time.time() + 20
            elif time.time() > state["deadline"]:
                raise TimeoutError("sqlite busy")

        with d:
            s.backup(d, pages=8192, progress=progress)
        s.close()
        d.close()
        return True
    except (sqlite3.Error, TimeoutError):
        try:
            dst.unlink()
        except OSError:
            pass
        return False


def esc(p: str) -> str:
    """Manifest is TSV: file names may contain tabs/newlines (seen in the wild)."""
    return p.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def unesc(p: str) -> str:
    out, i = [], 0
    while i < len(p):
        c = p[i]
        if c == "\\" and i + 1 < len(p):
            out.append({"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}.get(p[i + 1], p[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def manifest_path_for(archive: Path) -> Path:
    return archive.with_name(archive.name.replace(".tar.zst", ".files.tsv"))


def archive_unit(u: Unit, status: Status) -> dict:
    out = DEST / u.archive_rel
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    man_path = manifest_path_for(out)
    staging = DEST / ".staging"
    staging.mkdir(exist_ok=True)
    t0 = time.time()
    proc = subprocess.Popen([ZSTD, *ZSTD_LEVEL_ARGS, "-T0", "-q", "-c"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    arch_hash = hashlib.sha256()
    written = [0]
    drain_error = []

    def drain():
        try:
            with open(part, "wb") as fo:
                while True:
                    b = proc.stdout.read(4 * 1024 * 1024)
                    if not b:
                        break
                    arch_hash.update(b)
                    fo.write(b)
                    written[0] += len(b)
        except OSError as ex:
            drain_error.append(ex)
            proc.kill()

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    manifest, snapshotted, warnings = [], set(), []
    try:
        with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for e in u.entries:
                if CANCEL_FILE.exists():
                    raise Cancelled()
                ti = tarfile.TarInfo(e.arc)
                ti.mode = e.mode & 0o7777
                ti.mtime = e.mtime_ns / 1e9
                if e.kind == "d":
                    ti.type = tarfile.DIRTYPE
                    tar.addfile(ti)
                    continue
                if e.kind == "l":
                    ti.type = tarfile.SYMTYPE
                    ti.linkname = e.link
                    tar.addfile(ti)
                    manifest.append(f"{esc(e.arc)}\t0\t{e.mtime_ns}\t-\tl")
                    continue
                if e.kind == "g":
                    data = u.generated[e.arc]
                    ti.size = len(data)
                    ti.mtime = time.time()
                    r = HashingReader(io.BytesIO(data), len(data), status)
                    tar.addfile(ti, r)
                    manifest.append(f"{esc(e.arc)}\t{len(data)}\t0\t{r.h.hexdigest()}\tg")
                    continue
                if any(e.path == s + suf for s in snapshotted for suf in ("-wal", "-shm", "-journal")):
                    continue
                src, kind, snap = e.path, "f", None
                if is_sqlite(e.path):
                    snap = staging / f"snap-{os.getpid()}-{os.path.basename(e.path)}"
                    if sqlite_snapshot(e.path, snap):
                        src, kind = str(snap), "s"
                        snapshotted.add(e.path)
                    else:
                        snap = None
                try:
                    with open(src, "rb") as f:
                        size = os.fstat(f.fileno()).st_size
                        ti.size = size
                        r = HashingReader(f, size, status)
                        tar.addfile(ti, r)
                    if r.short:
                        warnings.append(f"{e.arc}: changed while being copied")
                    manifest.append(f"{esc(e.arc)}\t{size}\t{e.mtime_ns}\t{r.h.hexdigest()}\t{kind}")
                except PermissionError as ex:
                    warnings.append(f"{e.arc}: No access ({ex.strerror}) / Нет доступа")
                except FileNotFoundError:
                    warnings.append(f"{e.arc}: disappeared while being copied")
                except BrokenPipeError:
                    raise
                except OSError as ex:
                    if src == e.path:
                        warnings.append(f"{e.arc}: {ex.strerror}")
                    else:
                        raise
                finally:
                    if snap:
                        try:
                            snap.unlink()
                        except OSError:
                            pass
        proc.stdin.close()
        th.join()
        if drain_error:
            raise drain_error[0]
        if proc.wait() != 0:
            raise RuntimeError(f"zstd exited with code {proc.returncode}")
    except BaseException:
        try:
            proc.kill()
        except OSError:
            pass
        th.join(timeout=5)
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    with open(man_path.with_suffix(".tsv.tmp"), "w", encoding="utf-8") as f:
        f.write("# path\tsize\tmtime_ns\tsha256\tkind(f=file,s=sqlite snapshot,l=symlink,g=generated)\n")
        f.write("\n".join(manifest) + "\n")
    os.replace(man_path.with_suffix(".tsv.tmp"), man_path)
    os.replace(part, out)
    return {"bytes_archive": written[0], "archive_sha256": arch_hash.hexdigest(),
            "files": len(manifest), "seconds": round(time.time() - t0, 1), "warnings": warnings}


def verify_archive(archive: Path, manifest_path: Path, status: Status | None, spot_check: int = 25) -> tuple[bool, str, str]:
    """Decompress the archive and hash every member; compare with the manifest.
    Then compare up to `spot_check` unchanged files with the originals on the Mac."""
    want = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        p, size, mt, sha, kind = line.split("\t")
        want[unesc(p)] = (int(size), int(mt), sha, kind)
    proc = subprocess.Popen([ZSTD, "-dc", "--long=27", "-q"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    fh = hashlib.sha256()
    feed_error = []

    def feed():
        try:
            with open(archive, "rb") as f:
                while True:
                    b = f.read(4 * 1024 * 1024)
                    if not b:
                        break
                    fh.update(b)
                    proc.stdin.write(b)
        except BrokenPipeError:
            pass
        except OSError as ex:
            feed_error.append(ex)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    th = threading.Thread(target=feed, daemon=True)
    th.start()
    seen, bad = set(), []
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for m in tar:
                if m.isdir():
                    continue
                if m.issym():
                    seen.add(m.name)
                    continue
                f = tar.extractfile(m)
                h = hashlib.sha256()
                n = 0
                while True:
                    b = f.read(4 * 1024 * 1024)
                    if not b:
                        break
                    h.update(b)
                    n += len(b)
                    if status:
                        status.add_bytes(len(b))
                seen.add(m.name)
                w = want.get(m.name)
                if not w or w[2] != h.hexdigest() or w[0] != n:
                    bad.append(m.name)
    except (tarfile.TarError, OSError, EOFError) as ex:
        th.join(timeout=5)
        proc.kill()
        if not disk_ok():
            raise DiskGone()
        return False, f"archive unreadable: {ex}", ""
    th.join()
    rc = proc.wait()
    if feed_error and not disk_ok():
        raise DiskGone()
    if rc != 0 or feed_error:
        return False, f"zstd -d returned {rc} {feed_error[:1]}", fh.hexdigest()
    missing = [p for p in want if p not in seen]
    if bad or missing:
        return False, f"{len(bad)} mismatched, {len(missing)} missing: {(bad + missing)[:3]}", fh.hexdigest()
    cands = [p for p, (s, mt, sha, k) in want.items() if k == "f"]
    random.shuffle(cands)
    checked = mism = 0
    for p in cands:
        if checked >= spot_check:
            break
        src = HOME / p
        try:
            st = os.stat(src)
        except OSError:
            continue
        s, mt, sha, k = want[p]
        if st.st_size != s or st.st_mtime_ns != mt or s > 512 * 1024**2:
            continue
        h = hashlib.sha256()
        try:
            with open(src, "rb") as f:
                for b in iter(lambda: f.read(4 * 1024 * 1024), b""):
                    h.update(b)
        except OSError:
            continue
        checked += 1
        if h.hexdigest() != sha:
            mism += 1
    if mism:
        return False, f"compared with originals: {mism} of {checked} differ", fh.hexdigest()
    return True, f"{len(seen)} files identical byte for byte; {checked} compared with originals", fh.hexdigest()


# ---- generated units -----------------------------------------------------------
def run_cmd(cmd, timeout=60) -> str:
    # GIT_OPTIONAL_LOCKS=0: `git status` must not rewrite .git/index, or every repo
    # would look changed on the next run and be re-archived.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.stdout + (("\n[stderr]\n" + r.stderr) if r.returncode and r.stderr else "")
    except Exception as e:  # noqa: BLE001
        return f"[failed: {e}]"


def keychain_services() -> list[tuple[str, str, str]]:
    out = run_cmd(["security", "dump-keychain", str(HOME / "Library/Keychains/login.keychain-db")], timeout=60)
    items, cur = [], {}
    for line in out.splitlines():
        if line.startswith("keychain:"):
            if cur:
                items.append(cur)
            cur = {}
        m = re.match(r'\s+"(svce|acct)"<blob>=(?:"(.*)"|0x[0-9A-F]+\s+"(.*)")', line)
        if m:
            cur[m.group(1)] = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        m = re.match(r'class: (0x\w+|"\w+")', line)
        if m:
            cur["class"] = m.group(1).strip('"')
    if cur:
        items.append(cur)
    return [(i.get("class", ""), i.get("svce", ""), i.get("acct", "")) for i in items]


def build_keychain_export(log: Log | None) -> tuple[dict, list[str]]:
    """Values of selected login-keychain items (keychain_export_regex). Never printed anywhere."""
    res, names = {}, []
    for cls, svce, acct in keychain_services():
        names.append(f"{cls}\t{svce}\t{acct}")
        if (KEYCHAIN_EXPORT_RE is None or cls != "genp" or not svce or KEYCHAIN_EXPORT_SKIP_RE.search(svce)
                or not KEYCHAIN_EXPORT_RE.search(svce)):
            continue
        cmd = ["security", "find-generic-password", "-s", svce] + (["-a", acct] if acct else []) + ["-w"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            res[f"{svce} | {acct}"] = r.stdout.rstrip("\n") if r.returncode == 0 else None
        except subprocess.TimeoutExpired:
            res[f"{svce} | {acct}"] = None
    if log and KEYCHAIN_EXPORT_RE is not None:
        log.say(f"Keychain: exported {sum(v is not None for v in res.values())} of {len(res)} selected items (values never printed)")
    return res, sorted(set(names))


def build_inventory(units: list[Unit]) -> dict[str, str]:
    import plistlib
    inv = {}
    apps = []
    for d in (Path("/Applications"), HOME / "Applications"):
        if d.is_dir():
            for a in sorted(d.glob("*.app")):
                ver = ""
                try:
                    with open(a / "Contents/Info.plist", "rb") as f:
                        ver = plistlib.load(f).get("CFBundleShortVersionString", "")
                except Exception:  # noqa: BLE001
                    pass
                mas = (a / "Contents/_MASReceipt").exists()
                apps.append(f"{a.name}\t{ver}\t{'App Store' if mas else ''}\t{d}")
    inv["apps.tsv"] = "name\tversion\tsource\tfolder\n" + "\n".join(apps) + "\n"
    brew = shutil.which("brew") or "/opt/homebrew/bin/brew"
    inv["brew.txt"] = ("# brew leaves (installed on purpose)\n" + run_cmd([brew, "leaves"]) +
                       "\n# brew casks\n" + run_cmd([brew, "list", "--cask"]) +
                       "\n# brew taps\n" + run_cmd([brew, "tap"]) +
                       "\n# Restore: brew install <leaves> ; brew install --cask <casks>\n")
    inv["global-tools.txt"] = ("# pipx\n" + run_cmd(["pipx", "list", "--short"]) + "\n# uv tools\n" +
                               run_cmd(["uv", "tool", "list"]) + "\n# npm -g\n" + run_cmd(["npm", "-g", "ls", "--depth=0"]))
    la = []
    for p in sorted((HOME / "Library/LaunchAgents").glob("*.plist")):
        try:
            with open(p, "rb") as f:
                pl = plistlib.load(f)
            prog = pl.get("ProgramArguments") or [pl.get("Program", "")]
            la.append(f"{pl.get('Label', p.stem)}\t{' '.join(map(str, prog))[:300]}")
        except Exception:  # noqa: BLE001
            la.append(f"{p.stem}\t(unreadable)")
    inv["launchagents.tsv"] = "label\tcommand\n" + "\n".join(la) + "\n"
    inv["crontab.txt"] = run_cmd(["crontab", "-l"])
    rows = ["repo\tremote\tbranch\tuncommitted\tunpushed_on_current\tbranches_without_upstream"]
    for u in units:
        for item in u.items:
            if not (Path(item) / ".git").exists():
                continue
            repo = Path(item)
            g = lambda *a: run_cmd(["git", "-C", str(repo), *a], timeout=30).strip()  # noqa: E731
            remote = (g("remote", "get-url", "origin").splitlines() or ["(none)"])[0] if g("remote") else "(none)"
            br = g("branch", "--show-current")
            unc = len([x for x in g("status", "--porcelain").splitlines() if x.strip()])
            unp = g("rev-list", "--count", "@{u}..HEAD") if "fatal" not in g("rev-parse", "@{u}") else "no upstream"
            nou = g("for-each-ref", "--format=%(upstream:short)", "refs/heads").splitlines().count("")
            rows.append(f"{rel(repo)}\t{remote}\t{br}\t{unc}\t{unp}\t{nou}")
    inv["git-repos.tsv"] = "\n".join(rows) + "\n"
    sk = ["unit\tpath\tbytes"]
    for u in units:
        for arc, size in u.skipped:
            sk.append(f"{u.id}\t{arc}\t{size}")
    inv["skipped-big-media.tsv"] = "\n".join(sk) + "\n"
    inv["system.txt"] = (run_cmd(["sw_vers"]) + run_cmd(["sysctl", "-n", "machdep.cpu.brand_string", "hw.memsize"]) +
                         run_cmd(["df", "-h", "/", str(DISK)]) + run_cmd(["fdesetup", "status"]))
    return inv


# ---- helpers -----------------------------------------------------------------
def acquire_lock() -> bool:
    STATE.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            os.kill(int(LOCK_FILE.read_text().strip()), 0)
            return False
        except (ValueError, OSError):
            pass
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        if LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def find_app() -> Path | None:
    cands = [CFG.get("app_path") or "", str(HOME / "Applications/MacBackup.app"), "/Applications/MacBackup.app"]
    p = ENGINE_DIR
    for _ in range(4):   # engine bundled inside MacBackup.app/Contents/Resources/engine
        if p.suffix == ".app":
            cands.insert(0, str(p))
        p = p.parent
    for c in cands:
        if c and (Path(c) / "Contents/MacOS/MacBackup").exists():
            return Path(c)
    return None


def notify(text: str, title: str = "MacBackup"):
    """Notifications come from MacBackup.app itself (its icon); osascript only as a fallback."""
    app = find_app()
    if app:
        try:
            r = subprocess.run([str(app / "Contents/MacOS/MacBackup"), "--notify", title, text],
                               capture_output=True, timeout=15)
            if r.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    t = text.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{t}" with title "{title}"'], capture_output=True, timeout=10)


def scan_all(units: list[Unit], status: Status | None) -> list[str]:
    secret_hits: list[str] = []
    for u in units:
        if status:
            status.set(current_unit=u.id, message=T("scan_unit", t=u.title), units_total=len(units))
        scan_unit(u, secret_hits)
        if CANCEL_FILE.exists():
            raise Cancelled()
    return secret_hits


def make_generated_units(units: list[Unit], secret_hits: list[str], log: Log | None, with_keychain: bool,
                         write_disk: bool = True):
    extra = [Path(p).expanduser() if Path(p).expanduser().is_absolute() else HOME / p
             for p in SECRET_DEFAULT_PATHS + list(CFG["secret_extra_paths"])]
    paths = sorted({Path(p) for p in secret_hits} | {p for p in extra if p.exists()})
    dirs = [p for p in paths if p.is_dir()]
    paths = [p for p in paths if not any(p != d and d in p.parents for d in dirs)]
    sec = Unit("keys/secret-files", "keys", T("t_secret_files"), paths, media_rule=False, keep_names={"build", "dist", "target"})
    scan_unit(sec, None)
    units.append(sec)
    names = []
    if with_keychain:
        kc, names = build_keychain_export(log)
        gen = {"MacBackup-restore/keychain-item-names.tsv": ("class\tservice\taccount\n" + "\n".join(names) + "\n").encode()}
        if KEYCHAIN_EXPORT_RE is not None:
            gen["MacBackup-restore/keychain-work-items.json"] = json.dumps(kc, ensure_ascii=False, indent=1).encode()
        kunit = Unit("keys/keychain-export", "keys", T("t_kc_export"), [], media_rule=False, generated=gen)
        scan_unit(kunit, None)
        units.insert(0, kunit)
    inv = build_inventory(units)
    if write_disk and disk_ok():
        (DEST / "inventory").mkdir(parents=True, exist_ok=True)
        for k, v in inv.items():
            (DEST / "inventory" / k).write_text(v, encoding="utf-8")
        if names:
            (DEST / "inventory" / "keychain-item-names.tsv").write_text("class\tservice\taccount\n" + "\n".join(names) + "\n", encoding="utf-8")
    iu = Unit("system/inventory", "system", T("t_inventory"), [], media_rule=False,
              generated={f"MacBackup-restore/inventory/{k}": v.encode() for k, v in inv.items()})
    scan_unit(iu, None)
    units.append(iu)


def copy_mirrors(status: Status, log: Log, idx: dict):
    """Plain-folder copies (no compression), verified file by file with SHA-256."""
    for m in CFG["mirror_folders"]:
        src = Path(m["source"]).expanduser()
        dst = DISK / m["dest"]
        uid = m.get("id") or f"media/{slug(src.name)}"
        if not src.is_dir():
            continue
        if not disk_ok():
            raise DiskGone()
        status.set(phase="copy", current_unit=uid, message=T("mirror", t=m.get("title", src.name)))
        t0 = time.time()
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["rsync", "-a", "--exclude", ".DS_Store", f"{src}/", f"{dst}/"], capture_output=True, text=True)
        if r.returncode != 0:
            if not disk_ok():
                raise DiskGone()
            log.event("unit_fail", unit=uid, error=r.stderr[-500:])
            continue
        bad, n, total = [], 0, 0
        for p in src.rglob("*"):
            if p.is_file() and p.name != ".DS_Store":
                q = dst / p.relative_to(src)
                digests = []
                for path in (p, q):
                    h = hashlib.sha256()
                    try:
                        with open(path, "rb") as f:
                            for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
                                h.update(b)
                    except OSError:
                        if not disk_ok():
                            raise DiskGone()
                        h.update(b"missing")
                    digests.append(h.digest())
                n += 1
                total += p.stat().st_size
                if digests[0] != digests[1]:
                    bad.append(str(p.relative_to(src)))
        ok = not bad
        idx["units"][uid] = {
            "layer": "media", "title": m.get("title", src.name), "source": str(src), "archive": m["dest"],
            "files": n, "bytes_raw": total, "bytes_archive": total, "archive_sha256": "", "created_at": now_iso(),
            "verified_at": now_iso(), "verify_ok": ok,
            "verify_note": "every file compared by SHA-256" if ok else f"mismatch: {bad[:5]}",
            "seconds": round(time.time() - t0, 1), "fingerprint": ""}
        log.event("copy_done", unit=uid, bytes_raw=total, seconds=round(time.time() - t0, 1), message="ok" if ok else f"mismatch {len(bad)}")
        log.say(f"Mirror {uid}: {n} files, {human(total)}, {'identical' if ok else 'MISMATCH'}")


def cleanup_leftovers(log: Log):
    """Remove half-written files from an interrupted run (disk unplugged, Mac asleep, killed)."""
    if not DEST.is_dir():
        return
    n = 0
    for p in (DEST / "archives").rglob("*.part") if (DEST / "archives").is_dir() else []:
        p.unlink(missing_ok=True)
        n += 1
    for p in (DEST / "archives").rglob("*.tsv.tmp") if (DEST / "archives").is_dir() else []:
        p.unlink(missing_ok=True)
        n += 1
    if (DEST / ".staging").exists():
        shutil.rmtree(DEST / ".staging", ignore_errors=True)
        n += 1
    if n:
        log.event("info", message=f"cleanup: removed {n} leftovers of an interrupted run")
        log.say(f"Removed {n} leftovers of an interrupted run")


def copy_docs_to_disk():
    """README for people + AGENTS for agents + the engine itself, next to the archives."""
    if not disk_ok() or not DEST.is_dir():
        return
    try:
        personal = DATA / "disk-docs"
        for name in ("README.md", "AGENTS.md"):
            for src in (personal / name, ENGINE_DIR / f"DISK-{name}"):
                if src.exists():
                    shutil.copy2(src, DEST / name)
                    break
        (DEST / "tools").mkdir(exist_ok=True)
        shutil.copy2(Path(__file__), DEST / "tools" / "macbackup.py")
        copy_portable_zstd()
    except OSError:
        pass


def copy_portable_zstd():
    """macOS's built-in tar needs an external `zstd` to read .tar.zst, and a fresh Mac has none.
    Put the self-contained one (built by scripts/build-portable-zstd.sh, bundled in the app)
    on the disk, so restore is:  PATH="<disk>/MacBackup/tools:$PATH" tar -xf X -C ~"""
    src = Path(os.environ.get("MACBACKUP_PORTABLE_ZSTD", ENGINE_DIR / "zstd"))
    dst = DEST / "tools" / "zstd"
    if not src.is_file():
        return
    try:
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            return
        tmp = dst.with_name("zstd.partial")
        shutil.copy2(src, tmp)
        tmp.chmod(0o755)
        os.replace(tmp, dst)
    except OSError:
        pass


# ---- commands ----------------------------------------------------------------
def cmd_plan(args):
    units = build_units()
    t0 = time.time()
    hits = scan_all(units, None)
    make_generated_units(units, hits, None, with_keychain=False, write_disk=False)
    idx = load_index()
    by_layer, rows = {}, []
    for u in units:
        old = idx["units"].get(u.id, {})
        changed = old.get("fingerprint") != u.fingerprint
        bl = by_layer.setdefault(u.layer, [0, 0, 0])
        bl[0] += u.bytes_raw
        bl[1] += len(u.entries)
        bl[2] += sum(s for _, s in u.skipped)
        rows.append((u.bytes_raw, u.id, len(u.entries), sum(s for _, s in u.skipped), len(u.errors), changed))
    rows.sort(reverse=True)
    for b, uid, n, sk, er, ch in rows[: args.top]:
        print(f"{human(b):>10}  {n:>8} files  skipped video {human(sk):>9}  errors {er:>3}  {'changed' if ch else '       '}  {uid}")
    print("\nBy layer:")
    for layer, (b, n, sk) in by_layer.items():
        print(f"  {layer:9} {human(b):>10}  {n} files, big videos skipped {human(sk)}")
    print(f"\nTotal: {human(sum(u.bytes_raw for u in units))} in {len(units)} parts; scan {time.time() - t0:.0f} s")


def cmd_run(args) -> int:
    if not acquire_lock():
        print("A backup is already running", file=sys.stderr)
        return 3
    CANCEL_FILE.unlink(missing_ok=True)

    def on_term(signum, frame):
        raise Cancelled()

    signal.signal(signal.SIGTERM, on_term)
    run_id = dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log = Log(run_id)
    status = Status(run_id, args.mode, "running")
    t0 = time.time()
    done = skipped = failed = 0
    braw = barch = 0
    if not disk_ok():
        status.finish("failed", T("no_disk"))
        log.event("run_fail", mode=args.mode, error="disk not mounted")
        log.close()
        release_lock()
        return 2
    if not args.only and not PENDING_FILE.exists():
        write_json_atomic(PENDING_FILE, {"since": now_iso(), "run_id": run_id})
    status.set(pending_since=pending_since())
    try:
        DEST.mkdir(exist_ok=True)
        log.event("run_start", mode=args.mode, message=f"engine v{ENGINE_VERSION}")
        log.say(f"Backup {run_id} started ({args.mode}), destination {DEST}")
        cleanup_leftovers(log)
        idx = load_index()
        units = build_units()
        if args.only:
            units = [u for u in units if u.id.startswith(args.only)]
        status.set(phase="scan", message=T("scan"))
        hits = scan_all(units, status)
        if not args.only or args.only.startswith(("keys", "system")):
            make_generated_units(units, hits, log, with_keychain=not args.no_keychain)
        todo = []
        for u in units:
            old = idx["units"].get(u.id)
            if old:
                old["title"] = u.title   # follow the current language
            if (old and old.get("fingerprint") == u.fingerprint and old.get("verify_ok")
                    and (DEST / u.archive_rel).exists() and not args.force):
                skipped += 1
                log.event("unit_skip", unit=u.id)
            else:
                todo.append(u)
            for er in u.errors[:20]:
                log.event("warn", unit=u.id, message=("No access / Нет доступа: " if ("ermission" in er or "not permitted" in er) else "") + er)
        total = sum(u.bytes_raw for u in todo)
        status.set(phase="archive", units_total=len(units), units_skipped_unchanged=skipped, bytes_total=total * 2, bytes_done=0)
        log.say(f"Parts: {len(units)}, unchanged {skipped}, to write {len(todo)} ({human(total)})")
        for u in sorted(todo, key=lambda x: x.id):
            if CANCEL_FILE.exists():
                raise Cancelled()
            if not disk_ok():
                raise DiskGone()
            if disk_free() < u.bytes_raw + MIN_FREE_BYTES:
                raise RuntimeError(T("low_space", u=u.id))
            status.set(phase="archive", current_unit=u.id, message=T("compress", t=u.title))
            log.event("unit_start", unit=u.id, bytes_raw=u.bytes_raw)
            try:
                res = archive_unit(u, status)
                status.set(phase="verify", message=T("verify", t=u.title))
                arc = DEST / u.archive_rel
                ok, note, sha_read = verify_archive(arc, manifest_path_for(arc), status)
                if sha_read and sha_read != res["archive_sha256"]:
                    ok, note = False, "archive on disk differs from what was written"
            except (Cancelled, DiskGone):
                raise
            except Exception as ex:  # noqa: BLE001
                if not disk_ok():
                    raise DiskGone()
                failed += 1
                status.set(units_failed=failed)
                log.event("unit_fail", unit=u.id, error=str(ex)[:500])
                log.say(f"ERROR {u.id}: {ex}")
                continue
            for w in res["warnings"][:50]:
                log.event("warn", unit=u.id, message=w)
            idx["units"][u.id] = {
                "layer": u.layer, "title": u.title,
                "source": ", ".join(str(p) for p in u.items[:5]) + (" …" if len(u.items) > 5 else ""),
                "archive": u.archive_rel, "manifest": u.archive_rel.replace(".tar.zst", ".files.tsv"),
                "files": res["files"], "bytes_raw": u.bytes_raw, "bytes_archive": res["bytes_archive"],
                "archive_sha256": res["archive_sha256"], "fingerprint": u.fingerprint,
                "created_at": now_iso(), "verified_at": now_iso(), "verify_ok": ok, "verify_note": note,
                "seconds": res["seconds"], "skipped_large_files": len(u.skipped),
                "skipped_large_bytes": sum(s for _, s in u.skipped), "warnings": len(res["warnings"]),
            }
            save_index(idx)
            if ok:
                done += 1
                braw += u.bytes_raw
                barch += res["bytes_archive"]
                log.event("unit_done", unit=u.id, bytes_raw=u.bytes_raw, bytes_archive=res["bytes_archive"], seconds=res["seconds"], message=note)
                log.say(f"OK {u.id}: {human(u.bytes_raw)} -> {human(res['bytes_archive'])} in {res['seconds']} s; {note}")
            else:
                failed += 1
                log.event("verify_fail", unit=u.id, error=note)
                log.say(f"VERIFY FAILED {u.id}: {note}")
            status.set(units_done=done, units_failed=failed)
        if not args.only or args.only.startswith("media"):
            copy_mirrors(status, log, idx)
        if not args.only:
            present = {u.id for u in units}
            for uid, rec in idx["units"].items():
                rec["source_missing"] = uid not in present and not uid.startswith("media/")
        secs = round(time.time() - t0, 1)
        state = "done" if failed == 0 else "failed"
        if not args.only:
            idx["last_completed_at"] = now_iso()
            if failed == 0:
                idx["last_success_at"] = now_iso()
            PENDING_FILE.unlink(missing_ok=True)
        idx.setdefault("runs", []).append({"run_id": run_id, "mode": args.mode, "state": state, "seconds": secs,
                                           "units_done": done, "units_skipped_unchanged": skipped, "units_failed": failed,
                                           "bytes_raw": braw, "bytes_archive": barch})
        idx["runs"] = idx["runs"][-200:]
        save_index(idx)
        tot_arch = sum(r.get("bytes_archive", 0) for r in idx["units"].values())
        msg = T("done", d=done, s=skipped, f=failed, w=human(barch), m=int(secs // 60), a=human(tot_arch))
        log.event("run_done" if failed == 0 else "run_fail", mode=args.mode, units_done=done, units_skipped_unchanged=skipped,
                  units_failed=failed, bytes_raw=braw, bytes_archive=barch, seconds=secs, message=msg)
        log.say(msg)
        status.set(units_done=done)
        status.finish(state, msg)
        notify(T("done_short", d=done, f=failed, a=human(tot_arch)) if failed == 0 else T("errors", f=failed))
        return 0 if failed == 0 else 1
    except DiskGone:
        # Unplugged mid-run: not an error. Finished parts are already indexed; the next
        # mount resumes (launchd StartOnMount -> auto sees pending.json). No notification.
        log.event("run_interrupted", mode=args.mode, units_done=done, units_skipped_unchanged=skipped, units_failed=failed,
                  bytes_raw=braw, bytes_archive=barch, seconds=round(time.time() - t0, 1), message="backup disk disconnected")
        log.say("Backup disk disconnected - paused, will resume when it is connected again")
        status.finish("interrupted", T("interrupted"))
        return 5
    except Cancelled:
        log.event("run_cancel", mode=args.mode, units_done=done, units_skipped_unchanged=skipped, units_failed=failed,
                  bytes_raw=braw, bytes_archive=barch, seconds=round(time.time() - t0, 1))
        log.say("Stopped on request")
        status.finish("cancelled", T("cancelled"))
        return 4
    except Exception as ex:  # noqa: BLE001
        if not disk_ok():
            log.event("run_interrupted", mode=args.mode, message="backup disk disconnected", seconds=round(time.time() - t0, 1))
            status.finish("interrupted", T("interrupted"))
            return 5
        log.event("run_fail", mode=args.mode, error=str(ex)[:500], units_done=done, units_failed=failed,
                  units_skipped_unchanged=skipped, bytes_raw=braw, bytes_archive=barch, seconds=round(time.time() - t0, 1))
        log.say(f"FAILED: {ex}")
        status.finish("failed", T("failed", e=ex))
        notify(T("failed", e=ex))
        return 1
    finally:
        CANCEL_FILE.unlink(missing_ok=True)
        if disk_ok():
            shutil.rmtree(DEST / ".staging", ignore_errors=True)
        log.close()
        copy_docs_to_disk()
        release_lock()


def cmd_verify(args) -> int:
    if not acquire_lock():
        print("A backup is already running", file=sys.stderr)
        return 3
    run_id = dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S") + "-verify"
    log = Log(run_id)
    status = Status(run_id, "manual", "verifying")
    t0 = time.time()
    if not disk_ok():
        status.finish("failed", T("no_disk"))
        log.close()
        release_lock()
        return 2
    idx = load_index()
    recs = [(k, v) for k, v in idx["units"].items() if not k.startswith("media/") and (not args.only or k.startswith(args.only))]
    status.set(phase="verify", units_total=len(recs), bytes_total=sum(v.get("bytes_raw", 0) for _, v in recs))
    log.event("run_start", mode="verify", message=f"{len(recs)} archives")
    bad = 0
    try:
        for i, (k, v) in enumerate(sorted(recs)):
            if CANCEL_FILE.exists():
                raise Cancelled()
            status.set(current_unit=k, message=T("verify_one", t=v.get("title", k)), units_done=i)
            arc = DEST / v["archive"]
            if not disk_ok():
                raise DiskGone()
            if not arc.exists():
                ok, note, sha = False, "archive missing on the disk", ""
            else:
                ok, note, sha = verify_archive(arc, DEST / v.get("manifest", v["archive"].replace(".tar.zst", ".files.tsv")), status)
                if ok and sha != v.get("archive_sha256"):
                    ok, note = False, "archive checksum changed since it was written"
            v.update(verified_at=now_iso(), verify_ok=ok, verify_note=note)
            log.event("verify_ok" if ok else "verify_fail", unit=k, message=note)
            if not ok:
                bad += 1
                log.say(f"FAILED {k}: {note}")
        save_index(idx)
        msg = T("verified", n=len(recs), b=bad)
        log.event("run_done" if not bad else "run_fail", mode="verify", units_done=len(recs), units_failed=bad,
                  units_skipped_unchanged=0, bytes_raw=0, bytes_archive=0, seconds=round(time.time() - t0, 1), message=msg)
        log.say(msg)
        status.finish("done" if not bad else "failed", msg)
        return 0 if not bad else 1
    except DiskGone:
        status.finish("interrupted", T("interrupted"))
        log.event("run_interrupted", mode="verify", seconds=round(time.time() - t0, 1))
        return 5
    except Cancelled:
        try:
            save_index(idx)
        except DiskGone:
            pass
        status.finish("cancelled", T("cancelled"))
        log.event("run_cancel", mode="verify", seconds=round(time.time() - t0, 1))
        return 4
    finally:
        CANCEL_FILE.unlink(missing_ok=True)
        log.close()
        release_lock()


def cmd_auto(args) -> int:
    """Run by the scheduler hourly and on every disk mount. Quiet unless something is really overdue."""
    now = dt.datetime.now().astimezone()
    idx = load_index()
    last = idx.get("last_completed_at") or idx.get("last_success_at")
    pend = pending_since()
    due_at = dt.datetime.fromisoformat(last) + dt.timedelta(days=CFG["due_days"]) if last else None
    if due_at is None:
        first = STATE / "first_seen"
        if not first.exists():
            STATE.mkdir(parents=True, exist_ok=True)
            first.write_text(now_iso())
        due_at = dt.datetime.fromisoformat(first.read_text().strip())
    if not pend and now < due_at:
        return 0
    if disk_ok():
        args.mode, args.only, args.force, args.no_keychain = "auto", None, False, False
        return cmd_run(args)
    # Disk not connected: wait reminder_grace_days after the backup became due (or after an
    # unfinished run started), then remind at most once every reminder_every_hours.
    since = dt.datetime.fromisoformat(pend) if pend else due_at
    if now - since < dt.timedelta(days=CFG["reminder_grace_days"]):
        return 0
    stamp = STATE / "last_reminder"
    try:
        lr = float(stamp.read_text())
    except (OSError, ValueError):
        lr = 0
    if time.time() - lr < CFG["reminder_every_hours"] * 3600:
        return 0
    if pend:
        text = T("remind_pending", d=T("days_ago", n=(now - since).days))
    else:
        text = T("remind", d=T("days_ago", n=(now - dt.datetime.fromisoformat(last)).days) if last else T("never"))
    notify(text)
    STATE.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(time.time()))
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now_iso(), "run_id": "-", "event": "info", "message": f"reminder sent: {text}"}, ensure_ascii=False) + "\n")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan")
    p.add_argument("--top", type=int, default=60)
    p = sp.add_parser("run")
    p.add_argument("--mode", default="manual", choices=["manual", "auto"])
    p.add_argument("--only", help="only parts whose id starts with this (e.g. projects/my-app)")
    p.add_argument("--force", action="store_true", help="rebuild even if unchanged")
    p.add_argument("--no-keychain", action="store_true", help="skip the keychain export")
    p = sp.add_parser("verify")
    p.add_argument("--all", action="store_true")
    p.add_argument("--only")
    sp.add_parser("auto")
    sp.add_parser("cancel")
    sp.add_parser("status")
    a = ap.parse_args()
    LOGS.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    if a.cmd == "plan":
        cmd_plan(a)
        return 0
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "verify":
        return cmd_verify(a)
    if a.cmd == "auto":
        return cmd_auto(a)
    if a.cmd == "cancel":
        CANCEL_FILE.write_text(str(time.time()))
        print("Asked the running backup to stop")
        return 0
    if a.cmd == "status":
        print(STATUS_FILE.read_text(encoding="utf-8") if STATUS_FILE.exists() else "{}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
