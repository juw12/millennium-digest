"""Checks the updater on throw-away repositories: what it takes, and above all what it must leave alone.

    python tests/test_selfupdate.py

"GitHub" is a bare repository in a temporary folder, "pc" is the lab's checkout and "other" is the computer
that pushes a change. Exit code 0 = all checks passed, 1 = at least one failed.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import selfupdate  # noqa: E402

FAILURES = []
SETTINGS = {"enabled": True, "branch": "main", "proxy_fallback": ""}  # no proxy: every call stays on this disk


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def git(folder, *args):
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=folder, capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def push_change(other, name, content, message):
    """Another computer changes one file and pushes it to "GitHub"."""
    git(other, "pull", "--quiet", "--ff-only", "origin", "main")
    path = other / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(other, "add", name)
    git(other, "commit", "--quiet", "-m", message)
    git(other, "push", "--quiet", "origin", "main")
    return git(other, "rev-parse", "HEAD")


def head(folder):
    return git(folder, "rev-parse", "HEAD")


def main():
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        origin, pc, other = base / "github.git", base / "pc", base / "other"
        git(base, "init", "--quiet", "--bare", "-b", "main", str(origin))
        git(base, "clone", "--quiet", str(origin), str(other))
        (other / "tool.py").write_text("VALUE = 1\n", encoding="utf-8")
        (other / "settings.json").write_text('{"workers": 8}\n', encoding="utf-8")
        (other / ".gitignore").write_text("logs/\n", encoding="utf-8")
        git(other, "add", ".")
        git(other, "commit", "--quiet", "-m", "first version")
        git(other, "push", "--quiet", "origin", "HEAD:main")
        git(base, "clone", "--quiet", str(origin), str(pc))
        for folder in (pc, other):
            git(folder, "config", "user.name", "Test")
            git(folder, "config", "user.email", "test@example.invalid")

        state, sentence = selfupdate.check(pc, SETTINGS)
        check("nothing new: up to date", state == "current" and sentence.startswith("up to date"), sentence)

        new = push_change(other, "tool.py", "VALUE = 2\n", "second version")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("a pushed change arrives", state == "updated" and head(pc) == new, sentence)
        check("the file on the PC is the new one", (pc / "tool.py").read_text(encoding="utf-8") == "VALUE = 2\n")
        check("the sentence counts the changes", "1 new change(s)" in sentence, sentence)

        # ---- local work is never touched
        new = push_change(other, "tool.py", "VALUE = 3\n", "third version")
        before = head(pc)
        (pc / "settings.json").write_text('{"workers": 3}\n', encoding="utf-8")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("local edits: skipped and named", state == "skipped" and "settings.json" in sentence, sentence)
        check("local edits: nothing moved", head(pc) == before
              and (pc / "settings.json").read_text(encoding="utf-8") == '{"workers": 3}\n')
        git(pc, "checkout", "--quiet", "--", "settings.json")

        (pc / "logs").mkdir()
        (pc / "logs" / "pool.log").write_text("tick\n", encoding="utf-8")
        (pc / "scratch.txt").write_text("untracked\n", encoding="utf-8")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("untracked files do not block an update", state == "updated" and head(pc) == new, sentence)
        check("untracked files are still there", (pc / "scratch.txt").exists() and (pc / "logs" / "pool.log").exists())

        # ---- commits made on the PC
        (pc / "local.py").write_text("LOCAL = True\n", encoding="utf-8")
        git(pc, "add", "local.py")
        git(pc, "commit", "--quiet", "-m", "made on the PC")
        mine = head(pc)
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("an unpushed commit, nothing new: up to date and said so",
              state == "current" and "1 commit(s) made here are not on GitHub yet" in sentence, sentence)

        push_change(other, "tool.py", "VALUE = 4\n", "fourth version")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("both sides have commits: skipped, to be joined by hand",
              state == "skipped" and "joined by hand" in sentence and head(pc) == mine, sentence)
        check("the PC's own commit is intact", (pc / "local.py").exists()
              and (pc / "tool.py").read_text(encoding="utf-8") == "VALUE = 3\n")
        git(pc, "pull", "--quiet", "--rebase", "origin", "main")
        git(pc, "push", "--quiet", "origin", "main")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("joined by hand: up to date again", state == "current", sentence)

        # ---- a version that would stop the lab is not taken
        good = head(pc)
        push_change(other, "tool.py", "VALUE = (\n", "a file that does not compile")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("broken Python: not taken, file named",
              state == "skipped" and "tool.py" in sentence and "SyntaxError" in sentence and head(pc) == good, sentence)
        check("broken Python: the working file is kept", (pc / "tool.py").read_text(encoding="utf-8") == "VALUE = 4\n")
        fixed = push_change(other, "tool.py", "VALUE = 5\n", "fixed")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("the fix arrives, both changes at once", state == "updated" and head(pc) == fixed and "2 new" in sentence,
              sentence)

        good = head(pc)
        push_change(other, "settings.json", '{"workers": 8,}\n', "settings that are not JSON")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("broken JSON: not taken, file named",
              state == "skipped" and "settings.json" in sentence and head(pc) == good, sentence)
        push_change(other, "settings.json", '{"workers": 8}\n', "settings fixed")

        push_change(other, "notes/readme.txt", "(not code\n", "a text file with a bracket")
        git(other, "rm", "--quiet", "local.py")
        git(other, "commit", "--quiet", "-m", "a file removed")
        git(other, "push", "--quiet", "origin", "main")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("other file types and removed files are not compiled",
              state == "updated" and not (pc / "local.py").exists(), sentence)

        # ---- states in which nothing is tried
        git(pc, "checkout", "--quiet", "-b", "agent/experiment")
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("another branch: skipped", state == "skipped" and "agent/experiment" in sentence, sentence)
        git(pc, "checkout", "--quiet", "main")

        state, sentence = selfupdate.check(pc, dict(SETTINGS, enabled=False))
        check("switched off: nothing happens", state == "off", sentence)

        git(pc, "remote", "set-url", "origin", str(base / "gone.git"))
        state, sentence = selfupdate.check(pc, SETTINGS)
        check("GitHub not reachable: skipped", state == "skipped" and "not reachable" in sentence, sentence)
        git(pc, "remote", "set-url", "origin", str(origin))

        state, sentence = selfupdate.check(base / "other-folder-that-is-no-repo", SETTINGS)
        check("a folder that does not exist: skipped, no crash", state == "skipped", sentence)

        # ---- settings
        (pc / "lab_departments.json").write_text(json.dumps({"update": {"_note": "x", "enabled": False}}),
                                                 encoding="utf-8")
        check("settings: read from lab_departments.json",
              selfupdate.config(pc / "lab_departments.json")["enabled"] is False
              and "_note" not in selfupdate.config(pc / "lab_departments.json"))
        check("settings: defaults without the file", selfupdate.config(pc / "missing.json")["enabled"] is True)
        (pc / "lab_departments.json").unlink()

        # ---- what is kept for the log and for the facts note
        record_path, log_path = base / "state" / "selfupdate.json", base / "state" / "selfupdate.log"
        start = dt.datetime(2026, 11, 21, 9, 0, 0)
        selfupdate.remember("skipped", "skipped: GitHub not reachable", record_path, log_path, start)
        second = selfupdate.remember("skipped", "skipped: GitHub not reachable", record_path, log_path,
                                     start + dt.timedelta(minutes=30))
        check("a state that lasts keeps the time it began",
              second["since"] == "2026-11-21T09:00:00" and second["checked"] == "2026-11-21T09:30:00", str(second))
        third = selfupdate.remember("current", "up to date at abc1234", record_path, log_path,
                                    start + dt.timedelta(minutes=60))
        check("a new state starts its own time", third["since"] == "2026-11-21T10:00:00", str(third))
        check("the record can be read back", selfupdate.last_result(record_path) == third)
        check("no record: empty, no crash", selfupdate.last_result(base / "state" / "none.json") == {})
        check("every check is one log line", len(log_path.read_text(encoding="utf-8").splitlines()) == 3)

        lines = selfupdate.status(pc)
        check("status names branch, edits and the last check",
              any(line.startswith("branch: main") for line in lines)
              and any("local edits in tracked files: 0" in line for line in lines)
              and any("has not run in this folder yet" in line for line in lines), " | ".join(lines))

    script = selfupdate.task_script(r"C:\Python\pythonw.exe", r"E:\lab\automation\selfupdate.py")
    check("the scheduled task: named, every 30 minutes and at sign-in, replaces the old one",
          "-TaskName 'Millennium updates'" in script and "-RepetitionInterval (New-TimeSpan -Minutes 30)" in script
          and "-AtLogOn" in script and "-Force" in script, script)
    check("the scheduled task: runs on battery, catches up after sleep, never two at once, ended after 20 minutes",
          all(setting in script for setting in (
              "-AllowStartIfOnBatteries", "-DontStopIfGoingOnBatteries", "-StartWhenAvailable",
              "-MultipleInstances IgnoreNew", "-ExecutionTimeLimit (New-TimeSpan -Minutes 20)")), script)
    check("the scheduled task: runner and script quoted for PowerShell",
          "-Execute 'C:\\Python\\pythonw.exe'" in script
          and """-Argument '"E:\\lab\\automation\\selfupdate.py"'""" in script
          and "-WorkingDirectory 'E:\\lab\\automation'" in script, script)
    check("the scheduled task: a quote in a path is doubled",
          "-Execute 'C:\\O''Brien\\pythonw.exe'" in selfupdate.task_script(r"C:\O'Brien\pythonw.exe", "selfupdate.py"))
    if os.name != "nt":
        check("install on a computer without Task Scheduler: says so, exit 1", selfupdate.install() == 1)

    print()
    print("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} CHECK(S) FAILED: " + "; ".join(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
