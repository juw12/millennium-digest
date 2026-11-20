"""Checks the disk guard on a throw-away lab: what it removes, and above all what it must leave alone.

    python tests/test_disk_guard.py

Everything happens in a temporary folder with its own git repository and a real junction that points at a
folder outside the lab. Exit code 0 = all checks passed, 1 = at least one failed.
"""
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import disk_guard  # noqa: E402

FAILURES = []
HOUR = 3600
LIMITS = dict(disk_guard.DEFAULTS, low_free_gb=0, stop_free_gb=0)        # plenty of room
PRESSURE = dict(disk_guard.DEFAULTS, low_free_gb=10 ** 9, stop_free_gb=0)  # "drive nearly full"


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return path


def git(root, *arguments):
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


def build(base):
    """A small lab. Returns (lab, outside folder the junction points at)."""
    lab, outside = base / "lab", base / "outside"
    write(outside / "precious.txt", "must survive")
    write(lab / "prize_work" / "d1" / "data.bin", os.urandom(200_000))
    write(lab / "prize_work" / "d1" / "notes.md", "tracked dossier notes\n" * 40)
    write(lab / ".scratch_old" / "tracked_report.md", "the worker committed this")
    write(lab / "review_panel" / "p1" / "report.md", "panel report")
    git(lab, "init", "-q")
    git(lab, "add", "-A")
    git(lab, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    # untracked leftovers of a finished job
    write(lab / ".scratch_old" / "big.bin", os.urandom(400_000))
    write(lab / ".scratch_old" / "sub" / "run.log", "unique small evidence")
    write(lab / ".scratch_old" / "sub" / "notes_copy.md", (lab / "prize_work" / "d1" / "notes.md").read_bytes())
    subprocess.run(["cmd", "/c", "mklink", "/J", str(lab / ".scratch_old" / "link"), str(outside)], check=True,
                   capture_output=True)
    write(lab / ".scratch" / "panel_261119_16" / "clone.bin", os.urandom(100_000))
    write(lab / ".scratch" / "loose.bin", os.urandom(100_000))
    write(lab / "prize_work" / "d1" / ".scratch_j1" / "work.bin", os.urandom(100_000))
    write(lab / "prize_work" / "d1" / "results.npy", os.urandom(100_000))  # untracked data, not scratch
    write(lab / "review_panel" / "p1" / "copy" / "data.bin", (lab / "prize_work" / "d1" / "data.bin").read_bytes())
    write(lab / "review_panel" / "p1" / "copy" / "own.bin", os.urandom(200_000))
    write(lab / "prize_work" / "lean" / ".lake" / "build.olean", os.urandom(100_000))
    return lab, outside


def main():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        lab, outside = build(base)
        logs = base / "logs"
        disk_guard.running_job_names = lambda: set()
        now = time.time()

        dry = disk_guard.full_pass(lab, logs, LIMITS, dry_run=True, now=now + 7 * HOUR, say=lambda line: None)
        check("dry run reports space", dry["freed"] > 0)
        check("dry run removes nothing", (lab / ".scratch_old" / "big.bin").exists() and not logs.exists())

        fresh = disk_guard.full_pass(lab, logs, LIMITS, now=now + 1 * HOUR, say=lambda line: None)
        check("inside the waiting time nothing is removed", fresh["freed"] == 0 and
              (lab / ".scratch_old" / "big.bin").exists() and (lab / "review_panel" / "p1" / "copy" / "data.bin").exists())

        # a byte copy keeps the old write time of its source; its creation time is new, so it still counts as fresh
        aged = lab / ".scratch_old" / "big.bin"
        os.utime(aged, (now - 90 * 24 * HOUR, now - 90 * 24 * HOUR))
        check("old write time alone does not make scratch finished",
              not disk_guard.finished(lab / ".scratch_old", 6, now + 1 * HOUR, set())[0])

        disk_guard.running_job_names = lambda: {"panel26111916"}
        disk_guard.full_pass(lab, logs, LIMITS, now=now + 7 * HOUR, say=lambda line: None)
        check("scratch of a running job is left alone", (lab / ".scratch" / "panel_261119_16" / "clone.bin").exists())
        check("finished lab scratch: untracked files removed", not (lab / ".scratch_old" / "big.bin").exists()
              and not (lab / ".scratch" / "loose.bin").exists())
        check("tracked file in scratch kept", (lab / ".scratch_old" / "tracked_report.md").exists())
        check("junction removed, its target untouched", not (lab / ".scratch_old" / "link").exists()
              and (outside / "precious.txt").read_text() == "must survive")
        archives = list((logs / "scratch_archive").glob("*.zip"))
        names = [name for archive in archives for name in zipfile.ZipFile(archive).namelist()]
        check("unique small text file archived before removal", any(name.endswith("run.log") for name in names), names)
        check("copy of a tracked file not archived", not any(name.endswith("notes_copy.md") for name in names), names)
        check("dossier scratch waits longer", (lab / "prize_work" / "d1" / ".scratch_j1" / "work.bin").exists())
        check("identical review_panel copy removed", not (lab / "review_panel" / "p1" / "copy" / "data.bin").exists())
        check("its original kept", (lab / "prize_work" / "d1" / "data.bin").exists())
        check("review_panel file without a twin kept", (lab / "review_panel" / "p1" / "copy" / "own.bin").exists())
        check("tracked panel report kept", (lab / "review_panel" / "p1" / "report.md").exists())
        check("untracked data outside scratch kept", (lab / "prize_work" / "d1" / "results.npy").exists())
        check("build cache kept while there is room", (lab / "prize_work" / "lean" / ".lake" / "build.olean").exists())
        check("removed copies are logged", "data.bin" in (logs / "disk_guard_removed.log").read_text(encoding="utf-8"))

        disk_guard.running_job_names = lambda: set()
        disk_guard.full_pass(lab, logs, PRESSURE, now=now + 7 * HOUR, say=lambda line: None)
        check("low space: dossier scratch goes after the short wait",
              not (lab / "prize_work" / "d1" / ".scratch_j1" / "work.bin").exists())
        check("low space: finished job's scratch goes", not (lab / ".scratch" / "panel_261119_16").exists())
        check("low space: warm build cache kept", (lab / "prize_work" / "lean" / ".lake" / "build.olean").exists())
        disk_guard.full_pass(lab, logs, PRESSURE, now=now + 4 * 24 * HOUR, say=lambda line: None)
        check("low space: cold build cache removed", not (lab / "prize_work" / "lean" / ".lake").exists())
        check("untracked data outside scratch still kept", (lab / "prize_work" / "d1" / "results.npy").exists())
        status = subprocess.run(["git", "-C", str(lab), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True).stdout.strip()
        check("git sees no tracked file changed or deleted", status == "", status)

        disk_guard.tracked_files = lambda root: None
        write(lab / ".scratch_x" / "left.bin", b"0" * 1000)
        blind = disk_guard.full_pass(lab, logs, LIMITS, now=now + 30 * 24 * HOUR, say=lambda line: None)
        check("when git cannot answer nothing is removed", blind.get("skipped") == "git"
              and (lab / ".scratch_x" / "left.bin").exists())
    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
