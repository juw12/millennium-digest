"""Non-spam expert outreach: rule-checked Gmail DRAFTS only (address from config.json), with a contact ledger.

Commands
  python outreach.py status                       show the ledger and what is allowed next
  python outreach.py propose <email_spec.json>    check every rule; if all pass, save an Outlook draft
  python outreach.py mark <address> <state>       record sent | replied | declined | do-not-contact
  python outreach.py sync                         list drafts that still need to be marked sent
  python outreach.py followups                    draft at most one polite follow-up per person when due
  python outreach.py open <address>               reopen the saved Gmail draft link for that person
  python outreach.py send <address>               send a logged draft from Gmail with its PDF (respects holds)

Nothing is ever sent automatically: `send` only runs when Juwen asks for a specific, already-reviewed email.
"""
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY = json.loads((HERE / "outreach_policy.json").read_text(encoding="utf-8"))
EXPERTS = json.loads((HERE / "experts.json").read_text(encoding="utf-8"))
LEDGER_FILE = HERE / "contacts_log.json"
NO_OBLIGATION_LINE = (
    "If this isn't something you have time for, no reply is needed, and I won't write again "
    "on this unless I have something genuinely new."
)
TODAY = dt.date.today()


def load_ledger():
    if LEDGER_FILE.exists():
        return json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    return {"contacts": []}


def save_ledger(ledger):
    LEDGER_FILE.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def days_since(iso_date):
    return (TODAY - dt.date.fromisoformat(iso_date)).days


def check_rules(spec, ledger, is_follow_up=False):
    """Returns a list of rule violations (empty list = allowed)."""
    problems = []
    to = spec["to"].strip().lower()
    if "," in to or ";" in to:
        problems.append("one recipient per email only (no CC, no lists)")
    expert = EXPERTS.get(to)
    if expert is None:
        problems.append(f"{to} is not in experts.json (add name, public email source and their papers first)")
    elif POLICY["require_expert_public_email_source"] and not expert.get("email_source"):
        problems.append("no public source recorded for this address")

    body = spec["body"]
    words = len(body.split())
    if words > POLICY["max_body_words"]:
        problems.append(f"body is {words} words; limit {POLICY['max_body_words']} (shorter emails get read)")
    for rule, needles in POLICY["required_in_body"].items():
        if not any(needle.lower() in body.lower() for needle in needles):
            problems.append(f"body is missing the {rule.replace('_', ' ')} ({needles[0]!r})")
    if expert and not is_follow_up and not any(work.lower() in body.lower() for work in expert["their_work"]):
        problems.append("not personalised: body cites none of this expert's own work")

    attachment = spec.get("attachment")
    if attachment:
        size_mb = Path(attachment).stat().st_size / 1e6
        if size_mb > POLICY["max_attachment_mb"]:
            problems.append(f"attachment is {size_mb:.1f} MB; limit {POLICY['max_attachment_mb']} MB")

    history = [c for c in ledger["contacts"] if c["to"] == to]
    if any(c["state"] in ("declined", "do-not-contact") for c in history):
        problems.append("this person asked not to be contacted")
    unanswered = [c for c in history if c["state"] in ("drafted", "sent")]
    if not is_follow_up:
        if len(unanswered) >= POLICY["max_unanswered_emails_per_person"]:
            problems.append("there is already an unanswered email to this person")
        recent = [c for c in history if days_since(c["date"]) < POLICY["min_days_between_contacts_same_person"]]
        if recent:
            problems.append(f"contacted {days_since(recent[-1]['date'])} days ago; wait {POLICY['min_days_between_contacts_same_person']} days")
        new_last_30 = [c for c in ledger["contacts"] if days_since(c["date"]) < 30 and not c.get("follow_up")]
        if len(new_last_30) >= POLICY["max_new_drafts_per_30_days"]:
            problems.append(f"{len(new_last_30)} new contacts in the last 30 days; limit {POLICY['max_new_drafts_per_30_days']}")
        holds = [c for c in ledger["contacts"] if c.get("topic") == spec.get("topic") and c.get("hold_until")]
        for held in holds:
            if TODAY < dt.date.fromisoformat(held["hold_until"]) and held["to"] != to:
                problems.append(f"topic {spec.get('topic')} is on hold until {held['hold_until']} (waiting for {held['to']})")

        evidence_file = spec.get("evidence")
        if not evidence_file or not Path(evidence_file).exists():
            problems.append("no EVIDENCE.json for the study (pre-registration + gates)")
        else:
            evidence = json.loads(Path(evidence_file).read_text(encoding="utf-8"))
            if not Path(evidence.get("preregistration", "")).exists():
                problems.append("pre-registration file missing")
            if not evidence.get("all_gates_passed"):
                problems.append("study gates have not all passed")
            if attachment:
                same_report = [c for c in history if c.get("report_hash") == file_hash(attachment)]
                if same_report:
                    problems.append("this person already received this exact report")
    return problems


from settings import EMAIL as GMAIL_ACCOUNT, NAME as SENDER_NAME
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def gmail_compose_url(to, subject, body):
    from urllib.parse import quote
    return (
        f"https://mail.google.com/mail/?authuser={GMAIL_ACCOUNT}&view=cm&fs=1"
        f"&to={quote(to)}&su={quote(subject)}&body={quote(body)}"
    )


def save_draft(to, subject, body, attachment=None, open_window=True):
    """Opens a pre-filled Gmail compose window (your Gmail account) and highlights the attachment
    in File Explorer so it can be dragged in. Gmail auto-saves it to Drafts. Nothing is sent."""
    url = gmail_compose_url(to, subject, body)
    if open_window:
        subprocess.Popen([EDGE, "--profile-directory=Default", "--new-window", url])
        if attachment:
            subprocess.Popen(["explorer", "/select,", str(attachment)])
    return url


def propose(spec_path):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    if NO_OBLIGATION_LINE.lower() not in spec["body"].lower():
        spec["body"] = spec["body"].replace("Thank you for your time.", NO_OBLIGATION_LINE + "\n\nThank you for your time.")
    ledger = load_ledger()
    problems = check_rules(spec, ledger)
    if problems:
        print("NOT drafted. Rules not met:")
        for problem in problems:
            print("  -", problem)
        return 1
    compose_url = save_draft(spec["to"], spec["subject"], spec["body"], spec.get("attachment"))
    ledger["contacts"].append({
        "to": spec["to"].lower(),
        "name": EXPERTS[spec["to"].lower()]["name"],
        "topic": spec.get("topic"),
        "subject": spec["subject"],
        "date": TODAY.isoformat(),
        "state": "drafted",
        "report_hash": file_hash(spec["attachment"]) if spec.get("attachment") else None,
        "compose_url": compose_url,
    })
    save_ledger(ledger)
    print(f"Gmail draft opened for {spec['to']}. Drag in the highlighted PDF, review, and press Send yourself.")
    return 0


def mark(address, state):
    ledger = load_ledger()
    entries = [c for c in ledger["contacts"] if c["to"] == address.lower()]
    if not entries:
        print(f"{address} is not in the ledger")
        return 1
    entries[-1]["state"] = state
    entries[-1][f"{state}_date"] = TODAY.isoformat()
    if state in ("replied", "declined", "do-not-contact"):
        for entry in ledger["contacts"]:
            if entry.get("topic") == entries[-1].get("topic"):
                entry.pop("hold_until", None)
    save_ledger(ledger)
    print(f"{address}: {state}")
    return 0


def sync():
    """Gmail cannot be read from this PC, so sent/replied states are recorded with `mark`.
    Returns reminders for drafts that may have been sent but are not yet marked."""
    ledger = load_ledger()
    reminders = []
    for entry in ledger["contacts"]:
        if entry["state"] == "drafted" and days_since(entry["date"]) >= 3:
            reminders.append(f"{entry['to']}: still 'drafted' - if you sent it, run: python outreach.py mark {entry['to']} sent")
    return reminders


def followups(open_windows=True):
    ledger = load_ledger()
    drafted = []
    for entry in ledger["contacts"]:
        if entry["state"] != "sent" or entry.get("follow_up") or entry.get("followed_up"):
            continue
        sent_date = entry.get("sent_date", entry["date"])
        if days_since(sent_date) < POLICY["follow_up_after_days_without_reply"]:
            continue
        follow_ups_so_far = [c for c in ledger["contacts"] if c["to"] == entry["to"] and c.get("follow_up")]
        if len(follow_ups_so_far) >= POLICY["max_follow_ups_per_person"]:
            continue
        name = entry["name"].split()[-1]
        body = (
            f"Dear {entry['name'].split()[0] if 'Nina' in entry['name'] else 'Professor ' + name},\n\n"
            f"A short, single follow-up on my email of {sent_date} (\"{entry['subject']}\"). "
            "I know you are busy. If you could point me to a reference or tell me the effect is already known, "
            "that would be very helpful, but no reply is needed, and I won't write again on this. "
            "(The analysis was done with an AI assistant and checked as carefully as I could.)\n\n"
            f"Is there anything I should read first?\n\nBest regards,\n{SENDER_NAME}\n{GMAIL_ACCOUNT}\n"
        )
        spec = {"to": entry["to"], "subject": "Re: " + entry["subject"], "body": body}
        problems = check_rules(spec, ledger, is_follow_up=True)
        if problems:
            continue
        compose_url = save_draft(spec["to"], spec["subject"], body, open_window=open_windows)
        entry["followed_up"] = TODAY.isoformat()
        ledger["contacts"].append({**{k: entry[k] for k in ("to", "name", "topic")}, "subject": spec["subject"],
                                   "date": TODAY.isoformat(), "state": "drafted", "follow_up": True,
                                   "compose_url": compose_url})
        drafted.append(entry["to"])
    save_ledger(ledger)
    return drafted


def send_logged(address, override_hold=False):
    """Sends an email that is already in the ledger as 'drafted' (body + attachment), from Gmail."""
    import gmail_send
    ledger = load_ledger()
    entries = [c for c in ledger["contacts"] if c["to"] == address.lower() and c["state"] == "drafted"]
    if not entries:
        print(f"no drafted email for {address} in the ledger")
        return 1
    entry = entries[-1]
    holds = [c for c in ledger["contacts"] if c.get("topic") == entry.get("topic") and c.get("hold_until") and c["to"] != entry["to"]]
    active_holds = [h for h in holds if TODAY < dt.date.fromisoformat(h["hold_until"])]
    if active_holds and not override_hold:
        print(f"NOT sent: topic {entry.get('topic')} is on hold until {active_holds[0]['hold_until']} (waiting for {active_holds[0]['to']})")
        return 1
    body_file = Path(entry["body_file"])
    lines = body_file.read_text(encoding="utf-8").splitlines()
    body = "\n".join(lines[4:]) if lines and lines[0].startswith("To:") else "\n".join(lines)
    spec = {"to": entry["to"], "body": body, "attachment": entry.get("attachment")}
    content_problems = [p for p in check_rules(spec, {"contacts": []}, is_follow_up=bool(entry.get("follow_up")))
                        if "EVIDENCE" not in p and "pre-registration" not in p and "gates" not in p]
    if content_problems:
        print("NOT sent. Content rules not met:")
        for problem in content_problems:
            print("  -", problem)
        return 1
    message_id = gmail_send.send(entry["to"], entry["subject"], body, entry.get("attachment"))
    entry["state"] = "sent"
    entry["sent_date"] = TODAY.isoformat()
    entry["message_id"] = message_id
    save_ledger(ledger)
    print(f"SENT from {GMAIL_ACCOUNT} to {entry['to']} with {Path(entry['attachment']).name if entry.get('attachment') else 'no attachment'}")
    return 0


def status():
    ledger = load_ledger()
    print(f"{'to':36} {'topic':5} {'state':10} {'date':10} note")
    for entry in ledger["contacts"]:
        note = f"hold until {entry['hold_until']}" if entry.get("hold_until") else ("follow-up" if entry.get("follow_up") else "")
        print(f"{entry['to']:36} {str(entry.get('topic')):5} {entry['state']:10} {entry['date']:10} {note}")
    new_last_30 = [c for c in ledger["contacts"] if days_since(c["date"]) < 30 and not c.get("follow_up")]
    print(f"\nNew contacts in last 30 days: {len(new_last_30)} (limit {POLICY['max_new_drafts_per_30_days']})")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "propose":
        sys.exit(propose(sys.argv[2]))
    if command == "mark":
        sys.exit(mark(sys.argv[2], sys.argv[3]))
    if command == "send":
        sys.exit(send_logged(sys.argv[2], override_hold="--override-hold" in sys.argv))
    if command == "open":
        matches = [c for c in load_ledger()["contacts"] if c["to"] == sys.argv[2].lower() and c.get("compose_url")]
        if not matches:
            print("no saved draft link for that address")
            sys.exit(1)
        subprocess.Popen([EDGE, "--profile-directory=Default", "--new-window", matches[-1]["compose_url"]])
        sys.exit(0)
    if command == "sync":
        print("\n".join(sync()) or "no changes")
    elif command == "followups":
        print(followups() or "no follow-ups due")
    else:
        sys.exit(status())
