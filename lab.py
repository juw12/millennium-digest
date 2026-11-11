"""Millennium Lab: a department of research bots that never sits idle.

ensure_jobs()   Every department in lab_departments.json keeps its slots filled in research_queue.json: as soon
                as a job is done (or needs review) the next one is queued. Research departments work idea cards
                from the idea bank (lab_ideas.py), best value x odds / cost first; the Director's Office refills
                the bank from the lab notebook. lab_pool.py runs the jobs in parallel and calls this every tick;
                ai_watchdog.py calls it every 30 minutes as well.
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
if str(HERE) not in sys.path:  # lab.py is also imported by scripts that run from other folders
    sys.path.insert(0, str(HERE))
import lab_ideas  # noqa: E402

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
ID_DATE = "%y%m%d"  # six digits: the model gateway shows workers a longer digit run (20261111) as a hash
SERVICE_RULES = (
    " Finish ONE concrete deliverable - not a plan. Never claim a proof or a solution; never submit, post or email "
    "anything. Commit your own files in the research repo. Your result goes in the result file the pool names, "
    "never in research_queue.json."
)
RESEARCH_RULES = (
    " Finish ONE concrete, verifiable step beyond what is already known (a script with a gate and its numbers, or "
    "a derivation checked numerically) - not a plan. Reproducing a published value is only the gate that your code "
    "is right; it is not the result. Empirical/heuristic only: never claim a proof or a solution; never submit, "
    "post or email anything. Commit your own files in the research repo. Your result goes in the result file the "
    "pool names, never in research_queue.json."
)
WANTED = {  # which catalogue problems a department may take
    "erdos": lambda item: item.get("family") == "erdos",
    "prize": lambda item: item.get("computable") and item.get("family") != "erdos" and item["id"] != "KAGGLE-SQ",
}


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


def _catalog():
    return (_read(CATALOG, {}) or {}).get("items", [])


def _done_topics(jobs):
    """Catalogue problems the lab is finished with: a job has a result, or two jobs failed on it."""
    done, failed = set(), {}
    for job in jobs:
        topic = job.get("topic_id")
        if topic and job.get("status") in ("done", "needs_review"):
            done.add(topic)
        elif topic and job.get("status") == "failed":
            failed[topic] = failed.get(topic, 0) + 1
    return done | {topic for topic, count in failed.items() if count >= 2}


def _taken_topics(jobs):
    """Problems an open job works on, or may still pick from its candidate list."""
    taken = set()
    for job in jobs:
        if job.get("status") in OPEN:
            taken.update(job.get("candidates") or [])
            if job.get("topic_id"):
                taken.add(job["topic_id"])
    return taken


def _open_topics(jobs, want, realistic_first=False):
    """Catalogue problems nobody has worked yet, biggest reward first. realistic_first puts rewards up to
    50,000 USD ahead of the headline prizes, which a one-hour job cannot move."""
    dossiers, done, taken = _done_dossiers(), _done_topics(jobs), _taken_topics(jobs)
    pool = [item for item in _catalog() if want(item) and _slug(item["id"]) not in dossiers
            and item["id"] not in done and item["id"] not in taken]
    reward = lambda item: item.get("reward_usd") or 0  # noqa: E731
    pool.sort(key=lambda item: (realistic_first and reward(item) > 50000, -reward(item), item["id"]))
    return pool


def _next_catalog_item(jobs, want, realistic_first=False):
    pool = _open_topics(jobs, want, realistic_first)
    return pool[0] if pool else None


def _slate(pool, size=6):
    """Candidate problems for a programme card: computable ones first, then a spread over the rest of the list.
    Problems that already have an idea card of their own are left to that card."""
    carded = {card.get("topic_id") for card in lab_ideas.ranked(statuses=("open", "claimed", "won"))}
    pool = [item for item in pool if item["id"] not in carded]
    chosen = [item for item in pool if item.get("computable")][:size]
    rest = [item for item in pool if item not in chosen]
    need = size - len(chosen)
    if need <= 0 or len(rest) <= need:
        return chosen + rest[:max(0, need)]
    if need == 1:
        return chosen + rest[:1]
    return chosen + [rest[round(index * (len(rest) - 1) / (need - 1))] for index in range(need)]


def _topic_text(item):
    slug = _slug(item["id"])
    return (f" Problem: {item['id']} {item['title']} ({item.get('reward', '')}). Statement: "
            f"{str(item.get('statement', ''))[:700]} Source: {item['url']} Dossier: prize_work/{slug}.md (exactly "
            f"this name); scripts in prize_work/{slug}/.")


def _next_card(dept_id, jobs):
    """(card, wait) - see lab_ideas.next_card. wait: only ideas the Director has not reviewed are left and one of
    the department's jobs already works on one."""
    if dept_id not in lab_ideas.CARD_DEPTS:
        return None, False
    open_jobs = [job for job in jobs if job.get("status") in OPEN]
    held = {job.get("idea_id") for job in open_jobs}
    working = {job.get("idea_id") for job in open_jobs if job_dept(job) == dept_id}
    return lab_ideas.next_card(dept_id, held, _taken_topics(jobs), working)


def _waiting_for(dept_id, jobs):
    """Finished jobs the Referee ("review") or the Review Panel ("panel") still has to check, most important
    last: claimed progress and needs_review results come after plain reproductions. A job that an open job of the
    same department already reviews is left out, so two reviewers never get the same result."""
    if dept_id == "review":
        skip, flag = ("review", "panel", "director"), "reviewed"  # a simulated review is not a result to re-run
    else:
        skip, flag = ("panel", "review", "tools", "scout", "director"), "panel_reviewed"
    busy = {job_id for job in jobs if job.get("dept") == dept_id and job.get("status") in OPEN
            for job_id in job.get("reviews") or []}
    waiting = [job for job in jobs if job.get("dept") and job.get("dept") not in skip
               and job.get("status") in ("done", "needs_review") and not job.get(flag) and job["id"] not in busy]
    waiting.sort(key=lambda job: (job.get("novelty") in lab_ideas.PROGRESS, job.get("status") == "needs_review"))
    return waiting


def _review_line(job, size):
    claim = ""
    if job.get("novelty"):
        word = job["novelty"]
        if job.get("claimed_novelty"):  # the lab or the Referee lowered the class: show both words
            word = f"{job['claimed_novelty']}, recorded as {job['novelty']}"
        claim = f" [claims {word}; baseline: {str(job.get('baseline') or 'none given')[:160]}]"
    return f"{job['id']}{claim} - {str(job.get('result', ''))[:size]}"


def squares_baseline():
    """Trivial packing: n unit squares on a ceil(sqrt n) grid. Score = sum (L / sqrt n - 1), n = 1..1000."""
    return sum(math.ceil(math.sqrt(n)) / math.sqrt(n) - 1 for n in range(1, 1001))


def _last_result(jobs, dept):
    finished = [job for job in jobs if job_dept(job) == dept and job.get("status") in ("done", "needs_review")]
    return finished[-1].get("result", "") if finished else ""


def plan_job(dept, jobs, today, number, config=None):
    """Title, instructions and fields for the department's next job (None = nothing to do now). A research
    department works its best open idea card; without one it falls back to its standing brief. Only one of its
    jobs at a time may work an idea the Director has not reviewed."""
    dept_id = dept["id"]
    job_id = f"lab_{dept_id}_{today:{ID_DATE}}_{number}"
    card, wait = _next_card(dept_id, jobs)
    if wait:
        return None  # an empty slot beats a second job on an idea the Director has not reviewed
    previous = "" if card else _last_result(jobs, dept_id)
    carry = f" Your previous result: {previous[:400]}" if previous else ""
    extra = {"idea_id": card["id"]} if card else {}
    title = f"{dept['name']}: {card['title']}" if card else None
    if dept_id in ("bsd", "rh"):
        roadmap = _read(ROADMAP, {}) or {}
        title = title or f"{dept['name']}: next step on {dept['problem']}"
        topic = f" Roadmap: {roadmap.get(dept_id.upper(), {}).get('next', '')}{carry}"
    elif dept_id in WANTED:  # erdos, prize: one catalogue problem per job
        want, realistic = WANTED[dept_id], dept_id == "prize"
        item = None
        if card and card.get("topic_id"):
            item = next((entry for entry in _catalog() if entry["id"] == card["topic_id"]), None)
        if card and item is None and card.get("repeat"):  # a programme: the worker picks its target from a slate
            slate = _slate(_open_topics(jobs, want, realistic))
            if not slate:
                return None
            topic = (" Candidate problems - pick the ONE where this idea has the best chance and report its id as "
                     "'topic_id': " + " | ".join(
                         f"{entry['id']} {entry['title']} ({entry.get('reward', '')}) {entry['url']}; dossier "
                         f"prize_work/{_slug(entry['id'])}.md; {str(entry.get('statement', ''))[:150]}"
                         for entry in slate))
            extra["candidates"] = [entry["id"] for entry in slate]
        elif card and item is None:  # the card names its own target
            topic = ""
        else:
            item = item or _next_catalog_item(jobs, want, realistic)
            if not item:
                return None
            title = title or f"{dept['name']}: {item['title']} ({item['reward']})"
            topic = _topic_text(item)
            extra.update({"topic_id": item["id"], "topic_url": item["url"]})
    elif dept_id == "squares":
        best = _read(SQUARES_SCORE, {}) or {}
        state = f"current best valid score {best['score']:.4f}" if best.get("valid") else "no valid solver yet"
        title = title or f"{dept['name']}: improve the Squares in Squares score ({state})"
        topic = f" Trivial-grid baseline score: {squares_baseline():.4f}. Status: {state}.{carry}"
    elif dept_id == "director":
        departments = (config or {}).get("departments", [])
        earlier = [job for job in jobs if job.get("dept") == "director"]
        target, open_cards = lab_ideas.director_target(config or {}, earlier[-1].get("focus_dept") if earlier else None)
        if not target:
            return None
        name = next((entry["name"] for entry in departments if entry["id"] == target), target)
        title = f"{dept['name']}: new ideas for {name} ({open_cards} open cards)"
        topic = f" Focus department: {target} ({name}). Notes file: director_notes/{job_id}.md."
        extra = {"focus_dept": target}
    elif dept_id == "scout":
        digests = sorted((HERE / "digests").glob("digest_*.md"))
        latest = digests[-1].name if digests else "none yet"
        title = f"{dept['name']}: brief the newest papers on NS, Yang-Mills, P vs NP, Hodge ({latest})"
        topic = f" Latest digest: automation/digests/{latest}. Note file: scout_notes/{today.isoformat()}.md."
    elif dept_id == "review":
        waiting = _waiting_for("review", jobs)[-3:]
        if not waiting:
            return None
        title = f"{dept['name']}: review {', '.join(job['id'] for job in waiting)}"
        topic = (" Jobs to review: " + "; ".join(_review_line(job, 200) for job in waiting)
                 + " List them in 'reviewed_jobs' of your result file.")
        extra = {"reviews": [job["id"] for job in waiting]}
    elif dept_id == "panel":
        waiting = _waiting_for("panel", jobs)[-2:]
        if not waiting:
            return None
        title = f"{dept['name']}: simulated expert review of {', '.join(job['id'] for job in waiting)}"
        topic = (" Results to review: " + "; ".join(_review_line(job, 240) for job in waiting)
                 + " Write review_panel/<job id>.md for each and list them in 'reviewed_jobs' of your result file.")
        extra = {"reviews": [job["id"] for job in waiting]}
    elif dept_id == "tools":
        title = f"{dept['name']}: {dept.get('task', 'keep the lab tooling working')}"
        topic = carry
    elif dept_id == "writer":
        title = f"{dept['name']}: advance the first-month package (BSD note, RH note, prize index)"
        topic = f" Package status: {first_month_status()['text']}.{carry}"
    else:
        title = f"{dept['name']}: next step"
        topic = carry
    return {
        "id": job_id,
        "title": title,
        "project": dept_id,
        "dept": dept_id,
        "status": "needs_ai",
        "not_before": today.isoformat(),
        "queued": dt.datetime.now().isoformat(timespec="seconds"),
        "why": dept["brief"] + topic + (RESEARCH_RULES if dept_id in lab_ideas.CARD_DEPTS else SERVICE_RULES),
        **extra,
    }


def ensure_jobs(today=None, dry_run=False):
    """Queue the next job for every department that has a free slot. Returns the new job ids."""
    today = today or dt.date.today()
    config = _read(DEPARTMENTS, {}) or {}
    queue = _read(QUEUE)
    if not queue or not config:
        return []
    jobs = queue.setdefault("jobs", [])
    if not dry_run:
        try:
            lab_ideas.reconcile(jobs)  # cards whose job is gone go back to the bank
        except (OSError, ValueError):
            pass  # an unreadable idea bank never stops the lab: departments fall back to their standing briefs
    open_lab = [job for job in jobs if job_dept(job) and job.get("status") in OPEN]
    room = config.get("max_open", 8) - len(open_lab)
    created = []
    now = dt.datetime.now()
    for dept in config.get("departments", []):
        if dept.get("paused"):
            continue
        mine = [job for job in jobs if job_dept(job) == dept["id"]]
        open_mine = sum(1 for job in mine if job.get("status") in OPEN)
        last_queued = max((job.get("queued") or job.get("bridged") or "" for job in mine), default="")
        if last_queued and dept.get("every_hours"):
            if (now - dt.datetime.fromisoformat(last_queued[:19])).total_seconds() < dept["every_hours"] * 3600:
                continue
        while room > 0 and open_mine < dept.get("slots", 1):
            prefix = f"lab_{dept['id']}_{today:{ID_DATE}}_"
            number = 1 + sum(1 for job in jobs if job.get("id", "").startswith(prefix))
            job = plan_job(dept, jobs, today, number, config)
            if not job:
                break
            created.append(job)
            jobs.append(job)
            room -= 1
            open_mine += 1
    if created and not dry_run:
        fresh = _read(QUEUE) or queue  # add to the file as it is now: another script may have written meanwhile
        known = {job.get("id") for job in fresh.setdefault("jobs", [])}
        created = [job for job in created if job["id"] not in known]
        fresh["jobs"].extend(created)
        temp = QUEUE.with_name(QUEUE.name + ".lab.tmp")
        temp.write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(QUEUE)
        for job in created:
            try:
                if job.get("idea_id"):
                    lab_ideas.claim(job["idea_id"], job["id"])
            except (OSError, ValueError):
                pass  # reconcile() and the open-job check in _next_card keep the card from being issued twice
    return [job["id"] for job in created]


# ------------------------------------------------------------------ live state for the lab page
def _agent_jobs():
    data = _read(AGENT_JOBS, []) or []
    return data if isinstance(data, list) else []


def _pool_status(job_id):
    entry = (_read(HERE / "logs" / "pool_state.json", {}) or {}).get(job_id)
    if not entry or not entry.get("pid"):
        return None
    try:
        import lab_pool
        running = lab_pool.alive(entry["pid"])
    except Exception:
        running = False
    return {"status": "running" if running else "queued", "rounds": len(entry.get("attempts", [])) + 1,
            "updated": entry.get("started"), "engine": entry.get("engine")}


def _agent_status(job_id, agent_jobs):
    pooled = _pool_status(job_id)
    if pooled:
        return pooled
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
        open_jobs = [job for job in mine if job.get("status") in OPEN]
        running = [job for job in open_jobs if (_pool_status(job["id"]) or {}).get("status") == "running"]
        current = (running or open_jobs or [None])[-1]
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
        "ideas": lab_ideas.summary(),
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
    print(f"idea cards {state['ideas'].get('cards', {})}, notebook lines {state['ideas'].get('notes', 0)}")
    for milestone in state["milestones"]:
        print(f"  {milestone['id']} {milestone['due']} {'DONE' if milestone['done'] else f'{milestone['progress']:.0%}'}  {milestone['note']}")


if __name__ == "__main__":
    main()
