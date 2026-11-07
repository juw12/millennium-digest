"""Prepares follow-up emails as Gmail DRAFTS when they become due. It never sends anything.

Runs daily (Windows task "Millennium follow-up drafts"). For every outbox file named *_followup.txt:
  * the header lines give To: and Subject:; the body follows the first blank line after the header;
  * it becomes due 21 days after the original email to that address was sent (contacts_log.json, sent_date);
  * if that person has already replied (ledger state replied/answered), it is skipped: answer them instead;
  * when due, gmail_compose.compose() saves it as a Gmail draft and opens your Gmail Drafts. You read it and
    press Send yourself.
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
    return state, rows


def main():
    today = dt.date.today()
    state, rows = plan(today)
    if "--status" in sys.argv:
        for path, to, _, _, action, _ in rows:
            print(f"{path.name:40s} {to:40s} {action}")
        return
    due_rows = [r for r in rows if r[4] == "draft now"]
    if not due_rows:
        return
    import gmail_compose  # imported only when needed (opens Edge / Gmail)

    for path, to, subject, body, _, _ in due_rows:
        try:
            result = gmail_compose.compose(to, subject, body, None)
        except Exception as error:  # tell the user rather than fail silently
            gmail_compose.message(f"Follow-up draft for {to} could not be prepared:\n{error}", gmail_compose.MB_ICONWARN)
            continue
        state[path.name] = today.isoformat()
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        gmail_compose.message(f"Follow-up to {to} is ready (21 days without reply).\n\n{result}")


if __name__ == "__main__":
    main()
