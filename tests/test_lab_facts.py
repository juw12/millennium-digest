"""Checks the facts note and its self-check on a throw-away lab, and that the tracked settings agree.

    python tests/test_lab_facts.py

The lab in the temporary folder has every file the note reads. Each self-check is first shown to stay quiet
on a lab whose files agree, then to speak when one file is changed. Exit code 0 = all checks passed.
"""
import copy
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import lab_facts  # noqa: E402

FAILURES = []
TODAY = dt.date(2026, 11, 21)
NOW = dt.datetime(2026, 11, 21, 12, 40)

DEPARTMENTS = {
    "departments": [
        {"id": "rh", "name": "Zeta Desk", "problem": "Riemann Hypothesis", "every_hours": 20, "brief": "Continue."},
        {"id": "prize", "name": "Bounty Hunters", "problem": "Computer-attackable prize problems", "slots": 2,
         "brief": "Work the idea card."},
        {"id": "squares", "name": "Packing Shop", "problem": "Squares in Squares (Kaggle, $50k)", "slots": 3,
         "brief": "Kaggle Squares in Squares, $50k; $5k milestone prizes to the leaderboard leader."},
    ],
}
TARGETS = {
    "milestones": [
        {"id": "M1", "title": "Lab at full speed", "due": "2026-11-17", "check": "steps_per_day", "goal": 8},
        {"id": "K1", "title": "Kaggle Squares milestone prize: $5,000 to the leaderboard leader", "due": "2027-01-17",
         "done": False, "note": "manual: Juwen submits from his Kaggle account"},
        {"id": "M5", "title": "Squares in Squares final submission (top 5 win $12k-$5k)", "due": "2027-07-18"},
    ],
    "not_on_timeline": "A proof of any Millennium problem.",
}
PRIZES = {
    "_note": "Paid math problems.",
    "prizes": [
        {"name": "Squares in Squares (Kaggle)", "sponsor": "Google LLC",
         "amount": "$50,000 prize pool: places 1-5 win $12,000, $10,000, $8,000, $5,000, $5,000",
         "deadline": "entry 2027-07-11, final 2027-07-18", "fit": "high", "status": "open"},
        {"name": "Millennium Prize Problems", "sponsor": "Clay Mathematics Institute", "amount": "$1,000,000 each",
         "deadline": "none", "fit": "very low", "status": "open"},
    ],
}
CATALOG = {
    "checked": "2026-11-17", "error": None,
    "items": [
        {"id": "PPL 043", "title": "Sun · Alternating sums of consecutive primes", "reward": "$1,000"},
        {"id": "PPL 090", "title": "Shallit #3 · NFA separating-word bounds", "reward": "£50"},
        {"id": "PPL 167", "title": "Poseidon-31 reduced-round attack", "reward": "At least $5,000 from $90,000 fund"},
        {"id": "KAGGLE-SQ", "title": "Squares in Squares", "reward": "$50,000 pool"},
    ],
}
JOBS = [
    {"id": "lab_squares_261120_1", "dept": "squares", "status": "done", "finished": "2026-11-20T08:00:00",
     "title": "Packing Shop: certify four rows", "referee": "pass", "novelty": "negative"},
    {"id": "lab_squares_261120_2", "dept": "squares", "status": "needs_review", "finished": "2026-11-20T21:10:00",
     "title": "Packing Shop: census"},
    {"id": "lab_squares_261117_1", "dept": "squares", "status": "done", "finished": "2026-11-17T03:00:00",
     "title": "Packing Shop: audit"},
    {"id": "lab_squares_261110_1", "dept": "squares", "status": "done", "finished": "2026-11-10T03:00:00",
     "title": "Packing Shop: older than a week"},
    {"id": "lab_squares_261121_1", "dept": "squares", "status": "done", "finished": "2026-11-21T01:00:00",
     "title": "Packing Shop: finished today"},
    {"id": "lab_squares_261121_2", "dept": "squares", "status": "running", "title": "Packing Shop: running"},
    {"id": "lab_prize_261120_1", "dept": "prize", "status": "done", "finished": "2026-11-20T10:00:00",
     "topic_id": "PPL 090", "title": "Bounty Hunters: PPL 090 (Shallit #3, £50): exact table"},
    {"id": "lab_prize_261119_1", "dept": "prize", "status": "done", "finished": "2026-11-19T10:00:00",
     "topic_id": "PPL 090", "title": "Bounty Hunters: PPL 090 census"},
    {"id": "lab_prize_261118_1", "dept": "prize", "status": "done", "finished": "2026-11-18T10:00:00",
     "topic_id": "PPL 043", "title": "Bounty Hunters: PPL 043 (Sun alternating sums of consecutive primes, $1,000)"},
    {"id": "lab_prize_261121_1", "dept": "prize", "status": "needs_ai", "title": "Bounty Hunters: next"},
    {"id": "lab_rh_261120_1", "dept": "rh", "status": "done", "finished": "2026-11-20T18:52:00",
     "title": "Zeta Desk: CUE template audit"},
    {"id": "lab_rh_261119_9", "dept": "rh", "status": "done", "finished": True, "title": "finish time is not text"},
    {"id": "lab_panel_261120_1", "dept": "panel", "status": "done", "finished": "2026-11-20T23:00:00",
     "panel_verdict": str({"lab_squares_261120_1": "pass"})},
    {"id": "daily_rh_1", "focus": "rh", "status": "done", "finished": "2026-11-05T09:00:00", "title": "first version"},
]
UPDATER = {"checked": "2026-11-21T12:30:00", "state": "current", "result": "up to date at abc1234",
           "since": "2026-11-20T08:00:00"}


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")


def build_lab(base, **changed):
    """A lab whose files agree. `changed` replaces one file's content (None removes the file)."""
    lab = base / "lab"
    automation = lab / "automation"
    files = {
        automation / "lab_departments.json": changed.get("departments", DEPARTMENTS),
        automation / "lab_targets.json": changed.get("targets", TARGETS),
        automation / "prizes.json": changed.get("prizes", PRIZES),
        automation / "prize_catalog.json": changed.get("catalog", CATALOG),
        automation / "logs" / "selfupdate.json": changed.get("updater", UPDATER),
        lab / "research_queue.json": changed.get("queue", {"jobs": JOBS}),
        lab / "outbox" / "public_decisions.json": {"hold": {"a.md": "wait", "b.md": "wait"}, "published": {"c.md": "x"}},
        lab / "prize_work" / "squares-in-squares" / "best_score.json": {"valid": True, "score": 22.108257},
        lab / "prize_work" / "squares-in-squares" / "submission.csv": "n,side\n",
    }
    for path, data in files.items():
        if data is None:
            path.unlink(missing_ok=True)
        else:
            write(path, data)
    return automation


def milestone_status(milestone, jobs, today):
    """Stands in for lab.milestone_status, which reads the real lab's folders."""
    if milestone.get("check") == "steps_per_day":
        return {"done": True, "note": "9.0 finished steps/day over the last 3 days (goal 8)"}
    return {"done": bool(milestone.get("done")), "note": milestone.get("note", "set by hand")}


def findings_for(base, **changed):
    facts = lab_facts.collect(build_lab(base, **changed), TODAY, NOW, milestone_status)
    return lab_facts.self_check(facts)


def changed_copy(original, edit):
    data = copy.deepcopy(original)
    edit(data)
    return data


def main():
    figures = lab_facts.money_figures
    check("money: $50k is $50,000", figures("Kaggle, $50k") == figures("$50,000 prize pool") == {("$", 50000)})
    check("money: a range gives both ends", figures("top 5 win $12k-$5k") == {("$", 12000), ("$", 5000)})
    check("money: millions", figures("was $10M fund; $1,000,000 each; $2 million")
          == {("$", 10_000_000), ("$", 1_000_000), ("$", 2_000_000)})
    check("money: the currency is part of the figure", figures("£50") == {("£", 50)} and figures("£50") != figures("$50"))
    check("money: two figures in one reward", figures("At least $5,000 from $90,000 fund") == {("$", 5000), ("$", 90000)})
    check("money: a word after the figure is not a scale", figures("$5 Kaggle credit") == {("$", 5)})
    check("money: text without a currency sign has no figure", figures("120,000,000 yen, 8 workers, n = 1..1000") == set())
    check("catalogue numbers: PPL 043, PPL-43 and 043 are one problem",
          lab_facts.catalog_number("PPL 043") == lab_facts.catalog_number("PPL-43") == lab_facts.catalog_number("043") == 43
          and lab_facts.catalog_number("KAGGLE-SQ") is None)

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        automation = build_lab(base)
        facts = lab_facts.collect(automation, TODAY, NOW, milestone_status)
        squares, prize, rh = (facts["per_desk"][name] for name in ("squares", "prize", "rh"))
        check("desk counts: yesterday", (squares["yesterday"], prize["yesterday"], rh["yesterday"]) == (2, 1, 1), str(squares))
        check("desk counts: the 7 days before today, today itself left out", squares["week"] == 3 and prize["week"] == 3,
              f"{squares} {prize}")
        check("desk counts: all finished and open", (squares["total"], squares["open"]) == (5, 1)
              and (prize["total"], prize["open"]) == (3, 1), f"{squares} {prize}")
        check("a finish time that is not text is counted as finished, on no day",
              rh["total"] == 3 and rh["yesterday"] == 1 and rh["week"] == 1, str(rh))
        check("jobs of the first version count for their desk", rh["last"] == dt.date(2026, 11, 20))
        check("results: finished, useful, past a reproduction",
              (facts["finished_total"], facts["useful"], facts["useful_strong"]) == (12, 1, 1),
              str((facts["finished_total"], facts["useful"], facts["useful_strong"])))
        check("results: published and held", (facts["published"], facts["held"]) == (1, 2))
        check("prize problems: jobs counted, prize from the catalogue",
              [(p["id"], p["reward"], p["jobs"]) for p in facts["problems"]]
              == [("PPL 090", "£50", 2), ("PPL 043", "$1,000", 1)], str(facts["problems"]))

        findings = lab_facts.self_check(facts)
        check("a lab whose files agree: nothing found", findings == [], " | ".join(findings))

        note = lab_facts.render(facts, findings)
        check("note: private, dated", "publish: false" in note and "updated: 2026-11-21" in note)
        check("note: the packing desk's prize is on its row",
              "| Packing Shop (`squares`) | Squares in Squares (Kaggle, $50k) | 3 at once | 2 | 3 | 5 | 1 |" in note)
        check("note: how often a desk runs", "| Zeta Desk (`rh`) | Riemann Hypothesis | every 20 h |" in note)
        check("note: the prize list with fit", "| Squares in Squares (Kaggle) | Google LLC | $50,000 prize pool" in note
              and "| high | open |" in note and "| very low | open |" in note)
        check("note: milestones with who submits", "manual: Juwen submits from his Kaggle account" in note)
        check("note: a desk without settings is still shown", "| `panel` | not a desk in lab_departments.json |" in note)
        check("note: score, submission file and what no file says",
              "best score 22.108257" in note and "is on disk: yes" in note
              and "Whether anything was submitted to Kaggle is not stated" in note)
        check("note: updater and a clean self-check", "up to date at abc1234" in note and "Nothing found" in note)

        vault = base / "vault" / "notes"
        check("no vault folder on this computer: nothing written",
              lab_facts.write_note(vault / lab_facts.NOTE_NAME, automation, TODAY, NOW, milestone_status) is None
              and not vault.exists())
        vault.mkdir(parents=True)
        written = lab_facts.write_note(vault / lab_facts.NOTE_NAME, automation, TODAY, NOW, milestone_status)
        check("vault folder there: note written, findings returned",
              written == [] and (vault / lab_facts.NOTE_NAME).read_text(encoding="utf-8") == note
              and not list(vault.glob("*.tmp")))

        # ---- each self-check speaks when one file is changed
        found = findings_for(base, prizes=changed_copy(PRIZES, lambda data: data["prizes"][0].update(amount="$50,000 prize pool")))
        check("money in a desk text or milestone that prizes.json lacks: named with its places",
              found == ["$12,000 is named in lab_targets.json (M5) but nowhere in prizes.json",
                        "$5,000 is named in lab_departments.json (squares), lab_targets.json (K1), lab_targets.json (M5) "
                        "but nowhere in prizes.json"], " | ".join(found))

        found = findings_for(base, prizes=changed_copy(
            PRIZES, lambda data: data["prizes"][0].update(deadline="entry 2026-11-01, final 2026-11-08")))
        check("an open prize past its last date", len(found) == 1 and "2026-11-08 has passed" in found[0], " | ".join(found))

        found = findings_for(base, targets=changed_copy(TARGETS, lambda data: data["milestones"][1].update(due="2026-11-01")))
        check("a milestone past its date that is not done", len(found) == 1 and found[0].startswith("milestone K1 was due 2026-11-01"),
              " | ".join(found))

        quiet = [job for job in JOBS if not (job.get("dept") == "prize" and job["status"] == "needs_ai")]
        aged = [dict(job, finished="2026-11-15T10:00:00") if job.get("dept") == "prize" else job for job in quiet]
        found = findings_for(base, queue={"jobs": aged})
        check("a desk with nothing open and nothing finished for days",
              len(found) == 1 and "Bounty Hunters (prize) last finished a job on 2026-11-15" in found[0], " | ".join(found))
        recent = [dict(job, finished="2026-11-19T10:00:00") if job.get("dept") == "prize" else job for job in quiet]
        check("the same desk two days after its last job: not yet", findings_for(base, queue={"jobs": recent}) == [])
        slow = changed_copy(DEPARTMENTS, lambda data: data["departments"].append(
            {"id": "scout", "name": "Library", "problem": "Hodge", "every_hours": 160}))
        scout_job = [{"id": "lab_scout_261105_1", "dept": "scout", "status": "done", "finished": "2026-11-05T10:00:00"}]
        check("a desk that runs every 160 h is given 20 days",
              findings_for(base, departments=slow, queue={"jobs": JOBS + scout_job}) == [])
        found = findings_for(base, departments=slow)
        check("a desk that never finished a job", found == ["Library (scout) has no finished job and nothing open"],
              " | ".join(found))

        wrong = JOBS + [
            {"id": "lab_prize_261112_9", "dept": "prize", "status": "done", "finished": "2026-11-12T09:12:00",
             "title": "PPL 043 (Bounty Hunters, $100) extension job"},
            {"id": "lab_prize_261114_12", "dept": "prize", "status": "done", "finished": "2026-11-14T05:24:00",
             "title": "block swap", "result": "PPL 090 block-swap-classification row, $50"},
            {"id": "lab_prize_261114_13", "dept": "prize", "status": "done", "finished": "2026-11-14T06:00:00",
             "title": "PPL 167 (Poseidon-31, $5k floor / $90k fund) survey; see also PPL 090 and PPL 043 ($1,000)"},
            {"id": "lab_prize_261114_14", "dept": "prize", "status": "done", "finished": "2026-11-14T07:00:00",
             "title": "PPL 999 ($7) is not in the catalogue; KAGGLE-SQ $50,000"},
        ]
        found = findings_for(base, queue={"jobs": wrong})
        check("a prize next to a catalogue number that the catalogue does not have",
              found == ["PPL 043: the catalogue says $1,000, but 1 job text(s) say $100 (first: lab_prize_261112_9)",
                        "PPL 090: the catalogue says £50, but 1 job text(s) say $50 (first: lab_prize_261114_12)"],
              " | ".join(found))

        found = findings_for(base, catalog=dict(CATALOG, checked="2026-11-01"))
        check("a catalogue not refreshed for more than two weeks", found == [
            "the prize catalogue was last refreshed on 2026-11-01, 20 days ago"], " | ".join(found))
        found = findings_for(base, catalog=dict(CATALOG, error="only 12 cards parsed"))
        check("a catalogue whose last refresh failed", len(found) == 1 and "only 12 cards parsed" in found[0])
        found = findings_for(base, catalog=None)
        check("no catalogue: said once, rewards not checked", found == [
            "prize_catalog.json could not be read, so prizes of catalogue problems are not checked"], " | ".join(found))

        found = findings_for(base, updater=None)
        check("the updater never ran", len(found) == 1 and "has not run on this computer yet" in found[0], " | ".join(found))
        stuck = {"checked": "2026-11-21T12:30:00", "state": "skipped", "since": "2026-11-20T08:00:00",
                 "result": "skipped: 2 commit(s) made here are not on GitHub and 1 on GitHub are not here"}
        found = findings_for(base, updater=stuck)
        check("the updater stuck for more than a day", len(found) == 1 and "since 2026-11-20T08:00:00" in found[0],
              " | ".join(found))
        check("the updater skipping for an hour: not yet",
              findings_for(base, updater=dict(stuck, since="2026-11-21T11:30:00")) == [])
        found = findings_for(base, updater={"checked": "2026-11-21T12:30:00", "state": "off", "since": "2026-11-21T12:00:00",
                                            "result": "switched off"})
        check("the updater switched off: said at once", len(found) == 1 and "switched off" in found[0], " | ".join(found))

        found = findings_for(base, queue="this is not JSON")
        check("an unreadable queue: one finding, not one per desk",
              found == ["research_queue.json could not be read, so the parts of this note that come from it are empty"],
              " | ".join(found))
        facts = lab_facts.collect(build_lab(base, queue="this is not JSON"), TODAY, NOW, milestone_status)
        check("an unreadable queue: the note is still written", "## Self-check" in lab_facts.render(facts, found))

        def broken(facts):
            raise KeyError("boom")

        original = lab_facts.CHECKS
        lab_facts.CHECKS = (broken,) + original
        try:
            found = findings_for(base)
        finally:
            lab_facts.CHECKS = original
        check("a check that breaks is reported, the others still run",
              len(found) == 1 and found[0].startswith("broken could not run (KeyError"), " | ".join(found))

        def failing_status(milestone, jobs, today):
            raise TypeError("'bool' object is not subscriptable")

        facts = lab_facts.collect(build_lab(base), TODAY, NOW, failing_status)
        check("a milestone that cannot be worked out does not cost the note",
              all("could not be worked out (TypeError)" in milestone["note"] for milestone in facts["milestones"]))

    # ---- the settings this repository ships must agree with each other
    shipped = lab_facts.collect(HERE, TODAY, NOW, milestone_status)
    found = lab_facts.check_money_listed(shipped)
    check("shipped files: every prize a desk or milestone names is in prizes.json", found == [], " | ".join(found))
    found = lab_facts.check_deadlines(shipped)
    check("shipped files: no open prize is past its last date", found == [], " | ".join(found))
    check("shipped files: the packing desk names its prize",
          any(desk["id"] == "squares" and ("$", 50000) in lab_facts.money_figures(desk["problem"])
              for desk in shipped["departments"]))

    print()
    print("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} CHECK(S) FAILED: " + "; ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
