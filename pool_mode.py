"""Lab pool mode (Auto / Light / Full) and the dashboard card that shows and switches it.

The pool (lab_pool.py) runs at most pool.workers jobs at once (lab_departments.json). In Auto mode it drops to
pool.work_hours.workers during pool.work_hours (weekdays 08:00-17:00 by default) so the PC stays usable.

A dashboard button can force Light or Full. A forced mode lasts until the next Auto switch (e.g. Full pressed at
10:00 on a weekday holds until 17:00), then Auto takes over again, so the lab can never get stuck in a mode.
The choice lives in pool_mode.json: {"mode": "light" | "full", "until": ISO time or null, "set_at": ISO time}.

Expected jobs = workers x hours x the lab's measured throughput (finished jobs per worker-hour over the last
72 h of logs/pool.log). Run times barely depend on how many jobs run at once (the work mostly waits on the AI
model), so output scales with the number of workers.
"""
import ctypes
import datetime as dt
import html
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPARTMENTS = HERE / "lab_departments.json"
MODE_FILE = HERE / "pool_mode.json"
STATE = HERE / "logs" / "pool_state.json"
POOL_LOG = HERE / "logs" / "pool.log"
QUEUE = HERE.parent / "research_queue.json"
POOL_TASK = "Millennium lab pool"

MODES = ("auto", "light", "full")
RATE_WINDOW_H = 72
FALLBACK_RATE = 1.0  # finished jobs per worker-hour when the log is too short to measure
STEP_MIN = 10  # projection step = the pool's tick

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(.*)$")
START = re.compile(r"^start (\S+) on \w+")
END = re.compile(r"^(\S+): (?:opencode|copilot) (exited|timeout|stopped waiting|no connection|copilot )")
VERDICT = re.compile(r"^(\S+): idea \S+ (worked|partial|failed|blocked)$")


# ------------------------------------------------------------------ config and schedule

def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def pool_config():
    return (_read_json(DEPARTMENTS, {}) or {}).get("pool", {})


def full_workers(pool):
    return int(pool.get("workers", 3))


def light_workers(pool):
    work_hours = pool.get("work_hours") or {}
    return min(full_workers(pool), int(work_hours.get("workers", 3)))


def in_work_hours(work_hours, when):
    """True inside work_hours: {"days": [0..6, Monday = 0], "start": "HH:MM", "end": "HH:MM"} (local time)."""
    if not work_hours:
        return False
    days = work_hours.get("days", [0, 1, 2, 3, 4])
    start = dt.datetime.strptime(work_hours.get("start", "08:00"), "%H:%M").time()
    end = dt.datetime.strptime(work_hours.get("end", "17:00"), "%H:%M").time()
    return when.weekday() in days and start <= when.time() < end


def next_switch(pool, now):
    """The next time Auto changes between Light and Full (None without a schedule)."""
    work_hours = pool.get("work_hours")
    if not work_hours:
        return None
    current = in_work_hours(work_hours, now)
    candidates = []
    for offset in range(9):
        day = (now + dt.timedelta(days=offset)).date()
        for key, default in (("start", "08:00"), ("end", "17:00")):
            moment = dt.datetime.combine(day, dt.datetime.strptime(work_hours.get(key, default), "%H:%M").time())
            if moment > now:
                candidates.append(moment)
    for moment in sorted(candidates):
        if in_work_hours(work_hours, moment) != current:
            return moment
    return None


def load_override(now=None):
    """The forced mode still in force, or None (Auto)."""
    now = now or dt.datetime.now()
    data = _read_json(MODE_FILE, {}) or {}
    mode = data.get("mode")
    if mode not in ("light", "full"):
        return None
    until = data.get("until")
    if until:
        try:
            if dt.datetime.fromisoformat(until) <= now:
                return None
        except ValueError:
            return None
    return data


def workers_at(pool, when, override=None, forced=None):
    """(workers, "light" | "full", source) at `when`. forced = "light"/"full" ignores schedule and override."""
    light, full = light_workers(pool), full_workers(pool)
    if forced:
        return (light, "light", "forced") if forced == "light" else (full, "full", "forced")
    if override:
        until = override.get("until")
        if not until or when < dt.datetime.fromisoformat(until):
            mode = override["mode"]
            return (light if mode == "light" else full), mode, "manual"
    if in_work_hours(pool.get("work_hours"), when):
        return light, "light", "schedule"
    return full, "full", "schedule"


def scheduled_workers(pool, now=None):
    """Worker limit lab_pool.py uses right now (schedule plus any dashboard override)."""
    now = now or dt.datetime.now()
    return workers_at(pool, now, load_override(now))[0]


def set_mode(mode, now=None):
    """Save the dashboard choice. Returns a one-line description of what happens."""
    mode = (mode or "").lower()
    if mode not in MODES:
        raise ValueError(f"unknown pool mode: {mode}")
    now = now or dt.datetime.now()
    pool = pool_config()
    if mode == "auto":
        data = {"mode": "auto", "until": None, "set_at": now.isoformat(timespec="seconds")}
        summary = "Lab pool back on the Auto schedule"
    else:
        until = next_switch(pool, now)
        data = {"mode": mode, "until": until.isoformat(timespec="seconds") if until else None,
                "set_at": now.isoformat(timespec="seconds")}
        workers = light_workers(pool) if mode == "light" else full_workers(pool)
        summary = f"Lab pool set to {mode.title()} ({workers} jobs)" + (
            f" until {until:%a %H:%M}, then Auto" if until else "")
    temp = MODE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temp.replace(MODE_FILE)
    return summary


def apply_now():
    """Run one pool tick now (the scheduled task ignores the request if a tick is already running)."""
    try:
        subprocess.Popen(["schtasks", "/Run", "/TN", POOL_TASK], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ live data

def _alive(pid):
    if not pid:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def running_jobs():
    """[{id, dept, engine, started (datetime), title}] for live workers."""
    state = _read_json(STATE, {}) or {}
    titles = {job.get("id"): job.get("title", "") for job in (_read_json(QUEUE, {}) or {}).get("jobs", [])}
    jobs = []
    for job_id, entry in state.items():
        if not (entry.get("pid") and _alive(entry["pid"])):
            continue
        try:
            started = dt.datetime.fromisoformat(entry.get("started", ""))
        except ValueError:
            started = None
        parts = job_id.split("_")
        jobs.append({
            "id": job_id,
            "dept": parts[1] if len(parts) > 2 else "",
            "engine": entry.get("engine", ""),
            "started": started,
            "title": titles.get(job_id, ""),
        })
    return sorted(jobs, key=lambda job: job["started"] or dt.datetime.max)


def lab_memory_mb():
    """Working set of the OpenCode worker processes (MB), or None if tasklist fails."""
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq opencode.exe"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    total_kb = 0
    for line in result.stdout.splitlines():
        fields = [field.strip('"') for field in line.split('","')]
        if len(fields) >= 5 and fields[0].lower().startswith("opencode"):
            total_kb += int(re.sub(r"[^\d]", "", fields[4]) or 0)
    return total_kb / 1024


def log_stats(now=None):
    """Finished jobs today / this week, verdicts today, and the throughput (jobs per worker-hour)."""
    now = now or dt.datetime.now()
    try:
        lines = POOL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    week_start = dt.datetime.combine(now.date() - dt.timedelta(days=now.weekday()), dt.time())
    window_start = now - dt.timedelta(hours=RATE_WINDOW_H)
    running = set()
    last_time = None
    worker_hours = 0.0
    done_window = done_today = done_week = 0
    verdicts_today = {"worked": 0, "partial": 0, "failed": 0, "blocked": 0}
    for raw in lines:
        match = LINE.match(raw)
        if not match:
            continue
        moment = dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
        message = match.group(2)
        if last_time and moment > window_start:
            span_start = max(last_time, window_start)
            worker_hours += len(running) * (moment - span_start).total_seconds() / 3600
        last_time = moment
        if started := START.match(message):
            running.add(started.group(1))
        elif ended := END.match(message):
            running.discard(ended.group(1))
            if ended.group(2) == "exited":
                done_window += moment >= window_start
                done_today += moment.date() == now.date()
                done_week += moment >= week_start
        elif verdict := VERDICT.match(message):
            if moment.date() == now.date():
                verdicts_today[verdict.group(2)] += 1
    if last_time:
        worker_hours += len(running) * max(0.0, (now - max(last_time, window_start)).total_seconds()) / 3600
    rate = done_window / worker_hours if worker_hours >= 12 and done_window >= 20 else FALLBACK_RATE
    return {
        "rate": rate,
        "measured": worker_hours >= 12 and done_window >= 20,
        "done_today": done_today,
        "done_week": done_week,
        "verdicts_today": verdicts_today,
    }


def expected_jobs(pool, start, end, rate, override=None, forced=None):
    """Jobs expected to finish between start and end at the given worker plan."""
    total = 0.0
    moment = start
    step = dt.timedelta(minutes=STEP_MIN)
    while moment < end:
        chunk = min(step, end - moment)
        workers = workers_at(pool, moment, override, forced)[0]
        total += workers * rate * chunk.total_seconds() / 3600
        moment += chunk
    return total


def snapshot(now=None):
    """Everything the card shows, as plain data (also handy for tests)."""
    now = now or dt.datetime.now()
    pool = pool_config()
    override = load_override(now)
    workers, mode, source = workers_at(pool, now, override)
    stats = log_stats(now)
    rate = stats["rate"]
    midnight = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time())
    week_end = dt.datetime.combine(now.date() - dt.timedelta(days=now.weekday()) + dt.timedelta(days=7), dt.time())
    plans = {"current": dict(override=override), "light": dict(forced="light"), "full": dict(forced="full")}
    projection = {}
    for name, plan in plans.items():
        rest_today = expected_jobs(pool, now, midnight, rate, **plan)
        rest_week = expected_jobs(pool, now, week_end, rate, **plan)
        projection[name] = {
            "rest_today": rest_today,
            "today": stats["done_today"] + rest_today,
            "week": stats["done_week"] + rest_week,
        }
    hours = []
    for offset in range(24):
        moment = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=offset)
        hour_workers, hour_mode, _ = workers_at(pool, max(moment, now), override)
        hours.append((moment, hour_workers, hour_mode))
    return {
        "now": now,
        "pool": pool,
        "workers": workers,
        "mode": mode,
        "source": source,
        "override": override,
        "next_switch": next_switch(pool, now),
        "light": light_workers(pool),
        "full": full_workers(pool),
        "stats": stats,
        "projection": projection,
        "hours": hours,
        "jobs": running_jobs(),
        "memory_mb": lab_memory_mb(),
        "timeout": int(pool.get("timeout_minutes", 100)),
    }


# ------------------------------------------------------------------ dashboard card

def header_chip(data=None):
    data = data or snapshot()
    tone = "good" if data["mode"] == "light" else "neutral"
    label = f"Lab {data['mode'].title()} &middot; {len(data['jobs'])}/{data['workers']}"
    return f'<a class="chip {tone}" href="#pool" title="Lab pool mode and live jobs">{label}</a>'


def _hours_svg(hours, full):
    width, height, bar_gap = 720, 86, 3
    bar_width = width / len(hours)
    bars = []
    for index, (moment, workers, mode) in enumerate(hours):
        bar_height = 0 if not full else (height - 22) * workers / full
        x = index * bar_width
        color = "#f59e0b" if mode == "light" else "#0f766e"
        bars.append(
            f'<rect x="{x + bar_gap / 2:.1f}" y="{height - 16 - bar_height:.1f}" width="{bar_width - bar_gap:.1f}" '
            f'height="{bar_height:.1f}" rx="3" fill="{color}"><title>{moment:%a %H}:00 - {workers} jobs '
            f'({mode})</title></rect>'
        )
        if index % 3 == 0:
            bars.append(f'<text x="{x + bar_width / 2:.1f}" y="{height - 3}" font-size="10" text-anchor="middle" '
                        f'fill="#64748b">{moment:%H}h</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" '
            f'aria-label="Job slots for the next 24 hours">{"".join(bars)}</svg>')


def _mode_button(mode, label, active, hint):
    style = "btn" if active else "btn ghost"
    return f'<a class="{style}" href="millennium://poolmode/{mode}" title="{html.escape(hint)}">{label}</a>'


def section_html(now=None, data=None):
    try:
        data = data or snapshot(now)
    except Exception as error:  # the card must never break the page
        return (f'<section class="card anchor" id="pool"><div class="card-head"><div><h2>Lab pool</h2></div></div>'
                f'<p class="empty">Pool data unavailable: {html.escape(repr(error))}</p></section>')
    now = data["now"]
    override = data["override"]
    if override:
        until = override.get("until")
        source_text = (f"Manual {override['mode'].title()} until "
                       f"{dt.datetime.fromisoformat(until):%a %H:%M}, then Auto" if until else
                       f"Manual {override['mode'].title()} (no schedule to return to)")
    else:
        switch = data["next_switch"]
        source_text = "Auto schedule" + (f" &middot; switches at {switch:%a %H:%M}" if switch else "")
    work_hours = data["pool"].get("work_hours") or {}
    schedule_text = (f"Auto = {data['light']} jobs on weekdays {work_hours.get('start', '08:00')}-"
                     f"{work_hours.get('end', '17:00')}, {data['full']} at all other times" if work_hours else
                     f"Auto = {data['full']} jobs at all times")
    current_auto = override is None
    buttons = "".join([
        _mode_button("auto", "Auto", current_auto, schedule_text),
        _mode_button("light", f"Light now ({data['light']})", bool(override) and override["mode"] == "light",
                     "Fewer jobs, PC stays responsive; returns to Auto at the next schedule switch"),
        _mode_button("full", f"Full now ({data['full']})", bool(override) and override["mode"] == "full",
                     "Most research output; returns to Auto at the next schedule switch"),
    ])

    stats = data["stats"]
    projection = data["projection"]
    running = len(data["jobs"])
    memory = data["memory_mb"]
    memory_text = f"{memory / 1024:.1f} GB" if memory else "-"
    verdicts = stats["verdicts_today"]
    judged = sum(verdicts.values())
    kpis = [
        ("Mode", f"{data['mode'].title()} &middot; {data['workers']} slots",
         source_text, "accent" if data["mode"] == "full" else "good"),
        ("Running now", f"{running} / {data['workers']}",
         "extra jobs finish first, then the limit applies" if running > data["workers"] else "jobs in progress",
         "neutral"),
        ("Finished today", str(stats["done_today"]),
         f"{verdicts['worked']} worked &middot; {verdicts['partial']} partial &middot; {verdicts['failed']} failed"
         if judged else "no verdicts yet", "neutral"),
        ("Expected today", f"&asymp; {projection['current']['today']:.0f}",
         f"+{projection['current']['rest_today']:.0f} more by midnight", "neutral"),
        ("Lab RAM now", memory_text, "OpenCode worker processes", "neutral"),
    ]
    kpi_html = "".join(
        f'<div class="kpi {tone}"><span class="kpi-label">{label}</span><span class="kpi-value">{value}</span>'
        f'<span class="kpi-note">{note}</span></div>'
        for label, value, note, tone in kpis
    )

    rows = []
    for name, label in (("current", "Current plan"), ("light", f"Light all the time ({data['light']})"),
                        ("full", f"Full all the time ({data['full']})")):
        values = projection[name]
        week_delta = values["week"] - projection["full"]["week"]
        delta_text = "" if name == "full" else f' <span class="pm-delta">({week_delta:+.0f} vs Full)</span>'
        rows.append(
            f'<tr class="{"pm-current" if name == "current" else ""}"><td>{label}</td>'
            f'<td>&asymp; {values["rest_today"]:.0f}</td><td>&asymp; {values["today"]:.0f}</td>'
            f'<td>&asymp; {values["week"]:.0f}{delta_text}</td></tr>'
        )

    job_rows = []
    for job in data["jobs"]:
        started = job["started"]
        elapsed = (now - started).total_seconds() / 60 if started else 0
        percent = min(100, 100 * elapsed / max(data["timeout"], 1))
        tone = "#0f766e" if elapsed < 45 else "#f59e0b" if elapsed < data["timeout"] * 0.8 else "#e11d48"
        title = job["title"] or job["id"]
        job_rows.append(
            f'<tr><td><b>{html.escape(job["dept"].title())}</b><div class="pm-title" title="{html.escape(title)}">'
            f'{html.escape(title[:110])}</div></td><td>{html.escape(job["engine"])}</td>'
            f'<td class="pm-elapsed" data-started="{started.isoformat() if started else ""}">{elapsed:.0f} min</td>'
            f'<td style="min-width:140px"><div class="meter"><span style="width:{percent:.0f}%;background:{tone}">'
            f'</span></div><span class="kpi-note">stops at {data["timeout"]} min</span></td></tr>'
        )
    jobs_html = (
        '<div class="table-wrap"><table><tr><th>Job</th><th>Engine</th><th>Elapsed</th><th>Time limit</th></tr>'
        + "".join(job_rows) + "</table></div>" if job_rows else '<p class="empty">No jobs running right now.</p>'
    )
    rate_note = (f"{stats['rate']:.2f} finished jobs per worker-hour, measured over the last {RATE_WINDOW_H} h"
                 if stats["measured"] else f"assumed {FALLBACK_RATE:.1f} jobs per worker-hour (not enough log yet)")

    return f"""
<section class="card anchor" id="pool"><div class="card-head"><div><h2>Lab pool</h2>
  <p class="sub">How many AI research jobs may run at once. {html.escape(schedule_text)}. A manual choice lasts until
  the next Auto switch. Running jobs are never stopped; the new limit applies as they finish.</p></div>
  <div class="pm-buttons">{buttons}</div></div>
  <style>
   .pm-buttons {{ display:flex; gap:6px; flex-wrap:wrap; }}
   .pm-title {{ font-size:12px; color:var(--muted); max-width:520px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
   tr.pm-current td {{ font-weight:600; }}
   .pm-delta {{ color:var(--muted); font-weight:400; font-size:12px; }}
   .pm-legend span {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin:0 4px 0 10px; vertical-align:-1px; }}
  </style>
  <div class="kpis">{kpi_html}</div>
  <h3 style="margin:14px 0 4px;font-size:14px">Next 24 hours</h3>
  <div class="chart">{_hours_svg(data["hours"], data["full"])}</div>
  <p class="note pm-legend"><span style="background:#f59e0b"></span>Light <span style="background:#0f766e"></span>Full
   &middot; bar height = job slots per hour (current plan)</p>
  <h3 style="margin:14px 0 4px;font-size:14px">Expected finished jobs</h3>
  <div class="table-wrap"><table><tr><th>Plan</th><th>Rest of today</th><th>Today total</th><th>This week (Mon-Sun)</th></tr>
  {''.join(rows)}</table></div>
  <p class="note">Estimate = job slots &times; hours &times; {rate_note}. Assumes the PC stays on and the AI
   model stays reachable; outages lower all three plans alike.</p>
  <h3 style="margin:14px 0 4px;font-size:14px">Running now</h3>
  {jobs_html}
  <script>(function () {{
    function tick() {{
      document.querySelectorAll('#pool .pm-elapsed[data-started]').forEach(function (cell) {{
        var started = Date.parse(cell.getAttribute('data-started'));
        if (!isNaN(started)) {{ cell.textContent = Math.max(0, Math.round((Date.now() - started) / 60000)) + ' min'; }}
      }});
    }}
    setInterval(tick, 30000);
  }})();</script>
</section>"""


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in MODES:
        print(set_mode(sys.argv[1]))
        apply_now()
    else:
        info = snapshot()
        print(f"mode {info['mode']} ({info['source']}), {len(info['jobs'])}/{info['workers']} running, "
              f"rate {info['stats']['rate']:.2f}/worker-h, expected today {info['projection']['current']['today']:.0f}")
