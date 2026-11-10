"""Parallel AI worker pool for the Millennium Lab.

The Cairn autopilot runs one job at a time for every project. Lab jobs therefore no longer go there:
this pool runs up to `pool.workers` lab jobs at the same time (lab_departments.json), each as its own
OpenCode or Copilot CLI process, so Cairn stays free for other projects.

Every tick (scheduled task "Millennium lab pool", every 10 minutes):
  1. finished workers -> their result file lab_results/<job id>.json is merged into research_queue.json
     (workers never edit the queue themselves, so parallel jobs cannot overwrite each other);
  2. a worker that died without a result is retried once with the other engine, then marked failed;
  3. a worker running longer than `timeout_minutes` is stopped (same retry rule);
  4. free slots are filled with open lab jobs, prize departments first.
State: logs/pool_state.json (git-ignored). Log: logs/pool.log, one log file per worker run.

Usage: python lab_pool.py            one tick
       python lab_pool.py --status   show running workers
"""
import ctypes
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUEUE = ROOT / "research_queue.json"
PROTOCOL = ROOT / "AI_JOB_PROTOCOL.md"
RESULTS = ROOT / "lab_results"
LOG_DIR = HERE / "logs"
STATE = LOG_DIR / "pool_state.json"
DEPARTMENTS = HERE / "lab_departments.json"
COPILOT = Path(os.path.expandvars(r"%LOCALAPPDATA%\GitHubCopilotCLI\copilot.exe"))
OPEN = ("needs_ai", "running")
PRIORITY = ["squares", "erdos", "panel", "review", "prize", "writer", "tools", "bsd", "rh", "scout"]
DETACHED = 0x00000008 | 0x00000200 | 0x08000000 | 0x00004000  # DETACHED | NEW_GROUP | NO_WINDOW | BELOW_NORMAL priority

sys.path.insert(0, str(HERE))
import lab  # noqa: E402


def log(text):
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "pool.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {text}\n")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    temp = Path(path).with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def settings():
    pool = (read_json(DEPARTMENTS, {}) or {}).get("pool", {})
    return {
        "workers": int(pool.get("workers", 3)),
        "engines": pool.get("engines", ["opencode", "copilot"]),
        "timeout_minutes": int(pool.get("timeout_minutes", 100)),
        "max_attempts": int(pool.get("max_attempts", 2)),
    }


# ------------------------------------------------------------------ processes (Windows)
def alive(pid):
    """True while the process exists (os.kill(pid, 0) would terminate it on Windows)."""
    if not pid:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def kill_tree(pid):
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=0x08000000)


def environment():
    env = dict(os.environ)
    # a localhost proxy that is not running would make every request fail (same rule as Cairn)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        match = re.match(r"https?://(localhost|127\.0\.0\.1):(\d+)", env.get(key, ""))
        if match:
            import socket
            try:
                socket.create_connection(("127.0.0.1", int(match.group(2))), timeout=1).close()
            except OSError:
                env.pop(key, None)
    return env


COPILOT_FLAG = LOG_DIR / "copilot_needs_login.txt"


def _copilot_needs_login(log_path):
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return False
    return "/login" in text and ("GH_TOKEN" in text or "authenticate" in text)


def copilot_usable():
    """Copilot CLI exists and has not reported 'please log in' in the last 24 h (then it is tried again)."""
    if not COPILOT.exists():
        return False
    if COPILOT_FLAG.exists():
        age_hours = (dt.datetime.now().timestamp() - COPILOT_FLAG.stat().st_mtime) / 3600
        return age_hours > 24
    return True


# ------------------------------------------------------------------ jobs
def pool_job(job):
    return bool(job.get("dept")) or job.get("id", "").startswith(("daily_", "tool_"))


def prompt_for(job):
    result_file = RESULTS / f"{job['id']}.json"
    return (
        f"You are running unattended for Juwen; he will not answer questions. Do research job `{job['id']}` "
        f"({job['title']}). Work in {ROOT}. Read {PROTOCOL} first and follow it, especially the sections "
        f"'Lab jobs' and 'Parallel pool'. The job's instructions: {job.get('why', '')}\n\n"
        "Other AI workers run at the same time: only touch files that belong to this job, commit only your own "
        "files (git add <your paths>; if .git/index.lock exists, wait 15 s and retry), and NEVER edit "
        f"research_queue.json. When finished write {result_file} as JSON: "
        '{"status": "done" | "needs_review" | "failed", "result": "1-3 sentences with the numbers", '
        '"finished": "<ISO time>", "files": ["paths you changed"]} plus any review/panel fields the protocol '
        "asks for. Keep any single computation under 20 minutes and single-threaded (8 workers share 8 cores). Never push, never post, never email. End with STATUS: DONE."
    )


def command(engine, job):
    prompt = prompt_for(job)
    if engine == "copilot":
        return [str(COPILOT), "-p", prompt, "--allow-all", "--no-ask-user", "--add-dir", str(ROOT)]
    exe = shutil.which("opencode") or "opencode"
    return [exe, "run", "--standalone", "--auto", "--format", "json", "--title", f"Lab: {job['id']}", prompt]


def start(job, engine, state):
    LOG_DIR.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now()
    log_path = LOG_DIR / f"pool_{job['id']}_{engine}_{stamp:%Y%m%d_%H%M}.log"
    env = environment()
    for key in ("OMP_NUM_THREADS", "NUMBA_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        env[key] = "1"  # 8 workers on 8 cores: each worker's own computations stay single-threaded
    if engine == "copilot":
        env.setdefault("HTTPS_PROXY", "http://localhost:3128")
        env.setdefault("HTTP_PROXY", "http://localhost:3128")
    with open(log_path, "w", encoding="utf-8") as handle:
        process = subprocess.Popen(command(engine, job), cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, env=env, creationflags=DETACHED)
    entry = state.setdefault(job["id"], {"attempts": []})
    entry.update({"pid": process.pid, "engine": engine, "started": stamp.isoformat(timespec="seconds"),
                  "log": str(log_path)})
    log(f"start {job['id']} on {engine} (pid {process.pid})")


def merge_results(queue):
    """Copy every lab_results/<id>.json into its queue job. Returns the merged ids."""
    merged = []
    jobs = {job["id"]: job for job in queue.get("jobs", [])}
    for path in sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []:
        job = jobs.get(path.stem)
        data = read_json(path, None)
        if not job or not isinstance(data, dict) or job.get("result_merged") == path.stat().st_mtime:
            continue
        status = data.get("status", "done")
        job["status"] = status if status in ("done", "needs_review", "failed") else "done"
        job["result"] = str(data.get("result", ""))[:1500]
        job["finished"] = data.get("finished") or dt.datetime.now().isoformat(timespec="seconds")
        for key in ("files", "review", "panel", "reviewed", "panel_verdict"):
            if key in data:
                job[key] = data[key]
        for reviewed_id in data.get("reviewed_jobs", []):  # referee / panel mark the jobs they checked
            if reviewed_id in jobs:
                jobs[reviewed_id]["reviewed" if job.get("dept") == "review" else "panel_reviewed"] = job["id"]
        job["result_merged"] = path.stat().st_mtime
        merged.append(job["id"])
    return merged


def save_queue_merged(queue, original_json):
    """Write only the jobs this tick changed on top of the CURRENT file, so a slow tick never overwrites
    results or jobs that other scripts added meanwhile."""
    before = {job["id"]: json.dumps(job, sort_keys=True) for job in json.loads(original_json)}
    mine = {job["id"]: job for job in queue.get("jobs", []) if before.get(job["id"]) != json.dumps(job, sort_keys=True)}
    fresh = read_json(QUEUE, None) or queue
    fresh_jobs = fresh.setdefault("jobs", [])
    index = {job["id"]: position for position, job in enumerate(fresh_jobs)}
    for job_id, job in mine.items():
        if job_id in index:
            fresh_jobs[index[job_id]] = job
        else:
            fresh_jobs.append(job)
    write_json(QUEUE, fresh)


def tick():
    config = settings()
    queue = read_json(QUEUE, None)
    if not queue:
        return
    state = read_json(STATE, {})
    original = json.dumps(queue.get("jobs", []), sort_keys=True)
    jobs = {job["id"]: job for job in queue.get("jobs", [])}
    changed = False

    # 1-3: finished, dead and slow workers
    for job_id, entry in list(state.items()):
        pid = entry.get("pid")
        if not pid:
            continue
        job = jobs.get(job_id)
        started = dt.datetime.fromisoformat(entry["started"])
        minutes = (dt.datetime.now() - started).total_seconds() / 60
        if alive(pid):
            if minutes <= config["timeout_minutes"]:
                continue
            kill_tree(pid)
            outcome = f"timeout after {minutes:.0f} min"
        else:
            outcome = "exited"
            if entry["engine"] == "copilot" and _copilot_needs_login(entry.get("log")):
                outcome = "copilot not logged in"
                COPILOT_FLAG.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        entry["attempts"].append({"engine": entry["engine"], "started": entry["started"], "outcome": outcome,
                                  "log": entry.get("log")})
        entry["pid"] = None
        log(f"{job_id}: {entry['engine']} {outcome}")
    if merge_results(queue):
        changed = True
    for job_id, entry in state.items():
        job = jobs.get(job_id)
        real_attempts = [a for a in entry.get("attempts", []) if a["outcome"] != "copilot not logged in"]
        if (job and job.get("status") in OPEN and not entry.get("pid")
                and len(real_attempts) >= config["max_attempts"]):
            job["status"] = "failed"
            job["error"] = "pool: no result after " + ", ".join(a["outcome"] for a in entry["attempts"])
            changed = True
            log(f"{job_id}: marked failed")

    # 4: fill free slots
    running = sum(1 for entry in state.values() if entry.get("pid") and alive(entry["pid"]))
    today = dt.date.today().isoformat()
    candidates = [job for job in queue.get("jobs", []) if pool_job(job) and job.get("status") == "needs_ai"
                  and (job.get("not_before") or today) <= today and not state.get(job["id"], {}).get("pid")
                  and len([a for a in state.get(job["id"], {}).get("attempts", [])
                           if a["outcome"] != "copilot not logged in"]) < config["max_attempts"]]
    candidates.sort(key=lambda job: PRIORITY.index(lab.job_dept(job)) if lab.job_dept(job) in PRIORITY else 99)
    slot = 0
    for job in candidates:
        if running >= config["workers"]:
            break
        history = state.get(job["id"], {}).get("attempts", [])
        engines = config["engines"]
        if history:  # a retry always switches engine
            engine = next((e for e in engines if e != history[-1]["engine"]), engines[0])
        else:
            engine = engines[slot % len(engines)]
        if engine == "copilot" and not copilot_usable():
            engine = "opencode"
        start(job, engine, state)
        job["pool"] = {"engine": engine, "started": state[job["id"]]["started"]}
        running += 1
        slot += 1
        changed = True
    write_json(STATE, state)
    if changed:
        save_queue_merged(queue, original)


def status():
    state = read_json(STATE, {})
    for job_id, entry in state.items():
        if entry.get("pid") and alive(entry["pid"]):
            print(f"running  {job_id:32} {entry['engine']:8} since {entry['started']}")
    print(f"{sum(1 for e in state.values() if e.get('pid') and alive(e['pid']))} running, "
          f"{settings()['workers']} slots")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        LOG_DIR.mkdir(exist_ok=True)
        import faulthandler
        hang_log = open(LOG_DIR / "pool_hang.txt", "w", encoding="utf-8")
        faulthandler.dump_traceback_later(240, exit=True, file=hang_log)  # a tick takes ~1 s; never hang
        lock = LOG_DIR / "lab_pool.lock"
        if lock.exists():
            try:
                holder = int(lock.read_text().strip() or 0)
            except ValueError:
                holder = 0
            if alive(holder) and (dt.datetime.now().timestamp() - lock.stat().st_mtime) < 300:
                sys.exit(0)
            if alive(holder):
                kill_tree(holder)
                log(f"killed stuck pool tick (pid {holder})")
        lock.write_text(str(os.getpid()))
        try:
            lab.ensure_jobs()
            tick()
            lab.write_state()
        except Exception as error:
            log(f"pool error: {error}")
            raise
        finally:
            lock.unlink(missing_ok=True)
