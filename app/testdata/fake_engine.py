#!/usr/bin/env python3
"""Fake MacBackup engine for testing the app. Same commands as the real one, but it only
pretends: ~30 seconds of progress written to testdata/data/state/status.json, no files copied.
Uses MACBACKUP_DATA (set by the app)."""
import json, os, sys, time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("MACBACKUP_DATA") or os.path.join(ROOT, "data")
STATUS = os.path.join(HOME, "state", "status.json")
CANCEL = os.path.join(HOME, "state", "cancel.request")
RUNS = os.path.join(HOME, "logs", "runs.jsonl")
GB = 1_000_000_000

def now(): return datetime.now().astimezone().isoformat(timespec="seconds")

def read_status():
    try:
        with open(STATUS) as f: return json.load(f)
    except Exception: return {}

def write_status(st):
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATUS)

def event(e):
    with open(RUNS, "a") as f: f.write(json.dumps(e, ensure_ascii=False) + "\n")

def alive(pid):
    try: os.kill(pid, 0); return True
    except PermissionError: return True
    except Exception: return False

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    os.makedirs(os.path.dirname(RUNS), exist_ok=True)
    st = read_status()
    if cmd == "status":
        print(json.dumps(st, ensure_ascii=False, indent=1)); return 0
    if cmd == "cancel":
        open(CANCEL, "w").close(); print("Asked the engine to stop"); return 0
    if cmd == "auto":  # real engine decides itself whether a run is due; fake one just runs
        sys.argv += ["--mode", "auto"]; cmd = "run"
    if cmd not in ("run", "verify"):
        print("unknown command", file=sys.stderr); return 2
    if st.get("state") in ("running", "verifying") and st.get("pid") and alive(int(st["pid"])) and st.get("pid") != 1:
        print("A backup is already running (pid %s)" % st["pid"]); return 3
    if os.path.exists(CANCEL): os.remove(CANCEL)
    verifying = cmd == "verify"
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "manual"
    run_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log = open(os.path.join(HOME, "logs", f"run-{run_id}.log"), "w")
    start = time.time()
    units = int(os.environ.get("FAKE_UNITS", "30"))
    total = units * GB
    st.update({"state": "verifying" if verifying else "running", "run_id": run_id, "mode": mode,
               "started_at": now(), "finished_at": None, "phase": "verify" if verifying else "scan",
               "units_total": units, "units_done": 0, "units_skipped_unchanged": 0, "units_failed": 0,
               "bytes_total": total, "bytes_done": 0, "speed_bps": 0.0, "eta_seconds": None, "pid": os.getpid()})
    event({"ts": now(), "run_id": run_id, "event": "run_start", "mode": mode})
    for i in range(units):
        if os.path.exists(CANCEL):
            os.remove(CANCEL)
            st.update({"state": "cancelled", "finished_at": now(), "updated_at": now(), "message": "Stopped", "pid": None})
            write_status(st)
            event({"ts": now(), "run_id": run_id, "event": "run_cancel", "mode": mode, "units_done": i,
                   "seconds": round(time.time() - start, 1)})
            log.write("Stopped on request\n"); return 0
        unit = f"projects/test-project-{i + 1}"
        done = total * i / units
        el = max(time.time() - start, 0.1)
        st.update({"phase": "verify" if verifying else ("scan" if i < 2 else "archive"), "current_unit": unit,
                   "units_done": i, "units_skipped_unchanged": i // 3, "bytes_done": done,
                   "speed_bps": done / el, "eta_seconds": int((units - i) * 1.0), "updated_at": now(),
                   "message": ("Verifying " if verifying else "Compressing ") + unit})
        write_status(st)
        log.write(f"{datetime.now():%H:%M:%S} {unit}\n"); log.flush()
        time.sleep(1)
    secs = round(time.time() - start, 1)
    st.update({"state": "done", "finished_at": now(), "updated_at": now(), "units_done": units, "bytes_done": total,
               "phase": "finish", "message": "Done", "pid": None, "current_unit": None, "eta_seconds": 0})
    if not verifying: st["last_success_at"] = now()
    write_status(st)
    if not verifying:
        event({"ts": now(), "run_id": run_id, "event": "run_done", "mode": mode, "units_done": units - units // 3,
               "units_skipped_unchanged": units // 3, "units_failed": 0, "bytes_raw": total,
               "bytes_archive": total // 3, "seconds": secs})
    log.write("Done\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
