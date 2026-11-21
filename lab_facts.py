"""The lab's facts in one private note, written from the lab's own files once a day, with a self-check.

Why: "which desk can earn money?" and "how many jobs ran yesterday?" were answered on another computer
from scattered work notes, and wrongly (square packing was called a desk without a prize; it is the $50,000
Kaggle competition). This note is the one place to read first. Each section names the file it comes from.
Nothing is estimated: what a file does not say is shown as "not stated".

The self-check lists where the lab's files disagree with each other or something is late:
  - a money figure in a desk's text or a milestone that prizes.json does not have
  - an open prize whose deadline has passed, a milestone past its date that is not done
  - a desk that has finished nothing for days and has nothing open
  - a job text or a job's work note that names a prize for a catalogue problem that the catalogue does not
    (PPL 043: $100 / $1,000)
  - a prize catalogue that was not refreshed, a file that cannot be read, an updater that is stuck

It reads only. It never changes the queue, the settings or a result.

    python lab_facts.py            write the note (publish_notes.py does this once a day)
    python lab_facts.py --print    print the note, write nothing
    python lab_facts.py --check    print the self-check only; exit code 1 when it found something
"""
import datetime as dt
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import lab_useful  # noqa: E402
import selfupdate  # noqa: E402

NOTE_NAME = "millennium-lab-facts.md"
NOTE = Path.home() / "Documents" / "Obsidian Vault" / "notes" / NOTE_NAME
FINISHED = ("done", "needs_review")
OPEN = ("needs_ai", "running")
NEEDED = ("departments", "targets", "prizes", "queue")  # without these the note cannot say much
MONEY = re.compile(r"(C\$|[$£€¥₹])\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K|M|million|bn|billion)?\b")
SCALE = {"k": 1_000, "K": 1_000, "M": 1_000_000, "million": 1_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000}
# A catalogue number, at most 80 characters that name no other catalogue number, then a money figure.
PPL_MONEY = re.compile(r"PPL[ -]?(\d{2,3})\b(?:(?!PPL)[^\n]){0,80}?" + MONEY.pattern)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
JOB_NOTE = re.compile(r"lab_[a-z]+_\d{6,}_\d+")  # a work note is named after the lab job it reports on
WORK_NOTES = "Update Log"      # folder in the vault where every lab job leaves its note
NOTE_BYTES = 8000              # how much of one work note the self-check reads
PUBLIC_REPO = "../../open-problems-lab"  # publish_notes.py's default, relative to the automation folder
QUIET_HOURS = 72           # a desk with nothing open is looked at after this long without a finished job ...
QUIET_PERIODS = 3          # ... or after this many of its own "every_hours", whichever is longer
CATALOG_MAX_DAYS = 14      # prize_catalog.py refreshes weekly; two missed runs are worth a look
UPDATER_STUCK_HOURS = 24
PROBLEM_DAYS = 30          # "prize problems worked on" looks this far back
PROBLEMS_SHOWN = 20
FINDINGS_PER_CHECK = 12


def lab_paths(here=HERE):
    """Every file the note reads. `here` is the automation folder; the lab is its parent."""
    here = Path(here)
    root = here.parent
    squares = root / "prize_work" / "squares-in-squares"
    return {
        "departments": here / "lab_departments.json",
        "targets": here / "lab_targets.json",
        "prizes": here / "prizes.json",
        "catalog": here / "prize_catalog.json",
        "updater": here / "logs" / "selfupdate.json",
        "queue": root / "research_queue.json",
        "decisions": root / "outbox" / "public_decisions.json",
        "squares_score": squares / "best_score.json",
        "submission": squares / "submission.csv",
    }


def work_notes(vault):
    """[(file name, start of its text)] for the lab jobs' own notes in the vault. Empty when there is no vault."""
    folder = Path(vault) / WORK_NOTES if vault else None
    if folder is None or not folder.is_dir():
        return []
    notes = []
    for path in sorted(folder.glob("*.md")):
        if not JOB_NOTE.search(path.name):
            continue
        try:
            with path.open("rb") as handle:
                notes.append((path.name, handle.read(NOTE_BYTES).decode("utf-8", errors="replace")))
        except OSError:
            continue
    return notes


def public_note_count(here, departments):
    """Notes in the public repo's notes folder, where publish_notes.py looks too. None when the repo is not here."""
    publish = departments.get("publish") if isinstance(departments, dict) else None
    repo = (publish or {}).get("repo") or PUBLIC_REPO
    folder = (Path(here) / repo).resolve() / "notes"
    return len(list(folder.glob("*.md"))) if folder.is_dir() else None


def read_json(path):
    """The file's content, or None when it is missing or not JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def money_figures(text):
    """Every money figure in a text as (currency sign, whole amount): "$50k" and "$50,000" are both ("$", 50000)."""
    figures = set()
    for sign, number, scale in MONEY.findall(str(text or "")):
        try:
            figures.add((sign, int(round(float(number.replace(",", "")) * SCALE.get(scale, 1)))))
        except ValueError:
            continue
    return figures


def show_money(figure):
    sign, amount = figure
    return f"{sign}{amount:,}"


def finished_day(job):
    """The day a job finished. The queue has held finish times that are not text (True), so nothing is assumed."""
    stamp = job.get("finished") or job.get("bridged") or ""
    try:
        return dt.date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None


def desk_of(job):
    """Department of a queue job, as lab.job_dept does it (daily_* jobs of the first version map by focus)."""
    if job.get("dept"):
        return job["dept"]
    if str(job.get("id", "")).startswith("daily_"):
        return {"rh": "rh", "bsd": "bsd", "prize": "prize"}.get(job.get("focus"))
    return None


def catalog_number(item_id):
    """"PPL 043", "PPL-43" and "043" are the same catalogue problem: 43. None for ids without a number."""
    digits = re.sub(r"\D", "", str(item_id or ""))
    return int(digits) if digits else None


def _milestone_state(milestone, jobs, today, status_of):
    try:
        status = status_of(milestone, jobs, today)
        return bool(status.get("done")), str(status.get("note") or "")
    except Exception as error:  # one milestone that cannot be worked out must not cost the whole note
        return bool(milestone.get("done")), f"could not be worked out ({type(error).__name__})"


def collect(here=HERE, today=None, now=None, milestone_status=None, vault=None):
    """Everything the note shows, read from the files. Missing files are named in facts["unreadable"]."""
    now = now or dt.datetime.now()
    today = today or now.date()
    paths = lab_paths(here)
    data = {name: read_json(path) for name, path in paths.items() if name != "submission"}
    unreadable = [paths[name].name for name in NEEDED if not isinstance(data[name], dict)]
    departments = (data["departments"] or {}).get("departments", []) if isinstance(data["departments"], dict) else []
    targets = data["targets"] if isinstance(data["targets"], dict) else {}
    prizes = data["prizes"] if isinstance(data["prizes"], dict) else {}
    catalog = data["catalog"] if isinstance(data["catalog"], dict) else {}
    decisions = data["decisions"] if isinstance(data["decisions"], dict) else {}
    jobs = [job for job in (data["queue"] or {}).get("jobs", []) if isinstance(job, dict)] \
        if isinstance(data["queue"], dict) else []

    yesterday = today - dt.timedelta(days=1)
    week = {today - dt.timedelta(days=offset) for offset in range(1, 8)}
    per_desk = defaultdict(lambda: {"yesterday": 0, "week": 0, "total": 0, "open": 0, "last": None})
    for job in jobs:
        row = per_desk[desk_of(job) or "(no desk)"]
        if job.get("status") in OPEN:
            row["open"] += 1
        if job.get("status") not in FINISHED:
            continue
        row["total"] += 1
        day = finished_day(job)
        if day is None:
            continue
        row["yesterday"] += day == yesterday
        row["week"] += day in week
        row["last"] = max(row["last"], day) if row["last"] else day

    if milestone_status is None:
        import lab  # here, not at the top: lab.py reads the real lab's files, which a test must be able to avoid
        milestone_status = lab.milestone_status
    milestones = []
    for milestone in targets.get("milestones", []):
        done, note = _milestone_state(milestone, jobs, today, milestone_status)
        milestones.append({"id": milestone.get("id", "?"), "due": str(milestone.get("due") or ""),
                           "title": str(milestone.get("title") or ""), "done": done, "note": note})

    by_number = {catalog_number(item.get("id")): item for item in catalog.get("items", [])
                 if isinstance(item, dict) and catalog_number(item.get("id")) is not None}
    by_id = {str(item.get("id")): item for item in catalog.get("items", []) if isinstance(item, dict)}
    since = today - dt.timedelta(days=PROBLEM_DAYS)
    worked = Counter()
    for job in jobs:
        day = finished_day(job)
        if job.get("topic_id") and job.get("status") in FINISHED and day and day >= since:
            worked[str(job["topic_id"])] += 1
    problems = []
    for topic, count in worked.most_common(PROBLEMS_SHOWN):
        item = by_id.get(topic) or by_number.get(catalog_number(topic)) or {}
        problems.append({"id": topic, "title": str(item.get("title") or "not in the catalogue"),
                         "reward": str(item.get("reward") or "not stated"), "jobs": count})

    useful = lab_useful.useful_jobs(paths["queue"])
    score = data["squares_score"] if isinstance(data["squares_score"], dict) else {}
    return {
        "now": now, "today": today, "paths": paths, "unreadable": unreadable,
        "departments": departments, "per_desk": dict(per_desk), "jobs": jobs,
        "prizes": prizes, "targets": targets, "milestones": milestones,
        "catalog": catalog, "catalog_by_number": by_number, "problems": problems,
        "finished_total": sum(1 for job in jobs if job.get("status") in FINISHED),
        "useful": len(useful), "useful_strong": sum(map(lab_useful.is_strong, useful)),
        "published": len(decisions.get("published") or {}), "held": len(decisions.get("hold") or {}),
        "decisions_read": bool(decisions),
        "public_notes": public_note_count(here, data["departments"]),
        "work_notes": work_notes(vault),
        "squares_score": score, "submission_file": paths["submission"].exists(),
        "updater": data["updater"] if isinstance(data["updater"], dict) else {},
    }


# ------------------------------------------------------------------ the self-check
def check_sources(facts):
    return [f"{name} could not be read, so the parts of this note that come from it are empty"
            for name in facts["unreadable"]]


def check_money_listed(facts):
    """Money named in a desk's text or a milestone has to be in prizes.json, the lab's list of what can pay."""
    listed = money_figures(json.dumps(facts["prizes"], ensure_ascii=False))
    named = defaultdict(list)
    for desk in facts["departments"]:
        for figure in money_figures(f"{desk.get('problem', '')} {desk.get('brief', '')}"):
            named[figure].append(f"lab_departments.json ({desk.get('id', '?')})")
    for milestone in facts["milestones"]:
        for figure in money_figures(f"{milestone['title']} {milestone['note']}"):
            named[figure].append(f"lab_targets.json ({milestone['id']})")
    return [f"{show_money(figure)} is named in {', '.join(sorted(set(places)))} but nowhere in prizes.json"
            for figure, places in sorted(named.items(), key=lambda pair: -pair[0][1]) if figure not in listed]


def check_deadlines(facts):
    late = []
    for prize in facts["prizes"].get("prizes", []):
        dates = ISO_DATE.findall(str(prize.get("deadline") or ""))
        if str(prize.get("status") or "").lower().startswith("open") and dates \
                and max(dates) < facts["today"].isoformat():
            late.append(f"prizes.json lists {prize.get('name', '?')} as open, but its last date {max(dates)} has passed")
    return late


def _queue_missing(facts):
    return facts["paths"]["queue"].name in facts["unreadable"]


def check_milestones(facts):
    if _queue_missing(facts):  # measured milestones would all read "nothing finished"; check_sources says why
        return []
    return [f"milestone {milestone['id']} was due {milestone['due']} and is not done ({milestone['note'] or 'no note'})"
            for milestone in facts["milestones"]
            if not milestone["done"] and ISO_DATE.fullmatch(milestone["due"])
            and milestone["due"] < facts["today"].isoformat()]


def check_desks(facts):
    quiet = []
    if _queue_missing(facts):
        return quiet
    for desk in facts["departments"]:
        row = facts["per_desk"].get(desk.get("id"), {"open": 0, "last": None})
        if row["open"]:
            continue
        try:
            hours = max(QUIET_HOURS, QUIET_PERIODS * float(desk.get("every_hours") or 0))
        except (TypeError, ValueError):
            hours = QUIET_HOURS
        allowed = math.ceil(hours / 24)
        name = f"{desk.get('name', '?')} ({desk.get('id', '?')})"
        if row["last"] is None:
            quiet.append(f"{name} has no finished job and nothing open")
        elif (facts["today"] - row["last"]).days > allowed:
            quiet.append(f"{name} last finished a job on {row['last']} and has nothing open (looked at after {allowed} days)")
    return quiet


def check_rewards(facts):
    """A text that puts a prize next to a catalogue number has to agree with the catalogue.

    Read: each job's title and result in the queue, and the note each job leaves in the vault. The first real
    run (2026-11-21) found nothing in the queue: the wrong figures ("PPL 043 ... $100") are in the work notes,
    and those are what a later reader, human or AI, takes the prize from.
    """
    if not facts["catalog_by_number"]:
        return []
    texts = [(str(job.get("id")), f"{job.get('title', '')}\n{job.get('result', '')}") for job in facts["jobs"]]
    texts += [(f"note {name}", text) for name, text in facts["work_notes"]]
    wrong = defaultdict(list)
    for label, text in texts:
        for match in PPL_MONEY.finditer(text):
            item = facts["catalog_by_number"].get(int(match.group(1)))
            figure = next(iter(money_figures(match.group(0)[match.start(2) - match.start(0):])), None)
            if not item or figure is None:
                continue
            if figure not in money_figures(item.get("reward")):
                wrong[(str(item.get("id")), str(item.get("reward") or "not stated"), figure)].append(label)
    return [f"{item_id}: the catalogue says {reward}, but {len(labels)} job text(s) or work note(s) say "
            f"{show_money(figure)} (first: {labels[0]})"
            for (item_id, reward, figure), labels in sorted(wrong.items())]


def check_catalog(facts):
    catalog = facts["catalog"]
    if not catalog:
        return ["prize_catalog.json could not be read, so prizes of catalogue problems are not checked"]
    found = []
    if catalog.get("error"):
        found.append(f"the last prize catalogue refresh failed ({str(catalog['error'])[:100]})")
    checked = str(catalog.get("checked") or "")
    if ISO_DATE.fullmatch(checked):
        age = (facts["today"] - dt.date.fromisoformat(checked)).days
        if age > CATALOG_MAX_DAYS:
            found.append(f"the prize catalogue was last refreshed on {checked}, {age} days ago")
    else:
        found.append("the prize catalogue does not say when it was last refreshed")
    return found


def check_updater(facts):
    record = facts["updater"]
    if not record:
        return ["the updater has not run on this computer yet (logs/selfupdate.json is missing): "
                "run `python selfupdate.py install` once"]
    if record.get("state") not in ("skipped", "off"):
        return []
    try:
        hours = (facts["now"] - dt.datetime.fromisoformat(str(record.get("since")))).total_seconds() / 3600
    except ValueError:
        hours = None
    if record.get("state") == "off":
        return [f"the updater is switched off, so changes from GitHub do not arrive ({record.get('result')})"]
    if hours is not None and hours < UPDATER_STUCK_HOURS:
        return []
    return [f"the updater has not been able to update since {record.get('since')}: {record.get('result')}"]


CHECKS = (check_sources, check_money_listed, check_deadlines, check_milestones, check_desks, check_rewards,
          check_catalog, check_updater)


def self_check(facts):
    """Every finding as one sentence. A check that breaks is reported as a finding, never hidden."""
    findings = []
    for check in CHECKS:
        try:
            found = check(facts)
        except Exception as error:
            found = [f"{check.__name__} could not run ({type(error).__name__}: {str(error)[:80]})"]
        findings.extend(found[:FINDINGS_PER_CHECK])
        if len(found) > FINDINGS_PER_CHECK:
            findings.append(f"... and {len(found) - FINDINGS_PER_CHECK} more of the same kind ({check.__name__})")
    return findings


# ------------------------------------------------------------------ the note
def _cell(text, limit=160):
    return " ".join(str(text if text not in (None, "") else "not stated").split()).replace("|", "/")[:limit]


def _runs(desk):
    parts = []
    if desk.get("slots"):
        parts.append(f"{desk['slots']} at once")
    if desk.get("every_hours"):
        parts.append(f"every {desk['every_hours']} h")
    return ", ".join(parts) or "when there is work"


def render(facts, findings):
    today = facts["today"]
    lines = [
        "---", 'title: "Millennium lab: facts"', "type: note", "tags: [millennium, lab, facts]",
        f"updated: {today.isoformat()}", "publish: false", "---", "",
        f"Written by the lab from its own files on {facts['now']:%Y-%m-%d %H:%M} (`lab_facts.py`, once a day). "
        "Read this before saying what the lab does, how fast it is or what it can earn. Nothing here is "
        'estimated: "not stated" means the file does not say. Private: not for sharing.', "",
        "## What the lab is for", "",
        f"> Not on any timeline: {_cell(facts['targets'].get('not_on_timeline'), 400)}", "",
        "Source: `lab_targets.json`.", "",
        "## Desks", "",
        f"| Desk | Works on | Runs | Finished {today - dt.timedelta(days=1)} | Finished in the 7 days before {today} "
        "| Finished in all | Open now |",
        "|---|---|---|---|---|---|---|",
    ]
    known = set()
    for desk in facts["departments"]:
        known.add(desk.get("id"))
        row = facts["per_desk"].get(desk.get("id"), {"yesterday": 0, "week": 0, "total": 0, "open": 0})
        lines.append(f"| {_cell(desk.get('name'))} (`{_cell(desk.get('id'))}`) | {_cell(desk.get('problem'))} "
                     f"| {_runs(desk)} | {row['yesterday']} | {row['week']} | {row['total']} | {row['open']} |")
    for desk_id, row in sorted(facts["per_desk"].items()):
        if desk_id not in known:
            lines.append(f"| `{_cell(desk_id)}` | not a desk in lab_departments.json | - | {row['yesterday']} "
                         f"| {row['week']} | {row['total']} | {row['open']} |")
    lines += ["", "A finished job is one with status done or needs_review; the day is its finish time in the queue. "
              "Source: `lab_departments.json`, `research_queue.json`.", "",
              "## Money: the prizes the lab lists", "",
              "| Prize | Paid by | Amount | Deadline | Fit for this lab | Status |", "|---|---|---|---|---|---|"]
    for prize in facts["prizes"].get("prizes", []):
        lines.append(f"| {_cell(prize.get('name'))} | {_cell(prize.get('sponsor'))} | {_cell(prize.get('amount'), 220)} "
                     f"| {_cell(prize.get('deadline'))} | {_cell(prize.get('fit'))} | {_cell(prize.get('status'))} |")
    lines += ["", "Source: `prizes.json` (its own note says when it was last checked by hand). "
              "The lab never submits, posts or e-mails anything by itself.", "",
              "## Milestones", "", "| Id | Due | Milestone | Done | State |", "|---|---|---|---|---|"]
    for milestone in facts["milestones"]:
        lines.append(f"| {_cell(milestone['id'])} | {_cell(milestone['due'])} | {_cell(milestone['title'], 220)} "
                     f"| {'yes' if milestone['done'] else 'no'} | {_cell(milestone['note'], 220)} |")
    lines += ["", "Source: `lab_targets.json`, measured by `lab.py` where it can be.", "",
              f"## Prize problems the desks finished jobs on since {today - dt.timedelta(days=PROBLEM_DAYS)}", ""]
    if facts["problems"]:
        lines += ["| Problem | Title | Prize in the catalogue | Jobs finished |", "|---|---|---|---|"]
        lines += [f"| {_cell(problem['id'])} | {_cell(problem['title'])} | {_cell(problem['reward'])} | {problem['jobs']} |"
                  for problem in facts["problems"]]
    else:
        lines.append("No finished job in the queue names a catalogue problem (`topic_id`) in that time.")
    catalog = facts["catalog"]
    lines += ["", f"Source: `prize_catalog.json` (refreshed {_cell(catalog.get('checked'))}), `research_queue.json`. "
              "A prize is paid for what the problem's own page asks for, usually a proof or a record, "
              "not for a job on the problem.", "",
              "## Results", "",
              f"- {facts['finished_total']} finished jobs in the queue; {facts['useful']} passed both checks "
              f"(Referee pass and Review Panel pass); {facts['useful_strong']} of those go past a reproduction. "
              "Source: `research_queue.json`.",
              ("- Public notes: "
               + (f"{facts['public_notes']} in the public repo's `notes` folder"
                  if facts["public_notes"] is not None else "the public repo is not on this computer, so not stated")
               + (f"; {facts['published']} of the public ones were put there by the publish bridge, "
                  f"{facts['held']} are on its hold list (`outbox/public_decisions.json`)."
                  if facts["decisions_read"] else "; the bridge's record (`outbox/public_decisions.json`) could not be read.")),
              ]
    score = facts["squares_score"]
    if score:
        lines.append(f"- Squares in Squares: best score {_cell(score.get('score'))}, valid: {_cell(score.get('valid'))} "
                     "(lower is better). Source: `prize_work/squares-in-squares/best_score.json`.")
    else:
        lines.append("- Squares in Squares: score not stated (`best_score.json` could not be read).")
    lines.append(f"- A `submission.csv` for Kaggle is on disk: {'yes' if facts['submission_file'] else 'no'}. "
                 "Whether anything was submitted to Kaggle is not stated in any file the lab keeps.")
    updater = facts["updater"]
    lines += ["", "## Automation", "",
              (f"- Updater, last check {_cell(updater.get('checked'))}: {_cell(updater.get('result'), 300)}. "
               "Source: `logs/selfupdate.json`." if updater
               else "- Updater: has not run on this computer yet (`logs/selfupdate.json` is missing)."),
              "", "## Self-check", ""]
    if findings:
        lines.append(f"{len(findings)} thing(s) to look at. Each is a place where the lab's files disagree or "
                     "something is late; none is changed automatically.")
        lines.append("")
        lines += [f"- {_cell(finding, 400)}" for finding in findings]
    else:
        lines.append("Nothing found: the files agree with each other and nothing is late.")
    return "\n".join(lines) + "\n"


def write_note(path=NOTE, here=HERE, today=None, now=None, milestone_status=None):
    """Writes the note. Returns the findings, or None when the vault folder is not on this computer."""
    path = Path(path)
    if not path.parent.exists():
        return None
    facts = collect(here, today, now, milestone_status, vault=path.parent.parent)  # <vault>/notes/<this note>
    findings = self_check(facts)
    temp = path.with_suffix(".tmp")
    temp.write_text(render(facts, findings), encoding="utf-8")
    temp.replace(path)
    return findings


def main(argv):
    facts = collect(vault=NOTE.parent.parent)
    findings = self_check(facts)
    if "--check" in argv:
        print("\n".join(findings) or "Nothing found.")
        return 1 if findings else 0
    if "--print" in argv:
        print(render(facts, findings))
        return 0
    written = write_note()
    print(f"{NOTE}: " + ("not written, the vault folder is not on this computer" if written is None
                         else f"written, {len(written)} thing(s) to look at"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
