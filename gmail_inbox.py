"""Reads Gmail for the outreach contacts and drafts the answers. It never sends anything.

Runs by itself every 2 hours (scheduled task "Millennium Gmail check") and on demand:

    python gmail_inbox.py            check now; a message box tells you when a draft is waiting
    python gmail_inbox.py --quiet    the same without message boxes (for tests)
    python gmail_inbox.py --dry-run  read and report only: no ledger change, no draft

What one check does, per contact in contacts_log.json that is drafted, sent or replied:
  * reads the Gmail threads with that address (headless Edge, the helper profile in edge_gmail_profile
    that gmail_compose.py signs in once; if that Edge window is open, it attaches to it instead);
  * your own message in a thread  -> a "drafted" email is marked sent (no more "I sent it" clicks);
  * a new message from them      -> saved to ../inbox/, state "replied", and an answer is drafted:
      - ../outbox/<TOPIC>_<Surname>_reply_<YYYYMMDD>.txt is used if it already exists (hand-written),
        otherwise a short thank-you is written from the reply (arXiv ids and sections it mentions);
      - the answer is saved as a reply draft inside that Gmail thread, so it threads correctly;
  * your message after their last reply -> marked answered;
  * a follow-up due under the 21-day rule (outreach.followups) is saved as a draft in the thread too.
You read the draft in Gmail and press Send; the next check sees it was sent.
"""
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import history
import outreach

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROFILE_DIR = HERE / "edge_gmail_profile"
DEBUG_PORT = 9333  # gmail_compose.py's helper Edge, when it is open
INBOX_DIR = ROOT / "inbox"
OUTBOX_DIR = ROOT / "outbox"
STATE_FILE = HERE / "gmail_inbox.json"  # last check: time, result (local, git-ignored)
LOG = HERE / "gmail_inbox.log"
ME = outreach.GMAIL_ACCOUNT.lower()
GMAIL = "https://mail.google.com/mail/u/0/"
WATCHED_STATES = {"drafted", "sent", "replied"}
MAX_THREADS = 5
PAGE_TIMEOUT_MS = 45000
DATE_FORMATS = (
    "%d %b %Y, %H:%M",
    "%a, %d %b %Y, %H:%M",
    "%b %d, %Y, %I:%M %p",
    "%a, %b %d, %Y, %I:%M %p",
    "%d %B %Y, %H:%M",
)
TITLE = "Millennium Dashboard"
QUOTE_START = re.compile(
    r"^(From:|差出人:|-----Original Message|On .{5,120} wrote:|Le .{5,120} a écrit|Am .{5,120} schrieb)",
    re.M,
)
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")


def log(text):
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp}  {text}\n")


def thread_link(thread_id):
    """Opens the thread for this account in any signed-in Gmail (the pinned Chrome app included)."""
    return f"https://mail.google.com/mail/?authuser={quote(ME)}#all/{thread_id}"


# ---------------------------------------------------------------- reading Gmail

def _helper_running():
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=2
        ).close()
        return True
    except OSError:
        return False


@contextmanager
def gmail_browser():
    """Yields a Playwright browser context signed in to Gmail (headless unless the helper is open)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if _helper_running():
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
            yield browser.contexts[0]
            return
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="msedge", headless=True
        )
        try:
            yield context
        finally:
            context.close()


def _parse_date(title):
    cleaned = re.sub(r"\s+", " ", (title or "").replace("\u202f", " ")).strip()
    cleaned = re.sub(r" \(.*\)$", "", cleaned)
    for pattern in DATE_FORMATS:
        try:
            return dt.datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    return dt.datetime.now().replace(second=0, microsecond=0)


def _strip_quote(text):
    """Their new words only: drops the quoted earlier email that mail clients append."""
    match = QUOTE_START.search(text)
    text = text[:match.start()] if match else text
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _wait_for_results(page, timeout_s=30):
    """Number of result rows, 0 when Gmail says nothing matched. Raises if not signed in."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if ME not in page.title().lower() and "accounts.google.com" in page.url:
            raise RuntimeError("the Gmail helper profile is signed out")
        rows = page.query_selector_all("div[role=main] tr.zA")
        if rows:
            return len(rows)
        main = page.query_selector("div[role=main]")
        if main and "No messages matched" in (main.inner_text() or ""):
            return 0
        time.sleep(0.5)
    if ME not in page.title().lower():
        raise RuntimeError(f"Gmail did not load as {ME} (title: {page.title()!r})")
    return 0


def _read_thread(context, thread_id):
    """Messages of one thread (drafts are not messages), read in a fresh tab."""
    page = context.new_page()
    try:
        page.goto(GMAIL + "#all/" + thread_id, timeout=PAGE_TIMEOUT_MS)
        page.wait_for_selector("h2.hP", timeout=PAGE_TIMEOUT_MS)
        return _read_open_thread(page, thread_id)
    finally:
        page.close()


def _read_open_thread(page, thread_id):
    expand = page.query_selector('[aria-label="Expand all"]')
    if expand and expand.is_visible():
        expand.click()
        time.sleep(1.5)
    page.wait_for_selector("div.adn", timeout=15000)
    subject_node = page.query_selector("h2.hP")
    messages = []
    for node in page.query_selector_all("div.adn"):
        sender = node.query_selector("span.gD")
        stamp = node.query_selector("span.g3")
        body = node.query_selector("div.a3s")
        if not sender:
            continue
        messages.append({
            "from": (sender.get_attribute("email") or "").lower(),
            "name": sender.get_attribute("name") or "",
            "date": _parse_date(stamp.get_attribute("title") if stamp else ""),
            "text": _strip_quote((body.inner_text() if body else "").replace("\u00a0", " ")),
        })
    return {
        "id": thread_id,
        "subject": subject_node.inner_text().strip() if subject_node else "",
        "messages": messages,
    }


def read_threads(context, address):
    """Every recent Gmail thread with `address` (sent or received), newest first."""
    query = f"(from:{address} OR to:{address}) -in:draft newer_than:180d"
    page = context.new_page()
    try:
        page.goto(GMAIL + "#search/" + quote(query, safe=""), timeout=PAGE_TIMEOUT_MS)
        _wait_for_results(page)
        thread_ids = []
        for node in page.query_selector_all("div[role=main] tr.zA [data-legacy-thread-id]"):
            thread_id = node.get_attribute("data-legacy-thread-id")
            if thread_id and thread_id not in thread_ids:
                thread_ids.append(thread_id)
    finally:
        page.close()
    return [_read_thread(context, thread_id) for thread_id in thread_ids[:MAX_THREADS]]


# ---------------------------------------------------------------- drafting

def _type_lines(page, text):
    for number, line in enumerate(text.rstrip("\n").split("\n")):
        if number:
            page.keyboard.press("Enter")
        if line:
            page.keyboard.insert_text(line)


def save_thread_draft(context, thread_id, body):
    """Opens the thread, clicks Reply, types `body`, waits until Gmail has saved the draft.
    Never presses Send. Returns True when Gmail confirmed the draft was saved."""
    page = context.new_page()
    try:
        page.goto(GMAIL + "#all/" + thread_id, timeout=PAGE_TIMEOUT_MS)
        page.wait_for_selector("div.adn", timeout=PAGE_TIMEOUT_MS)
        reply = page.locator("span.ams.bkH").last  # the "Reply" button under the last message
        reply.click()
        editor = page.locator('div[aria-label="Message Body"][contenteditable="true"]').last
        editor.wait_for(timeout=20000)
        editor.click()
        _type_lines(page, body)
        deadline = time.time() + 30
        while time.time() < deadline:
            if page.get_by_text(re.compile(r"^(Draft saved|Saved)$")).count():
                time.sleep(1)
                return True
            time.sleep(0.5)
        return False
    finally:
        page.close()


def _salutation(entry):
    body_file = entry.get("body_file")
    if body_file and Path(body_file).exists():
        first = Path(body_file).read_text(encoding="utf-8").strip().splitlines()[:1]
        if first and first[0].startswith("Dear"):
            return first[0]
    return f"Dear Professor {entry['name'].split()[-1]},"


def compose_answer(entry, reply_text):
    """A short, safe thank-you built from what they wrote. Hand-written drafts take precedence."""
    references = sorted(set(re.findall(r"(?:arxiv\.org/abs/|arXiv:)(\d{4}\.\d{4,5})", reply_text, re.I)))
    sections = sorted(set(re.findall(r"\bSection (\d+(?:\.\d+)*)", reply_text)))
    thanks = "Thank you very much for your reply"
    if references:
        thanks += ", and for pointing me to " + " and ".join(f"arXiv:{ref}" for ref in references)
    plan = "I will read it carefully"
    if sections:
        plan += " (especially Section " + ", ".join(sections) + ")"
    plan += " and compare it with what I measured."
    if not references:
        plan = "It is very helpful, and I will compare what you wrote with what I measured."
    return (
        f"{_salutation(entry)}\n\n{thanks}. {plan} I will only write again if I have something genuinely new.\n\n"
        f"Thank you again for your time.\n\nBest regards,\n{outreach.SENDER_NAME}\n"
    )


def answer_file(entry, message):
    surname = re.sub(r"\W", "", entry["name"].split()[-1])
    return OUTBOX_DIR / f"{entry.get('topic', 'X')}_{surname}_reply_{message['date']:%Y%m%d}.txt"


def write_answer(entry, message, subject):
    """The answer text: the hand-written outbox file if present, else a generated one saved there."""
    path = answer_file(entry, message)
    if not path.exists():
        body = compose_answer(entry, message["text"])
        path.write_text(f"To: {entry['to']}\nSubject: Re: {subject}\n\n{body}", encoding="utf-8")
        return path, body, True
    lines = path.read_text(encoding="utf-8").splitlines()
    return path, "\n".join(lines[3:]).strip() + "\n", False


def save_incoming(entry, message, subject):
    INBOX_DIR.mkdir(exist_ok=True)
    surname = re.sub(r"\W", "", entry["name"].split()[-1])
    path = INBOX_DIR / f"{entry.get('topic', 'X')}_{surname}_{message['date']:%Y%m%d-%H%M}.txt"
    path.write_text(
        f"From: {message['name']} <{message['from']}>\nDate: {message['date']:%Y-%m-%d %H:%M}\n"
        f"Subject: {subject}\n\n{message['text']}\n",
        encoding="utf-8",
    )
    return path


def _key(message):
    raw = f"{message['from']}|{message['date']:%Y-%m-%d %H:%M}|{message['text'][:300]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- one check

def latest_entries(ledger):
    latest = {}
    for entry in ledger["contacts"]:
        latest[entry["to"].lower()] = entry
    return [entry for entry in latest.values() if entry.get("state") in WATCHED_STATES]


def _release_holds(ledger, topic):
    for entry in ledger["contacts"]:
        if entry.get("topic") == topic:
            entry.pop("hold_until", None)


def sync_contact(context, ledger, entry, dry_run=False):
    """Updates one ledger entry from Gmail. Returns a list of human-readable events."""
    address = entry["to"].lower()
    name = entry.get("name", address)
    threads = read_threads(context, address)
    events = []
    everything = [(thread, m) for thread in threads for m in thread["messages"]]
    mine = sorted(((m["date"], thread) for thread, m in everything if m["from"] == ME),
                  key=lambda item: item[0])
    theirs = sorted(((m["date"], thread, m) for thread, m in everything if m["from"] == address),
                    key=lambda item: item[0])
    first_sync = "seen_messages" not in entry
    seen = set(entry.get("seen_messages", []))
    entry_day = dt.date.fromisoformat(entry["date"])

    sent_after_entry = [(when, thread) for when, thread in mine if when.date() >= entry_day]
    if entry["state"] == "drafted" and sent_after_entry:
        when, thread = sent_after_entry[0]
        events.append(f"{name}: your email was sent on {when:%d %b} (seen in Gmail)")
        if not dry_run:
            entry["state"] = "sent"
            entry["sent_date"] = when.date().isoformat()
            entry["gmail_thread"] = thread["id"]
            if "ON HOLD" in entry.get("note", ""):
                entry.pop("note")
            history.record("watch", f"Your email to {name} was sent (seen in Gmail)")
    elif mine and not entry.get("gmail_thread") and not dry_run:
        entry["gmail_thread"] = mine[-1][1]["id"]

    for when, thread, message in theirs:
        key = _key(message)
        if key in seen:
            continue
        known = first_sync and entry.get("replied_date") and when.date() <= dt.date.fromisoformat(entry["replied_date"])
        seen.add(key)
        if known:
            continue  # already handled before this tool existed
        if when.date() < entry_day:
            continue  # belongs to an earlier exchange
        events.append(f"{name} replied on {when:%d %b %H:%M}: {message['text'][:120]!r}")
        if dry_run:
            continue
        incoming = save_incoming(entry, message, thread["subject"])
        entry["state"] = "replied"
        entry["replied_date"] = when.date().isoformat()
        entry.pop("answer_sent_date", None)
        entry["gmail_thread"] = thread["id"]
        _release_holds(ledger, entry.get("topic"))
        path, body, generated = write_answer(entry, message, thread["subject"])
        saved = save_thread_draft(context, thread["id"], body)
        entry["reply_draft"] = {
            "file": str(path), "incoming": str(incoming), "gmail": saved, "generated": generated,
            "for": key, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "question": "?" in message["text"],
        }
        _queue_ai_answer(name, entry, path)
        history.record("watch", f"{name} replied; your answer was drafted"
                       + (" in the Gmail thread" if saved else f" in {path.name}"))
        events.append(f"  answer drafted ({'hand-written' if not generated else 'generated'}): "
                      + ("saved in the Gmail thread" if saved else "Gmail draft NOT confirmed"))

    if entry["state"] == "replied" and theirs and mine and not entry.get("answer_sent_date"):
        if mine[-1][0] > theirs[-1][0]:
            events.append(f"{name}: your answer was sent on {mine[-1][0]:%d %b} (seen in Gmail)")
            if not dry_run:
                entry["answer_sent_date"] = mine[-1][0].date().isoformat()
                history.record("watch", f"Your reply to {name} was sent (seen in Gmail)")

    if entry.get("follow_up") and entry["state"] == "drafted" and not entry.get("gmail_draft") and mine:
        if not dry_run:
            saved = save_thread_draft(context, mine[-1][1]["id"], entry["body"])
            entry["gmail_draft"] = dt.datetime.now().isoformat(timespec="seconds") if saved else None
            entry["gmail_thread"] = mine[-1][1]["id"]
            history.record("watch", f"Follow-up to {name} drafted in the Gmail thread")
        events.append(f"{name}: follow-up drafted in the Gmail thread")

    if not dry_run:
        entry["seen_messages"] = sorted(seen)
    return events


def _queue_ai_answer(name, entry, draft_path):
    """An expert replied: queue a needs_ai job so the next AI session writes and sends the answer
    (user's standing approval 2026-11-07). Unattended code never sends generated text itself."""
    queue_path = HERE.parent / "research_queue.json"
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    job_id = f"answer_{entry['to'].split('@')[0]}_{dt.date.today():%Y%m%d}"
    if any(job.get("id") == job_id for job in queue.get("jobs", [])):
        return
    queue.setdefault("jobs", []).append({
        "id": job_id,
        "title": f"Answer {name} (they replied) - AI session writes AND sends it",
        "project": "outreach",
        "status": "needs_ai",
        "why": f"Reply saved; starting draft at {draft_path}. Send from the Gmail thread after the policy checks.",
    })
    queue_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        import ai_bridge  # hand it to the always-on Precedent Agent right away
        ai_bridge.bridge()
    except Exception:
        pass


def check(dry_run=False):
    ledger = outreach.load_ledger()
    if not dry_run:
        outreach.followups(open_windows=False)
        ledger = outreach.load_ledger()
    events = []
    with gmail_browser() as context:
        for entry in latest_entries(ledger):
            try:
                events += sync_contact(context, ledger, entry, dry_run=dry_run)
            except Exception as error:
                if "signed out" in str(error):
                    raise
                events.append(f"{entry['to']}: could not read ({error})")
                log(traceback.format_exc().strip().replace("\n", " / "))
    if not dry_run:
        outreach.save_ledger(ledger)
    return events


def _save_state(ok, note, events):
    STATE_FILE.write_text(json.dumps({
        "at": dt.datetime.now().isoformat(timespec="seconds"), "ok": ok, "note": note, "events": events,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _rebuild_dashboard():
    python = Path(sys.executable).with_name("python.exe")
    subprocess.run([str(python), str(HERE / "dashboard.py")], cwd=HERE,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _tell(events):
    drafted = [line for line in events if "drafted" in line]
    if not drafted:
        return
    text = "\n".join(line.strip() for line in events) + (
        "\n\nThe drafts are in your Gmail Drafts, inside each thread. Nothing was sent.\n"
        "Open Gmail now to read them and press Send?"
    )
    if ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x4 | 0x40 | 0x40000) == 6:
        import gmail_app

        command = gmail_app.chrome_command()
        if command:
            subprocess.Popen(command + [f"--app={GMAIL}?authuser={quote(ME)}#drafts"])


def main():
    dry_run = "--dry-run" in sys.argv
    if "--soon" in sys.argv:
        return check_soon()
    result = run_once(dry_run)
    if not dry_run:
        import repo_sync

        repo_sync.push_if_ahead()  # commits saved while the network was down go up now
    return result


SOON_DELAYS_S = (300, 600, 900)  # checks at +5, +15 and +30 min after you open a draft


def check_soon():
    """After a dashboard button opened a draft: look again a few times so 'sent' shows up within
    minutes instead of at the next 2-hourly check. Stops as soon as Gmail shows something new."""
    for delay in SOON_DELAYS_S:
        time.sleep(delay)
        if run_once(False) == 0 and load_state().get("events"):
            return 0
    return 0


def run_once(dry_run):
    try:
        events = check(dry_run=dry_run)
    except Exception as error:
        note = str(error)
        log("check failed: " + traceback.format_exc().strip().replace("\n", " / "))
        if not dry_run and "signed out" in note:
            try:
                import notify_juwen

                notify_juwen.gmail_sign_in_needed("2-hourly Gmail check")
            except Exception as notice_error:  # the notice must never hide the real failure
                log(f"sign-in notice failed: {notice_error}")
        if not dry_run:
            _save_state(False, note, [])
        print("check failed:", note)
        return 1
    for line in events or ["no news"]:
        print(line)
    if dry_run:
        return 0
    _save_state(True, "", events)
    log("checked: " + (" | ".join(events) if events else "no news"))
    if events:
        _rebuild_dashboard()
        if "--quiet" not in sys.argv:
            _tell(events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
