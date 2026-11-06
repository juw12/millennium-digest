"""Handles the dashboard buttons (millennium:// links). Nothing is ever sent from here.

  millennium://open/<address>   open the logged draft in Gmail, filled in, and highlight its PDF in
                                File Explorer so you can drag it in and press Send yourself
  millennium://sent/<address>   after you sent it: mark it as sent in the ledger (asks first)

Registered for the current Windows user by register_buttons.ps1.
"""
import ctypes
import datetime as dt
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import outreach

HERE = Path(__file__).resolve().parent
TITLE = "Millennium Dashboard"
MB_OK = 0x0
MB_YESNO = 0x4
MB_ICONINFO = 0x40
MB_ICONWARN = 0x30
MB_TOPMOST = 0x40000
IDYES = 6


def message(text, flags=MB_OK | MB_ICONINFO):
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | MB_TOPMOST)


def latest_entry(ledger, address):
    entries = [c for c in ledger["contacts"] if c["to"] == address]
    return entries[-1] if entries else None


def active_topic_hold(ledger, entry):
    today = dt.date.today()
    for other in ledger["contacts"]:
        if (
            other.get("topic") == entry.get("topic")
            and other["to"] != entry["to"]
            and other.get("hold_until")
            and dt.date.fromisoformat(other["hold_until"]) > today
        ):
            return other
    return None


def draft_body(entry):
    lines = Path(entry["body_file"]).read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("To:"):
        lines = lines[4:]
    return "\n".join(lines).strip() + "\n"


def open_draft(address):
    ledger = outreach.load_ledger()
    entry = latest_entry(ledger, address)
    if entry is None or entry["state"] != "drafted":
        message(f"No open draft for {address} in the ledger.", MB_OK | MB_ICONWARN)
        return
    hold = active_topic_hold(ledger, entry)
    if hold:
        message(
            f"Not opened: the {entry.get('topic')} topic is on hold until {hold['hold_until']} "
            f"(waiting for {hold.get('name', hold['to'])}).",
            MB_OK | MB_ICONWARN,
        )
        return
    limit = outreach.POLICY.get("max_new_drafts_per_30_days", 2)
    recent = [c for c in ledger["contacts"] if outreach.days_since(c["date"]) < 30 and not c.get("follow_up")]
    if len(recent) > limit:
        answer = message(
            f"Your rule allows {limit} new contacts per 30 days and the ledger has {len(recent)} "
            f"(drafts count).\n\nOpen this draft anyway?",
            MB_YESNO | MB_ICONWARN,
        )
        if answer != IDYES:
            return
    url = outreach.gmail_compose_url(entry["to"], entry["subject"], draft_body(entry))
    subprocess.Popen([outreach.EDGE, "--profile-directory=Default", "--new-window", url])
    attachment = entry.get("attachment")
    if attachment and Path(attachment).exists():
        subprocess.Popen(["explorer", "/select,", str(attachment)])
        steps = (
            "Gmail is open with the email filled in, and the PDF is highlighted in File Explorer.\n\n"
            "1. Drag the PDF into the email.\n"
            "2. Read it once, then press Send.\n"
            "3. Back on the dashboard, click 'I sent it'."
        )
    else:
        steps = "Gmail is open with the email filled in.\nPress Send, then click 'I sent it' on the dashboard."
    message(steps)


def mark_sent(address):
    ledger = outreach.load_ledger()
    entry = latest_entry(ledger, address)
    if entry is None:
        message(f"{address} is not in the ledger.", MB_OK | MB_ICONWARN)
        return
    if entry["state"] != "drafted":
        message(f"{entry.get('name', address)} is already marked '{entry['state']}'.")
        return
    answer = message(
        f"Did you send the email to {entry.get('name', address)}?\n\n"
        "This marks it as sent and starts the 21-day reply clock.",
        MB_YESNO | MB_ICONINFO,
    )
    if answer != IDYES:
        return
    outreach.mark(address, "sent")
    subprocess.run(
        [str(Path(sys.executable).with_name("python.exe")), str(HERE / "dashboard.py")],
        cwd=HERE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    message("Logged as sent. Press F5 on the dashboard to see it.")


def main():
    if len(sys.argv) < 2:
        message("No action given.", MB_OK | MB_ICONWARN)
        return
    parsed = urlparse(sys.argv[1])
    action = parsed.netloc.lower()
    address = unquote(parsed.path.strip("/")).lower()
    if action == "open":
        open_draft(address)
    elif action == "sent":
        mark_sent(address)
    else:
        message(f"Unknown action: {action}", MB_OK | MB_ICONWARN)


if __name__ == "__main__":
    main()
