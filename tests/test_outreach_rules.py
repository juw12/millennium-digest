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
    outreach.TARGETS_FILE = folder / "collab" / "targets.json"
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
        check("policy: a steady pace (3 in 7 days, 10 in 30 days)",
              policy["max_new_contacts_per_7_days"] == 3 and policy["max_new_drafts_per_30_days"] == 10)
        check("policy: the anti-spam rules are unchanged",
              policy["one_recipient_per_email"] and policy["max_unanswered_emails_per_person"] == 1
              and policy["max_follow_ups_per_person"] == 1 and policy["max_body_words"] <= 420
              and policy["require_expert_public_email_source"])

        check("a clean spec passes", problems_for(spec_path, []) == [], str(problems_for(spec_path, [])))
        five_old = [contact(f"p{i}@example.org", 19) for i in range(5)]
        check("five contacts 19 days ago no longer block (the old limit of 2 did)",
              problems_for(spec_path, five_old) == [], str(problems_for(spec_path, five_old)))
        two_this_week = [contact(f"q{i}@example.org", 3) for i in range(2)]
        check("two new people this week do not block a third", problems_for(spec_path, two_this_week) == [])
        three_this_week = [contact(f"q{i}@example.org", 3) for i in range(3)]
        check("three new people this week block a fourth",
              any("7 days" in p for p in problems_for(spec_path, three_this_week)))
        ten = [contact(f"r{i}@example.org", 10 + i) for i in range(10)]
        check("ten new people in 30 days block an eleventh", any("30 days" in p for p in problems_for(spec_path, ten)))
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

    # co-work offers: the lab's finished work on the other person's own public result comes first
    with tempfile.TemporaryDirectory() as folder:
        spec_path, evidence, sent = setup(folder)
        report = Path(folder) / "REPORT.md"
        report.write_text("replay report", encoding="utf-8")
        offer_body = BODY.replace("Is the bound known?", "I replayed your archive. Would it help if I checked step 3?")

        def offer(written_days_ago=3, body=offer_body, **changes):
            data = {"kind": "cowork", "work_done": True, "report": str(report), "report_job": "lab_collab_1",
                    "their_public_source": "https://example.org/archive",
                    "written": (TODAY - dt.timedelta(days=written_days_ago)).isoformat(), **changes}
            evidence.write_text(json.dumps(data), encoding="utf-8")
            spec = dict(json.loads(spec_path.read_text(encoding="utf-8")), body=body)
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

        def referee(word):
            job = {"id": "lab_collab_1", "status": "done", **({"referee": word} if word else {})}
            outreach.RESEARCH_QUEUE.write_text(json.dumps({"jobs": [job]}), encoding="utf-8")

        offer()
        referee(None)
        check("co-work: a report the Referee has not re-run waits",
              any("not re-run yet" in p for p in problems_for(spec_path, [])), str(problems_for(spec_path, [])))
        referee("fix")
        check("co-work: a report the Referee sent back waits",
              any("has not passed lab_collab_1 (fix)" in p for p in problems_for(spec_path, [])))
        referee("pass")
        offer(report_job="")
        check("co-work: an offer that does not name the job behind its report is refused",
              any("report_job" in p for p in problems_for(spec_path, [])))
        offer(report_job="lab_collab_9")
        check("co-work: a job the queue does not know has no verdict",
              any("lab_collab_9" in p for p in problems_for(spec_path, [])))
        outreach.POLICY["cowork"]["report_must_pass_referee"] = False
        check("co-work: with the Referee rule switched off the same offer is allowed",
              problems_for(spec_path, []) == [], str(problems_for(spec_path, [])))
        outreach.POLICY["cowork"]["report_must_pass_referee"] = True
        offer()
        check("co-work: an offer with a finished report, after the cool-off, is allowed (no study plan needed)",
              problems_for(spec_path, []) == [], str(problems_for(spec_path, [])))
        offer(written_days_ago=1)
        check("co-work: an offer written yesterday waits (cool-off)",
              any("may go out" in p for p in problems_for(spec_path, [])))
        offer(written=None)
        check("co-work: no 'written' date is refused", any("'written'" in p for p in problems_for(spec_path, [])))
        offer(work_done=False)
        check("co-work: an offer before the work is done is refused",
              any("finished report" in p for p in problems_for(spec_path, [])))
        offer(report=str(Path(folder) / "missing.md"))
        check("co-work: a report that does not exist is refused",
              any("finished report" in p for p in problems_for(spec_path, [])))
        offer(their_public_source="")
        check("co-work: their result needs a public source",
              any("public source for the result" in p for p in problems_for(spec_path, [])))
        offer(body=BODY)
        check("co-work: an offer that does not ask what would help is refused",
              any("what would help" in p for p in problems_for(spec_path, [])))
        offer(body=offer_body.replace("No mathematician has read the argument.", "It is all fine."))
        check("co-work: an offer that hides what is unchecked is refused",
              any("has not been checked" in p for p in problems_for(spec_path, [])))

        offer()
        answered = contact("new@example.org", 20, "replied", answer_sent_date=(TODAY - dt.timedelta(days=8)).isoformat())
        three_new = [contact(f"q{i}@example.org", 3) for i in range(3)]
        check("co-work: to somebody who answered it is a conversation - allowed after 7 days, caps do not apply",
              problems_for(spec_path, [answered] + three_new) == [], str(problems_for(spec_path, [answered] + three_new)))
        just_answered = contact("new@example.org", 20, "replied", answer_sent_date=(TODAY - dt.timedelta(days=2)).isoformat())
        check("co-work: two days after the lab's last mail in a conversation it waits",
              any("in a conversation wait 7 days" in p for p in problems_for(spec_path, [just_answered])))
        check("co-work: a first contact still counts against the weekly cap",
              any("7 days" in p for p in problems_for(spec_path, three_new)))
        check("co-work: an unanswered e-mail to the same person still blocks",
              any("unanswered" in p for p in problems_for(spec_path, [answered, contact("new@example.org", 9)])))
        check("co-work: declined still means never again",
              any("not to be contacted" in p for p in problems_for(spec_path, [contact("new@example.org", 90, "declined")])))
        evidence.write_text(json.dumps({"preregistration": str(Path(folder) / "PLAN.md"), "all_gates_passed": True}),
                            encoding="utf-8")
        check("a claim e-mail to somebody who answered 20 days ago still waits 60 days",
              any("wait 60 days" in p for p in problems_for(spec_path, [answered])))

        offer()
        outreach.save_ledger({"contacts": [answered]})
        outreach.run_queue()
        ledger = outreach.load_ledger()["contacts"]
        check("co-work: the queue sends it and the ledger entry is a conversation mail without follow-up",
              sent == [("new@example.org", "A question")] and ledger[-1].get("conversation") is True
              and ledger[-1].get("no_follow_up") is True and ledger[-1].get("kind") == "cowork", str(ledger[-1]))
        check("co-work: a conversation mail is not counted as a new contact",
              outreach.new_contacts({"contacts": ledger}, 7) == [])
        outreach.POLICY["follow_up_after_days_without_reply"] = 0
        check("co-work: no follow-up is ever drafted for an offer", outreach.followups(open_windows=False) == [])
        outreach.POLICY["cowork"]["enabled"] = False
        offer()
        check("co-work: switched off, the evidence is judged as a study and refused",
              any("gates" in p for p in problems_for(spec_path, [])), str(problems_for(spec_path, [])))

    # the recurring queue
    with tempfile.TemporaryDirectory() as folder:
        spec_path, evidence, sent = setup(folder)
        outreach.save_ledger({"contacts": [contact(f"q{i}@example.org", 3) for i in range(3)]})
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

    with tempfile.TemporaryDirectory() as folder:  # contributions for open projects
        setup(folder)
        folder = Path(folder)
        (folder / "collab" / "p" / "contribution").mkdir(parents=True)
        (folder / "collab" / "p" / "contribution" / "READY.md").write_text("ready", encoding="utf-8")
        ready = {"id": "p", "kind": "project", "state": "ready", "repo": "owner/name", "ready_job": "lab_collab_1",
                 "ready_file": "collab/p/contribution/READY.md", "ready_since": (TODAY - dt.timedelta(days=1)).isoformat()}

        def projects(*targets, referee=None):
            outreach.TARGETS_FILE.write_text(json.dumps({"targets": list(targets)}), encoding="utf-8")
            jobs = [job for job in json.loads(outreach.RESEARCH_QUEUE.read_text(encoding="utf-8"))["jobs"]
                    if job["id"] != "lab_collab_1"]
            jobs.append({"id": "lab_collab_1", "status": "done", **({"referee": referee} if referee else {})})
            outreach.RESEARCH_QUEUE.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")

        def queued(prefix):
            return [job for job in json.loads(outreach.RESEARCH_QUEUE.read_text(encoding="utf-8"))["jobs"]
                    if job["id"].startswith(prefix)]

        projects(ready)
        lines = outreach.project_pr_jobs()
        check("project: a contribution waits for the Referee and for its cool-off",
              queued("open_pr_") == [] and "Referee has not passed lab_collab_1" in lines[0]
              and "2 days later" in lines[0], str(lines))
        ready["ready_since"] = (TODAY - dt.timedelta(days=2)).isoformat()
        projects(ready, referee="fix: the count is off")
        check("project: 'fix' from the Referee is not a pass", queued("open_pr_") == [] and "fix" in outreach.project_pr_jobs()[0])
        projects(ready, referee="pass - re-run, numbers match")
        check("project: --status hands nothing over", "allowed" in outreach.project_pr_jobs(status_only=True)[0]
              and queued("open_pr_") == [])
        projects(dict(ready, needs_juwen=True, needs_juwen_why="a CLA"), referee="pass")
        check("project: one that needs Juwen is never handed over",
              "needs Juwen: a CLA" in outreach.project_pr_jobs()[0] and queued("open_pr_") == [])
        projects(dict(ready, _withdrawn=True), referee="pass")
        check("project: a withdrawn one is never handed over",
              "withdrawn" in outreach.project_pr_jobs()[0] and queued("open_pr_") == [])
        earlier = {"id": "q", "kind": "project", "state": "opened", "repo": "owner/name",
                   "opened_on": (TODAY - dt.timedelta(days=10)).isoformat()}
        projects(ready, earlier, referee="pass")
        check("project: one pull request per project in 30 days",
              "10 days ago" in outreach.project_pr_jobs()[0] and queued("open_pr_") == [])
        outreach.POLICY["project_contributions"]["enabled"] = False
        projects(ready, referee="pass")
        check("project: switched off in the policy means nothing is handed over",
              "switched off" in outreach.project_pr_jobs()[0] and queued("open_pr_") == [])
        outreach.POLICY["project_contributions"]["enabled"] = True
        second = dict(ready, id="p2", repo="other/name")
        projects(ready, second, referee="pass")
        lines = outreach.project_pr_jobs()
        jobs = queued("open_pr_")
        check("project: passed, waited, not withdrawn: ONE AI session job per run, the other next run",
              len(jobs) == 1 and jobs[0]["id"] == f"open_pr_p_{ready['ready_since'].replace('-', '')}"
              and jobs[0]["status"] == "needs_ai" and "next run" in lines[1], str(lines))
        text = jobs[0]["why"]
        check("project: the job says to re-read the project's rules, to disclose the AI help and when to leave it to Juwen",
              "rule on AI help again" in text and "prepared with an AI assistant" in text and "needs_juwen" in text
              and "owner/name" in text and "juw12" in text, text)
        outreach.project_pr_jobs()
        check("project: the same contribution is handed over once", len(queued("open_pr_p_")) == 1
              and len(queued("open_pr_p2_")) == 1)
        opened = dict(ready, state="opened", opened_on=TODAY.isoformat(), pr_url="https://example.org/pull/7")
        projects(opened, referee="pass")
        outreach.project_pr_jobs()
        outreach.project_pr_jobs()
        watch = queued("pr_watch_")
        check("project: an open pull request gets one look a week",
              len(watch) == 1 and watch[0]["id"] == "pr_watch_p_2026w48" and "https://example.org/pull/7" in watch[0]["why"]
              and "Written with an AI assistant" in watch[0]["why"], str(watch))
        projects({"id": "z", "kind": "people", "state": "ready"}, referee="pass")
        check("project: a people target is none of this step's business", outreach.project_pr_jobs() == [])

    print(f"\n{len(FAILURES)} failed" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
