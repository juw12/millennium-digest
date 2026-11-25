"""Checks the outreach rules and the recurring queue without Gmail and without the real ledger.

    python tests/test_outreach_rules.py

Every file it touches is in a temporary folder; gmail_compose is replaced by a recorder.
Exit code 0 = all checks passed, 1 = at least one failed.
"""
import datetime as dt
import json
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import outreach  # noqa: E402

FAILURES = []
TODAY = dt.date(2026, 11, 25)
BODY = (
    "Dear Dr Example,\n\nThis was drafted with an AI assistant. I read your paper arXiv:0000.00001.\n\n"
    "Is the bound known? No mathematician has read the argument.\n\n"
    "If this isn't something you have time for, no reply is needed, and I won't write again on this unless I have "
    "something genuinely new.\n\nThank you for your time.\n\nBest regards,\nA Sender\n"
)


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def contact(to, days_ago, state="sent", **extra):
    return {"to": to, "name": to, "topic": "T", "subject": "s", "state": state,
            "date": (TODAY - dt.timedelta(days=days_ago)).isoformat(), **extra}


def setup(folder, policy_changes=None):
    """Points outreach at a temporary folder. Returns (spec path, sent list)."""
    folder = Path(folder)
    outreach.TODAY = TODAY
    outreach.POLICY = json.loads((HERE / "outreach_policy.json").read_text(encoding="utf-8"))
    outreach.POLICY.update(policy_changes or {})
    outreach.EXPERTS = {}
    outreach.EXPERTS_FILE = folder / "experts.json"
    outreach.LEDGER_FILE = folder / "contacts_log.json"
    outreach.QUEUE_DIR = folder / "outreach"
    outreach.QUEUE_DIR.mkdir(exist_ok=True)
    outreach.RESEARCH_QUEUE = folder / "research_queue.json"
    outreach.RESEARCH_QUEUE.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    plan = folder / "PLAN.md"
    plan.write_text("plan", encoding="utf-8")
    evidence = outreach.QUEUE_DIR / "a-EVIDENCE.json"
    evidence.write_text(json.dumps({"preregistration": str(plan), "all_gates_passed": True}), encoding="utf-8")
    spec = {
        "to": "new@example.org", "topic": "T", "subject": "A question", "body": BODY, "evidence": str(evidence),
        "_expert_entry": {"new@example.org": {"name": "Dr Example", "email_source": "https://example.org/paper",
                                               "their_work": ["0000.00001"]}},
    }
    spec_path = outreach.QUEUE_DIR / "a-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    sent = []
    fake = types.ModuleType("gmail_compose")
    fake.send_now = lambda to, subject, body: sent.append((to, subject)) or True
    sys.modules["gmail_compose"] = fake
    fake_bridge = types.ModuleType("ai_bridge")
    fake_bridge.bridge = lambda: []
    sys.modules["ai_bridge"] = fake_bridge
    return spec_path, evidence, sent


def problems_for(spec_path, contacts):
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    outreach.add_expert(spec)
    return outreach.check_rules(outreach.prepared(spec), {"contacts": contacts})


def main():
    with tempfile.TemporaryDirectory() as folder:
        spec_path, evidence, sent = setup(folder)
        policy = outreach.POLICY
        check("policy: a steady pace (2 in 7 days, 8 in 30 days)",
              policy["max_new_contacts_per_7_days"] == 2 and policy["max_new_drafts_per_30_days"] == 8)
        check("policy: the anti-spam rules are unchanged",
              policy["one_recipient_per_email"] and policy["max_unanswered_emails_per_person"] == 1
              and policy["max_follow_ups_per_person"] == 1 and policy["max_body_words"] <= 420
              and policy["require_expert_public_email_source"])

        check("a clean spec passes", problems_for(spec_path, []) == [], str(problems_for(spec_path, [])))
        five_old = [contact(f"p{i}@example.org", 19) for i in range(5)]
        check("five contacts 19 days ago no longer block (the old limit of 2 did)",
              problems_for(spec_path, five_old) == [], str(problems_for(spec_path, five_old)))
        two_this_week = [contact(f"q{i}@example.org", 3) for i in range(2)]
        check("two new people this week block a third",
              any("7 days" in p for p in problems_for(spec_path, two_this_week)))
        eight = [contact(f"r{i}@example.org", 10 + i) for i in range(8)]
        check("eight new people in 30 days block a ninth", any("30 days" in p for p in problems_for(spec_path, eight)))
        check("an unanswered e-mail to the same person blocks, whatever its age",
              any("unanswered" in p for p in problems_for(spec_path, [contact("new@example.org", 200)])))
        check("the same person within 60 days blocks even after a reply",
              any("wait 60 days" in p for p in problems_for(spec_path, [contact("new@example.org", 30, "replied")])))
        check("the same person after 60 days and a reply is allowed",
              problems_for(spec_path, [contact("new@example.org", 61, "replied")]) == [])
        check("declined means never again",
              any("not to be contacted" in p for p in problems_for(spec_path, [contact("new@example.org", 400, "declined")])))

        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for needle, body in (("AI assistant", BODY.replace("AI assistant", "helper")),
                             ("no reply is needed", BODY.replace("no reply is needed", "reply if you like")
                              .replace("Thank you for your time.", "Thanks.")),
                             ("not personalised", BODY.replace("0000.00001", "a paper"))):
            changed = dict(spec, body=body)
            found = outreach.check_rules(outreach.prepared(changed), {"contacts": []})
            check(f"body rule still enforced: {needle}", any(needle in p for p in found), str(found))
        two_people = dict(spec, to="new@example.org, other@example.org")
        check("one recipient only", any("one recipient" in p for p in outreach.check_rules(two_people, {"contacts": []})))

        # question e-mails
        plan = json.loads(evidence.read_text(encoding="utf-8"))["preregistration"]
        evidence.write_text(json.dumps({"preregistration": plan, "all_gates_passed": False}), encoding="utf-8")
        check("gates not passed: a claim e-mail is refused", any("gates" in p for p in problems_for(spec_path, [])))
        evidence.write_text(json.dumps({"preregistration": plan, "all_gates_passed": False, "kind": "question",
                                        "question_gates_passed": True}), encoding="utf-8")
        check("gates not passed: a question that says what is unchecked is allowed", problems_for(spec_path, []) == [],
              str(problems_for(spec_path, [])))
        silent = dict(spec, body=BODY.replace("No mathematician has read the argument.", "It is surely right."))
        check("a question that hides what is unchecked is refused",
              any("has not been checked" in p for p in outreach.check_rules(outreach.prepared(silent), {"contacts": []})))
        evidence.write_text(json.dumps({"preregistration": plan, "all_gates_passed": False, "kind": "question",
                                        "question_gates_passed": False}), encoding="utf-8")
        check("a question whose own checks failed is refused", any("rests on" in p for p in problems_for(spec_path, [])))

    # the recurring queue
    with tempfile.TemporaryDirectory() as folder:
        spec_path, evidence, sent = setup(folder)
        outreach.save_ledger({"contacts": [contact(f"q{i}@example.org", 3) for i in range(2)]})
        lines = outreach.run_queue()
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        check("queue: a blocked spec is not sent and the reason is written into it",
              sent == [] and "7 days" in " ".join(spec.get("_last_refusal", {}).get("rules", [])), str(lines))
        check("queue: the expert carried by the spec is added to experts.json",
              "new@example.org" in json.loads(outreach.EXPERTS_FILE.read_text(encoding="utf-8")))

        outreach.save_ledger({"contacts": []})
        second = outreach.QUEUE_DIR / "b-spec.json"
        other = json.loads(spec_path.read_text(encoding="utf-8"))
        other.pop("_last_refusal")
        other["to"] = "second@example.org"
        other["_expert_entry"] = {"second@example.org": {"name": "Dr Two", "email_source": "https://example.org/2",
                                                         "their_work": ["0000.00001"]}}
        second.write_text(json.dumps(other), encoding="utf-8")
        status_lines = outreach.run_queue(status_only=True)
        check("queue --status changes nothing", sent == [] and len(status_lines) == 2 and outreach.load_ledger()["contacts"] == [],
              str(status_lines))
        outreach.run_queue()
        ledger = outreach.load_ledger()["contacts"]
        check("queue: one e-mail per run, oldest file first",
              sent == [("new@example.org", "A question")] and len(ledger) == 1 and ledger[0]["state"] == "sent", str(sent))
        check("queue: the sent spec is marked", json.loads(spec_path.read_text(encoding="utf-8")).get("_sent") == TODAY.isoformat())
        outreach.run_queue()
        check("queue: the next run sends the next spec and never the first one again",
              [to for to, _ in sent] == ["new@example.org", "second@example.org"], str(sent))
        outreach.run_queue()
        check("queue: nothing left, nothing sent", len(sent) == 2)

        jobs = json.loads(outreach.RESEARCH_QUEUE.read_text(encoding="utf-8"))["jobs"]
        weekly = [job for job in jobs if job["id"].startswith("outreach_weekly_")]
        check("weekly review: queued once per week, for an AI session",
              len(weekly) == 1 and weekly[0]["status"] == "needs_ai" and weekly[0]["id"] == "outreach_weekly_2026w48", str(jobs))
        check("weekly review: next week gets its own job",
              outreach.weekly_review_job(TODAY + dt.timedelta(days=7)) == "outreach_weekly_2026w49")

        sys.modules["gmail_compose"].send_now = lambda to, subject, body: False
        outreach.save_draft = lambda *args, **kwargs: "https://draft.example"
        third = outreach.QUEUE_DIR / "c-spec.json"
        other["to"] = "third@example.org"
        other["_expert_entry"] = {"third@example.org": {"name": "Dr Three", "email_source": "https://example.org/3",
                                                        "their_work": ["0000.00001"]}}
        third.write_text(json.dumps(other), encoding="utf-8")
        outreach.POLICY["max_new_contacts_per_7_days"] = 5
        outreach.run_queue()
        check("queue: a send Gmail does not confirm becomes a draft and is not tried again",
              json.loads(third.read_text(encoding="utf-8")).get("_drafted") == TODAY.isoformat()
              and outreach.load_ledger()["contacts"][-1]["state"] == "drafted" and list(outreach.waiting_specs()) == [])

    print(f"\n{len(FAILURES)} failed" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
