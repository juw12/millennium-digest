"""publish_notes.py: only a reviewed, rested, clean note goes up, with its banner, report, scripts and README row.

    python tests/test_publish_notes.py

Builds a throw-away lab (queue, outbox, reports, a tracked gate folder) and a throw-away public repo
with its own "GitHub" (a bare repo), then runs the real gate and the real publish against them.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab_useful  # noqa: E402
import publish_notes  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + ("" if condition else f"  {detail}"))
    if not condition:
        FAILURES.append(name)


def git(folder, *args):
    result = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=folder,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout


def note(folder, name, header, body="Numbers follow.\n", days_old=5):
    path = folder / name
    path.write_text(f"# Title of {name[11:-3]}\nTo: Somebody (to publish himself - do NOT post from this repo)\n"
                    f"Date: 2026-11-01\n{header}\n\n{body}", encoding="utf-8")
    old = (dt.datetime.now() - dt.timedelta(days=days_old)).timestamp()
    os.utime(path, (old, old))
    return path


def main():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        lab, automation = base / "lab", base / "lab" / "automation"
        outbox = lab / "outbox" / "public"
        for path in (automation, outbox, lab / "review_notes", lab / "prize_work" / "gate-a" / ".scratch"):
            path.mkdir(parents=True)
        jobs = [
            {"id": "lab_erdos_261101_1", "referee": "pass", "novelty": "extension"},
            {"id": "lab_erdos_261101_2", "referee": "pass", "novelty": "negative"},
            {"id": "lab_erdos_261101_3", "referee": "fail", "novelty": "extension"},
            {"id": "lab_erdos_261101_4", "referee": "pass", "novelty": "internal"},
            {"id": "lab_panel_261101_9", "status": "done", "finished": "2026-11-02T10:00:00", "panel_verdict": str({
                "lab_erdos_261101_1": "pass", "lab_erdos_261101_2": "pass", "lab_erdos_261101_3": "pass",
                "lab_erdos_261101_4": "pass"})},
        ]
        (lab / "research_queue.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
        (lab / "review_notes" / "lab_erdos_261101_1.md").write_text("Referee re-run: all values match.\n", encoding="utf-8")
        (lab / "review_notes" / "lab_erdos_261101_2.md").write_text("Write to someone@example.org first.\n", encoding="utf-8")
        gate = lab / "prize_work" / "gate-a"
        (gate / "run.py").write_text("print(42)\n", encoding="utf-8")
        (gate / "secret.py").write_text("PATH = 'C:\\\\Users\\\\me\\\\x'\n", encoding="utf-8")
        (gate / "big.bin").write_bytes(b"0" * 10)
        (gate / "untracked.py").write_text("print('scratch of the day')\n", encoding="utf-8")
        (gate / ".scratch" / "tmp.py").write_text("print(0)\n", encoding="utf-8")
        git(lab, "init", "-q")
        git(lab, "add", "prize_work/gate-a/run.py", "prize_work/gate-a/secret.py", "prize_work/gate-a/big.bin",
            "prize_work/gate-a/.scratch/tmp.py")
        git(lab, "commit", "-qm", "base")

        good = note(outbox, "2026-11-01-erdos11-good.md",
                    "Review gate: panel PASS + referee PASS `review_notes/lab_erdos_261101_1.md`\nGate: `prize_work/gate-a/`")
        note(outbox, "2026-11-01-erdos12-negative.md", "Review gate: PASS `review_notes/lab_erdos_261101_2.md`")
        note(outbox, "2026-11-01-erdos13-failed.md", "Review gate: `review_notes/lab_erdos_261101_3.md`")
        note(outbox, "2026-11-01-erdos14-internal.md", "Review gate: `review_notes/lab_erdos_261101_4.md`")
        note(outbox, "2026-11-01-erdos15-fresh.md", "Review gate: `review_notes/lab_erdos_261101_1.md`", days_old=1)
        note(outbox, "2026-11-01-erdos16-wait.md", "Review gate: `review_notes/lab_erdos_261101_1.md`",
             body="Section 3: panel FIX, do not post yet.\n")
        note(outbox, "2026-11-01-erdos17-leak.md", "Review gate: `review_notes/lab_erdos_261101_1.md`",
             body="Ask someone@example.org.\n")
        note(outbox, "2026-11-01-erdos18-held.md", "Review gate: `review_notes/lab_erdos_261101_1.md`")
        note(outbox, "2026-11-01-erdos19-corrected.md", "Review gate: `review_notes/lab_erdos_261101_1.md`")
        note(outbox, "2026-11-03-erdos19-correction.md", "Review gate: `review_notes/lab_erdos_261101_1.md`")
        note(outbox, "2026-11-01-erdos20-nojob.md", "Review gate: PASS")
        note(outbox, "2026-11-01-erdos21-unknown.md", "Review gate: `review_notes/lab_erdos_261109_77.md`")
        note(outbox, "2026-11-01-kaggle-squares-thing.md", "Review gate: `review_notes/lab_erdos_261101_1.md`")
        note(outbox, "ppl-001-status-audit-2026-11-01.md", "Review gate: `review_notes/lab_erdos_261101_1.md`")
        (lab / "outbox" / "public_decisions.json").write_text(
            json.dumps({"hold": {"2026-11-01-erdos18-held.md": "already known"}}), encoding="utf-8")

        origin, public = base / "origin.git", base / "public"
        git(base, "init", "-q", "--bare", "-b", "main", str(origin))
        git(base, "clone", "-q", str(origin), str(public))
        (public / "notes").mkdir()
        (public / "notes" / "2026-10-01-old.md").write_text("# Old\n", encoding="utf-8")
        (public / "README.md").write_text(
            "# Lab\n\n| Note | Problem | Kind | What it says |\n|---|---|---|---|\n| [old](notes/2026-10-01-old.md) | - | Reproduction | Old. |\n\n## After\n",
            encoding="utf-8")
        git(public, "checkout", "-q", "-b", "main")
        git(public, "add", "-A")
        git(public, "commit", "-qm", "first")
        git(public, "push", "-q", "origin", "main")
        for key in ("user.name", "user.email"):
            git(public, "config", key, "t@example.com" if key.endswith("email") else "t")

        publish_notes.HERE, publish_notes.ROOT = automation, lab
        publish_notes.OUTBOX, publish_notes.DECISIONS = outbox, lab / "outbox" / "public_decisions.json"
        publish_notes.LOG = lab / "logs" / "publish_notes.log"
        lab_useful.QUEUE = lab / "research_queue.json"
        (automation / "lab_departments.json").write_text(json.dumps(
            {"publish": {"repo": str(public), "cooloff_days": 3, "max_per_run": 5, "proxy_fallback": ""}}), encoding="utf-8")
        (automation / "experts.json").write_text(json.dumps({"a@b.org": {"name": "Private Person"}}), encoding="utf-8")
        sent = []
        sys.modules["notify_juwen"] = type("Stub", (), {"notify": staticmethod(lambda *args, **kwargs: sent.append(args))})

        rows, settings, repo, words = publish_notes.review()
        outcome = {path.name: (ok, info) for path, ok, info in rows}

        def kept(name, phrase):
            ok, info = outcome[name]
            check(f"{name[11:-3] or name}: stays ({phrase})", not ok and any(phrase in reason for reason in info), info)

        check("the reviewed, rested, clean note passes", outcome[good.name][0], outcome[good.name][1])
        check("a negative result passes and is labelled so",
              outcome["2026-11-01-erdos12-negative.md"][0]
              and outcome["2026-11-01-erdos12-negative.md"][1]["kind"] == "**Negative result**")
        kept("2026-11-01-erdos13-failed.md", "not Referee pass")
        kept("2026-11-01-erdos14-internal.md", "internal check")
        kept("2026-11-01-erdos15-fresh.md", "resting")
        kept("2026-11-01-erdos16-wait.md", "must wait")
        kept("2026-11-01-erdos17-leak.md", "private text")
        kept("2026-11-01-erdos18-held.md", "hold list")
        kept("2026-11-01-erdos19-corrected.md", "later correction")
        kept("2026-11-03-erdos19-correction.md", "audit, correction")
        kept("2026-11-01-erdos20-nojob.md", "no research job")
        kept("2026-11-01-erdos21-unknown.md", "not in the queue")
        kept("2026-11-01-kaggle-squares-thing.md", "audit, correction or competition")
        kept("ppl-001-status-audit-2026-11-01.md", "not a dated research note")
        for sample in ("C:\\Users\\me\\x", "C:\\\\Users\\\\me", "e:/Projects/lab/x.py"):
            check(f"a path on this PC is private: {sample}", publish_notes.private_hits(sample))
        check("ordinary maths text is not private", not publish_notes.private_hits("f(c) >= 0.104 at c = 1/2; see A003002 (ratio 3:2)"))
        check("an expert's name makes a text private", publish_notes.private_hits("thanks to Private Person", words))

        before = git(public, "rev-parse", "HEAD").strip()
        check("dry run: exit 0", publish_notes.main(["--dry-run"]) == 0)
        check("dry run changes nothing", git(public, "rev-parse", "origin/main").strip() == before
              and not (public / "notes" / good.name).exists())

        check("publish: exit 0", publish_notes.main([]) == 0)
        files = git(origin, "ls-tree", "-r", "--name-only", "main").splitlines()
        check("both notes are on GitHub", f"notes/{good.name}" in files and "notes/2026-11-01-erdos12-negative.md" in files, files)
        check("no other note went up", sum(name.startswith("notes/") for name in files) == 3, files)
        up = git(origin, "show", f"main:notes/{good.name}")
        check("the banner is in, the addressee line is out", "No human mathematician has reviewed it" in up and "To:" not in up, up[:300])
        check("the clean referee report went up", "review_notes/lab_erdos_261101_1.md" in files)
        check("a referee report with an address stays home", "review_notes/lab_erdos_261101_2.md" not in files)
        check("the tracked gate script went up", "prize_work/gate-a/run.py" in files)
        check("private, untracked, scratch and non-text gate files stay home",
              not any(name.endswith(("secret.py", "untracked.py", "tmp.py", "big.bin")) for name in files), files)
        readme = git(origin, "show", "main:README.md")
        table = [line for line in readme.splitlines() if line.startswith("|")]
        check("README: one row per new note, inside the table", len(table) == 5 and "| Erdős #11 | Extension |" in table[3]
              and readme.rstrip().endswith("## After"), readme)
        check("the local public copy is up to date and clean", (public / "notes" / good.name).exists()
              and not git(public, "status", "--porcelain").strip())
        check("the work tree and its branch are gone", "publish-" not in git(public, "worktree", "list")
              and "agent/publish" not in git(public, "branch", "--list"))
        saved = json.loads(publish_notes.DECISIONS.read_text(encoding="utf-8"))
        check("the decision file remembers both and keeps the hold list", len(saved["published"]) == 2 and len(saved["hold"]) == 1)
        check("one notice was sent", len(sent) == 1)

        head = git(origin, "rev-parse", "main").strip()
        check("a second run publishes nothing", publish_notes.main([]) == 0 and git(origin, "rev-parse", "main").strip() == head)

        (automation / "lab_departments.json").write_text(json.dumps(
            {"publish": {"repo": str(public), "enabled": False, "cooloff_days": 0}}), encoding="utf-8")
        check("switched off: nothing goes up", publish_notes.main([]) == 0 and git(origin, "rev-parse", "main").strip() == head)
    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
