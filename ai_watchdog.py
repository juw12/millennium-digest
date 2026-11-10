"""Backup plan for AI jobs: if the Precedent Agent (OpenCode) fails or is too slow, GitHub Copilot CLI takes over;
if Copilot also fails, Juwen gets one email (juwpegatron@gmail.com).

Runs every 30 minutes (scheduled task "Millennium AI watchdog"). For each research-queue job that is still
"needs_ai" after ai_bridge.py handed it to the agent:
  - the agent never picked it up within PICKUP_HOURS, or
  - the agent job ended as failed / blocked / stopped / done-without-result, or
  - the agent has been on it longer than SLOW_HOURS
-> stop the agent job (so two AIs never work on the same files), then run Copilot once with the same
   protocol (AI_JOB_PROTOCOL.md). If the job is still not done afterwards -> email Juwen.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESEARCH_DIR = HERE.parent
QUEUE_JSON = RESEARCH_DIR / "research_queue.json"
PROTOCOL = RESEARCH_DIR / "AI_JOB_PROTOCOL.md"
AGENT_JOBS = Path(r"C:\Users\juwen_lie\Desktop\automation\agent\precedent-agent\state\jobs.json")
AGENT_API = "http://127.0.0.1:4790/api"
COPILOT = Path(r"C:\Users\juwen_lie\AppData\Local\GitHubCopilotCLI\copilot.exe")
LOG_DIR = HERE / "logs"  # git-ignored
LOCK = LOG_DIR / "ai_watchdog.lock"

PICKUP_HOURS = 2
SLOW_HOURS = 6
DONE_GRACE_MINUTES = 30
COPILOT_TIMEOUT_MINUTES = 120

sys.path.insert(0, str(HERE))
import notify_juwen  # noqa: E402


def log(text):
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / "ai_watchdog.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {text}\n")


def load_queue():
    return json.loads(QUEUE_JSON.read_text(encoding="utf-8"))


def save_queue(queue):
    QUEUE_JSON.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def agent_job_for(job_id):
    if not AGENT_JOBS.exists():
        return None
    matches = [job for job in json.loads(AGENT_JOBS.read_text(encoding="utf-8")) if f"`{job_id}`" in job.get("goal", "")]
    return matches[-1] if matches else None


def hours_since(stamp):
    if not stamp:
        return 0.0
    return (dt.datetime.now() - dt.datetime.fromisoformat(str(stamp).replace(" ", "T"))).total_seconds() / 3600


def needs_fallback(job, agent_job):
    """Returns a reason string when Copilot should take over, else None."""
    if agent_job is None:
        return "agent never picked it up" if hours_since(job["bridged"]) > PICKUP_HOURS else None
    status = agent_job.get("status")
    if status in ("failed", "blocked", "stopped", "error"):
        return f"agent job {status}: {agent_job.get('blocked_reason', '')}".strip()
    if status == "done" and hours_since(agent_job.get("updated")) * 60 > DONE_GRACE_MINUTES:
        return "agent said done but the research job is still open"
    if status in ("queued", "running") and hours_since(job["bridged"]) > SLOW_HOURS:
        return f"agent too slow (> {SLOW_HOURS} h)"
    return None


def stop_agent_job(agent_job):
    if not agent_job or agent_job.get("status") not in ("queued", "running"):
        return
    request = urllib.request.Request(
        f"{AGENT_API}/stop",
        data=json.dumps({"id": agent_job["id"]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(request, timeout=10).close()
    except Exception as error:
        log(f"could not stop agent job {agent_job['id']}: {error}")


def run_copilot(job):
    prompt = (
        f"You are running unattended for Juwen; he will not answer questions. "
        f"Do research job `{job['id']}` ({job['title']}) from {QUEUE_JSON}. "
        f"Read and follow {PROTOCOL} exactly: it covers research rules, email rules (sending is pre-approved), "
        f"and how to mark the job done. Work in {RESEARCH_DIR}. Never push the research repo."
    )
    LOG_DIR.mkdir(exist_ok=True)
    output = LOG_DIR / f"copilot_{job['id']}_{dt.datetime.now():%Y%m%d_%H%M}.log"
    environment = dict(os.environ)
    environment.setdefault("HTTPS_PROXY", "http://localhost:3128")
    environment.setdefault("HTTP_PROXY", "http://localhost:3128")
    with output.open("w", encoding="utf-8") as handle:
        try:
            completed = subprocess.run(
                [str(COPILOT), "-p", prompt, "--allow-all", "--no-ask-user", "--add-dir", str(RESEARCH_DIR)],
                cwd=RESEARCH_DIR,
                stdout=handle,
                stderr=subprocess.STDOUT,
                env=environment,
                timeout=COPILOT_TIMEOUT_MINUTES * 60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return completed.returncode, output
        except subprocess.TimeoutExpired:
            return "timeout", output


def check(dry_run=False):
    queue = load_queue()
    decisions = []
    for job in queue.get("jobs", []):
        if job.get("status") != "needs_ai" or not job.get("bridged"):
            continue
        if job.get("dept") or job["id"].startswith(("daily_", "tool_")):
            continue  # lab_pool.py retries lab jobs with the other engine itself
        fallback = job.get("fallback") or {}
        if fallback.get("finished"):
            decisions.append(f"{job['id']}: both AIs tried; notice {'already sent' if job.get('juwen_notified') else 'due'}")
            if dry_run:
                continue
            if not job.get("juwen_notified"):
                sent = notify_juwen.notify(
                    f"stuck_{job['id']}",
                    f"AI job stuck: {job['id']}",
                    f"Both automatic AIs could not finish the research job '{job['title']}'.\n\n"
                    f"OpenCode: {fallback.get('reason', 'failed')}. Copilot: exit {fallback.get('result')} "
                    f"(log: {fallback.get('log')}).\n\n"
                    "What to do: open Vector on your PC and type: go\n"
                    "Nothing else needs you; all other automation keeps running.",
                    min_hours=24,
                )
                if sent:
                    job["juwen_notified"] = dt.datetime.now().isoformat(timespec="seconds")
                    save_queue(queue)
            continue
        agent_job = agent_job_for(job["id"])
        reason = needs_fallback(job, agent_job)
        decisions.append(f"{job['id']}: {reason or 'agent is on it, no action'}")
        if not reason or dry_run:
            continue
        log(f"{job['id']}: switching to Copilot ({reason})")
        stop_agent_job(agent_job)
        job["fallback"] = {"engine": "copilot", "reason": reason, "started": dt.datetime.now().isoformat(timespec="seconds")}
        save_queue(queue)
        result, output = run_copilot(job)
        queue = load_queue()  # Copilot edits the queue itself when it finishes
        job = next(item for item in queue["jobs"] if item["id"] == job["id"])
        job.setdefault("fallback", {}).update(
            {"finished": dt.datetime.now().isoformat(timespec="seconds"), "result": str(result), "log": str(output)}
        )
        save_queue(queue)
        log(f"{job['id']}: Copilot finished ({result}); job status now {job.get('status')}")
        return decisions  # one fallback per run; the next run sends the notice if it is still open
    return decisions


def main():
    if "--dry-run" in sys.argv:
        for line in check(dry_run=True) or ["no bridged AI jobs open"]:
            print(line)
        return
    LOG_DIR.mkdir(exist_ok=True)
    if LOCK.exists() and hours_since(dt.datetime.fromtimestamp(LOCK.stat().st_mtime).isoformat()) < 3:
        return
    LOCK.write_text(str(os.getpid()))
    try:
        catch_up_daily_step()
        check()
        import lab
        lab.write_state()  # show Copilot results right away
    except Exception as error:
        log(f"watchdog error: {error}")
    finally:
        LOCK.unlink(missing_ok=True)


def catch_up_daily_step():
    """Every 30 minutes: refill the lab (one open job per department), hand new jobs to the agent and
    refresh lab/lab_state.js for the live 3D lab page."""
    import ai_bridge
    import lab
    created = lab.ensure_jobs()
    if created:
        log("lab jobs queued: " + ", ".join(created))
    posted = ai_bridge.bridge()
    if posted:
        log("handed to the agent: " + ", ".join(posted))
    lab.write_state()


if __name__ == "__main__":
    main()
