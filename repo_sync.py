"""Pushes commits that are saved on this PC but not yet on GitHub (e.g. when the proxy was down).

Called after every scheduled Gmail check (gmail_inbox.py, every 2 h). Only tracked code goes up: private
files (ledger, contacts, digests, dashboard) are git-ignored. Never force-pushes, never rewrites history.

    python repo_sync.py      push now if anything is waiting
"""
import datetime as dt
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "repo_sync.log"
PUSH_TIMEOUT_S = 90


def _git(*args, timeout=15):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", *args], cwd=HERE, capture_output=True, text=True, timeout=timeout, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _log(text):
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")


def waiting_commits():
    try:
        result = _git("rev-list", "--count", "@{u}..HEAD")
        return int(result.stdout.strip() or 0) if result.returncode == 0 else 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def push_if_ahead():
    """Returns a short status line; never raises."""
    waiting = waiting_commits()
    if not waiting:
        return "nothing to push"
    try:
        result = _git("push", "--quiet", timeout=PUSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        status = f"push of {waiting} commit(s) timed out (network/proxy); will retry next check"
    except OSError as error:
        status = f"push failed: {error}"
    else:
        if result.returncode == 0:
            status = f"pushed {waiting} commit(s)"
        else:
            status = f"push refused: {(result.stderr or result.stdout).strip()[:200]}"
    _log(status)
    return status


if __name__ == "__main__":
    print(push_if_ahead())
