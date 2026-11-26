"""daily_report.py: the mail names machine-checked steps beside tokens, and keeps Vector's steps apart.

    python tests/test_daily_report.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_report  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + ("" if condition else f"  {detail}"))
    if not condition:
        FAILURES.append(name)


def main():
    info = {"proved": 5, "total": 10}
    lab = {"id": "F1", "proved_by": "lab_flagship_261125_2"}
    vector = [{"id": name, "proved_by": "installed"} for name in ("F2", "F3")]

    line, card = daily_report.checked_steps([lab, *vector], info, 1_500_000_000)
    check("all three steps are counted, 5 of 10 in all", "3 on this day" in line and "5 of 10 in all" in line, line)
    check("who wrote them is named", "lab workers: F1" in line and "Vector session, installed: F2, F3" in line, line)
    check("the cost divides by the lab's own steps only", "1.50 B lab tokens per step a lab worker proved" in line, line)
    check("the card says how many were the lab's", card == "3 (1 by lab workers)", card)

    line, card = daily_report.checked_steps(vector, info, 1_500_000_000)
    check("no lab step: the tokens are said to have bought none", "bought none" in line and "per step" not in line, line)

    line, card = daily_report.checked_steps([], {"proved": 0, "total": 10}, None)
    check("an empty day is plain", "0 on this day, 0 of 10 in all; tokens not recorded" in line and card == "0 (0 by lab workers)", line)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        text = "\n".join(daily_report.people_lines(root))
        check("people: an empty lab says so", "none recorded yet" in text and "Mail waiting to go out: 0" in text, text)
        (root / "collab").mkdir()
        (root / "collab" / "targets.json").write_text(json.dumps({"targets": [
            {"id": "b-project", "state": "ready", "priority": 2, "next": "open it", "ready_file": "collab/b/READY.md"},
            {"id": "a-people", "state": "replayed", "priority": 1, "next": "write the offer"}]}), encoding="utf-8")
        queue = root / "outbox" / "outreach"
        queue.mkdir(parents=True)
        mail = {"to": "x@example.org", "subject": "Replay of your archive", "body": "b"}
        (queue / "1-offer.json").write_text(json.dumps(dict(mail, _last_refusal={"rules": ["cool-off"]})), encoding="utf-8")
        (queue / "2-sent.json").write_text(json.dumps(dict(mail, _sent="2026-11-25")), encoding="utf-8")
        (queue / "3-gone.json").write_text(json.dumps(dict(mail, _withdrawn=True)), encoding="utf-8")
        (queue / "1-EVIDENCE.json").write_text(json.dumps({"kind": "cowork"}), encoding="utf-8")
        lines = daily_report.people_lines(root)
        text = "\n".join(lines)
        check("people: targets are counted and listed by priority",
              "1 ready" in text and "1 replayed" in text and text.index("a-people") < text.index("b-project"), text)
        check("people: only mail that has not gone out is listed, with what it waits for",
              "Mail waiting to go out: 1" in text and "1-offer.json to x@example.org" in text and "waits: cool-off" in text
              and "2-sent" not in text and "3-gone" not in text and "EVIDENCE" not in text, text)
        check("people: how to stop a mail is said", "_withdrawn" in text, text)
        check("people: a ready contribution is said to be opened by the lab, and how to stop it",
              "Ready, the lab opens the pull request itself" in text and "b-project - collab/b/READY.md" in text
              and "Needs you" not in text, text)
        (root / "collab" / "targets.json").write_text(json.dumps({"targets": [
            {"id": "b-project", "state": "ready", "priority": 2, "next": "-", "ready_file": "collab/b/READY.md",
             "needs_juwen": True, "needs_juwen_why": "the project asks for a signed agreement"},
            {"id": "c-project", "state": "opened", "priority": 3, "next": "-", "pr_url": "https://example.org/pull/1"},
            {"id": "d-project", "state": "closed", "priority": 4, "next": "-", "needs_juwen": True}]}), encoding="utf-8")
        (root / "collab" / "NEEDS_JUWEN.md").write_text(
            "# asked of Juwen\n- [ ] 2026-11-26 Dr X: a call next week (thread)\n- [x] 2026-11-20 Dr Y: done\n", encoding="utf-8")
        lines = daily_report.people_lines(root)
        text = "\n".join(lines)
        check("people: what only Juwen can do comes first, with the reason",
              lines[0] == "- Needs you: b-project - the project asks for a signed agreement - collab/b/READY.md"
              and lines[1] == "- Needs you: 2026-11-26 Dr X: a call next week (thread)", text)
        check("people: a ticked line and a closed target no longer ask for him",
              "Dr Y" not in text and "Needs you: d-project" not in text, text)
        check("people: a target that needs him is not said to be opened by the lab; an open pull request is linked",
              "the lab opens the pull request itself" not in text
              and "- Pull request open: c-project - https://example.org/pull/1" in text, text)
    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
