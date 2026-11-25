"""Lean gate: a result counts only when a machine has checked it (Juwen, 2026-11-25).

Every 30 minutes (task "Millennium Lean gate"):
1. prize_work/lean/claim_cache.py --check checks the Lean claim files that are new or changed (the lab's own
   checker: check_claim.py verdicts checked / kernel_trust / sorry / failed; a timeout is never a verdict).
2. Every job that listed files in `lean_claims` gets one verdict, judged on the files' CURRENT bytes (a cached
   verdict for older bytes does not count): failed or sorry if any file is, pending while one is unchecked,
   kernel_trust if one needs native_decide, else checked. Written to logs/lean_gate.json (the queue itself is
   never written here, so the pool and this gate cannot race).
3. flagship.settle() marks flagship lemmas proved and adds the sub-lemmas workers proposed.
4. An email goes to Juwen at once for every newly proved flagship lemma and every new Lean-checked strong result.

    python lean_gate.py             check, judge, settle, alert
    python lean_gate.py --no-check  judge from the cache only (fast, no Lean run)
"""
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import flagship  # noqa: E402
import lab_useful  # noqa: E402

ROOT = HERE.parent
QUEUE = ROOT / "research_queue.json"
PRIZE_WORK = ROOT / "prize_work"
LEAN_DIR = PRIZE_WORK / "lean"
CACHE = LEAN_DIR / "claim_cache.json"
RESULTS = ROOT / "lab_results"
STATE = HERE / "logs" / "lean_gate.json"
LOG = HERE / "logs" / "lean_gate.log"
LOCK = HERE / "logs" / "lean_gate.lock"
CHECK_MINUTES = 25
ORDER = ("failed", "sorry", "no_audit", "pending", "kernel_trust", "checked")  # worst first


def log(text):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {text}\n")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def relative(claim):
    """A worker's path ('prize_work/x/lean/A.lean', 'x\\lean\\A.lean' or absolute) as the cache key 'x/lean/A.lean',
    or None when it is not a .lean file inside prize_work."""
    text = str(claim).strip().replace("\\", "/")
    base = str(PRIZE_WORK).replace("\\", "/").rstrip("/") + "/"
    if text.lower().startswith(base.lower()):
        text = text[len(base):]
    elif text.startswith("prize_work/"):
        text = text[len("prize_work/"):]
    if not text.endswith(".lean") or ".." in text.split("/") or text.startswith("/"):
        return None
    return text


def file_verdict(claim, cache):
    key = relative(claim)
    if not key:
        return "failed"  # not a Lean file in the lab: nothing to check
    path = PRIZE_WORK / key
    try:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "failed"  # listed but missing
    entry = (cache.get("claims") or {}).get(key)
    if not entry or entry.get("sha") != sha:
        return "pending"
    return entry.get("verdict") or "pending"


def job_verdict(claims, cache):
    verdicts = [file_verdict(claim, cache) for claim in claims]
    if not verdicts:
        return None
    return next((word for word in ORDER if word in verdicts), "pending")  # the worst file decides


def run_checker():
    """One bounded claim_cache run; a timeout only means the next run continues."""
    env = dict(os.environ)
    env.setdefault("HTTPS_PROXY", "http://localhost:3128")
    try:
        run = subprocess.run([sys.executable.replace("pythonw", "python"), "claim_cache.py", "--check", "--max-kb", "400",
                              "--chunk", "8", "--bisect"], cwd=LEAN_DIR, capture_output=True, text=True,
                             timeout=CHECK_MINUTES * 60, env=env, creationflags=0x08000000 if os.name == "nt" else 0)
        log(f"claim_cache exit {run.returncode}: {(run.stdout or run.stderr).strip().splitlines()[-1:]}")
    except subprocess.TimeoutExpired:
        log(f"claim_cache stopped after {CHECK_MINUTES} min (continues next run)")
    except OSError as error:
        log(f"claim_cache not run: {error}")


def judge(jobs, cache):
    return {job["id"]: job_verdict(job.get("lean_claims") or [], cache)
            for job in jobs if job.get("lean_claims")}


def alerts(state, jobs, verdicts, newly_proved, ledger, send):
    """Emails for newly proved lemmas and new Lean-checked strong results; each one once (state['alerted'])."""
    alerted = set(state.get("alerted", []))
    by_id = {lemma["id"]: lemma for lemma in (ledger or {}).get("lemmas", [])}
    for lemma_id in newly_proved:
        key = f"lemma:{lemma_id}"
        lemma = by_id.get(lemma_id, {})
        if key not in alerted and send(key, f"Flagship lemma {lemma_id} proved: {lemma.get('title', '')}",
                                       f"{lemma.get('statement', '')}\n\nChecked: {lemma.get('verdict')} "
                                       f"(job {lemma.get('proved_by')}).\n\nFiles: {', '.join(lemma.get('lean_claims') or [])}"):
            alerted.add(key)
    useful = {job["id"] for job in lab_useful.useful_from(jobs)}
    for job in jobs:
        key = f"strong:{job['id']}"
        if key in alerted or job["id"] not in useful or not lab_useful.is_strong(job):
            continue
        if verdicts.get(job["id"]) in flagship.MACHINE_CHECKED:
            if send(key, f"Lean-checked result: {job.get('title', job['id'])[:90]}",
                    f"{job.get('result', '')}\n\nClass: {job.get('novelty')}. Referee: {job.get('referee')}. "
                    f"Lean: {verdicts[job['id']]}.\n\nFiles: {', '.join(job.get('lean_claims') or [])}"):
                alerted.add(key)
    state["alerted"] = sorted(alerted)


def _send(key, subject, text):
    import notify_juwen
    return notify_juwen.notify(key, subject, text, min_hours=0)


def main(check=True, send=_send):
    if LOCK.exists() and (dt.datetime.now().timestamp() - LOCK.stat().st_mtime) < 3 * 3600:
        return  # another gate run is still working
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        if check:
            run_checker()
        jobs = read_json(QUEUE, {}).get("jobs", [])
        cache = read_json(CACHE, {})
        verdicts = judge(jobs, cache)
        state = read_json(STATE, {})
        ledger, newly, added = flagship.settle(jobs, verdicts, RESULTS,
                                               claim_verdict=lambda claims: job_verdict(claims, cache))
        if ledger is not None:
            flagship.save(ledger)
        for lemma_id in newly:
            log(f"flagship lemma {lemma_id} proved")
        for lemma_id in added:
            log(f"flagship lemma {lemma_id} added (proposed by a worker)")
        alerts(state, jobs, verdicts, newly, ledger, send)
        state.update({"checked": dt.datetime.now().isoformat(timespec="seconds"), "jobs": verdicts})
        write_json(STATE, state)
        log(f"judged {len(verdicts)} job(s): " + ", ".join(f"{word} {list(verdicts.values()).count(word)}"
                                                         for word in ORDER if word in verdicts.values()))
    finally:
        LOCK.unlink(missing_ok=True)


def aicc_snapshot():
    """The gateway breaks tokens down by model for the current day only, so the day's record is saved on every
    gate run (every 30 minutes) for usage_report.py. Never stops the gate."""
    try:
        import usage_report
        usage_report.aicc_snapshot()
    except Exception as error:
        log(f"AICC snapshot failed: {error!r}")


if __name__ == "__main__":
    aicc_snapshot()
    main(check="--no-check" not in sys.argv)
