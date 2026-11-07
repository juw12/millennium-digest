"""Handles the dashboard buttons (millennium:// links). Nothing is ever sent from here.

  millennium://open/<address>   open the logged draft in your pinned Gmail app (gmail_app.py), filled
                                in, with its PDF pasted in; you read it and press Send yourself
  millennium://reply/<address>  open your drafted answer to someone who replied (outbox/*reply*.txt)
  millennium://refresh          rebuild the page

Sent / replied / answered are detected in Gmail by gmail_inbox.py (every 2 h, plus +5/+15/+30 min
after open/reply/thread), so the dashboard no longer shows "I sent it" or "They replied" buttons.
The old links (sent/, answered/, replied/) still work as a manual fallback.

handle() is used by both the desktop app (in-process) and the millennium:// link handler for a browser
(registered for the current Windows user by register_buttons.ps1). Every press is written to the
activity log (history.py).
"""
import ctypes
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import gmail_app
import history
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
    # Your pinned Gmail app (Chrome, already signed in): filled in, PDF pasted in, you press Send.
    if gmail_app.open_compose(entry["to"], entry["subject"], draft_body(entry), entry.get("attachment")):
        return
    helper = python_for_helpers()
    if helper:
        # Runs in the background: opens Gmail, attaches the PDF, then shows a short note.
        subprocess.Popen(
            [str(helper), str(HERE / "gmail_compose.py"), entry["to"]],
            cwd=HERE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    # Fallback without Python + Playwright: filled-in Gmail plus the PDF highlighted for dragging.
    url = outreach.gmail_compose_url(entry["to"], entry["subject"], draft_body(entry))
    subprocess.Popen([outreach.EDGE, "--profile-directory=Default", "--new-window", url])
    attachment = entry.get("attachment")
    if attachment and Path(attachment).exists():
        subprocess.Popen(["explorer", "/select,", str(attachment)])
    message("Gmail is open with the email filled in; drag the highlighted PDF in, then press Send.")


def python_for_helpers():
    """pythonw.exe that has Playwright (the desktop .exe cannot import it itself)."""
    candidates = []
    if not getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).with_name("pythonw.exe"))
    found = shutil.which("pythonw")
    if found:
        candidates.append(Path(found))
    local = os.environ.get("LOCALAPPDATA", "")
    candidates += sorted(Path(local, "Programs", "Python").glob("Python3*/pythonw.exe"), reverse=True)
    for candidate in candidates:
        if candidate.exists() and (candidate.parent / "Lib" / "site-packages" / "playwright").exists():
            return candidate
    return None


def mark_sent(address, rebuild=True):
    ledger = outreach.load_ledger()
    entry = latest_entry(ledger, address)
    if entry is None:
        message(f"{address} is not in the ledger.", MB_OK | MB_ICONWARN)
        return False
    if entry["state"] != "drafted":
        message(f"{entry.get('name', address)} is already marked '{entry['state']}'.")
        return False
    answer = message(
        f"Did you send the email to {entry.get('name', address)}?\n\n"
        "This marks it as sent and starts the 21-day reply clock.",
        MB_YESNO | MB_ICONINFO,
    )
    if answer != IDYES:
        return False
    outreach.mark(address, "sent")
    history.record("button", f"You marked the email to {entry.get('name', address)} as sent")
    if not rebuild:
        return True
    subprocess.run(
        [str(Path(sys.executable).with_name("python.exe")), str(HERE / "dashboard.py")],
        cwd=HERE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    message("Logged as sent. Press F5 on the dashboard to see it.")
    return True


def _reply_draft(address):
    import dashboard_extras

    path = dashboard_extras.find_reply_draft(address)
    if path is None:
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    subject = lines[1].split(":", 1)[1].strip() if len(lines) > 1 and lines[1].lower().startswith("subject:") else ""
    body = "\n".join(lines[3:]).strip() + "\n"
    return address, subject, body


def open_reply(address):
    """Your drafted answer to someone who replied. If gmail_inbox.py already saved it as a draft in the
    Gmail thread, that thread opens; otherwise Gmail compose opens filled in (no attachment)."""
    entry = latest_entry(outreach.load_ledger(), address)
    if entry and (entry.get("reply_draft") or {}).get("gmail") and entry.get("gmail_thread"):
        history.record("button", f"You opened the reply draft to {address} (Gmail thread)")
        return open_thread(address)
    draft = _reply_draft(address)
    if draft is None:
        message(f"No drafted reply for {address} in the outbox folder.", MB_OK | MB_ICONWARN)
        return False
    history.record("button", f"You opened the reply draft to {address}")
    to, subject, body = draft
    if gmail_app.open_compose(to, subject, body):
        return False
    helper = python_for_helpers()
    if helper:
        subprocess.Popen(
            [str(helper), str(HERE / "gmail_compose.py"), "--reply", address],
            cwd=HERE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return False
    to, subject, body = draft
    subprocess.Popen([outreach.EDGE, "--profile-directory=Default", "--new-window", outreach.gmail_compose_url(to, subject, body)])
    message("Gmail is open with your reply filled in. Read it, press Send, then click 'I sent it'.")
    return False


def open_thread(address):
    """Opens your Gmail thread with this person in the pinned Gmail app (drafts show inside it)."""
    import gmail_inbox

    entry = latest_entry(outreach.load_ledger(), address)
    thread = (entry or {}).get("gmail_thread")
    url = gmail_inbox.thread_link(thread) if thread else (
        f"https://mail.google.com/mail/?authuser={outreach.GMAIL_ACCOUNT}#search/{address}"
    )
    command = gmail_app.chrome_command()
    if command:
        subprocess.Popen(command + [f"--app={url}"])
    else:
        subprocess.Popen([outreach.EDGE, "--profile-directory=Default", "--new-window", url])
    return False


def check_gmail_now():
    """Runs gmail_inbox.py in the background; it rebuilds the dashboard and tells you if a draft is waiting."""
    helper = python_for_helpers()
    if helper is None:
        message("Python was not found, so Gmail cannot be checked from here.", MB_OK | MB_ICONWARN)
        return False
    pythonw = Path(helper).with_name("pythonw.exe")
    subprocess.Popen([str(pythonw if pythonw.exists() else helper), str(HERE / "gmail_inbox.py")], cwd=HERE,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    history.record("button", "You asked for a Gmail check")
    message("Checking Gmail in the background (about 3 minutes). A message appears if a draft is waiting; "
            "the dashboard updates by itself when the check is done.")
    return False


def check_gmail_soon():
    """After a button opened something to send: Gmail is checked again at +5, +15 and +30 minutes,
    so the ledger shows 'sent' without an 'I sent it' click."""
    helper = python_for_helpers()
    if helper is None:
        return
    subprocess.Popen([str(helper), str(HERE / "gmail_inbox.py"), "--soon"], cwd=HERE,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


CATCH_UP_AFTER_H = 2.5  # the scheduled check runs every 2 h; older than this means the PC slept through it


def poll(stamp):
    """Called by the open dashboard every 10 s (desktop app only). Returns True when the data changed
    since the page was built, so the app rebuilds and reloads it. Also starts a catch-up Gmail check when
    the PC slept through the scheduled ones. Never shows a message box: errors just mean 'no change'."""
    try:
        import dashboard_interactive
        import dashboard_extras
        import gmail_inbox

        built = int(stamp)
        state = gmail_inbox.load_state()
        last = dt.datetime.fromisoformat(state["at"]) if state.get("at") else None
        ui = dashboard_extras.ui_state()
        started = ui.get("auto_check_started")
        started_at = dt.datetime.fromisoformat(started) if started else None
        now = dt.datetime.now()
        stale = last is None or (now - last).total_seconds() > CATCH_UP_AFTER_H * 3600
        recently_started = started_at and (now - started_at).total_seconds() < 3600
        if stale and not recently_started:
            helper = python_for_helpers()
            if helper:
                ui["auto_check_started"] = now.isoformat(timespec="seconds")
                dashboard_extras.save_ui_state(ui)
                subprocess.Popen([str(helper), str(HERE / "gmail_inbox.py")], cwd=HERE,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                history.record("watch", "Gmail check started (the PC missed the scheduled one)")
        return dashboard_interactive.inputs_changed_since(built)
    except Exception:
        return False


def mark_answered(address):
    ledger = outreach.load_ledger()
    entry = latest_entry(ledger, address)
    if entry is None:
        message(f"{address} is not in the ledger.", MB_OK | MB_ICONWARN)
        return False
    name = entry.get("name", address)
    if message(f"Did you send your reply to {name}?", MB_YESNO | MB_ICONINFO) != IDYES:
        return False
    entry["answer_sent_date"] = dt.date.today().isoformat()
    outreach.save_ledger(ledger)
    history.record("button", f"You sent your thank-you reply to {name}")
    return True


def mark_replied(address):
    ledger = outreach.load_ledger()
    entry = latest_entry(ledger, address)
    if entry is None or entry["state"] != "sent":
        message(f"{address} is not waiting for a reply.", MB_OK | MB_ICONWARN)
        return False
    name = entry.get("name", address)
    if message(f"Did {name} reply?\n\nThis stops the follow-up clock.", MB_YESNO | MB_ICONINFO) != IDYES:
        return False
    outreach.mark(address, "replied")
    history.record("button", f"You recorded a reply from {name}")
    return True


def mark_seen(page):
    import dashboard
    import dashboard_extras

    digests = dashboard.digest_history()
    if not digests:
        return False
    state = dashboard_extras.ui_state()
    state.setdefault("seen_pages", {})[page] = digests[-1]["date"]
    dashboard_extras.save_ui_state(state)
    history.record("button", f"You looked at the changed page: {page}")
    return True


def reload_app_window():
    """Rebuilds the page and makes the desktop window really reload it.
    The app's own rebuild_and_show() points the window at the URL it already shows, which WebView2
    ignores, so new data never appeared. A JavaScript reload always re-reads the file."""
    import dashboard
    import webview  # bundled in the desktop app; only called when running inside it

    dashboard.build()
    windows = list(getattr(webview, "windows", []))
    _app_log(f"reload_app_window: {len(windows)} window(s)")
    for window in windows:
        window.evaluate_js("window.location.reload()")
    return bool(windows)


def _app_log(text):
    with (HERE / "app_actions.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")


def handle(action, argument, in_app=False):
    """Run one button. Returns True when the caller should rebuild the page itself.
    Inside the desktop app the rebuild + reload happens here (see reload_app_window)."""
    result = _dispatch(action, argument, in_app)
    if result and in_app:
        try:
            if reload_app_window():
                return False
        except Exception as error:
            _app_log(f"reload_app_window failed: {error!r}")  # fall back to the app's own rebuild
    return result


def _dispatch(action, argument, in_app=False):
    """Run one button. Returns True when the page should be rebuilt."""
    if action == "poll":
        return poll(argument)
    if action == "open":
        history.record("button", f"You opened the draft to {argument}")
        open_draft(argument)
        check_gmail_soon()
        return False
    if action == "sent":
        return mark_sent(argument, rebuild=not in_app) and in_app
    if action == "reply":
        result = open_reply(argument)
        check_gmail_soon()
        return result
    if action == "thread":
        result = open_thread(argument)
        check_gmail_soon()
        return result
    if action == "checkmail":
        return check_gmail_now()
    if action == "answered":
        return mark_answered(argument)
    if action == "replied":
        return mark_replied(argument)
    if action == "seen":
        return mark_seen(argument)
    if action == "refresh":
        return True
    message(f"Unknown action: {action}", MB_OK | MB_ICONWARN)
    return False


def main():
    if len(sys.argv) < 2:
        message("No action given.", MB_OK | MB_ICONWARN)
        return
    parsed = urlparse(sys.argv[1])
    action = parsed.netloc.lower()
    argument = unquote(parsed.path.strip("/"))
    if action != "seen":
        argument = argument.lower()
    if handle(action, argument):  # "sent" rebuilds and confirms by itself outside the app
        subprocess.run(
            [str(Path(sys.executable).with_name("python.exe")), str(HERE / "dashboard.py")],
            cwd=HERE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        message("Done. Press F5 on the dashboard to see it.")


if __name__ == "__main__":
    main()
