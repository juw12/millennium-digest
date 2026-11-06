"""Progress per Millennium problem: papers tracked, our study, every email and what happens next.

Pure read-only summary of local files (digests, study EVIDENCE.json, outreach ledger, roadmap.json).
dashboard.py renders it; nothing here sends, writes or changes state.
"""
import datetime as dt
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ROADMAP_FILE = HERE / "roadmap.json"  # local, editable: "next on my end" per problem

# key, name, digest sections that count for it, ledger topic code, study folder
PROBLEMS = [
    ("RH", "Riemann Hypothesis",
     ["Riemann Hypothesis", "Riemann zeros: spacing statistics (our RH study)"], "RH", "rh_project"),
    ("BSD", "Birch and Swinnerton-Dyer",
     ["Birch and Swinnerton-Dyer", "Murmurations (our BSD study)"], "BSD", "bsd_project"),
    ("NS", "Navier-Stokes regularity", ["Navier-Stokes regularity"], "NS", None),
    ("YM", "Yang-Mills mass gap", ["Yang-Mills mass gap"], "YM", None),
    ("PNP", "P vs NP", ["P vs NP"], "PNP", None),
    ("HODGE", "Hodge conjecture", ["Hodge conjecture"], "HODGE", None),
    ("POINCARE", "Poincare conjecture", [], "POINCARE", None),
]
SOLVED = {"POINCARE": "Solved (Perelman, 2003). Not tracked."}
STAGES = ["Watching papers", "Own study", "Report written", "Experts contacted", "Expert feedback"]


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _date(value):
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def digest_counts():
    """{section name: [(date, count), ...]} over every weekly digest, oldest first."""
    series = {}
    for path in sorted((HERE / "digests").glob("digest_*.md")):
        day = path.stem.replace("digest_", "")
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"## (.+?) \((\d+) new\)", line)
            if match:
                series.setdefault(match.group(1), []).append((day, int(match.group(2))))
    return series


def email_timeline(contact, policy, today):
    """Ordered steps for one contact, each (label, date or None, done flag)."""
    follow_up_days = policy.get("follow_up_after_days_without_reply", 21)
    drafted = _date(contact.get("date"))
    sent = _date(contact.get("sent_date"))
    replied = _date(contact.get("replied_date"))
    answered = _date(contact.get("answer_sent_date"))
    steps = [("Drafted", drafted, True)]
    if sent:
        steps.append(("Sent", sent, True))
    elif contact.get("hold_until"):
        steps.append((f"On hold until {contact['hold_until']}", None, False))
    else:
        steps.append(("Waiting for your Send", None, False))
    if replied:
        steps.append(("Reply received", replied, True))
        if contact.get("reply_draft"):
            steps.append(("Thank-you sent" if answered else "Thank-you drafted, not sent", answered, bool(answered)))
    elif sent and contact.get("state") == "sent":
        waited = (today - sent).days
        due = sent + dt.timedelta(days=follow_up_days)
        steps.append((f"Waiting for reply, day {waited} of {follow_up_days}", None, False))
        steps.append((f"One follow-up allowed from {due.isoformat()}", None, False))
    elif contact.get("state") in ("declined", "do-not-contact"):
        steps.append((contact["state"].replace("-", " ").capitalize(), _date(contact.get(f"{contact['state']}_date")), True))
    return steps


def all_problems():
    """The fixed list plus any problem the Clay watch added (clay_watch.json)."""
    try:
        import clay_watch

        extra = [(p["key"], p["name"], [p["name"]], p["key"], None) for p in clay_watch.auto_problems()]
        return PROBLEMS + extra
    except Exception:
        return PROBLEMS


def watch_notes(key):
    try:
        import clay_watch

        return clay_watch.notes(key)
    except Exception:
        return []


def problems(ledger, policy, today=None):
    today = today or dt.date.today()
    roadmap = _read_json(ROADMAP_FILE, {})
    counts = digest_counts()
    result = []
    for key, name, sections, topic, study_dir in all_problems():
        latest = sum(counts[s][-1][1] for s in sections if s in counts)
        total = sum(c for s in sections if s in counts for _, c in counts[s])
        weeks = max((len(counts[s]) for s in sections if s in counts), default=0)
        study = None
        if study_dir:
            evidence = _read_json(ROOT / study_dir / "EVIDENCE.json", {})
            if evidence:
                study = {
                    "name": evidence.get("study", study_dir),
                    "gates_passed": bool(evidence.get("all_gates_passed")),
                    "gates": evidence.get("gates", ""),
                    "report": evidence.get("report", ""),
                    "findings": str(ROOT / study_dir / "FINDINGS.md"),
                }
        contacts = [c for c in ledger.get("contacts", []) if c.get("topic") == topic]
        emails = [
            {
                "name": c.get("name", c["to"]),
                "address": c["to"],
                "state": c.get("state", ""),
                "note": c.get("note", ""),
                "steps": email_timeline(c, policy, today),
            }
            for c in contacts
        ]
        stage = 0 if key not in SOLVED else -1
        if study:
            stage = 1
            if study["report"] and Path(study["report"]).exists():
                stage = 2
        if any(c.get("sent_date") for c in contacts):
            stage = max(stage, 3)
        if any(c.get("state") == "replied" for c in contacts):
            stage = max(stage, 4)
        result.append({
            "key": key,
            "name": name,
            "solved": SOLVED.get(key),
            "papers_latest": latest,
            "papers_total": total,
            "weeks": weeks,
            "study": study,
            "emails": emails,
            "stage": stage,
            "next": roadmap.get(key, {}).get("next", "") or "Watching new arXiv papers in the Monday digest.",
            "done": roadmap.get(key, {}).get("done", []),
            "watch_notes": watch_notes(key),
        })
    return result
