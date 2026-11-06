"""Monday job: digest -> public project page -> push -> local dashboard.

1. millennium_monitor.py writes this week's digest (and opens it).
2. make_site.py rebuilds docs/index.html from the public parts only.
3. If the page changed, commit just docs/index.html and push it (GitHub Pages redeploys).
4. dashboard.py rebuilds the private local dashboard.

Every step is logged to weekly.log. A failing step is logged and the next steps still run.
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "weekly.log"
PRIVATE_MARKERS = ("@", "contacts_log", "outreach.py mark")


def log(message):
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp}  {message}\n")


def run(command):
    result = subprocess.run(
        command,
        cwd=HERE,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = (result.stdout + result.stderr).strip().splitlines()
    tail = " | ".join(output[-3:])
    return result.returncode, tail


def page_is_public_only():
    page = (HERE / "docs" / "index.html").read_text(encoding="utf-8")
    return not any(marker in page for marker in PRIVATE_MARKERS)


def main():
    log("weekly run started")

    code, tail = run([sys.executable, "millennium_monitor.py"])
    log(f"digest: exit {code}  {tail}")

    code, tail = run([sys.executable, "make_site.py"])
    log(f"site: exit {code}  {tail}")

    if code == 0 and page_is_public_only():
        run(["git", "add", "docs/index.html"])
        changed, _ = run(["git", "diff", "--cached", "--quiet"])
        if changed:
            today = dt.date.today().isoformat()
            run(["git", "commit", "-q", "-m", f"Weekly digest page {today}"])
            run(["git", "pull", "--rebase", "-q"])
            code, tail = run(["git", "push", "-q", "origin", "main"])
            log(f"publish: push exit {code}  {tail}")
        else:
            log("publish: page unchanged, nothing pushed")
    elif code == 0:
        log("publish: BLOCKED - page contained a private marker, not pushed")

    code, tail = run([sys.executable, "dashboard.py"])
    log(f"dashboard: exit {code}  {tail}")
    log("weekly run finished")


if __name__ == "__main__":
    main()
