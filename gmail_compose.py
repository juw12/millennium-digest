"""Opens a Gmail draft with the PDF already attached; you read it and press Send.
Exception: send_now() sends follow-ups automatically (user request 2026-11-07, policy auto_send_follow_ups).

How it works (no password or API key is stored):
  * Edge runs with its own small profile in automation\\edge_gmail_profile (gitignored), with a local-only
    control port. The first time, you sign in to Gmail in that window once; Edge remembers it.
  * The script opens Gmail's compose page with To / Subject / Body filled in, waits until Gmail has
    loaded, then puts the PDF into Gmail's own "Attach files" input and waits until the upload shows.
  * It does not connect to the window while the Google sign-in page is open.
  * Once Gmail shows "Draft saved", it closes that compose tab and opens your pinned Gmail app
    (Chrome, taskbar) on Drafts, where you read the email and press Send yourself.

  python gmail_compose.py <address>    open the logged draft for <address> (from contacts_log.json)
  python gmail_compose.py --reply <address>   open your drafted answer to someone who replied
  python gmail_compose.py --test       compose a test email to yourself with the BSD report attached
"""
import ctypes
import json
import os
import re
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
# Talk to the local Edge window directly, never through HTTP(S)_PROXY (px answers 502 for localhost).
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
        with LOCAL.open(f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2) as response:
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
                LOCAL.open(f"http://127.0.0.1:{DEBUG_PORT}/json/close/{tab['id']}", timeout=5).close()
        request = urllib.request.Request(
            f"http://127.0.0.1:{DEBUG_PORT}/json/new?{urllib.parse.quote(url, safe='')}",
            method="PUT",
        )
        LOCAL.open(request, timeout=5).close()


def signed_in_tab(tab):
    """A loaded Gmail tab is titled '... - <address> - Gmail'. The URL alone is not proof: a
    signed-out profile briefly lists mail.google.com before redirecting to workspace.google.com."""
    return (
        tab["url"].startswith("https://mail.google.com/mail/")
        and outreach.GMAIL_ACCOUNT.lower() in tab.get("title", "").lower()
    )


def send_to_sign_in(tab, compose_url):
    """Replaces Gmail's signed-out landing page with Google's sign-in page that returns to the draft."""
    sign_in = (
        "https://accounts.google.com/ServiceLogin?service=mail"
        f"&Email={urllib.parse.quote(outreach.GMAIL_ACCOUNT)}"
        f"&continue={urllib.parse.quote(compose_url, safe='')}"
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{DEBUG_PORT}/json/new?{urllib.parse.quote(sign_in, safe='')}",
        method="PUT",
    )
    LOCAL.open(request, timeout=5).close()
    LOCAL.open(f"http://127.0.0.1:{DEBUG_PORT}/json/close/{tab['id']}", timeout=5).close()


def wait_for_gmail(started_at, compose_url):
    """Waits (without connecting) until a Gmail tab is loaded and signed in as GMAIL_ACCOUNT."""
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
        if any(signed_in_tab(t) for t in open_tabs):
            return
        for tab in open_tabs:
            if "workspace.google.com" in tab["url"]:
                send_to_sign_in(tab, compose_url)
        if not shown_hint and any("accounts.google.com" in t["url"] for t in open_tabs):
            shown_hint = True
            hint = (
                f"First time only: sign in as {outreach.GMAIL_ACCOUNT} in the Edge window "
                "that just opened (this helper window keeps its own sign-in, separate from Chrome).\n\n"
                "The draft and the PDF will appear by themselves after you sign in."
            )
            threading.Thread(target=message, args=(hint,), daemon=True).start()
        time.sleep(1.5)
    raise RuntimeError("Gave up waiting for the Gmail sign-in (10 minutes).")


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SAVE_WAIT_S = 45


def open_pinned_gmail_drafts():
    """Opens the Gmail app pinned on the taskbar (Chrome --app) at the Drafts folder."""
    drafts_url = f"https://mail.google.com/mail/u/?authuser={outreach.GMAIL_ACCOUNT}#drafts"
    if Path(CHROME).exists():
        subprocess.Popen([CHROME, f"--app={drafts_url}"])
    else:
        subprocess.Popen([outreach.EDGE, "--profile-directory=Default", f"--app={drafts_url}"])


def find_gmail_page(browser, timeout_s=30):
    """Playwright can attach before the tab has committed its URL (it shows up as ''),
    so poll until a mail.google.com page is visible instead of reading the list once."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pages = [p for c in browser.contexts for p in c.pages if p.url.startswith("https://mail.google.com/")]
        if pages:
            return pages[-1]
        time.sleep(0.5)
    seen = [p.url[:60] for c in browser.contexts for p in c.pages]
    raise RuntimeError(f"Connected to Edge but found no Gmail tab (tabs: {seen}).")


def save_draft(attachment, subject):
    """Fills in the attachment (if any), waits until Gmail says the draft is saved, then closes the
    compose tab. The draft stays in Gmail's Drafts folder; nothing is sent.
    Returns (attached, subject_ok, saved)."""
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"  # Playwright's CDP link must bypass px too
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        page = find_gmail_page(browser)
        page.wait_for_selector('input[name="subjectbox"]', timeout=60_000)
        attached = True
        if attachment:
            file_input = page.locator('input[type="file"][name="Filedata"]').last
            file_input.wait_for(state="attached", timeout=60_000)
            file_input.set_input_files(str(attachment))
            name = Path(attachment).name
            chip = page.locator(f'[aria-label*="{name}"], div:text-is("{name}")')
            chip.first.wait_for(state="visible", timeout=UPLOAD_WAIT_S * 1000)
            progress = page.locator('div[role="progressbar"]')
            deadline = time.time() + UPLOAD_WAIT_S
            while progress.count() and time.time() < deadline:
                time.sleep(0.5)
            attached = chip.count() > 0
        subject_ok = page.locator('input[name="subjectbox"]').last.input_value() == subject
        # Gmail autosaves; wait for its "Draft saved" / "Saved" note before closing the tab.
        saved = False
        deadline = time.time() + SAVE_WAIT_S
        while time.time() < deadline:
            note = page.get_by_text(re.compile(r"^\s*(Draft )?[Ss]aved\s*$"))
            if note.count() and note.first.is_visible():
                saved = True
                break
            time.sleep(1.0)
        if saved and attached:
            time.sleep(1.5)
            page.close()
    return attached, subject_ok, saved


def compose(to, subject, body, attachment):
    started_at = time.time()
    compose_url = outreach.gmail_compose_url(to, subject, body)
    open_compose_tab(compose_url)
    wait_for_gmail(started_at, compose_url)
    time.sleep(1.0)
    attached, subject_ok, saved = save_draft(attachment, subject)
    if attachment and not attached:
        raise RuntimeError("Gmail opened, but the PDF upload did not finish; attach it by hand.")
    note = "" if subject_ok else "\n(Check the subject line; it did not match the ledger.)"
    with_pdf = f" with {Path(attachment).name} attached" if attachment else ""
    if not saved:
        return (
            f"The draft{with_pdf} is open in Gmail, but Gmail has not confirmed it is saved yet, "
            "so I left that window open.\nRead it, press Send, then click 'I sent it'." + note
        )
    open_pinned_gmail_drafts()
    return (
        f"Draft saved in your Gmail Drafts{with_pdf}.\n\n"
        "Your pinned Gmail app is opening on Drafts: open it, read it once, press Send, "
        "then click 'I sent it' on the dashboard." + note
    )


SEND_WAIT_S = 40


def send_now(to, subject, body):
    """FOLLOW-UPS ONLY (the user asked for these to go out without him, 2026-11-07).
    Opens the same signed-in Edge helper window, checks recipient and subject, presses Gmail's Send
    and waits for Gmail's "Message sent" note. Returns True only when Gmail confirmed the send."""
    started_at = time.time()
    compose_url = outreach.gmail_compose_url(to, subject, body)
    open_compose_tab(compose_url)
    wait_for_gmail(started_at, compose_url)
    time.sleep(1.0)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        deadline = time.time() + 30
        page = None
        while page is None and time.time() < deadline:
            compose_pages = [
                p for c in browser.contexts for p in c.pages
                if p.url.startswith("https://mail.google.com/") and "view=cm" in p.url
            ]
            page = compose_pages[-1] if compose_pages else None
            time.sleep(0.5)
        if page is None:
            raise RuntimeError("Gmail compose page not found")
        page.wait_for_selector('input[name="subjectbox"]', timeout=60_000)
        if page.locator('input[name="subjectbox"]').last.input_value() != subject:
            raise RuntimeError("subject did not match; not sent")
        if to.lower() not in page.content().lower():
            raise RuntimeError("recipient not visible in the compose window; not sent")
        page.locator('div[role="button"][data-tooltip^="Send"]').last.click()
        sent = False
        deadline = time.time() + SEND_WAIT_S
        while time.time() < deadline:
            if page.is_closed():
                sent = True  # Gmail closes the standalone compose page after a successful send
                break
            note = page.get_by_text(re.compile(r"Message sent"))
            if note.count():
                sent = True
                break
            time.sleep(0.5)
        if not page.is_closed():
            page.close()
    log(f"send_now to={to} subject={subject!r} confirmed={sent}")
    return sent


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    if sys.argv[1] == "--test":
        to = outreach.GMAIL_ACCOUNT
        subject = "Millennium Dashboard test - attachment check (do not send)"
        body = "Test of the Open draft button. Delete this draft.\n"
        attachment = HERE.parent / "bsd_project" / "report" / "Murmuration_amplitude_report.pdf"
    elif sys.argv[1] == "--reply" and len(sys.argv) > 2:
        import action

        draft = action._reply_draft(sys.argv[2].lower())
        if draft is None:
            message(f"No drafted reply for {sys.argv[2]} in the outbox folder.", MB_ICONWARN)
            return
        to, subject, body = draft
        attachment = None
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
