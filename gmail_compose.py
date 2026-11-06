"""Opens a Gmail draft with the PDF already attached. Nothing is ever sent: you read it and press Send.

How it works (no password or API key is stored):
  * Edge runs with its own small profile in automation\\edge_gmail_profile (gitignored), with a local-only
    control port. The first time, you sign in to Gmail in that window once; Edge remembers it.
  * The script opens Gmail's compose page with To / Subject / Body filled in, waits until Gmail has
    loaded, then puts the PDF into Gmail's own "Attach files" input and waits until the upload shows.
  * It does not connect to the window while the Google sign-in page is open.

  python gmail_compose.py <address>    open the logged draft for <address> (from contacts_log.json)
  python gmail_compose.py --test       compose a test email to yourself with the BSD report attached
"""
import ctypes
import json
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import outreach  # noqa: E402

PROFILE_DIR = HERE / "edge_gmail_profile"
DEBUG_PORT = 9333
SIGN_IN_WAIT_S = 600
UPLOAD_WAIT_S = 90
TITLE = "Millennium Dashboard"
MB_ICONINFO = 0x40
MB_ICONWARN = 0x30
MB_TOPMOST = 0x40000


LOG_FILE = HERE / "gmail_compose.log"


def log(text):
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")


def message(text, flags=MB_ICONINFO):
    log(text.replace("\n", " | "))
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags | MB_TOPMOST)


def tabs():
    """Tab list from Edge's local control port, or None if this Edge window is not running."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2) as response:
            return [t for t in json.load(response) if t.get("type") == "page"]
    except OSError:
        return None


def open_compose_tab(url):
    if tabs() is None:
        subprocess.Popen([
            outreach.EDGE,
            f"--user-data-dir={PROFILE_DIR}",
            f"--remote-debugging-port={DEBUG_PORT}",
            "--remote-allow-origins=http://127.0.0.1",
            "--no-first-run",
            "--new-window",
            url,
        ])
        for _ in range(40):
            time.sleep(0.5)
            if tabs() is not None:
                break
        else:
            raise RuntimeError("Edge did not start its Gmail window.")
    else:
        for tab in tabs():
            if "accounts.google.com" in tab["url"]:
                urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/close/{tab['id']}", timeout=5).close()
        request = urllib.request.Request(
            f"http://127.0.0.1:{DEBUG_PORT}/json/new?{urllib.parse.quote(url, safe='')}",
            method="PUT",
        )
        urllib.request.urlopen(request, timeout=5).close()


def wait_for_gmail(started_at):
    """Waits (without connecting) until a tab is on mail.google.com, i.e. you are signed in."""
    shown_hint = False
    misses = 0
    while time.time() - started_at < SIGN_IN_WAIT_S:
        open_tabs = tabs()
        if open_tabs is None:
            misses += 1
            if misses >= 10:
                raise RuntimeError("The Gmail window was closed.")
            time.sleep(1.5)
            continue
        misses = 0
        if any(t["url"].startswith("https://mail.google.com/mail/") for t in open_tabs):
            return
        if not shown_hint and any("accounts.google.com" in t["url"] for t in open_tabs):
            shown_hint = True
            hint = (
                "First time only: sign in to Gmail in the Edge window that just opened.\n\n"
                "The draft and the PDF will appear by themselves after you sign in."
            )
            threading.Thread(target=message, args=(hint,), daemon=True).start()
        time.sleep(1.5)
    raise RuntimeError("Gave up waiting for the Gmail sign-in (10 minutes).")


def attach(attachment, subject):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        pages = [p for c in browser.contexts for p in c.pages if p.url.startswith("https://mail.google.com/")]
        page = pages[-1]
        page.bring_to_front()
        file_input = page.locator('input[type="file"][name="Filedata"]').last
        file_input.wait_for(state="attached", timeout=60_000)
        page.wait_for_selector('input[name="subjectbox"]', timeout=60_000)
        file_input.set_input_files(str(attachment))
        chip = page.locator(f'[aria-label*="{Path(attachment).name}"], div:text-is("{Path(attachment).name}")')
        chip.first.wait_for(state="visible", timeout=UPLOAD_WAIT_S * 1000)
        progress = page.locator('div[role="progressbar"]')
        deadline = time.time() + UPLOAD_WAIT_S
        while progress.count() and time.time() < deadline:
            time.sleep(0.5)
        attached = chip.count() > 0
        subject_now = page.locator('input[name="subjectbox"]').last.input_value()
        # Leave Edge and the draft open for you; just disconnect.
    return attached, subject_now == subject


def compose(to, subject, body, attachment):
    started_at = time.time()
    open_compose_tab(outreach.gmail_compose_url(to, subject, body))
    wait_for_gmail(started_at)
    time.sleep(1.0)
    if not attachment:
        return "Gmail is open with the email filled in. Read it, press Send, then click 'I sent it'."
    attached, subject_ok = attach(attachment, subject)
    if not attached:
        raise RuntimeError("Gmail opened, but the PDF upload did not finish; attach it by hand.")
    note = "" if subject_ok else "\n(Check the subject line; it did not match the ledger.)"
    return (
        f"Draft ready in Gmail with {Path(attachment).name} attached.\n\n"
        "Read it once, press Send, then click 'I sent it' on the dashboard." + note
    )


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    if sys.argv[1] == "--test":
        to = outreach.GMAIL_ACCOUNT
        subject = "Millennium Dashboard test - attachment check (do not send)"
        body = "Test of the Open draft button. Delete this draft.\n"
        attachment = HERE.parent / "bsd_project" / "report" / "Murmuration_amplitude_report.pdf"
    else:
        address = sys.argv[1].lower()
        entries = [c for c in outreach.load_ledger()["contacts"] if c["to"] == address and c["state"] == "drafted"]
        if not entries:
            message(f"No open draft for {address} in the ledger.", MB_ICONWARN)
            return
        entry = entries[-1]
        import action

        to, subject, body = entry["to"], entry["subject"], action.draft_body(entry)
        attachment = entry.get("attachment")
        if attachment and not Path(attachment).exists():
            message(f"The PDF for this draft is missing:\n{attachment}", MB_ICONWARN)
            return
    try:
        result = compose(to, subject, body, attachment)
    except Exception as error:  # report anything to the user instead of failing silently
        message(f"Could not finish the draft automatically:\n{error}", MB_ICONWARN)
        if attachment:
            subprocess.Popen(["explorer", "/select,", str(attachment)])
        return
    message(result)


if __name__ == "__main__":
    main()
