"""Monday job: digest -> public project page -> push -> local dashboard.

1. millennium_monitor.py writes this week's digest (and opens it).
2. make_site.py rebuilds docs/index.html from the public parts only.
3. If the page changed, commit just docs/index.html and push it (GitHub Pages redeploys).
4. dashboard.py rebuilds the private local dashboard.

Every step is logged to weekly.log, including errors; a failing step does not stop the next ones.
Test without the slow digest step: python weekly.py --skip-digest
"""
import datetime as dt
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "weekly.log"
PRIVATE_MARKERS = ("@", "contacts_log", "outreach.py mark")
GIT_FALLBACKS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
    Path(r"C:\Program Files\Git\cmd\git.exe"),
]
# Scheduled tasks start with pythonw.exe; use python.exe next to it for child scripts.
PYTHON = str(Path(sys.executable).with_name("python.exe"))


def log(message):
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp}  {message}\n")


def find_git():
    found = shutil.which("git")
    if found:
        return found
    for candidate in GIT_FALLBACKS:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("git.exe not found on PATH or in the usual install folders")


def run(command):
    result = subprocess.run(
        command,
        cwd=HERE,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode, " | ".join(output[-3:])


def step(name, function):
    try:
        function()
    except Exception:
        log(f"{name}: ERROR " + traceback.format_exc().strip().replace("\n", " / "))


def digest():
    code, tail = run([PYTHON, "millennium_monitor.py"])
    log(f"digest: exit {code}  {tail}")


def site_and_publish():
    code, tail = run([PYTHON, "make_site.py"])
    log(f"site: exit {code}  {tail}")
    if code != 0:
        return
    page = (HERE / "docs" / "index.html").read_text(encoding="utf-8")
    if any(marker in page for marker in PRIVATE_MARKERS):
        log("publish: BLOCKED - page contained a private marker, not pushed")
        return
    git = find_git()
    run([git, "add", "docs/index.html"])
    changed, _ = run([git, "diff", "--cached", "--quiet"])
    if not changed:
        log("publish: page unchanged, nothing pushed")
        return
    today = dt.date.today().isoformat()
    run([git, "commit", "-q", "-m", f"Weekly digest page {today}"])
    run([git, "pull", "--rebase", "-q"])
    code, tail = run([git, "push", "-q", "origin", "main"])
    log(f"publish: push exit {code}  {tail}")


def prize_watch():
    code, tail = run([PYTHON, "prize_watch.py"])
    log(f"prize watch: exit {code}  {tail}")


def dashboard():
    code, tail = run([PYTHON, "dashboard.py"])
    log(f"dashboard: exit {code}  {tail}")


def ui_test():
    """Click every control on the dashboard and public pages (tests/test_dashboard_ui.py)."""
    code, tail = run([PYTHON, "tests/test_dashboard_ui.py", "--no-build"])
    log(f"ui test: exit {code}  {tail}")
    if code != 0:
        with open(HERE / "app_errors.log", "a", encoding="utf-8") as errors:
            errors.write(f"weekly ui test FAILED: {tail}\n")


def main():
    log("weekly run started" + (" (test: digest skipped)" if "--skip-digest" in sys.argv else ""))
    if "--skip-digest" not in sys.argv:
        step("digest", digest)
    step("publish", site_and_publish)
    step("prize watch", prize_watch)
    step("dashboard", dashboard)
    step("ui test", ui_test)
    log("weekly run finished")


if __name__ == "__main__":
    main()
