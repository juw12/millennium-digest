"""Daily progress: show on the dashboard that every day gets real research steps.

The work itself is planned by lab.py (eight departments, one open AI job each, refilled every 30 minutes by
ai_watchdog.py and at 02:00 by research_tick.py). This module only reports what happened:
day_log()           What actually happened per day (finished jobs, research commits, findings, prize notes,
                    digests), classified as research / upkeep / idle.
section_html()      The "Daily progress" card: 14-day strip, streak, steps today vs target, next milestone,
                    recent days.

Usage: python daily_progress.py   print the last 14 days
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
NO_WINDOW = 0x08000000


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


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


def _lab_summary_html():
    """Steps today vs target, open department jobs and the next milestone (from lab.py)."""
    try:
        import lab
        state = lab.build_state()
    except Exception as error:  # the card must render even if the lab config is broken
        return f"<span class='chip bad'>lab unavailable</span> {html.escape(str(error)[:120])}"
    steps, target = state["steps_today"], state["target_per_day"]
    chip = "good" if steps >= target else "warn" if steps else "neutral"
    text = (f"<span class='chip {chip}' id='dp-steps'>{steps} / {target} steps</span> "
            f"{state['open_jobs']} department jobs open")
    upcoming = state.get("next_milestone")
    if upcoming:
        text += (f"<br><span class='sub'>Next milestone: {html.escape(upcoming['title'])} "
                 f"(due {upcoming['due']}, {upcoming['days_left']} days) &middot; {html.escape(upcoming['note'])}</span>")
    return text


# ---------------------------------------------------------------- dashboard card
_COLORS = {"research": "#0d9488", "upkeep": "#f59e0b", "idle": "#e11d48", "pending": "#cbd5e1"}
_LABELS = {"research": "research step", "upkeep": "upkeep only", "idle": "idle", "pending": "today, in progress"}


def section_html(today=None):
    today = today or dt.date.today()
    log = day_log(today)
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
    today_html = _lab_summary_html()
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
  <p class="sub">Eight lab departments each keep one AI research job open; a finished job is replaced within
  30 minutes, so the lab works around the clock (<a href="../lab/index.html">open the 3D lab</a>)</p></div>
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
    for day, events in sorted(day_log().items()):
        state = "research" if events["research"] else "upkeep" if events["upkeep"] else "idle"
        print(f"{day}  {state:8}  {len(events['research'])} research, {len(events['upkeep'])} upkeep")


if __name__ == "__main__":
    main()
