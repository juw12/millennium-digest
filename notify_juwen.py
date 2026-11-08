"""Emails Juwen (juwpegatron@gmail.com only) when the automation needs him, e.g. a Gmail sign-in.

Uses the same Gmail relay as the job digest (it can only send to juwpegatron@gmail.com and does not
depend on the Edge helper being signed in). Each kind of notice is sent at most once per `min_hours`.
"""
import datetime as dt
import html
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "notify_state.json"  # local, git-ignored
LOG_FILE = HERE / "notify.log"
JOB_DIGEST = Path(r"C:\Users\juwen_lie\Desktop\automation\1_job-hunting\job-digest\job_digest.py")


def _log(text):
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {text}\n")


def _relay_sender():
    spec = importlib.util.spec_from_file_location("job_digest", JOB_DIGEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.send_via_gmail


def notify(key, subject, text, min_hours=12):
    """Sends one short email to Juwen unless the same `key` was sent in the last `min_hours`. Returns True if sent."""
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    last = state.get(key)
    if last and dt.datetime.now() - dt.datetime.fromisoformat(last) < dt.timedelta(hours=min_hours):
        return False
    body = "<p>" + "</p><p>".join(html.escape(part) for part in text.strip().split("\n\n")) + "</p>"
    body += "<p style='color:#666'>Sent automatically by the Millennium research automation on your PC.</p>"
    try:
        _relay_sender()(None, f"[Millennium] {subject}", body)
    except Exception as error:
        _log(f"notice '{key}' FAILED: {error}")
        return False
    state[key] = dt.datetime.now().isoformat(timespec="seconds")
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _log(f"notice '{key}' sent: {subject}")
    return True


def gmail_sign_in_needed(where):
    return notify(
        "gmail_sign_in",
        "Gmail needs you to sign in once",
        f"The automatic email sender found the Gmail helper window signed out ({where}).\n\n"
        "What to do (1 minute): on your PC, open the Edge window titled Gmail that the automation opened, "
        "sign in as juwpegatron@gmail.com, and close nothing else. If no window is open, double-click "
        "E:\\Projects\\MillenniumResearch\\automation\\gmail_compose.py or wait for the next 2-hourly check to open it.\n\n"
        "Until then, emails to experts wait as drafts; nothing is lost.",
    )


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        print("sent" if notify("test", "Test notice", "This is a test of the Millennium notice email.", 0) else "not sent")
