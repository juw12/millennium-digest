"""Everything that happened, in one list: app versions (from git) and an activity log.

activity_log.jsonl (local, git-ignored) gets one line per button press in the app. The timeline also
derives older events from files that already record them: the outreach ledger dates, weekly digests,
the weekly job log and the study reports, so nothing done before the log existed is lost.
"""
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ACTIVITY_FILE = HERE / "activity_log.jsonl"
GIT_FALLBACKS = [
    Path.home() / "AppData" / "Local" / "Programs" / "Git" / "cmd" / "git.exe",
    Path(r"C:\Program Files\Git\cmd\git.exe"),
]


def record(kind, text, **extra):
    """Append one event; never raises (logging must not break a button)."""
    entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "text": text, **extra}
    try:
        with ACTIVITY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _git():
    found = shutil.which("git")
    if found:
        return found
    return next((str(path) for path in GIT_FALLBACKS if path.exists()), None)


def versions():
    """Every commit of this repo, oldest = v0.1. Returns newest first:
    [{"version", "date", "hash", "subject"}]."""
    git = _git()
    if not git:
        return []
    try:
        result = subprocess.run(
            [git, "-C", str(HERE), "log", "--reverse", "--date=format:%Y-%m-%d %H:%M", "--pretty=%h%x09%ad%x09%s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for index, line in enumerate(result.stdout.splitlines(), start=1):
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append({"version": f"v0.{index}", "hash": parts[0], "date": parts[1], "subject": parts[2]})
    return rows[::-1]


def _recorded():
    events = []
    if ACTIVITY_FILE.exists():
        for line in ACTIVITY_FILE.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def _derived(ledger):
    events = []
    for contact in ledger.get("contacts", []):
        name = contact.get("name", contact["to"])
        topic = contact.get("topic", "")
        for field, label in (
            ("date", "Email drafted"),
            ("sent_date", "Email sent"),
            ("replied_date", "Reply received from"),
            ("answer_sent_date", "Thank-you sent to"),
            ("declined_date", "Declined:"),
        ):
            if contact.get(field):
                events.append({"at": contact[field], "kind": "email", "text": f"{label} {name} ({topic})"})
    for path in sorted((HERE / "digests").glob("digest_*.md")):
        day = path.stem.replace("digest_", "")
        events.append({"at": day, "kind": "digest", "text": "Weekly arXiv digest written"})
    log_path = HERE / "weekly.log"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d):\d\d\s+(weekly run (started|finished).*|publish: .*)", line)
            if match:
                events.append({"at": match.group(1), "kind": "job", "text": "Weekly job: " + match.group(2)})
    for study in ("bsd_project", "rh_project"):
        evidence_path = ROOT / study / "EVIDENCE.json"
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        report = Path(evidence.get("report", ""))
        if report.is_file():
            stamp = dt.datetime.fromtimestamp(report.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            events.append({"at": stamp, "kind": "study", "text": f"Study report written: {evidence.get('study', study)}"})
    return events


def activity(ledger, limit=80):
    """Newest first. Recorded button presses win over derived duplicates on the same day."""
    seen = set()
    merged = []
    for event in sorted(_recorded() + _derived(ledger), key=lambda e: str(e["at"]), reverse=True):
        key = (str(event["at"])[:10], event["text"].lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
    return merged[:limit]
