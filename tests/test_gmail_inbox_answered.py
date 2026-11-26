"""Checks what the Gmail check does with a reply, without Gmail and without the real ledger.

    python tests/test_gmail_inbox_answered.py

A reply that was already answered from the account gets no generated draft: only a "read it" job.
A reply nobody has answered gets its draft and the usual job. Exit code 0 = all checks passed.
"""
import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import gmail_inbox  # noqa: E402

FAILURES = []
PERSON = "person@example.org"


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def message(sender, when, words):
    return {"from": sender, "name": sender, "date": when, "text": words}


def run(messages):
    """One sync of one contact against a faked thread. Returns (entry, events, drafts saved, jobs queued)."""
    drafts, jobs = [], []
    thread = {"id": "t1", "subject": "A question", "messages": messages}
    gmail_inbox.read_threads = lambda context, address: [thread]
    gmail_inbox.save_incoming = lambda entry, incoming, subject: Path("inbox") / "reply.txt"
    gmail_inbox.write_answer = lambda entry, incoming, subject: (Path("outbox") / "answer.txt", "thanks", True)
    gmail_inbox.save_thread_draft = lambda context, thread_id, body: drafts.append(body) or True
    gmail_inbox._queue_ai_answer = lambda name, entry, path, answered=False: jobs.append((str(path), answered))
    gmail_inbox.history.record = lambda *arguments, **more: None
    entry = {"to": PERSON, "name": "A Person", "topic": "T", "state": "sent", "date": "2026-11-25",
             "seen_messages": []}
    events = gmail_inbox.sync_contact(None, {"contacts": [entry]}, entry)
    return entry, events, drafts, jobs


def main():
    asked = message(gmail_inbox.ME, dt.datetime(2026, 11, 25, 16, 21), "my question")
    reply = message(PERSON, dt.datetime(2026, 11, 26, 9, 34), "their answer")
    by_hand = message(gmail_inbox.ME, dt.datetime(2026, 11, 26, 9, 41), "thanks, written by hand")

    entry, events, drafts, jobs = run([asked, reply])
    check("a reply nobody answered: a draft is saved and the answer job is queued",
          len(drafts) == 1 and jobs == [(str(Path("outbox") / "answer.txt"), False)]
          and entry["state"] == "replied" and not entry.get("answer_sent_date"), str((drafts, jobs, entry)))

    entry, events, drafts, jobs = run([asked, reply, by_hand])
    check("a reply already answered from the account: no draft",
          drafts == [] and "reply_draft" not in entry, str((drafts, entry)))
    check("... a job to read it, pointing at the saved reply, marked as answered",
          jobs == [(str(Path("inbox") / "reply.txt"), True)], str(jobs))
    check("... and the ledger knows the reply and the day it was answered",
          entry["state"] == "replied" and entry.get("answer_sent_date") == "2026-11-26", str(entry))

    print()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
