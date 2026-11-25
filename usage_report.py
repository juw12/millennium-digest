"""Usage report: what each AI app and each model did for the lab on one day, and what it cost in tokens.

The AICC leaderboard mail shows how many tokens Juwen used. This shows what they bought: per AI app (OpenCode,
Talos, Copilot) and per gateway model - tokens, jobs finished and failed, time limits hit, referee passes,
machine-checked results, tokens per finished job - and how often an OpenCode worker left its pinned model for
the fallback model (the gateway rate-limits the pinned one).

Every number is read, none estimated:
    OpenCode   its own database, per assistant message (a session can change model half-way, so the session's
               model column is not enough)
    Talos      its own database (sessions table)
    AICC       the gateway's server-side record for Juwen's key (aicc_snapshot(), saved every 30 minutes by the
               Lean gate because the gateway only breaks down the current day by model)
    jobs       research_queue.json, pool.log, logs/lean_gate.json

Used by daily_report.py (one HTML mail a day + the Obsidian note). Saves logs/usage_reports/<day>.json.

    python usage_report.py                 print yesterday's usage section
    python usage_report.py 2026-11-24      that day
    python usage_report.py --backfill 7    save the last 7 days (the 7-day table reads these files)
    python usage_report.py --snapshot      save the gateway's record of today
"""
import collections
import datetime as dt
import html
import json
import os
import re
import sqlite3
import statistics
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import lab_useful  # noqa: E402

ROOT = HERE.parent
QUEUE = ROOT / "research_queue.json"
LOGS = HERE / "logs"
REPORTS = LOGS / "usage_reports"
AICC_DIR = LOGS / "aicc_usage"
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
TALOS_DB = Path.home() / ".talos" / "talos.db"
BENCH_DIR = ROOT / "prize_work" / "lean"
MAAS_API = "https://aicc-gateway.pegatroncorp.com/maas/api/v1"
POOL_TITLE = "Lab: "  # lab_pool.py names every worker session "Lab: <job id>"
PINNED_MODEL = "Pega-Coding"
# What the gateway names run on, from its own usage breakdown (2026-11-25). The gateway lists Pega-Coding under the
# raw name only; it was matched by volume against OpenCode's count.
REAL_MODELS = {"Pega-Coding": "DeepSeek-V4.1-Flash", "Pega-Chat": "Qwen3.8-27B", "Pega-Thinking": "MiniMax-M3",
               "MiniMax-M3": "MiniMax-M3", "GLM-5.3-Flash": "GLM-5.3-Flash", "Gemma4-31B": "Gemma4-31B"}
PARTS = ("input", "output", "reasoning", "cache_read", "cache_write")
FINISHED = ("done", "needs_review")


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def local_time(value):
    """A job's time stamp as local naive time. The queue holds both local stamps and UTC ones (+00:00)."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        moment = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def local_day(value):
    moment = local_time(value)
    return moment.date().isoformat() if moment else ""


def short(number):
    """1516370374 -> '1.52 B'."""
    if number is None:
        return "-"
    for limit, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= limit:
            return f"{number / limit:.2f} {unit}"
    return f"{number:.0f}"


def _connect(path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=10)


def _chunks(items, size=400):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ------------------------------------------------------------------ OpenCode
def opencode_sessions(day, titles=(), database=None, lab_root=None):
    """{session id: {"title", "lab", "pool", "created", "updated", "total", "models": {model: {...}}}} for every
    session created on `day`, plus the sessions named in `titles` from the week before (a job that ran over
    midnight). None when the database cannot be read."""
    start = dt.datetime.fromisoformat(day)
    first, last = start.timestamp() * 1000, (start + dt.timedelta(days=1)).timestamp() * 1000
    lab = str(lab_root or ROOT).replace("\\", "/").rstrip("/").lower()
    columns = ("id, title, replace(directory, '\\', '/'), time_created, time_updated, tokens_input + tokens_output "
               "+ tokens_reasoning + tokens_cache_read + tokens_cache_write")
    try:
        connection = _connect(database or OPENCODE_DB)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute(
            f"select {columns} from session_v2 where time_created >= ? and time_created < ?", (first, last)).fetchall()
        week = (start - dt.timedelta(days=7)).timestamp() * 1000
        for chunk in _chunks(titles):
            marks = ",".join("?" * len(chunk))
            rows += connection.execute(
                f"select {columns} from session_v2 where title in ({marks}) and time_created >= ? "
                "and time_created < ?", (*chunk, week, first)).fetchall()
        sessions = {}
        for session_id, title, directory, created, updated, total in rows:
            folder = (directory or "").lower()
            sessions[session_id] = {
                "title": title or "", "lab": folder == lab or folder.startswith(lab + "/"),
                "pool": (title or "").startswith(POOL_TITLE), "created": created / 1000, "updated": (updated or created) / 1000,
                "on_day": first <= created < last, "total": int(total or 0), "models": {}}
        for chunk in _chunks(sessions):
            marks = ",".join("?" * len(chunk))
            for row in connection.execute(
                    "select session_id, json_extract(data, '$.model.id'), count(*), "
                    "sum(json_extract(data, '$.tokens.input')), sum(json_extract(data, '$.tokens.output')), "
                    "sum(json_extract(data, '$.tokens.reasoning')), sum(json_extract(data, '$.tokens.cache.read')), "
                    "sum(json_extract(data, '$.tokens.cache.write')), min(seq), max(seq) from session_message "
                    f"where type = 'assistant' and session_id in ({marks}) group by 1, 2", chunk):
                entry = dict(zip(PARTS, (int(value or 0) for value in row[3:8])))
                entry.update(messages=row[2], first=row[8], last=row[9])
                sessions[row[0]]["models"][row[1] or "unknown"] = entry
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return sessions


def model_tokens(entry):
    return sum(entry[part] for part in PARTS)


def session_tokens(session):
    return sum(model_tokens(entry) for entry in session["models"].values())


def first_last(session):
    """(model of the first assistant message, model of the last one); (None, None) for an empty session."""
    models = session["models"]
    if not models:
        return None, None
    return min(models, key=lambda name: models[name]["first"]), max(models, key=lambda name: models[name]["last"])


def dominant(models):
    """The model that carried most of the tokens ({model: tokens})."""
    return max(models, key=models.get) if models else None


# ------------------------------------------------------------------ Talos
def talos_sessions(day, database=None, lab_root=None):
    """Talos sessions of the last 8 days up to the end of `day`: [{"id", "model", "lab", "created", "updated",
    "input", "output", "on_day"}]. [] when Talos's database cannot be read."""
    start = dt.datetime.fromisoformat(day)
    first, last = start.timestamp(), (start + dt.timedelta(days=1)).timestamp()
    lab = str(lab_root or ROOT).replace("/", "\\").rstrip("\\").lower()
    try:
        connection = _connect(database or TALOS_DB)
        try:
            rows = connection.execute(
                "select id, model, cwd, created, updated, tokens_in, tokens_out from sessions "
                "where created >= ? and created < ?", (first - 7 * 86400, last)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return []
    found = []
    for session_id, model, cwd, created, updated, tokens_in, tokens_out in rows:
        folder = (cwd or "").replace("/", "\\").lower()
        found.append({"id": session_id, "model": (model or "unknown").split("/", 1)[-1],
                      "lab": folder == lab or folder.startswith(lab + "\\"), "created": created,
                      "updated": updated or created, "input": int(tokens_in or 0), "output": int(tokens_out or 0),
                      "on_day": first <= created < last})
    return found


# ------------------------------------------------------------------ AICC gateway (server side)
def _api_key():
    key = os.environ.get("PEGA_API_KEY")
    if key:
        return key
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            return winreg.QueryValueEx(handle, "PEGA_API_KEY")[0]
    except (ImportError, OSError):
        return ""


def _maas(path):
    request = urllib.request.Request(MAAS_API + path, headers={
        "Authorization": "Bearer " + _api_key(), "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def aicc_snapshot(fetch=_maas, folder=None, now=None):
    """Save the gateway's own count of today, by model (the gateway gives the model split for today only, so the
    last snapshot of a day is that day's record). Returns the saved dict, or None when the gateway did not answer."""
    now = now or dt.datetime.now()
    try:
        data = fetch("/token-usage/daily")
    except Exception:
        return None
    if data.get("date") and data["date"] != now.date().isoformat():  # the gateway's day has not turned yet
        return None
    record = {"date": now.date().isoformat(), "as_of": now.isoformat(timespec="minutes"),
              "total": data.get("total_tokens") or 0,
              "models": [{"model": item.get("ai_model", "?"), "prompt": item.get("prompt_tokens") or 0,
                          "completion": item.get("completion_tokens") or 0, "total": item.get("total_tokens") or 0}
                         for item in data.get("breakdown") or []]}
    folder = Path(folder or AICC_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{record['date']}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return record


def aicc_record(day, fetch=_maas, folder=None):
    """{"total", "as_of", "models"} the gateway recorded for `day`: the final day total when the gateway answers,
    the model split from the day's last snapshot. {} when neither is there."""
    record = _read(Path(folder or AICC_DIR) / f"{day}.json", {})
    try:
        for item in fetch(f"/token-usage/range?start_date={day}&end_date={day}").get("data") or []:
            if item.get("date") == day:
                record = dict(record, total=item.get("total_tokens") or 0, final=True)
    except Exception:
        pass
    return record


# ------------------------------------------------------------------ jobs
def pool_events(day, log_path=None):
    """What pool.log says about `day`: {"timeouts": {job: engine}, "failed": [job ids marked failed],
    "runs": {engine: {"started": n, "exited": n, "timeout": n, ...}}}. A failed job has no finish time in the
    queue, so the log is the only place that dates it."""
    events = {"timeouts": {}, "failed": [], "runs": {}}
    try:
        lines = Path(log_path or LOGS / "pool.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events
    for line in lines:
        if not line.startswith(day):
            continue
        text = line[len(day):].split(None, 1)[-1] if len(line) > len(day) else ""
        start = re.match(r"start (\S+) on (\S+)", text)
        if start:
            runs = events["runs"].setdefault(start.group(2), collections.Counter())
            runs["started"] += 1
            continue
        match = re.match(r"(\S+): (.+)", text)
        if not match:
            continue
        job, what = match.groups()
        if what == "marked failed":
            events["failed"].append(job)
            continue
        end = re.match(r"(opencode|talos|copilot) (exited|timeout after|no connection|stopped waiting|\S.*)", what)
        if end:
            outcome = {"timeout after": "timeout"}.get(end.group(2), end.group(2))
            events["runs"].setdefault(end.group(1), collections.Counter())[outcome] += 1
            if outcome == "timeout":
                events["timeouts"][job] = end.group(1)
    events["runs"] = {engine: dict(counts) for engine, counts in events["runs"].items()}
    return events


def ended_on(day, jobs, failed=()):
    """The jobs that ended on `day` (local time): finished ones by their finish time, failed ones by pool.log."""
    failed = set(failed)
    return [job for job in jobs
            if (job.get("status") in FINISHED and local_day(job.get("finished")) == day)
            or (job.get("status") == "failed" and (str(job.get("id")) in failed or local_day(job.get("finished")) == day))]


def job_rows(day, jobs, sessions, talos, failed=()):
    """One row per job that ended on `day`: engine, the model that did most of the work, tokens of its sessions,
    minutes. The verdict fields are left out: they change for days and are read fresh by table()."""
    by_title = collections.defaultdict(list)
    for session in (sessions or {}).values():
        by_title[session["title"]].append(session)
    free = sorted((entry for entry in talos if entry["lab"]), key=lambda entry: entry["created"])
    used = set()
    rows = []
    for job in ended_on(day, jobs, failed):
        pool = job.get("pool") or {}
        engine = pool.get("engine") or "?"
        started, ended = local_time(pool.get("started")), local_time(job.get("finished"))
        models, switched = collections.Counter(), False
        if engine == "opencode":
            for session in by_title.get(POOL_TITLE + str(job["id"]), []):
                for name, entry in session["models"].items():
                    models[name] += model_tokens(entry)
                first, last = first_last(session)
                switched = switched or (first is not None and first != last)
        elif engine == "talos" and started:
            near = [entry for entry in free if entry["id"] not in used
                    and -10 <= entry["created"] - started.timestamp() <= 180 and entry["input"] > 50_000]
            if near:
                match = min(near, key=lambda entry: abs(entry["created"] - started.timestamp()))
                used.add(match["id"])
                models[match["model"]] += match["input"] + match["output"]
        rows.append({"id": str(job["id"]), "dept": job.get("dept") or "other", "engine": engine,
                     "model": dominant(models) or "?", "tokens": sum(models.values()), "switched": switched,
                     "minutes": round((ended - started).total_seconds() / 60, 1) if started and ended else None,
                     "failed": job.get("status") == "failed"})
    return rows


def table(rows, jobs, key, verdicts=None, timed_out=()):
    """Performance rows grouped by `key` (a function of a job row). Verdicts are read from the queue as it is now."""
    by_id = {str(job.get("id")): job for job in jobs}
    useful = {str(job["id"]) for job in lab_useful.useful_from(jobs)}
    verdicts = lab_useful.lean_verdicts() if verdicts is None else verdicts
    groups = collections.defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    result = []
    for name, members in groups.items():
        done = [row for row in members if not row["failed"]]
        queue_jobs = [by_id.get(row["id"], {}) for row in done]
        referee = [lab_useful._first_word(job.get("referee")) for job in queue_jobs]
        tokens = sum(row["tokens"] for row in members)
        minutes = [row["minutes"] for row in done if row["minutes"] is not None]
        result.append({
            "name": name, "finished": len(done), "failed": len(members) - len(done),
            "timeouts": sum(1 for row in members if row["id"] in timed_out),
            "referee_pass": referee.count("pass"), "referee_fix": referee.count("fix"),
            "useful": sum(1 for row in done if row["id"] in useful),
            "machine_checked": sum(1 for row, job in zip(done, queue_jobs) if row["id"] in useful
                                   and lab_useful.is_verified_strong(job, verdicts)),
            "lean_checked": sum(1 for row in done if verdicts.get(row["id"]) in lab_useful.MACHINE_CHECKED),
            "tokens": tokens, "tokens_per_finished": tokens / len(done) if done and tokens else None,
            "median_minutes": statistics.median(minutes) if minutes else None,
            "switched": sum(1 for row in members if row.get("switched"))})
    return sorted(result, key=lambda item: -(item["finished"] + item["failed"]))


# ------------------------------------------------------------------ the day's summary
def latest_bench(folder=None):
    """The newest Lean model bench (prize_work/lean/model_bench.py), or {}."""
    files = sorted(Path(folder or BENCH_DIR).glob("model_bench_*.json"))
    for path in reversed(files):
        data = _read(path, {})
        if data.get("models"):
            return dict(data, file=path.name)
    return {}


def collect(day=None, jobs=None, opencode_db=None, talos_db=None, lab_root=None, fetch=_maas, aicc_folder=None,
            pool_log=None):
    """Everything the report shows for `day`, as plain data (saved as logs/usage_reports/<day>.json)."""
    day = day or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    jobs = _read(QUEUE, {}).get("jobs", []) if jobs is None else jobs
    events = pool_events(day, pool_log)
    ended = ended_on(day, jobs, events["failed"])
    titles = [POOL_TITLE + str(job["id"]) for job in ended if (job.get("pool") or {}).get("engine") == "opencode"]
    sessions = opencode_sessions(day, titles, opencode_db, lab_root)
    talos = talos_sessions(day, talos_db, lab_root)

    apps = collections.defaultdict(lambda: dict({part: 0 for part in PARTS}, sessions=0, tokens=0))
    models = collections.defaultdict(lambda: {"opencode": 0, "talos": 0, "sessions": 0})
    pool_started = pool_switched = pool_tokens = pool_fallback_tokens = 0
    for session in (sessions or {}).values():
        if not session["on_day"]:
            continue
        app = ("OpenCode: lab workers" if session["lab"] and session["pool"] else
               "OpenCode: lab, by hand" if session["lab"] else "OpenCode: Juwen's other projects")
        counted = session_tokens(session)
        apps[app]["sessions"] += 1
        apps[app]["tokens"] += max(counted, session["total"])
        for name, entry in session["models"].items():
            for part in PARTS:
                apps[app][part] += entry[part]
            models[name]["opencode"] += model_tokens(entry)
        if session["total"] > counted:  # titles and summaries are not stored per message
            models["(not stored per message)"]["opencode"] += session["total"] - counted
        top = dominant({name: model_tokens(entry) for name, entry in session["models"].items()})
        if top:
            models[top]["sessions"] += 1
        if session["lab"] and session["pool"] and session["models"]:
            first, last = first_last(session)
            pool_started += 1
            pool_switched += first != last
            pool_tokens += counted
            pool_fallback_tokens += sum(model_tokens(entry) for name, entry in session["models"].items()
                                        if name != first)
    for entry in talos:
        if not entry["on_day"]:
            continue
        app = "Talos: lab workers" if entry["lab"] else "Talos: other"
        apps[app]["sessions"] += 1
        apps[app]["input"] += entry["input"]
        apps[app]["output"] += entry["output"]
        apps[app]["tokens"] += entry["input"] + entry["output"]
        models[entry["model"]]["talos"] += entry["input"] + entry["output"]
        models[entry["model"]]["sessions"] += 1

    rows = job_rows(day, jobs, sessions, talos, events["failed"])
    return {
        "day": day, "written": dt.datetime.now().isoformat(timespec="minutes"),
        "opencode_readable": sessions is not None,
        "apps": [dict(values, app=name) for name, values in sorted(apps.items(), key=lambda item: -item[1]["tokens"])],
        "models": [dict(values, model=name, real=REAL_MODELS.get(name, ""))
                   for name, values in sorted(models.items(), key=lambda item: -(item[1]["opencode"] + item[1]["talos"]))],
        "total": sum(values["tokens"] for values in apps.values()),
        "switching": {"sessions": pool_started, "switched": pool_switched, "tokens": pool_tokens,
                      "fallback_tokens": pool_fallback_tokens, "pinned": PINNED_MODEL},
        "aicc": aicc_record(day, fetch, aicc_folder),
        "jobs": rows,
        "timeouts": events["timeouts"],
        "runs": events["runs"],
    }


def save(summary, folder=None):
    folder = Path(folder or REPORTS)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{summary['day']}.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")


def week_rows(day, folder=None, days=7):
    """Job rows of the `days` days ending on `day`, from the saved daily summaries, and how many days were found."""
    rows, found, timed_out = [], 0, {}
    for back in range(days):
        name = (dt.date.fromisoformat(day) - dt.timedelta(days=back)).isoformat()
        saved = _read(Path(folder or REPORTS) / f"{name}.json", None)
        if saved:
            found += 1
            rows += saved.get("jobs", [])
            timed_out.update(saved.get("timeouts", {}))
    return rows, found, timed_out


# ------------------------------------------------------------------ text
def _performance_lines(title, rows, label):
    lines = [title, "", f"| {label} | Finished | Failed | Time limit | Referee pass / fix | Useful | Lean-checked | "
             "Tokens | Tokens per finished job | Median minutes |", "|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {row['name']} | {row['finished']} | {row['failed']} | {row['timeouts']} | "
            f"{row['referee_pass']} / {row['referee_fix']} | {row['useful']} | {row['lean_checked']} | "
            f"{short(row['tokens']) if row['tokens'] else '-'} | {short(row['tokens_per_finished'])} | "
            f"{row['median_minutes'] if row['median_minutes'] is not None else '-'} |")
    if not rows:
        lines.append("| none | | | | | | | | | |")
    return lines + [""]


def sections(summary, jobs=None, folder=None, bench=None):
    """The report's parts as data for both writers: {"apps", "models", "aicc", "by_app", "by_model", "week", ...}."""
    jobs = _read(QUEUE, {}).get("jobs", []) if jobs is None else jobs
    timed_out = summary.get("timeouts", {})
    before = (dt.date.fromisoformat(summary["day"]) - dt.timedelta(days=1)).isoformat()
    week, week_days, week_timeouts = week_rows(before, folder, days=6)  # the six saved days before this one
    week, week_days = week + summary["jobs"], week_days + 1
    week_timeouts.update(timed_out)
    return {
        "by_app": table(summary["jobs"], jobs, lambda row: row["engine"], timed_out=timed_out),
        "by_model": table(summary["jobs"], jobs, lambda row: f"{row['engine']} / {row['model']}", timed_out=timed_out),
        "week": table(week, jobs, lambda row: f"{row['engine']} / {row['model']}", timed_out=week_timeouts),
        "week_days": week_days,
        "bench": latest_bench() if bench is None else bench,
    }


def markdown(summary, jobs=None, folder=None, bench=None):
    """The usage part of the daily report."""
    parts = sections(summary, jobs, folder, bench)
    total = summary["total"] or 1
    lines = ["## AI apps and models", ""]
    if not summary["opencode_readable"]:
        lines += ["OpenCode's database could not be read, so the OpenCode numbers are missing.", ""]
    aicc = summary.get("aicc") or {}
    lines.append(f"Counted on this PC: {short(summary['total'])} tokens."
                 + (f" The AICC gateway recorded {short(aicc['total'])} for the same day"
                    + ("" if aicc.get("final") else f" (as of {aicc.get('as_of', '?')})")
                    + ". The two differ because this PC counts a session on the day it started and the gateway "
                    "counts each call when it happens." if aicc.get("total") else
                    " The AICC gateway's record for this day was not saved."))
    lines += ["", "### Tokens by AI app", "", "| App | Sessions | Input | Output + reasoning | Cache read | Total | Share |",
              "|---|---|---|---|---|---|---|"]
    for app in summary["apps"]:
        lines.append(f"| {app['app']} | {app['sessions']} | {short(app['input'])} | "
                     f"{short(app['output'] + app['reasoning'])} | {short(app['cache_read'])} | "
                     f"{short(app['tokens'])} | {100 * app['tokens'] / total:.0f}% |")
    lines += ["", "Copilot is switched off (the office proxy blocks it). Vector's own tokens do not go through the "
              "AICC gateway and are not counted here.", "", "### Tokens by model", "",
              "| Model | Runs on | OpenCode | Talos | Total | Share | Sessions led |", "|---|---|---|---|---|---|---|"]
    for model in summary["models"]:
        both = model["opencode"] + model["talos"]
        lines.append(f"| {model['model']} | {model['real'] or '-'} | {short(model['opencode'])} | "
                     f"{short(model['talos'])} | {short(both)} | {100 * both / total:.0f}% | {model['sessions']} |")
    if aicc.get("models"):
        lines += ["", f"AICC gateway record by model (as of {aicc.get('as_of', '?')}): " + "; ".join(
            f"{item['model']} {short(item['total'])}" for item in sorted(aicc["models"], key=lambda item: -item["total"]))
            + "."]
    switching = summary["switching"]
    if switching["sessions"]:
        lines += ["", f"Model switching: {switching['switched']} of {switching['sessions']} lab worker sessions "
                  f"({100 * switching['switched'] / switching['sessions']:.0f}%) started on {switching['pinned']} and "
                  f"ended on another model after a rate limit; "
                  f"{100 * switching['fallback_tokens'] / (switching['tokens'] or 1):.0f}% of the lab workers' tokens "
                  "ran on the fallback model."]
    lines.append("")
    if summary.get("runs"):
        lines += ["### Worker runs by AI app (a job can take more than one run)", "",
                  "| App | Started | Ended by itself | Hit the time limit | Lost the model | Stopped waiting | Other |",
                  "|---|---|---|---|---|---|---|"]
        for engine, counts in sorted(summary["runs"].items(), key=lambda item: -item[1].get("started", 0)):
            known = ("started", "exited", "timeout", "no connection", "stopped waiting")
            lines.append(f"| {engine} | {counts.get('started', 0)} | {counts.get('exited', 0)} | "
                         f"{counts.get('timeout', 0)} | {counts.get('no connection', 0)} | "
                         f"{counts.get('stopped waiting', 0)} | "
                         f"{sum(count for name, count in counts.items() if name not in known)} |")
        lines.append("")
    lines += _performance_lines("### Jobs by AI app", parts["by_app"], "App")
    lines += _performance_lines("### Jobs by app and model (the model that did most of the job)", parts["by_model"],
                                "App / model")
    lines += _performance_lines(f"### Last 7 days by app and model ({parts['week_days']} day(s) on file)",
                                parts["week"], "App / model")
    lines.append("Useful = referee pass and panel pass. Lean-checked = the Lean gate accepted the job's claims. "
                 "Reviews arrive a day or two after a job, so the newest day always looks worse than it is.")
    bench = parts["bench"]
    if bench:
        lines += ["", f"### Lean writing test ({str(bench.get('finished', ''))[:10]}, {len(bench.get('problems', []))} "
                  "small Mathlib theorems, graded by Lean)", "",
                  "| Model | Runs on | Proved first try | Proved after one repair | Tokens | Median seconds per answer |",
                  "|---|---|---|---|---|---|"]
        for name, row in sorted(bench["models"].items(), key=lambda item: -item[1]["pass_after_repair"]):
            lines.append(f"| {name} | {REAL_MODELS.get(name, '-')} | {row['pass_first']}/{row['of']} | "
                         f"{row['pass_after_repair']}/{row['of']} | {short(row['tokens_in'] + row['tokens_out'])} | "
                         f"{row['median_seconds']} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ HTML mail (the AICC mail's look)
STYLE = {
    "page": "background:#111217;color:#ccccdc;font-family:Segoe UI,Arial,sans-serif;font-size:14px;padding:16px;",
    "card": "background:#181b1f;border:1px solid #2c3235;padding:10px;vertical-align:top;",
    "label": "color:#9fa7b3;font-size:12px;",
    "value": "color:#F2CC0C;font-size:22px;font-weight:bold;margin-top:4px;",
    "h2": "color:#ffffff;font-size:20px;margin:0 0 4px 0;",
    "h3": "color:#73BF69;font-size:16px;margin:22px 0 8px 0;border-bottom:1px solid #2c3235;padding-bottom:4px;",
    "th": "background:#22252b;color:#9fa7b3;text-align:left;padding:6px 8px;border:1px solid #2c3235;font-size:12px;",
    "td": "padding:6px 8px;border:1px solid #2c3235;color:#ccccdc;font-size:13px;",
}


def _inline(text):
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b style='color:#ffffff;'>\1</b>", text)
    return re.sub(r"(?<![\w/])_(.+?)_(?![\w/])", r"<i>\1</i>", text)


def to_html(text):
    """The report's own small Markdown (headings, bullets, tables, bold) as mail-safe HTML."""
    out, table_rows, in_list = [], [], False

    def flush():
        nonlocal table_rows, in_list
        if table_rows:
            head, body = table_rows[0], table_rows[2:]
            out.append("<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin:6px 0 10px 0;'>")
            out.append("<tr>" + "".join(f"<th style='{STYLE['th']}'>{_inline(cell)}</th>" for cell in head) + "</tr>")
            for row in body:
                out.append("<tr>" + "".join(f"<td style='{STYLE['td']}'>{_inline(cell)}</td>" for cell in row) + "</tr>")
            out.append("</table>")
            table_rows = []
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in text.splitlines():
        if line.startswith("|"):
            if in_list:
                flush()
            table_rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
            continue
        if table_rows:
            flush()
        bullet = re.match(r"(\s*)- (.*)", line)
        if bullet:
            if not in_list:
                out.append("<ul style='margin:4px 0 8px 0;padding-left:22px;'>")
                in_list = True
            indent = "margin-left:18px;" if bullet.group(1) else ""
            out.append(f"<li style='margin:3px 0;{indent}'>{_inline(bullet.group(2))}</li>")
            continue
        flush()
        if line.startswith("# "):
            out.append(f"<div style='{STYLE['h2']}'>{_inline(line[2:])}</div>")
        elif line.startswith("### "):
            out.append(f"<div style='color:#8AB8FF;font-size:14px;font-weight:bold;margin:14px 0 4px 0;'>"
                       f"{_inline(line[4:])}</div>")
        elif line.startswith("## "):
            out.append(f"<div style='{STYLE['h3']}'>{_inline(line[3:])}</div>")
        elif line.strip():
            out.append(f"<p style='margin:6px 0;line-height:1.45;'>{_inline(line)}</p>")
    flush()
    return "\n".join(out)


def trend_html(days, target=0, highlight=None):
    """Daily tokens of the last 14 days as bars made of table cells (mail clients drop scripts and most CSS)."""
    if not days:
        return ""
    # Days after the report day are left out: the running day is only partly counted and would show as a miss.
    names = sorted(name for name in days if not highlight or name <= highlight)[-14:]
    if not names:
        return ""
    top = max(max(days[name] for name in names), target, 1)
    rows = []
    for name in names:
        width = max(1, round(520 * days[name] / top))
        colour = "#F2CC0C" if name == highlight else "#73BF69" if days[name] >= target else "#F2495C"
        rows.append(
            f"<tr><td style='color:#9fa7b3;font-size:12px;padding:2px 8px 2px 0;'>{name[5:]}</td>"
            f"<td style='padding:2px 0;'><table cellspacing='0' cellpadding='0'><tr>"
            f"<td style='background:{colour};width:{width}px;height:12px;font-size:1px;'>&nbsp;</td>"
            f"<td style='color:#ccccdc;font-size:12px;padding-left:8px;'>{short(days[name])}</td></tr></table></td></tr>")
    note = f"Green = at or over the {short(target)} target, red = under, yellow = the day of this report." if target else ""
    return (f"<div style='{STYLE['h3']}'>Tokens per day, last 14 days</div><table cellspacing='0' cellpadding='0'>"
            + "".join(rows) + f"</table><p style='color:#9fa7b3;font-size:12px;margin:6px 0;'>{note}</p>")


def mail_html(day, headline, cards, report_text, days=None, target=0):
    """The whole daily mail: cards on top, then the report, then the 14-day token bars."""
    width = max(12, 100 // max(1, len(cards)))
    cells = "".join(
        f"<td style='{STYLE['card']}width:{width}%;'><div style='{STYLE['label']}'>{html.escape(label)}</div>"
        f"<div style='{STYLE['value']}'>{html.escape(str(value))}</div></td>" for label, value in cards)
    # The title and the bold headline line are already shown above the cards.
    repeated = f"**{headline}.**"
    body = "\n".join(line for line in report_text.splitlines()
                     if not line.startswith("# ") and line.strip() != repeated)
    return (f"<div style='{STYLE['page']}'><div style='{STYLE['h2']}'>Millennium lab - {html.escape(day)}</div>"
            f"<p style='color:#9fa7b3;margin:2px 0 12px 0;'>{html.escape(headline)}</p>"
            f"<table cellspacing='6' cellpadding='0' style='width:100%;max-width:980px;'><tr>{cells}</tr></table>"
            f"{to_html(body)}{trend_html(days or {}, target, day)}</div>")


def main():
    arguments = sys.argv[1:]
    if "--snapshot" in arguments:
        record = aicc_snapshot()
        print(f"saved: {short(record['total'])} tokens as of {record['as_of']}" if record else "the gateway did not answer")
        return
    if "--backfill" in arguments:
        count = int(arguments[arguments.index("--backfill") + 1])
        for back in range(count, 0, -1):
            day = (dt.date.today() - dt.timedelta(days=back)).isoformat()
            summary = collect(day)
            save(summary)
            print(f"{day}: {short(summary['total'])} tokens, {len(summary['jobs'])} jobs")
        return
    day = next((value for value in arguments if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), None)
    summary = collect(day)
    print(markdown(summary))


if __name__ == "__main__":
    main()
