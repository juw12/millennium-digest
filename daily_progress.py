"""Daily progress: make sure every day gets at least one real research step, and show it on the dashboard.

ensure_daily_job()  Adds one "needs_ai" job per day to research_queue.json (id daily_YYYYMMDD), rotating
                    BSD -> RH -> prize problem of the day. ai_bridge.py hands it to the always-on agent and
                    ai_watchdog.py falls back to Copilot, so a day is never idle just because the computer
                    queue ran out. Only one daily job is open at a time (a stuck one is escalated by the
                    watchdog, not buried under new ones).
day_log()           What actually happened per day (finished jobs, research commits, findings, prize notes,
                    digests), classified as research / upkeep / idle.
section_html()      The "Daily progress" card: 14-day strip, streak, today's step, recent days.

Called by research_tick.py (02:00) and ai_watchdog.py (every 30 min, so a missed 02:00 run is caught up).
Usage: python daily_progress.py            ensure today's job and print the last 14 days
       python daily_progress.py --dry-run  print what would be added, change nothing
"""
import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUEUE = ROOT / "research_queue.json"
CATALOG = HERE / "prize_catalog.json"
ROADMAP = HERE / "roadmap.json"
PRIZE_WORK = ROOT / "prize_work"
FINDINGS = [ROOT / "bsd_project" / "FINDINGS.md", ROOT / "rh_project" / "FINDINGS.md"]
DIGESTS = HERE / "digests"
DAYS_SHOWN = 14
ROTATION = ["bsd", "rh", "prize"]  # by date ordinal, so the focus is predictable and evenly shared
NO_WINDOW = 0x08000000

RULES = (
    "Finish ONE concrete, verifiable step today (a script with a gate and its numbers, or a derivation "
    "checked numerically). Empirical/heuristic only: never claim a proof or a solution, never submit or "
    "publish anything. Record the result in this job's 'result' and set status done (needs_review if it "
    "looks like more than it is). Commit in the research repo."
)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def prize_of_the_day(today):
    """Next computer-attackable prize problem that has no note in prize_work/ yet (stable order by id)."""
    catalog = _read(CATALOG, {}) or {}
    candidates = sorted((item for item in catalog.get("items", []) if item.get("computable")), key=lambda i: i["id"])
    if not candidates:
        return None
    done = {path.stem for path in PRIZE_WORK.glob("*.md")} if PRIZE_WORK.exists() else set()
    fresh = [item for item in candidates if _slug(item["id"]) not in done] or candidates
    return fresh[today.toordinal() % len(fresh)]


def plan_for(today):
    """(focus, title, why, extra fields) for the given day."""
    focus = ROTATION[today.toordinal() % len(ROTATION)]
    roadmap = _read(ROADMAP, {}) or {}
    if focus == "prize":
        prize = prize_of_the_day(today)
        if prize:
            slug = _slug(prize["id"])
            title = f"Prize problem of the day: {prize['id']} {prize['title']} ({prize['reward']})"
            why = (
                f"Source: {prize['url']}. Read it and the work it cites, then write prize_work/{slug}.md with: exact "
                "statement and prize terms, best known result or record, why it is hard, and one small computation "
                f"you ran yourself (script in prize_work/{slug}/, with a gate that reproduces a known value). "
                + RULES
            )
            return focus, title, why, {"prize_id": prize["id"], "prize_url": prize["url"]}
        focus = "bsd"  # no catalogue yet: fall back to a research thread
    if focus == "rh":
        title = "RH daily step: next item on the RH roadmap"
        why = (
            f"Roadmap: {roadmap.get('RH', {}).get('next', '')} Read rh_project/FINDINGS.md first. Good candidates: "
            "compare the 9-height BBLM amplitudes with the Bogomolny et al. 2006 second-order term; widen the "
            "exploratory k ~ 1.1 fit. " + RULES
        )
        return focus, title, why, {"project_hint": "rh"}
    title = "BSD daily step: next item on the BSD roadmap"
    why = (
        f"Roadmap: {roadmap.get('BSD', {}).get('next', '')} Read bsd_project/FINDINGS.md first. Good candidates: "
        "derive a_c and c_c as explicit local densities (q-adic Hurwitz averages); compare with Sawin-Sutherland "
        "arXiv:2504.12295. " + RULES
    )
    return focus, title, why, {"project_hint": "bsd"}


def ensure_daily_job(today=None, dry_run=False):
    """Adds today's daily job if none is open. Returns the job id that is open for today, or None."""
    today = today or dt.date.today()
    queue = _read(QUEUE)
    if not queue:
        return None
    jobs = queue.setdefault("jobs", [])
    job_id = f"daily_{today:%Y%m%d}"
    if any(job["id"] == job_id for job in jobs):
        return job_id
    open_daily = [job for job in jobs if job["id"].startswith("daily_") and job.get("status") in ("needs_ai", "running")]
    if open_daily:
        return open_daily[-1]["id"]  # yesterday's step is still being worked on; the watchdog escalates it
    focus, title, why, extra = plan_for(today)
    job = {
        "id": job_id,
        "title": f"{title} [{today.isoformat()}]",
        "project": "prize" if focus == "prize" else focus,
        "status": "needs_ai",
        "daily": True,
        "focus": focus,
        "not_before": today.isoformat(),
        "why": why,
        **extra,
    }
    if dry_run:
        print(json.dumps(job, indent=2, ensure_ascii=False))
        return job_id
    jobs.append(job)
    QUEUE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return job_id


# ---------------------------------------------------------------- what happened per day
def _git_days(repo, since):
    try:
        output = subprocess.run(
            ["git", "-C", str(repo), "log", f"--since={since.isoformat()}", "--date=short", "--pretty=%ad|%s"],
            capture_output=True, text=True, encoding="utf-8", timeout=30, creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split("|", 1) for line in output.splitlines() if "|" in line]


def day_log(today=None, days=DAYS_SHOWN):
    """{date: {"research": [...], "upkeep": [...]}} for the last `days` days (today included)."""
    today = today or dt.date.today()
    first = today - dt.timedelta(days=days - 1)
    log = {first + dt.timedelta(days=offset): {"research": [], "upkeep": []} for offset in range(days)}

    def add(day, kind, text):
        if day in log and text not in log[day][kind]:
            log[day][kind].append(text)

    for job in (_read(QUEUE, {}) or {}).get("jobs", []):
        if job.get("status") == "done" and job.get("finished"):
            add(dt.date.fromisoformat(job["finished"][:10]), "research", f"Job done: {job['title']}")
    for day, subject in _git_days(ROOT, first):
        if "research_tick" in subject and "failed" in subject:
            continue
        add(dt.date.fromisoformat(day), "research", subject)
    for path in FINDINGS:
        if path.exists():
            for day in re.findall(r"^- (\d{4}-\d{2}-\d{2}):", path.read_text(encoding="utf-8"), flags=re.M):
                add(dt.date.fromisoformat(day), "research", f"New line in {path.parent.name}/FINDINGS.md")
    if PRIZE_WORK.exists():
        for note in PRIZE_WORK.glob("*.md"):
            add(dt.date.fromtimestamp(note.stat().st_mtime), "research", f"Prize note: {note.stem}")
    for digest in DIGESTS.glob("digest_*.md") if DIGESTS.exists() else []:
        found = re.search(r"(\d{4}-\d{2}-\d{2})", digest.name)
        if found:
            add(dt.date.fromisoformat(found.group(1)), "upkeep", "Weekly arXiv digest")
    for day, subject in _git_days(HERE, first):
        add(dt.date.fromisoformat(day), "upkeep", f"Tool: {subject}")
    return log


def streak(log, today):
    """Consecutive days with research up to today (today counts only once it has research)."""
    count, day = 0, today
    if not log.get(today, {}).get("research"):
        day = today - dt.timedelta(days=1)
    while log.get(day, {}).get("research"):
        count += 1
        day -= dt.timedelta(days=1)
    return count


# ---------------------------------------------------------------- dashboard card
_COLORS = {"research": "#0d9488", "upkeep": "#f59e0b", "idle": "#e11d48", "pending": "#cbd5e1"}
_LABELS = {"research": "research step", "upkeep": "upkeep only", "idle": "idle", "pending": "today, in progress"}


def section_html(today=None):
    today = today or dt.date.today()
    log = day_log(today)
    queue = _read(QUEUE, {}) or {}
    today_job = next((job for job in queue.get("jobs", []) if job["id"] == f"daily_{today:%Y%m%d}"), None)
    open_daily = [job for job in queue.get("jobs", []) if job["id"].startswith("daily_") and job.get("status") != "done"]
    cells, research_days = [], 0
    for day, events in sorted(log.items()):
        state = "research" if events["research"] else "upkeep" if events["upkeep"] else "idle"
        if day == today and state != "research":
            state = "pending"
        research_days += state == "research"
        tip = f"{day:%a %d %b}: {_LABELS[state]}"
        if events["research"] or events["upkeep"]:
            tip += " - " + "; ".join((events["research"] + events["upkeep"])[:4])
        cells.append(
            f"<div class='dp-cell' data-state='{state}' title='{html.escape(tip, quote=True)}'>"
            f"<span class='dp-box' style='background:{_COLORS[state]}'></span>"
            f"<span class='dp-day'>{day:%d}</span></div>"
        )
    if today_job:
        status = today_job.get("status", "needs_ai")
        chip = {"done": "good", "needs_review": "warn"}.get(status, "neutral")
        today_html = (
            f"<span class='chip {chip}'>{html.escape(status.replace('_', ' '))}</span> "
            f"{html.escape(today_job['title'])}"
            + (f"<br><span class='sub'>{html.escape(today_job.get('result', ''))}</span>" if today_job.get("result") else "")
        )
    elif open_daily:
        today_html = (f"<span class='chip warn'>carried over</span> {html.escape(open_daily[-1]['title'])} "
                      "<span class='sub'>(still open; today's step starts when it is done)</span>")
    else:
        focus, title, _, _ = plan_for(today)
        today_html = f"<span class='chip neutral'>planned</span> {html.escape(title)}"
    recent = []
    for day, events in sorted(log.items(), reverse=True)[:7]:
        items = events["research"][:3] or events["upkeep"][:2]
        text = "; ".join(items) if items else ("in progress" if day == today else "nothing recorded")
        recent.append(f"<li><b>{day:%a %d %b}</b> &middot; {html.escape(text[:260])}</li>")
    legend = " &middot; ".join(
        f"<span><span class='dp-box' style='background:{_COLORS[key]};display:inline-block'></span> {label}</span>"
        for key, label in _LABELS.items()
    )
    return f"""
<section class="card anchor" id="daily"><div class="card-head"><div><h2>Daily progress</h2>
  <p class="sub">Every day gets one research step: the nightly computer job if the queue has one, otherwise an AI step
  rotating BSD &rarr; RH &rarr; prize problem of the day</p></div>
  <span class="chip {'good' if streak(log, today) else 'bad'}" id="dp-streak">{streak(log, today)}-day streak</span></div>
  <style>
   .dp-strip {{ display:grid; grid-template-columns:repeat({DAYS_SHOWN}, minmax(0,1fr)); gap:4px; margin:6px 0 4px; }}
   .dp-cell {{ display:flex; flex-direction:column; align-items:center; gap:2px; }}
   .dp-box {{ width:100%; max-width:30px; height:22px; border-radius:5px; }}
   .dp-legend .dp-box {{ width:11px; height:11px; border-radius:3px; vertical-align:-1px; }}
   .dp-day {{ font-size:10.5px; color:var(--muted); }}
  </style>
  <div class="dp-strip" aria-label="Last {DAYS_SHOWN} days">{''.join(cells)}</div>
  <p class="note dp-legend">{legend} &middot; {research_days} of {DAYS_SHOWN} days with a research step</p>
  <p style="margin:10px 0 4px"><b>Today:</b> {today_html}</p>
  <ul class="plain" style="margin-top:8px">{''.join(recent)}</ul>
</section>"""


def main():
    dry_run = "--dry-run" in sys.argv
    job_id = ensure_daily_job(dry_run=dry_run)
    print(f"today's daily job: {job_id}")
    for day, events in sorted(day_log().items()):
        state = "research" if events["research"] else "upkeep" if events["upkeep"] else "idle"
        print(f"{day}  {state:8}  {len(events['research'])} research, {len(events['upkeep'])} upkeep")


if __name__ == "__main__":
    main()
