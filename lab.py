"""Millennium Lab: a department of research bots that never sits idle.

ensure_jobs()   Every department in lab_departments.json keeps exactly one job open in research_queue.json.
                As soon as a department's job is done (or needs review), the next one is queued, so the lab
                works continuously instead of one step a day. ai_bridge.py hands the jobs to the agent via the
                Cairn autopilot queue; ai_watchdog.py calls this every 30 minutes and falls back to Copilot.
write_state()   lab/lab_state.js for the live 3D lab page (lab/index.html) and the dashboard: every bot's
                current job and agent status, steps per day, and the milestones in lab_targets.json.

Usage: python lab.py            queue what is missing, write the state, print a summary
       python lab.py --dry-run  show what would be queued, change nothing
"""
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUEUE = ROOT / "research_queue.json"
DEPARTMENTS = HERE / "lab_departments.json"
TARGETS = HERE / "lab_targets.json"
CATALOG = HERE / "prize_catalog.json"
ROADMAP = HERE / "roadmap.json"
PRIZE_WORK = ROOT / "prize_work"
SQUARES_SCORE = PRIZE_WORK / "squares-in-squares" / "best_score.json"
STATE_JS = HERE / "lab" / "lab_state.js"
AGENT_JOBS = Path(r"C:\Users\juwen_lie\Desktop\automation\agent\precedent-agent\state\jobs.json")
OPEN = ("needs_ai", "running")
RULES = (
    " Finish ONE concrete, verifiable step (a script with a gate and its numbers, or a derivation checked "
    "numerically) - not a plan. Empirical/heuristic only: never claim a proof or a solution, never submit, post or "
    "email anything. When done, set this job's status to done (or needs_review if it looks like more than it is), "
    "'finished' to the ISO time, and 'result' to 1-3 sentences with the numbers. Commit in the research repo."
)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40]


def job_dept(job):
    """Department of a queue job (daily_* jobs from the first version map by their focus)."""
    if job.get("dept"):
        return job["dept"]
    if job.get("id", "").startswith("daily_"):
        return {"rh": "rh", "bsd": "bsd", "prize": "prize"}.get(job.get("focus"))
    return None


# ------------------------------------------------------------------ what each department works on next
def _done_dossiers():
    return {path.stem for path in PRIZE_WORK.glob("*.md")} if PRIZE_WORK.exists() else set()


def _taken_topics(jobs):
    return {job.get("topic_id") for job in jobs if job.get("topic_id") and job.get("status") in OPEN}


def _next_catalog_item(jobs, want):
    catalog = (_read(CATALOG, {}) or {}).get("items", [])
    done, taken = _done_dossiers(), _taken_topics(jobs)
    pool = [item for item in catalog if want(item) and _slug(item["id"]) not in done and item["id"] not in taken]
    pool.sort(key=lambda item: (-item.get("reward_usd", 0), item["id"]))
    return pool[0] if pool else None


def squares_baseline():
    """Trivial packing: n unit squares on a ceil(sqrt n) grid. Score = sum (L / sqrt n - 1), n = 1..1000."""
    return sum(math.ceil(math.sqrt(n)) / math.sqrt(n) - 1 for n in range(1, 1001))


def _last_result(jobs, dept):
    finished = [job for job in jobs if job_dept(job) == dept and job.get("status") in ("done", "needs_review")]
    return finished[-1].get("result", "") if finished else ""


def plan_job(dept, jobs, today, number):
    """Title, extra instructions and fields for the department's next job (None = nothing to do now)."""
    roadmap = _read(ROADMAP, {}) or {}
    previous = _last_result(jobs, dept["id"])
    carry = f" Your previous result: {previous[:400]}" if previous else ""
    extra = {}
    if dept["id"] in ("bsd", "rh"):
        key = dept["id"].upper()
        title = f"{dept['name']}: next step on {dept['problem']}"
        topic = f" Roadmap: {roadmap.get(key, {}).get('next', '')}{carry}"
    elif dept["id"] == "erdos":
        item = _next_catalog_item(jobs, lambda i: i.get("family") == "erdos")
        if not item:
            return None
        title = f"{dept['name']}: {item['title']} ({item['reward']})"
        topic = f" Problem: {item['id']} {item['title']}. Statement: {item.get('statement', '')} Source: {item['url']} (dossier id: {_slug(item['id'])})"
        extra = {"topic_id": item["id"], "topic_url": item["url"]}
    elif dept["id"] == "prize":
        item = _next_catalog_item(
            jobs, lambda i: i.get("computable") and i.get("family") != "erdos" and i["id"] != "KAGGLE-SQ")
        if not item:
            return None
        title = f"{dept['name']}: {item['title']} ({item['reward']})"
        topic = f" Problem: {item['id']} {item['title']}. {item.get('statement', '')} Source: {item['url']} (dossier id: {_slug(item['id'])})"
        extra = {"topic_id": item["id"], "topic_url": item["url"]}
    elif dept["id"] == "squares":
        best = _read(SQUARES_SCORE, {}) or {}
        state = f"current best valid score {best['score']:.4f}" if best.get("valid") else "no valid solver yet"
        title = f"{dept['name']}: improve the Squares in Squares score ({state})"
        topic = f" Trivial-grid baseline score: {squares_baseline():.4f}. Status: {state}.{carry}"
    elif dept["id"] == "scout":
        digests = sorted((HERE / "digests").glob("digest_*.md"))
        latest = digests[-1].name if digests else "none yet"
        title = f"{dept['name']}: brief the newest papers on NS, Yang-Mills, P vs NP, Hodge ({latest})"
        topic = f" Latest digest: automation/digests/{latest}. Note file: scout_notes/{today.isoformat()}.md."
    elif dept["id"] == "review":
        waiting = [job for job in jobs if job.get("dept") and job.get("dept") != "review"
                   and job.get("status") in ("done", "needs_review") and not job.get("reviewed")][-3:]
        if not waiting:
            return None
        title = f"{dept['name']}: review {', '.join(job['id'] for job in waiting)}"
        topic = " Jobs to review: " + "; ".join(f"{job['id']} - {job.get('result', '')[:200]}" for job in waiting)
        extra = {"reviews": [job["id"] for job in waiting]}
    elif dept["id"] == "writer":
        title = f"{dept['name']}: advance the first-month package (BSD note, RH note, prize index)"
        topic = f" Package status: {first_month_status()['text']}.{carry}"
    else:
        title = f"{dept['name']}: next step"
        topic = carry
    return {
        "id": f"lab_{dept['id']}_{today:%Y%m%d}_{number}",
        "title": title,
        "project": dept["id"],
        "dept": dept["id"],
        "status": "needs_ai",
        "not_before": today.isoformat(),
        "queued": dt.datetime.now().isoformat(timespec="seconds"),
        "why": dept["brief"] + topic + RULES,
        **extra,
    }


def ensure_jobs(today=None, dry_run=False):
    """Queue one job for every department that has none open. Returns the new job ids."""
    today = today or dt.date.today()
    config = _read(DEPARTMENTS, {}) or {}
    queue = _read(QUEUE)
    if not queue or not config:
        return []
    jobs = queue.setdefault("jobs", [])
    open_lab = [job for job in jobs if job_dept(job) and job.get("status") in OPEN]
    busy = {job_dept(job) for job in open_lab}
    room = config.get("max_open", 8) - len(open_lab)
    created = []
    for dept in config.get("departments", []):
        if room <= 0:
            break
        if dept["id"] in busy:
            continue
        number = 1 + sum(1 for job in jobs if job.get("id", "").startswith(f"lab_{dept['id']}_{today:%Y%m%d}_"))
        job = plan_job(dept, jobs, today, number)
        if not job:
            continue
        created.append(job)
        jobs.append(job)
        room -= 1
    if created and not dry_run:
        QUEUE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return [job["id"] for job in created]


# ------------------------------------------------------------------ live state for the lab page
def _agent_jobs():
    data = _read(AGENT_JOBS, []) or []
    return data if isinstance(data, list) else []


def _agent_status(job_id, agent_jobs):
    matches = [job for job in agent_jobs if f"`{job_id}`" in job.get("goal", "")]
    if not matches:
        return None
    last = matches[-1]
    return {"status": last.get("status"), "rounds": last.get("rounds"), "updated": last.get("updated")}


def _finished_day(job):
    stamp = job.get("finished") or job.get("bridged") or ""
    try:
        return dt.date.fromisoformat(stamp[:10])
    except ValueError:
        return None


def steps_per_day(jobs, today, days=7):
    counts = {today - dt.timedelta(days=offset): 0 for offset in range(days)}
    for job in jobs:
        if job.get("status") in ("done", "needs_review") and (job_dept(job) or job.get("finished")):
            day = _finished_day(job)
            if day in counts:
                counts[day] += 1
    return dict(sorted(counts.items()))


def first_month_status():
    parts = {
        "BSD note": (ROOT / "bsd_project" / "paper" / "bsd_note.md").exists(),
        "RH note": (ROOT / "rh_project" / "paper" / "rh_note.md").exists(),
        "30 prize dossiers": len(_done_dossiers()) >= 30,
        "Kaggle-ready submission": (PRIZE_WORK / "squares-in-squares" / "submission.csv").exists(),
    }
    done = sum(parts.values())
    text = ", ".join(f"{name} {'done' if ok else 'open'}" for name, ok in parts.items())
    text += f" ({len(_done_dossiers())} dossiers so far)"
    return {"fraction": done / len(parts), "text": text, "parts": parts}


def milestone_status(milestone, jobs, today):
    check = milestone.get("check")
    if check == "steps_per_day":
        last3 = list(steps_per_day(jobs, today, 3).values())
        average = sum(last3) / 3
        return {"progress": min(1.0, average / milestone["goal"]), "done": average >= milestone["goal"],
                "note": f"{average:.1f} finished steps/day over the last 3 days (goal {milestone['goal']})"}
    if check == "squares_valid":
        best = _read(SQUARES_SCORE, {}) or {}
        baseline = squares_baseline()
        if best.get("valid"):
            done = best["score"] < baseline
            return {"progress": 1.0 if done else 0.5, "done": done,
                    "note": f"best valid score {best['score']:.4f} vs grid baseline {baseline:.4f}"}
        return {"progress": 0.0, "done": False, "note": f"no valid solver yet; grid baseline {baseline:.4f}"}
    if check == "first_month_package":
        status = first_month_status()
        return {"progress": status["fraction"], "done": status["fraction"] >= 1, "note": status["text"]}
    done = bool(milestone.get("done"))
    return {"progress": 1.0 if done else 0.0, "done": done, "note": milestone.get("note", "set by hand")}


def build_state(today=None):
    today = today or dt.date.today()
    config = _read(DEPARTMENTS, {}) or {}
    jobs = (_read(QUEUE, {}) or {}).get("jobs", [])
    agent_jobs = _agent_jobs()
    bots = []
    for dept in config.get("departments", []):
        mine = [job for job in jobs if job_dept(job) == dept["id"]]
        current = next((job for job in reversed(mine) if job.get("status") in OPEN), None)
        finished = [job for job in mine if job.get("status") in ("done", "needs_review")]
        agent = _agent_status(current["id"], agent_jobs) if current else None
        if current is None:
            mood = "idle"
        elif agent and agent.get("status") == "running":
            mood = "working"
        elif current.get("fallback") and not current["fallback"].get("finished"):
            mood = "working"
        elif current.get("bridged"):
            mood = "queued"
        else:
            mood = "planning"
        if finished and finished[-1].get("status") == "needs_review" and mood != "working":
            mood = "review"
        done_today = sum(1 for job in finished if _finished_day(job) == today)
        bots.append({
            **{key: dept[key] for key in ("id", "name", "bot", "problem", "role", "color")},
            "mood": mood,
            "job": {"id": current["id"], "title": current["title"], "status": current["status"],
                    "agent": agent, "queued": current.get("queued") or current.get("bridged")} if current else None,
            "done_today": done_today,
            "done_total": len(finished),
            "last": ({"id": finished[-1]["id"], "result": finished[-1].get("result", "")[:300],
                      "status": finished[-1]["status"]} if finished else None),
        })
    per_day = steps_per_day(jobs, today)
    targets = _read(TARGETS, {}) or {}
    milestones = []
    for milestone in targets.get("milestones", []):
        status = milestone_status(milestone, jobs, today)
        days_left = (dt.date.fromisoformat(milestone["due"]) - today).days
        milestones.append({**{k: milestone[k] for k in ("id", "title", "due")}, **status, "days_left": days_left})
    next_due = next((m for m in milestones if not m["done"]), None)
    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "bots": bots,
        "steps_today": per_day.get(today, 0),
        "steps_by_day": [{"day": day.isoformat(), "steps": count} for day, count in per_day.items()],
        "target_per_day": config.get("target_steps_per_day", 8),
        "open_jobs": sum(1 for bot in bots if bot["job"]),
        "milestones": milestones,
        "next_milestone": next_due,
        "not_on_timeline": targets.get("not_on_timeline", ""),
        "squares_baseline": round(squares_baseline(), 4),
    }


def write_state(today=None):
    state = build_state(today)
    STATE_JS.parent.mkdir(exist_ok=True)
    temp = STATE_JS.with_suffix(".tmp")
    temp.write_text("window.LAB_STATE = " + json.dumps(state, ensure_ascii=False) + ";\n", encoding="utf-8")
    temp.replace(STATE_JS)
    return state


def main():
    dry_run = "--dry-run" in sys.argv
    created = ensure_jobs(dry_run=dry_run)
    print(("would queue: " if dry_run else "queued: ") + (", ".join(created) or "nothing (every department busy)"))
    state = build_state() if dry_run else write_state()
    for bot in state["bots"]:
        job = bot["job"]["title"][:70] if bot["job"] else "-"
        print(f"  {bot['bot']:9} {bot['name']:17} {bot['mood']:8} {job}")
    print(f"steps today {state['steps_today']} / target {state['target_per_day']}")
    for milestone in state["milestones"]:
        print(f"  {milestone['id']} {milestone['due']} {'DONE' if milestone['done'] else f'{milestone['progress']:.0%}'}  {milestone['note']}")


if __name__ == "__main__":
    main()
