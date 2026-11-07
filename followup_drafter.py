"""Prepares follow-up emails as Gmail DRAFTS when they become due. It never sends anything.

Runs daily, indefinitely (Windows task "Millennium follow-up drafts"). For every outbox file named *_followup.txt:
  * the header lines give To: and Subject:; the body follows the first blank line after the header;
  * it becomes due 21 days after the original email to that address was sent (contacts_log.json, sent_date);
  * if that person has already replied (ledger state replied/answered), it is skipped: answer them instead;
  * when due, gmail_compose.compose() saves it as a Gmail draft and opens your Gmail Drafts. You read it and
    press Send yourself.
Automatic follow-ups (no file needed): any contact whose email was SENT, who has not replied after 21 days and has
no hand-written *_followup.txt, gets ONE short polite follow-up draft (subject "Re: <original>"). Never a second one.
State (which follow-ups were drafted) is kept next to the outbox files, outside the public repo.

  python followup_drafter.py            normal daily run
  python followup_drafter.py --status   print what would happen, change nothing
"""
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTBOX = HERE.parent / "outbox"
STATE_FILE = OUTBOX / "followups_state.json"
LEDGER_FILE = HERE / "contacts_log.json"
WAIT_DAYS = 21
HEADER_KEYS = ("To:", "Subject:", "Thread:", "Status:")
AUTO_TEMPLATE = (
    "{greeting}\n\n"
    "I hope you don't mind a short follow-up to my email of {sent} (\"{subject}\").\n\n"
    "If you have a moment, even a one-line pointer (for example to a paper where this is already known) would help "
    "me a lot. If not, no reply is needed, and I won't write again on this.\n\n"
    "Thank you for your time.\n\n"
    "Best regards,\nJuwen Lie\n"
)


def parse_followup(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = {}
    body_start = 0
    for i, line in enumerate(lines):
        if not line.strip():
            body_start = i + 1
            break
        for key in HEADER_KEYS:
            if line.startswith(key):
                header[key[:-1].lower()] = line[len(key):].strip()
    body = "\n".join(lines[body_start:]).strip() + "\n"
    return header.get("to", "").lower(), header.get("subject", ""), body


def original_entry(address):
    ledger = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    entries = [c for c in ledger.get("contacts", []) if c.get("to", "").lower() == address]
    return entries[-1] if entries else None


def greeting_for(entry):
    """Re-uses the 'Dear ...,' line of the original email; falls back to a neutral greeting."""
    body_file = entry.get("body_file")
    if body_file and Path(body_file).exists():
        for line in Path(body_file).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("Dear "):
                return line.strip()
    return f"Dear {entry.get('name', 'Professor')},"


def auto_followups(today, state, handwritten_addresses):
    ledger = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    rows = []
    latest = {}
    for entry in ledger.get("contacts", []):
        latest[entry.get("to", "").lower()] = entry
    for address, entry in sorted(latest.items()):
        if entry.get("state") != "sent" or not entry.get("sent_date") or address in handwritten_addresses:
            continue
        key = f"auto:{address}"
        due = dt.date.fromisoformat(entry["sent_date"]) + dt.timedelta(days=WAIT_DAYS)
        subject = entry.get("subject", "")
        body = AUTO_TEMPLATE.format(greeting=greeting_for(entry), sent=entry["sent_date"], subject=subject)
        if key in state:
            action = f"done: drafted {state[key]}"
        elif today < due:
            action = f"wait until {due.isoformat()}"
        else:
            action = "draft now"
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        rows.append((key, address, reply_subject, body, action, due))
    return rows


def row_key(path):
    return path if isinstance(path, str) else path.name


def plan(today):
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    rows = []
    for path in sorted(OUTBOX.glob("*_followup.txt")):
        to, subject, body = parse_followup(path)
        entry = original_entry(to)
        if not to or entry is None or not entry.get("sent_date"):
            rows.append((path, to, subject, body, "skip: original email not found as sent", None))
            continue
        due = dt.date.fromisoformat(entry["sent_date"]) + dt.timedelta(days=WAIT_DAYS)
        if path.name in state:
            action = f"done: drafted {state[path.name]}"
        elif entry.get("state") in ("replied", "answered"):
            action = "skip: they replied, answer them instead"
        elif today < due:
            action = f"wait until {due.isoformat()}"
        else:
            action = "draft now"
        rows.append((path, to, subject, body, action, due))
    handwritten = {r[1] for r in rows}
    rows.extend(auto_followups(today, state, handwritten))
    return state, rows


def main():
    today = dt.date.today()
    state, rows = plan(today)
    if "--status" in sys.argv:
        for path, to, _, _, action, _ in rows:
            print(f"{row_key(path):45s} {to:40s} {action}")
        return
    due_rows = [r for r in rows if r[4] == "draft now"]
    if not due_rows:
        return
    import gmail_compose  # imported only when needed (opens Edge / Gmail)

    policy = json.loads((Path(__file__).resolve().parent / "outreach_policy.json").read_text(encoding="utf-8"))
    auto_send = policy.get("auto_send_follow_ups", False)
    for path, to, subject, body, _, _ in due_rows:
        if auto_send:
            try:
                if gmail_compose.send_now(to, subject, body):
                    state[row_key(path)] = f"sent {today.isoformat()}"
                    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
                    gmail_compose.log(f"follow-up SENT automatically to {to}")
                    continue
            except Exception as error:  # fall back to a draft below
                gmail_compose.log(f"auto-send to {to} failed: {error}")
        try:
            result = gmail_compose.compose(to, subject, body, None)
        except Exception as error:  # tell the user rather than fail silently
            gmail_compose.message(f"Follow-up draft for {to} could not be prepared:\n{error}", gmail_compose.MB_ICONWARN)
            continue
        state[row_key(path)] = today.isoformat()
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        gmail_compose.message(f"Follow-up to {to} could not be sent automatically, so it is a draft.\n\n{result}")


if __name__ == "__main__":
    main()
