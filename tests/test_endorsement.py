"""Checks the arXiv endorsement step without Gmail, without arXiv and without the real ledger.

    python tests/test_endorsement.py

Every file it touches is in a temporary folder. The specs it writes are run through the real outreach rules.
Exit code 0 = all checks passed, 1 = at least one failed.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import endorsement  # noqa: E402
import outreach  # noqa: E402

FAILURES = []
DAY = dt.date(2026, 11, 26)
BODY = (
    "Dear {salutation},\n\nThis was drafted with an AI assistant. I am writing because of {why_you}.\n\n"
    "No mathematician has read the paper.{asked_before}\n\n"
    "Would you be willing to endorse it? Page: {endorse_url} Code: {code}\n\n"
    "If this isn't something you have time for, no reply is needed, and I won't write again on this unless I have "
    "something genuinely new.\n\nThank you for your time.\n\nBest regards,\nA Sender\n"
)


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def candidate(number):
    return {
        "to": f"person{number}@example.org", "name": f"Person {number}", "salutation": f"Dr Person {number}",
        "why_you": f"your paper arXiv:0000.0000{number}", "email_source": "https://example.org/paper",
        "their_work": [f"0000.0000{number}"],
    }


def sent(to, day, topic="arXiv-endorsement", state="sent"):
    return {"to": to, "name": to, "topic": topic, "subject": "s", "state": state,
            "date": day.isoformat(), "sent_date": day.isoformat()}


class Lab:
    """A lab in a temporary folder: one need with three candidates, an empty ledger, a research queue."""

    def __init__(self, folder, contacts=(), **need_extra):
        self.root = Path(folder)
        self.needs = self.root / "outbox" / "endorsement"
        self.queue = self.root / "outbox" / "outreach"
        self.needs.mkdir(parents=True)
        self.queue.mkdir(parents=True)
        self.ledger = self.root / "contacts_log.json"
        self.research_queue = self.root / "research_queue.json"
        evidence = self.root / "EVIDENCE.json"
        plan = self.root / "PLAN.md"
        plan.write_text("plan", encoding="utf-8")
        evidence.write_text(json.dumps({"preregistration": str(plan), "kind": "question",
                                        "question_gates_passed": True}), encoding="utf-8")
        need = {
            "id": "arxiv-math.XX", "category": "math.XX", "code": "ABC123",
            "endorse_url": "https://arxiv.org/auth/endorse?x=ABC123", "topic": "arXiv-endorsement",
            "subject": "arXiv endorsement request (code {code})", "body": BODY, "evidence": str(evidence),
            "queue_job": "upload_job", "candidates": [candidate(1), candidate(2), candidate(3), candidate(4)],
            **need_extra,
        }
        self.need_file = self.needs / "arxiv-math.XX.json"
        self.need_file.write_text(json.dumps(need), encoding="utf-8")
        self.ledger.write_text(json.dumps({"contacts": list(contacts)}), encoding="utf-8")
        self.research_queue.write_text(json.dumps({"note": "n", "jobs": [
            {"id": "upload_job", "status": "needs_ai", "not_before": "2026-12-03", "bridged": "x", "why": "parked"}]}),
            encoding="utf-8")
        endorsement.NEEDS_DIR = self.needs
        endorsement.QUEUE_DIR = self.queue
        endorsement.LEDGER_FILE = self.ledger
        endorsement.RESEARCH_QUEUE = self.research_queue
        endorsement._tell_juwen = lambda *arguments: self.notices.append(arguments) or True
        self.notices = []

    def need(self):
        return json.loads(self.need_file.read_text(encoding="utf-8"))

    def specs(self):
        return sorted(path for path in self.queue.glob("*.json"))

    def jobs(self):
        return json.loads(self.research_queue.read_text(encoding="utf-8"))["jobs"]


def main():
    with tempfile.TemporaryDirectory() as folder:
        lab = Lab(folder)
        lines = endorsement.tick(today=DAY, status_only=True)
        check("status changes nothing", not lab.specs() and "request 1 is due" in lines[0], str(lines))
        endorsement.tick(today=DAY)
        specs = lab.specs()
        check("first run writes one spec", len(specs) == 1 and specs[0].name == "2026-11-26-arxiv-math.XX-1-person1.json",
              str([path.name for path in specs]))
        spec = json.loads(specs[0].read_text(encoding="utf-8"))
        check("the spec carries the code, the page and the person's own reason",
              "ABC123" in spec["subject"] and "https://arxiv.org/auth/endorse?x=ABC123" in spec["body"]
              and "Dear Dr Person 1," in spec["body"] and "arXiv:0000.00001" in spec["body"] and "{" not in spec["body"])
        check("the first request does not speak of earlier ones", "before you" not in spec["body"])
        check("the spec asks for no follow-up", spec.get("_no_follow_up") is True)
        outreach.EXPERTS = dict(spec["_expert_entry"])
        outreach.TODAY = DAY
        problems = outreach.check_rules(outreach.prepared(spec), {"contacts": []})
        check("the spec passes the real outreach rules", not problems, str(problems))
        endorsement.tick(today=DAY)
        check("a second run the same day writes nothing more", len(lab.specs()) == 1)
        status = json.loads((lab.needs / "status.json").read_text(encoding="utf-8"))
        text = json.dumps(status)
        check("status.json has no name and no address", "person1" not in text and "Person 1" not in text and "ABC123" not in text)
        check("status.json says a spec waits in the queue", status["needs"][0]["action"] == "waiting_in_queue")
        board = json.loads((lab.needs / "board.json").read_text(encoding="utf-8"))
        text = json.dumps(board)
        item = board["items"][0]
        check("board.json has no name, no address and no code",
              "person1" not in text and "Person 1" not in text and "ABC123" not in text)
        check("board.json has the shape a Submissions card reads",
              board["ok"] is True and len(board["ran"]) == 16 and len(board["summary"]) <= 300
              and item["state"] == "waiting" and item["needs_you"] is False and len(item["name"]) <= 80
              and len(item["status"]) <= 40 and len(item["next"]) <= 200 and len(item["detail"]) <= 400, text)
        check("board.md names who is on the list", "Person 1" in (lab.needs / "board.md").read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as folder:
        lab = Lab(folder, contacts=[sent("person1@example.org", DAY)])
        endorsement.tick(today=DAY + dt.timedelta(days=6))
        check("six days of silence: nobody else is asked", not lab.specs())
        status = json.loads((lab.needs / "status.json").read_text(encoding="utf-8"))["needs"][0]
        check("status.json names the day of the next request", status["next_not_before"] == "2026-12-03" and status["asked"] == 1)
        endorsement.tick(today=DAY + dt.timedelta(days=7))
        specs = lab.specs()
        check("seven days of silence: the second person is asked", len(specs) == 1 and "-2-person2" in specs[0].name)
        body = json.loads(specs[0].read_text(encoding="utf-8"))["body"]
        check("the second request says one person was asked before",
              "I asked one other researcher for this endorsement before you, last on 2026-11-26" in body, body)

    with tempfile.TemporaryDirectory() as folder:
        lab = Lab(folder, contacts=[sent("person1@example.org", DAY, state="declined")])
        endorsement.tick(today=DAY + dt.timedelta(days=1))
        check("after a no the next person is asked without the wait", len(lab.specs()) == 1)

    with tempfile.TemporaryDirectory() as folder:
        lab = Lab(folder, contacts=[sent("person1@example.org", DAY, state="replied")])
        endorsement.tick(today=DAY + dt.timedelta(days=30))
        check("an answer pauses the need: no spec", not lab.specs())
        item = json.loads((lab.needs / "board.json").read_text(encoding="utf-8"))["items"][0]
        check("an answer: the board says changed", item["state"] == "changed" and item["needs_you"] is False, str(item))
        check("an answer queues one AI job", [job["id"] for job in lab.jobs()] == ["upload_job", "endorsement_answer_arxiv_math_xx"])
        endorsement.tick(today=DAY + dt.timedelta(days=31))
        check("the AI job is queued once", len(lab.jobs()) == 2)
        check("read: one answer marked", endorsement.mark_answers_read("arxiv-math.XX", DAY + dt.timedelta(days=31)) == 1)
        endorsement.tick(today=DAY + dt.timedelta(days=31))
        check("after the answer was read the step goes on", len(lab.specs()) == 1 and "answer_job" not in lab.need())

    with tempfile.TemporaryDirectory() as folder:
        lab = Lab(folder, contacts=[sent("someone@example.org", DAY, topic="SameResult", state="replied")],
                  pause_on_reply_topics=["SameResult"])
        endorsement.tick(today=DAY)
        check("an answer on a topic the need names pauses it too", not lab.specs() and lab.need().get("answer_job"))

    with tempfile.TemporaryDirectory() as folder:
        days = [DAY, DAY + dt.timedelta(days=7), DAY + dt.timedelta(days=14)]
        lab = Lab(folder, contacts=[sent(f"person{n + 1}@example.org", day) for n, day in enumerate(days)])
        endorsement.tick(today=DAY + dt.timedelta(days=30))
        check("three people asked: it stops although a fourth is on the list",
              not lab.specs() and lab.need()["status"] == "stopped")
        check("stopping tells Juwen once", len(lab.notices) == 1 and "ABC123" in lab.notices[0][2])
        item = json.loads((lab.needs / "board.json").read_text(encoding="utf-8"))["items"][0]
        check("stopped: the board says rejected and needs Juwen",
              item["state"] == "rejected" and item["needs_you"] is True and item["changed"] == "2026-12-26"
              and len(item["next"]) <= 200, str(item))
        endorsement.tick(today=DAY + dt.timedelta(days=60))
        check("a stopped need stays stopped and silent", not lab.specs() and len(lab.notices) == 1)

    with tempfile.TemporaryDirectory() as folder:
        lab = Lab(folder, contacts=[sent("person1@example.org", DAY)])
        endorsement.tick(today=DAY + dt.timedelta(days=7))
        check("endorsed: the need is found", endorsement.mark("arxiv-math.XX", "endorsed", DAY + dt.timedelta(days=8)))
        spec = json.loads(lab.specs()[0].read_text(encoding="utf-8"))
        check("endorsed: the waiting request is withdrawn", spec.get("_withdrawn") == "2026-12-04")
        outreach.QUEUE_DIR = lab.queue
        check("endorsed: the outreach queue no longer sees it", not list(outreach.waiting_specs(DAY + dt.timedelta(days=9))))
        job = lab.jobs()[0]
        check("endorsed: the upload job is due today and not bridged",
              job["not_before"] == "2026-12-04" and "bridged" not in job and job["why"].startswith("ENDORSED on 2026-12-04"))
        endorsement.tick(today=DAY + dt.timedelta(days=40))
        check("endorsed: nobody else is asked", len(lab.specs()) == 1)
        item = json.loads((lab.needs / "board.json").read_text(encoding="utf-8"))["items"][0]
        check("endorsed: the board says accepted and needs Juwen for the upload",
              item["state"] == "accepted" and item["needs_you"] is True and item["changed"] == "2026-12-04", str(item))

    with tempfile.TemporaryDirectory() as folder:
        lab = Lab(folder)
        (lab.needs / "broken.json").write_text("{not json", encoding="utf-8")
        (lab.needs / "half.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
        lines = endorsement.tick(today=DAY)
        check("a broken or half-written need file is skipped", len(lines) == 1 and len(lab.specs()) == 1)

    with tempfile.TemporaryDirectory() as folder:
        import followup_drafter

        lab = Lab(folder, contacts=[{**sent("person1@example.org", DAY), "no_follow_up": True},
                                    sent("other@example.org", DAY, topic="SomethingElse")])
        followup_drafter.LEDGER_FILE = lab.ledger
        rows = followup_drafter.auto_followups(DAY + dt.timedelta(days=30), {}, set())
        check("no follow-up is drafted for an endorsement request, others keep theirs",
              [row[1] for row in rows] == ["other@example.org"], str([row[1] for row in rows]))
        saved_ledger, saved_draft, saved_rules = outreach.load_ledger, outreach.save_draft, outreach.check_rules
        drafts = []
        outreach.load_ledger = lambda: json.loads(lab.ledger.read_text(encoding="utf-8"))
        outreach.save_ledger = lambda ledger: None
        outreach.save_draft = lambda to, *rest, **more: drafts.append(to) or "url"
        outreach.check_rules = lambda *arguments, **more: []
        outreach.TODAY = DAY + dt.timedelta(days=30)
        outreach.followups(open_windows=False)
        outreach.load_ledger, outreach.save_draft, outreach.check_rules = saved_ledger, saved_draft, saved_rules
        check("outreach.followups skips it as well", drafts == ["other@example.org"], str(drafts))

    print()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
