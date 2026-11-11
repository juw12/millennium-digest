"""Parallel AI worker pool for the Millennium Lab.

The Cairn autopilot runs one job at a time for every project. Lab jobs therefore no longer go there:
this pool runs up to `pool.workers` lab jobs at the same time (lab_departments.json), each as its own
OpenCode or Copilot CLI process, so Cairn stays free for other projects.

Every tick (scheduled task "Millennium lab pool", every 10 minutes):
  1. finished workers -> their result file lab_results/<job id>.json is merged into research_queue.json
     (workers never edit the queue themselves, so parallel jobs cannot overwrite each other) and the lab learns
     from it: the job's idea card is settled, follow-up ideas are banked, one notebook line is written
     (lab_ideas.py);
  2. a worker that died without a result is retried once with the other engine, then marked failed
     (a login or connection failure is not the job's fault and is not counted). The retry is told what
     happened; "stopped waiting" is a worker that ended its turn while its own computation still ran;
  3. a worker running longer than `timeout_minutes` is stopped (same retry rule);
  4. lab.ensure_jobs() queues the next job of every department with a free slot - in the same tick, so a freed
     slot is not idle until the next one;
  5. free slots are filled: jobs that waited longer than `max_wait_hours` first, then by department priority.
     After a worker lost its model, the model is asked one small question by a process of its own and no
     OpenCode job is started until it answers (logs/model_unreachable.txt; the answer may take 25 minutes).
State: logs/pool_state.json (git-ignored). Log: logs/pool.log, one log file per worker run.

Usage: python lab_pool.py                 one tick
       python lab_pool.py --status        show running workers
       python lab_pool.py --model-check   ask the model one question (a tick starts this by itself)
"""
import time

SCRIPT_START = time.time()

import ctypes  # noqa: E402

try:  # the scheduled task starts this tick at below-normal priority; on a busy PC it then needs minutes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.c_void_p(-1), 0x00000020)  # NORMAL_PRIORITY_CLASS
except Exception:
    pass

import datetime as dt  # noqa: E402
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
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
# The Referee comes right after the Director: it holds one slot, and a claim nobody re-ran is what every later
# idea would be built on (2026-11-10: 83 finished results waited for it, 38 of them claiming progress).
PRIORITY = ["director", "review", "squares", "erdos", "panel", "prize", "writer", "tools", "bsd", "rh", "scout"]
INFRASTRUCTURE = ("copilot not logged in", "copilot out of quota", "no connection")  # not the job's fault
DETACHED = 0x00000008 | 0x00000200 | 0x08000000 | 0x00000020  # DETACHED | NEW_GROUP | NO_WINDOW | NORMAL priority
# (below-normal starved the workers on a busy PC: 1.4 s of CPU in 33 minutes, 2026-11-10)
PROMPT_ROOM = 21500  # characters a prompt may reach before lab memory is trimmed. The whole prompt travels on the
# command line: Windows allows 32,767 characters and a 22,600-character one reached the model whole (2026-11-11).
# At 12,150 a card worker saw 0-3 of the 8-10 notebook lines chosen for it.

sys.path.insert(0, str(HERE))
import lab  # noqa: E402
import lab_ideas  # noqa: E402


def log(text):
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "pool.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {text}\n")


def describe(error):
    """Type, message and place of an exception: "pool error: " plus an empty message said nothing (2026-11-11)."""
    frames = traceback.extract_tb(error.__traceback__)
    text = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
    if frames:
        text += (f" ({Path(frames[-1].filename).name} line {frames[-1].lineno}, "
                 + " > ".join(frame.name for frame in frames[-4:]) + ")")
    return text


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    temp = Path(path).with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lab_ideas.swap_in(temp, path)


def settings():
    pool = (read_json(DEPARTMENTS, {}) or {}).get("pool", {})
    return {
        "workers": int(pool.get("workers", 3)),
        "engines": pool.get("engines", ["opencode", "copilot"]),
        "timeout_minutes": int(pool.get("timeout_minutes", 100)),
        "max_attempts": int(pool.get("max_attempts", 2)),
        "max_wait_hours": float(pool.get("max_wait_hours", 3)),
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


def opencode_environment():
    """environment() for an OpenCode run, with OpenCode's file snapshots switched off. Left on, OpenCode takes a git
    snapshot of the whole project tree around every step. Measured 2026-11-11 in OpenCode's own log: up to two thirds
    of the workers' time went into those git calls, and one one-word answer took 679 s with snapshots and 449 s
    without (one run each). Only the undo of file changes is lost, and a headless worker never uses it. An inline
    config that is already set but cannot be read here is left as it is."""
    env = environment()
    try:
        config = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        config["snapshot"] = False
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    except (ValueError, TypeError):
        pass
    return env


COPILOT_FLAG = LOG_DIR / "copilot_needs_login.txt"
COPILOT_QUOTA_FLAG = LOG_DIR / "copilot_out_of_quota.txt"
COPILOT_QUOTA_HOURS = 6  # how long Copilot is left alone after it reports a used-up plan quota
MODEL_FLAG = LOG_DIR / "model_unreachable.txt"  # written when an OpenCode worker could not reach its model
MODEL_QUESTION = "Reply with the single word OK. Do not use any tools."
MODEL_CHECK_MINUTES = 25  # how long that answer may take: a busy PC needed 11 minutes for the one word
MODEL_CHECK_MARK = LOG_DIR / "model_check.pid"  # the check that is under way (its process id)


def _copilot_needs_login(log_path):
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return False
    return "/login" in text and ("GH_TOKEN" in text or "authenticate" in text)


def _copilot_out_of_quota(log_path):
    """Copilot stopped because the plan's quota is used up ("You have exceeded your monthly quota"). The message
    is the last thing in the log, so only the tail is read: a worker that quotes it earlier does not count."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")[-2000:].lower()
    except (OSError, TypeError):
        return False
    return "exceeded your" in text and "quota" in text


def _no_connection(log_path):
    """The engine could not reach its model (proxy or network down): a tiny log that holds only that error, or a
    longer run whose last words are the engine's own report that it lost the model."""
    try:
        path = Path(log_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        small = path.stat().st_size <= 2500
    except (OSError, TypeError):
        return False
    if small:
        return any(sign in text for sign in ("ECONNREFUSED", "Unable to connect", "UnsupportedContentType",
                                             "UnexpectedStatus"))
    tail = text[-1500:]
    last = tail.strip().rsplit("\n", 1)[-1]
    return "Failed to get response from the AI model" in tail or (
        last.startswith('{"type":"error"') and any(sign in last for sign in ("ECONNRESET", "ECONNREFUSED")))


WAITING = re.compile(r"\bbackground\b|STATUS:\s*IN PROGRESS|still (running|going)|when it (completes|finishes)|"
                     r"wait for it to (complete|finish)|awaiting (its )?completion", re.I)


def _stopped_waiting(log_path):
    """The worker ended its turn while its own computation was still running - its last words say so. A headless
    run ends with the turn, so whatever that computation found never reached a result file."""
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").strip().split("\n")
    except (OSError, TypeError):
        return False
    for line in reversed(lines[-6:]):
        if line.startswith('{"type":"text"'):
            return bool(WAITING.search(line[-1500:]))
        if line.startswith('{"type":"tool_use"'):
            return False
    return False


def exhausted(entry, max_attempts):
    """True when a job has used up its attempts. Infrastructure failures are not counted, but six runs without
    a connection end the retries (an hour of ticks)."""
    attempts = entry.get("attempts", [])
    real = [attempt for attempt in attempts if attempt.get("outcome") not in INFRASTRUCTURE]
    offline = [attempt for attempt in attempts if attempt.get("outcome") == "no connection"]
    return len(real) >= max_attempts or len(offline) >= 6


def copilot_usable():
    """Copilot CLI exists and has not reported 'please log in' in the last hour (then it is tried again,
    so a fresh /login is picked up automatically; a failed check costs ~1 min and is not counted as an attempt)."""
    if not COPILOT.exists():
        return False
    for flag, hours in ((COPILOT_FLAG, 1), (COPILOT_QUOTA_FLAG, COPILOT_QUOTA_HOURS)):
        if flag.exists() and (dt.datetime.now().timestamp() - flag.stat().st_mtime) / 3600 <= hours:
            return False  # tried again afterwards; delete the flag file in logs/ to try at once
    return True


def model_reachable():
    """False while OpenCode's model is known to be down. Once a worker has ended with "no connection", no OpenCode
    job is started until the model has answered one small question, so an outage costs no job an attempt. The
    question is asked by a process of its own (model_check): a tick has to end within minutes, and on a busy PC
    OpenCode needed 11 minutes to bring back one word. Asked inside the tick with a 60 s wait, the check could
    never pass and the pool stood still for three hours (2026-11-11). Without the flag file nothing is asked
    (delete it in logs/ to skip the check)."""
    if not MODEL_FLAG.exists():
        return True
    try:
        holder = int(MODEL_CHECK_MARK.read_text().strip() or 0)
        age = dt.datetime.now().timestamp() - MODEL_CHECK_MARK.stat().st_mtime
    except (OSError, ValueError):
        holder, age = 0, 0
    if not alive(holder) or age > (MODEL_CHECK_MINUTES + 5) * 60:  # no check under way (or one that hangs)
        try:
            check = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--model-check"], cwd=HERE,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=DETACHED)
            MODEL_CHECK_MARK.write_text(str(check.pid), encoding="utf-8")
        except OSError as error:
            log(f"model check could not be started: {error}")
    log("model unreachable: no OpenCode job started")
    return False


def model_check():
    """Ask the model one small question and wait up to MODEL_CHECK_MINUTES for the answer. An answer without an
    error deletes the flag file, and the next tick starts OpenCode jobs again."""
    answer_path = LOG_DIR / "model_check.log"
    exe = shutil.which("opencode") or "opencode"
    try:
        with open(answer_path, "w", encoding="utf-8") as handle:
            check = subprocess.Popen([exe, "run", "--standalone", "--auto", "--format", "json", "--title",
                                      "Lab: connection check", MODEL_QUESTION], cwd=ROOT, stdout=handle,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=opencode_environment(),
                                     creationflags=0x08000000)
            try:
                check.wait(timeout=MODEL_CHECK_MINUTES * 60)
            except subprocess.TimeoutExpired:  # a model that hangs is as unreachable as one that refuses
                kill_tree(check.pid)
        answer = answer_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        log(f"model check: OpenCode could not be started: {error}")
        return False
    finally:
        MODEL_CHECK_MARK.unlink(missing_ok=True)
    if '{"type":"text"' in answer and '{"type":"error"' not in answer:
        MODEL_FLAG.unlink(missing_ok=True)
        log("model reachable again")
        return True
    log("model check: " + ("the model answered with an error" if '{"type":"error"' in answer
                           else f"no answer within {MODEL_CHECK_MINUTES} min"))
    return False


# ------------------------------------------------------------------ jobs
def pool_job(job):
    return bool(job.get("dept")) or job.get("id", "").startswith(("daily_", "tool_"))


METHOD = (
    "METHOD - work like a careful researcher, in this order. Write steps 1, 3, 4 and 6 into your notes (the "
    "dossier or notes file this job names; otherwise lab_results/{job_id}.md).\n"
    "1. FRONTIER. State the best published result on exactly this question, with its source, next to the lab's "
    "own best number. If you cannot find it, finding it is your first deliverable; failing that, write 'frontier "
    "unknown' and your search terms, never a trivial bound.\n"
    "2. MEMORY. Build on the LAB MEMORY above. A dead end stays dead unless you bring a new argument.\n"
    "3. OPTIONS. Write down three genuinely different approaches with expected gain, odds of success and cost in "
    "minutes. At least one must be borrowed from another field (SAT/ILP solvers, numerical optimisation, "
    "statistics, coding theory, physics, computational geometry). Choose by gain x odds / cost. {card_rule}\n"
    "4. KILL TEST FIRST. Before the full run, spend at most 5 minutes on the cheapest test that could show the "
    "approach cannot work. If it fails, switch to the next option instead of polishing.\n"
    "5. EXECUTE, then check the result with an independent verifier (a second method or implementation, not the "
    "same code run twice). Reproducing a published value only proves your code: it is the gate, not the result.\n"
    "6. CLASSIFY honestly: internal (a tool, dataset, audit or fix, or a gain on the lab's own number only) | "
    "reproduction (a known value re-derived, or a corollary of published theorems) | extension (past the published "
    "frontier: name the value you passed and its source) | new (in no literature you could find: set status "
    "needs_review) | negative (the approach does not work, with the evidence).\n"
    "7. NEXT IDEAS. Propose at most 2 follow-up ideas, only ones worth running ahead of those in the bank; proposing "
    "none is fine, as every idea costs the lab a review. Each needs value (0-10), odds (0-1), cost (jobs), a kill test "
    "and a verifier (as in steps 4 and 5); without both it is marked down. Value is what the result is worth if it "
    "works: 9-10 beyond the published frontier, something a specialist would cite; 7-8 extends the frontier in range, "
    "precision or method; 4-6 a tool, dataset or bound that several later jobs need; 1-3 a reproduction, or a gain on "
    "one small case. Odds above 0.3 need evidence from this job. Propose nothing the LAB MEMORY lists as done, dead, "
    "blocked or already in the bank, and nothing that needs Juwen (a login, a download): name such a blocker in your "
    "result instead."
)
CARD_RULE = (
    "Option 1 is the IDEA CARD above: work it first and let its kill test decide. If it is killed, do your best "
    "alternative and report idea_verdict \"failed\" for the card. If the card could not be tested at all - an "
    "input is missing, a site needs a login, another job is still producing what it needs - do your best "
    "alternative and report idea_verdict \"blocked\" with the missing thing in \"blocked_by\": a card nobody "
    "tested has not failed."
)
NO_CARD_RULE = "No idea card came with this job, so all three options are yours."
PRACTICAL = (
    "PRACTICAL. The shell is Windows PowerShell: no '&&'. Write scripts to .py files and run them instead of long "
    "python -c lines, and set PYTHONIOENCODING=utf-8. The web goes through the proxy http://localhost:3128; if a "
    "site fails, try https://web.archive.org/web/2026/<url>; if the web is down, work from local references and "
    "mark every unchecked claim needs_review. Keep each computation under 20 minutes and single-threaded "
    "(8 workers share 8 cores). The PC is busy: if a shell command fails to start or times out, wait 20 s "
    "and retry up to 3 times before you report a failure."
)
PARALLEL = (
    "PARALLEL POOL. Other AI workers run at the same time: touch only files that belong to this job and commit "
    "only your own files (git add <your paths>; if .git/index.lock exists, wait 15 s and retry). NEVER edit "
    "research_queue.json, lab_ideas.json or lab_notebook.jsonl: the pool updates them from your result file."
)
HIDDEN = (
    "HIDDEN NUMBERS. The model gateway sometimes shows you a long number (8 or more digits in a row, also after a "
    "decimal point) as a 64-character hex string. Such a string standing where a number, a date or part of a name "
    "should be is that hidden number, not data: never report it as a data error and never quote it as a value. "
    "Use paths and job ids exactly as they are shown to you (the pool resolves them). When exact digits matter, "
    "compute and compare inside a script and point to the file that holds them."
)
CLOSING = (
    "ONE-SHOT RUN. This process ends the moment your reply ends, and nothing wakes you later. Never end your turn "
    "while a computation of yours is still running: keep polling it in the foreground (Start-Sleep 60, then read "
    "its output) until it is done, write the result file, and only then send your final message. Never claim a "
    "proof or a solution. Never push, post, email, upload or submit anything. End with STATUS: DONE."
)
RETRY = (
    "RETRY. An earlier run of this job ended without a result file ({reason}). Its notes, scripts and outputs may "
    "still be on disk: read your notes file and `git status` first, and continue from there rather than starting "
    "over."
)
RETRY_REASONS = {
    "stopped waiting": "it ended its turn while its own computation was still running",
    "exited": "it stopped early",
    "no connection": "the model connection dropped",
}


def result_schema(job):
    """The JSON a worker writes when it finishes. merge_results reads exactly these fields."""
    dept = lab.job_dept(job)
    fields = [
        '"status": "done" | "needs_review" | "failed"',
        '"result": "1-3 sentences with the numbers"',
        '"finished": "<ISO time>"',
        '"files": ["paths you changed"]',
        '"method": "one line: how you did it"',
    ]
    if dept in lab_ideas.CARD_DEPTS:
        fields += [
            '"novelty": "internal" | "reproduction" | "extension" | "new" | "negative"',
            '"baseline": "the published frontier you compared with, and its source"',
            '"metric": {"name": "...", "before": "...", "after": "..."}',
            '"idea_verdict": "worked" | "partial" | "failed" | "blocked"',
            '"blocked_by": "only with blocked: the input, login or job the card is waiting for"',
            '"next_ideas": [{"title": "...", "idea": "...", "why": "...", "verifier": "...", "kill": "...", '
            '"value": 0-10, "odds": 0-1, "cost": 1}]',
        ]
    if dept == "squares":
        fields.append('"strategy": "<NAME of your strategy file, if you wrote one>"')
    if job.get("candidates"):
        fields.append('"topic_id": "<id of the candidate problem you worked>"')
    if dept == "director":
        fields += [
            '"cards": [{"id": "<an existing card id - only when you rewrite that card>", "dept": "...", '
            '"title": "...", "idea": "...", "why": "...", "verifier": "...", '
            '"kill": "...", "value": 0-10, "odds": 0-1, "cost": 1, "repeat": false, "frontier": true, '
            '"topic_id": "<only for a card about one catalogue problem>"}]',
            '"kill": [{"id": "<card id>", "reason": "..."}]',
        ]
    if dept == "review":
        fields += ['"reviewed_jobs": ["<job id>"]', '"review": {"<job id>": "pass | fix | fail - one line"}',
                   '"supported_class": {"<job id>": "internal | reproduction | extension | new | negative"}']
    if dept == "panel":
        fields += ['"reviewed_jobs": ["<job id>"]', '"panel_verdict": {"<job id>": "pass | fix | reject - one line"}']
    return "{" + ", ".join(fields) + "}"


def prompt_for(job, earlier=()):
    """Job instructions, a note on earlier lost runs (`earlier`: their outcomes), the idea card, lab memory, the
    research method and the result schema. Lab memory gets the room that is left under PROMPT_ROOM, so a long
    job brief costs memory and never the other way round."""
    dept = lab.job_dept(job)
    result_file = RESULTS / f"{job['id']}.json"
    parts = [
        f"You are running unattended for Juwen; he will not answer questions. Do research job `{job['id']}` "
        f"({job['title']}). Work in {ROOT}. Read {PROTOCOL} first and follow it, especially the sections "
        "'Lab jobs' and 'Parallel pool'.",
        f"JOB INSTRUCTIONS: {job.get('why', '')}",
    ]
    lost = [outcome for outcome in earlier if not outcome.startswith("copilot")]
    if lost:
        parts.append(RETRY.format(reason=RETRY_REASONS.get(lost[-1], lost[-1])))
    card = None
    try:
        card = lab_ideas.get(job.get("idea_id"))
        if card:
            parts.append(lab_ideas.card_text(card))
    except Exception as error:  # the idea bank must never stop a worker from starting
        log(f"{job['id']}: idea card not shown: {error}")
    tail = []
    if dept in lab_ideas.CARD_DEPTS:
        tail.append(METHOD.format(job_id=job["id"], card_rule=CARD_RULE if card else NO_CARD_RULE))
    tail += [
        PRACTICAL,
        PARALLEL,
        HIDDEN,
        f"RESULT FILE - when finished write {result_file} as JSON: {result_schema(job)}",
        CLOSING,
    ]
    used = sum(len(part) + 2 for part in parts + tail)
    try:
        parts.append(lab_ideas.memory_text(job, max(800, PROMPT_ROOM - used)))
    except Exception as error:
        log(f"{job['id']}: lab memory not shown: {error}")
    return "\n\n".join(parts + tail)


def command(engine, job, earlier=()):
    prompt = prompt_for(job, earlier)
    if engine == "copilot":
        return [str(COPILOT), "-p", prompt, "--allow-all", "--no-ask-user", "--add-dir", str(ROOT)]
    exe = shutil.which("opencode") or "opencode"
    return [exe, "run", "--standalone", "--auto", "--format", "json", "--title", f"Lab: {job['id']}", prompt]


def start(job, engine, state):
    LOG_DIR.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now()
    log_path = LOG_DIR / f"pool_{job['id']}_{engine}_{stamp:%Y%m%d_%H%M}.log"
    env = opencode_environment() if engine == "opencode" else environment()
    for key in ("OMP_NUM_THREADS", "NUMBA_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        env[key] = "1"  # 8 workers on 8 cores: each worker's own computations stay single-threaded
    if engine == "copilot":
        env.setdefault("HTTPS_PROXY", "http://localhost:3128")
        env.setdefault("HTTP_PROXY", "http://localhost:3128")
    earlier = [attempt.get("outcome", "") for attempt in state.get(job["id"], {}).get("attempts", [])]
    with open(log_path, "w", encoding="utf-8") as handle:
        process = subprocess.Popen(command(engine, job, earlier), cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, env=env, creationflags=DETACHED)
    entry = state.setdefault(job["id"], {"attempts": []})
    entry.update({"pid": process.pid, "engine": engine, "started": stamp.isoformat(timespec="seconds"),
                  "log": str(log_path)})
    log(f"start {job['id']} on {engine} (pid {process.pid})")


TEXT_RESULT_FIELDS = {"novelty": 20, "baseline": 400, "method": 300, "idea_verdict": 20, "strategy": 80,
                      "blocked_by": 200}


def record_learning(job, data):
    """Feed a finished job back into the idea bank and the notebook. Every step stands alone: a problem in one is
    logged and never stops the result from being merged."""
    dept = lab.job_dept(job)
    verdict = None
    try:
        verdict = lab_ideas.settle(job, data)
        if verdict:
            log(f"{job['id']}: idea {job.get('idea_id')} {verdict}")
    except Exception as error:
        log(f"{job['id']}: idea not settled: {error}")
    if dept in lab_ideas.CARD_DEPTS:
        try:
            added = lab_ideas.add_cards(dept, data.get("next_ideas"), f"worker:{job['id']}")
            if added:
                log(f"{job['id']}: {len(added)} new idea card(s)")
        except Exception as error:
            log(f"{job['id']}: next ideas not stored: {error}")
    if dept == "director" and not job.get("director_applied"):
        try:
            changed, killed = lab_ideas.apply_director(job, data)
            job["director_applied"] = True
            log(f"{job['id']}: director wrote {len(changed)} card(s), killed {len(killed)}")
        except Exception as error:
            log(f"{job['id']}: director cards not applied: {error}")
    try:
        lab_ideas.note(job, data, verdict)
    except Exception as error:
        log(f"{job['id']}: notebook line not written: {error}")


MASKABLE = re.compile(r"(?<!\d)\d{5,}(?!\d)")
MASK = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def id_masks(queue):
    """{hash: number} for every long number in a job id. The model gateway can show a worker such a number as its
    SHA-256 hex, so the worker writes lab_x_<hash>_3.json or names a job by that hash."""
    runs = {run for job in queue.get("jobs", []) for run in MASKABLE.findall(str(job.get("id", "")))}
    return {hashlib.sha256(run.encode()).hexdigest(): run for run in runs}


def _unmask(text, masks):
    return MASK.sub(lambda found: masks.get(found.group(0), found.group(0)), text)


def _on_disk(entry):
    try:
        return (ROOT / entry).exists()
    except (OSError, TypeError, ValueError):
        return False


def unmasked(data, masks):
    """The result with the lab's own numbers put back where a worker copied their hash. A file entry keeps its
    hashed name while that is the only name on disk. A result without such a hash comes back untouched."""
    try:
        text = json.dumps(data, ensure_ascii=False)
        restored = _unmask(text, masks)
        if restored == text:
            return data
        clean = json.loads(restored)
        files, renamed = data.get("files"), clean.get("files")
        if isinstance(files, list) and isinstance(renamed, list) and len(files) == len(renamed):
            clean["files"] = [old if old != new and _on_disk(old) and not _on_disk(new) else new
                              for old, new in zip(files, renamed)]
        return clean
    except (AttributeError, TypeError, ValueError):
        return data


def adopt_masked_results(queue):
    """Give each lab_results file that a worker wrote under a hashed job id a copy under the real id, with the
    hashed ids inside it put back, so the pool, the Referee and the Panel find it. The worker's own file is never
    changed; a newer file under the real name is never replaced. Returns the names of the copies."""
    masks, adopted = id_masks(queue), []
    ids = {job["id"] for job in queue.get("jobs", [])}
    found = RESULTS.iterdir() if RESULTS.exists() else []
    paths = [path for path in found if MASK.search(path.name) and path.is_file()]
    for path in sorted(paths, key=lambda path: (path.suffix == ".json", path.name)):  # notes before results
        target = RESULTS / _unmask(path.name, masks)
        if target == path or target.stem not in ids:
            continue
        try:
            if target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
                continue
            if path.suffix == ".json":
                data = read_json(path, None)
                if not isinstance(data, dict):
                    continue  # half written or not a result: looked at again on the next tick
                write_json(target, unmasked(data, masks))
            else:  # latin-1 keeps every byte of a note as it is
                target.write_bytes(_unmask(path.read_bytes().decode("latin-1"), masks).encode("latin-1"))
        except OSError as error:
            log(f"{target.stem}: masked {path.suffix} file not adopted: {error}")
            continue
        adopted.append(target.name)
        log(f"{target.stem}: {target.suffix} file adopted from a gateway-masked name")
    return adopted


def merge_results(queue):
    """Copy every lab_results/<id>.json into its queue job, then feed it to the idea bank and the notebook.
    Returns the merged ids. Only known, bounded fields are copied: a worker cannot set idea_id or reviews.
    A Referee's verdicts go on to the jobs it reviewed, their cards and their notebook lines."""
    merged, masks = [], id_masks(queue)
    jobs = {job["id"]: job for job in queue.get("jobs", [])}
    for path in sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []:
        job = jobs.get(path.stem)
        data = read_json(path, None)
        if not job or not isinstance(data, dict) or job.get("result_merged") == path.stat().st_mtime:
            continue
        data = unmasked(data, masks)
        if job.get("referee") and job.get("result_merged"):  # the verdict stays; the new text is not re-run
            log(f"{job['id']}: result file changed after the Referee's verdict ({job['referee']})")
        status = data.get("status", "done")
        job["status"] = status if status in ("done", "needs_review", "failed") else "done"
        job["result"] = str(data.get("result", ""))[:1500]
        job["finished"] = str(data.get("finished") or dt.datetime.now().isoformat(timespec="seconds"))[:40]
        for key in ("files", "review", "panel", "panel_verdict"):  # "reviewed" is the Referee's flag, not a worker's
            if key in data:
                job[key] = data[key]
        data.pop("claimed_novelty", None)  # only the lab writes this field
        # the lab's own lowering is redone from every new file; a class the Referee set stands
        earlier = None if job.get("referee") else job.pop("claimed_novelty", None)
        lowered = None
        try:
            lowered = lab_ideas.honest_class(job, data)
        except Exception as error:
            log(f"{job['id']}: class not checked: {error}")
            if earlier:  # the bank could not be read: the earlier lowering stays, in the queue and in the notebook
                job["claimed_novelty"] = earlier
                data["novelty"], data["claimed_novelty"] = "internal", earlier
        for key, size in TEXT_RESULT_FIELDS.items():
            if data.get(key) and not (key == "novelty" and job.get("claimed_novelty")):  # the Referee's class stands
                job[key] = str(data[key])[:size]
        if lowered and not job.get("claimed_novelty"):
            job["claimed_novelty"] = lowered
            if lowered != earlier:
                log(f"{job['id']}: class {lowered} -> internal (its card makes no frontier claim)")
        if isinstance(data.get("metric"), dict):
            job["metric"] = {str(key)[:40]: str(value)[:80] for key, value in list(data["metric"].items())[:4]}
        if isinstance(data.get("next_ideas"), list):
            job["next_ideas"] = [str(idea.get("title", ""))[:140] for idea in data["next_ideas"][:4]
                                 if isinstance(idea, dict)]
        if job.get("candidates") and not job.get("topic_id") and data.get("topic_id") in job["candidates"]:
            job["topic_id"] = data["topic_id"]  # the worker chose one of the problems it was offered
        reviewed = data.get("reviewed_jobs")
        flag = {"review": "reviewed", "panel": "panel_reviewed"}.get(job.get("dept"))
        for reviewed_id in reviewed if flag and isinstance(reviewed, list) else []:  # what they checked
            target = jobs.get(reviewed_id) if isinstance(reviewed_id, str) else None
            if not target:
                continue
            if job["status"] == "failed" and target.get(f"{flag}_failed") in (None, job["id"]):
                target[f"{flag}_failed"] = job["id"]  # a review that failed is no review: the job is issued once more
            else:
                target[flag] = job["id"]
        job["result_merged"] = path.stat().st_mtime
        merged.append(job["id"])
        record_learning(job, data)
        if job.get("dept") == "review" and job["status"] != "failed":
            try:
                for line in lab_ideas.apply_referee(job, data, jobs):
                    log(line)
            except Exception as error:
                log(f"{job['id']}: referee verdicts not applied: {error}")
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


def start_order(job, now, max_wait_hours):
    """Sort key for the free slots: a job that has waited longer than max_wait_hours goes first (a busy
    high-priority department cannot starve the others), then department priority, then age."""
    queued = str(job.get("queued") or "")
    try:
        waited = (now - dt.datetime.fromisoformat(queued[:19])).total_seconds() / 3600
    except ValueError:
        waited = 0
    dept = lab.job_dept(job)
    return (0 if waited > max_wait_hours else 1, PRIORITY.index(dept) if dept in PRIORITY else 99, queued)


def collect():
    """Steps 1-3: finished, dead and slow workers; result files; jobs that used up their attempts."""
    config = settings()
    queue = read_json(QUEUE, None)
    if not queue:
        return
    try:
        adopt_masked_results(queue)
    except Exception as error:
        log(f"masked result files not checked: {error}")
    state = read_json(STATE, {})
    original = json.dumps(queue.get("jobs", []), sort_keys=True)
    jobs = {job["id"]: job for job in queue.get("jobs", [])}
    changed = False
    for job_id, entry in list(state.items()):
        pid = entry.get("pid")
        if not pid:
            continue
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
            elif entry["engine"] == "copilot" and _copilot_out_of_quota(entry.get("log")):
                outcome = "copilot out of quota"
                COPILOT_QUOTA_FLAG.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
            elif _no_connection(entry.get("log")):
                outcome = "no connection"
                if entry["engine"] == "opencode":  # from now on the model is asked before a job is started
                    MODEL_FLAG.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
            elif not (RESULTS / f"{job_id}.json").exists() and _stopped_waiting(entry.get("log")):
                outcome = "stopped waiting"
        entry.setdefault("attempts", []).append({"engine": entry["engine"], "started": entry["started"],
                                                 "outcome": outcome, "log": entry.get("log")})
        entry["pid"] = None
        log(f"{job_id}: {entry['engine']} {outcome}")
    if merge_results(queue):
        changed = True
    for job_id, entry in state.items():
        job = jobs.get(job_id)
        if job and job.get("status") in OPEN and not entry.get("pid") and exhausted(entry, config["max_attempts"]):
            job["status"] = "failed"
            job["error"] = "pool: no result after " + ", ".join(a["outcome"] for a in entry["attempts"])
            changed = True
            log(f"{job_id}: marked failed")
            try:  # the idea goes back to the bank and the notebook remembers the loss
                lab_ideas.release(job)
                lab_ideas.note(job, {"status": "failed", "result": job["error"]}, "no result")
            except Exception as error:
                log(f"{job_id}: idea not released: {error}")
    try:
        write_json(STATE, state)
    finally:  # the queue holds the merged marks: without them the next tick merges the same results again
        if changed:
            save_queue_merged(queue, original)


def fill():
    """Step 5: start waiting jobs in the free slots. Runs after lab.ensure_jobs(), so a slot that was freed in
    this tick is filled by a job that was queued in this tick. A worker that has delivered its result but has
    not exited yet does not hold a slot (at most two such at a time, so the PC is never far over its limit)."""
    config = settings()
    model_ok = model_reachable()  # asked before the queue is read: the check can take a minute
    queue = read_json(QUEUE, None)
    if not queue:
        return
    state = read_json(STATE, {})
    original = json.dumps(queue.get("jobs", []), sort_keys=True)
    open_ids = {job["id"] for job in queue.get("jobs", []) if job.get("status") in OPEN}
    alive_ids = [job_id for job_id, entry in state.items() if entry.get("pid") and alive(entry["pid"])]
    running = max(sum(1 for job_id in alive_ids if job_id in open_ids), len(alive_ids) - 2)
    now = dt.datetime.now()
    today = now.date().isoformat()
    candidates = [job for job in queue.get("jobs", []) if pool_job(job) and job.get("status") == "needs_ai"
                  and (job.get("not_before") or today) <= today and not state.get(job["id"], {}).get("pid")
                  and not exhausted(state.get(job["id"], {}), config["max_attempts"])]
    candidates.sort(key=lambda job: start_order(job, now, config["max_wait_hours"]))
    slot = 0
    changed = False
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
        if engine == "opencode" and not model_ok:
            slot += 1  # the next job still gets the engine it would have had
            continue
        try:
            start(job, engine, state)
        except OSError as error:  # the engine is missing or Windows refused the command line: count it, go on
            entry = state.setdefault(job["id"], {"attempts": []})
            entry.setdefault("attempts", []).append({"engine": engine, "started": now.isoformat(timespec="seconds"),
                                                     "outcome": f"could not start: {error}"[:200], "log": None})
            entry["pid"] = None
            log(f"{job['id']}: could not start on {engine}: {error}")
            write_json(STATE, state)
            continue
        write_json(STATE, state)  # at once: a tick that dies later must not lose track of a running worker
        job["pool"] = {"engine": engine, "started": state[job["id"]]["started"]}
        running += 1
        slot += 1
        changed = True
    if changed:
        save_queue_merged(queue, original)


def tick():
    collect()
    fill()


def process_started():
    """Seconds between this process's creation and the first line of the script (interpreter startup)."""
    creation, exit_time, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
    handle = ctypes.c_void_p(-1)  # pseudo handle of the current process (must be pointer-sized)
    ok = ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                                ctypes.byref(kernel), ctypes.byref(user))
    if not ok or not creation.value:
        return 0.0
    created = creation.value / 1e7 - 11644473600  # FILETIME (100 ns since 1601) -> Unix seconds
    return max(0.0, SCRIPT_START - created)


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
    elif "--model-check" in sys.argv:
        try:
            model_check()
        except Exception as error:
            log(f"model check error: {error}")
            raise
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
            phases = [("startup", process_started())]
            for name, step in (("collect", collect), ("ensure_jobs", lab.ensure_jobs), ("fill", fill),
                               ("write_state", lab.write_state)):
                begun = time.time()
                step()
                phases.append((name, time.time() - begun))
            total = time.time() - SCRIPT_START + phases[0][1]
            if total > 20:  # normal tick ~1 s; record where a slow scheduled tick spent its time
                log("slow tick %.0f s: " % total + ", ".join(f"{name} {seconds:.1f}s" for name, seconds in phases))
        except Exception as error:
            log(f"pool error: {describe(error)}")
            raise
        finally:
            lock.unlink(missing_ok=True)
