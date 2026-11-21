"""Keeps the lab's automation on the newest version from GitHub, so a change made on another computer
arrives here by itself.

Runs from Task Scheduler ("Millennium updates", every 30 minutes) and on demand. It only fast-forwards:
on main, no edits in tracked files, nothing unpushed. It never merges, rebases, resets or touches local work.
Before it switches, every Python and JSON file the new version changes is read from GitHub's copy and has to
compile or parse; a version that does not is left where it is and named in the log, because a broken file
would stop every lab job on this PC with nobody watching.

The scheduled tasks (pool tick, watchdog, disk guard, ...) are short runs, so they use the new code at their
next start. The dashboard app uses it the next time it is opened.

    python selfupdate.py            check once (what the scheduled task does)
    python selfupdate.py status     where this copy stands; fetches nothing, changes nothing
    python selfupdate.py install    create or refresh the scheduled task (Windows)

Settings: "update" in lab_departments.json. "enabled": false switches it off.
Log: logs/selfupdate.log. Last result for other scripts: logs/selfupdate.json.
"""
import datetime as dt
import json
import ntpath
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "selfupdate.log"
RECORD = HERE / "logs" / "selfupdate.json"
DEPARTMENTS = HERE / "lab_departments.json"
DEFAULTS = {"enabled": True, "branch": "main", "proxy_fallback": "http://localhost:3128"}
TASK_NAME = "Millennium updates"
TASK_MINUTES = 30
TASK_LIMIT_MINUTES = 20  # a check that hangs is ended; the next one starts 30 minutes after the last began
LOCAL_TIMEOUT = 180  # seconds; ReachPilot's updater on this PC saw plain git calls pass 60 s at 100 % CPU
FETCH_TIMEOUT = 120
GIT_FALLBACKS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
    Path(r"C:\Program Files\Git\cmd\git.exe"),
]
NAMES_SHOWN = 3  # how many file names a log line spells out before it says "and N more"


def find_git():
    """Task Scheduler starts with a short PATH, so the usual Git for Windows folders are tried too."""
    found = shutil.which("git")
    if found:
        return found
    for candidate in GIT_FALLBACKS:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("git not found on PATH or in the usual install folders")


def config(path=DEPARTMENTS):
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8")).get("update", {})
    except (OSError, ValueError, AttributeError):
        saved = {}
    return {**DEFAULTS, **{key: value for key, value in saved.items() if not key.startswith("_")}}


def git(folder, *args, timeout=LOCAL_TIMEOUT, proxy=None, raw=False):
    """One git call in `folder`. raw=True returns stdout as bytes (a file exactly as GitHub has it)."""
    command = [find_git(), *(["-c", f"http.proxy={proxy}"] if proxy else []), *args]
    text_options = {} if raw else {"text": True, "encoding": "utf-8", "errors": "replace"}
    return subprocess.run(
        command,
        cwd=folder,
        capture_output=True,
        timeout=timeout,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        **text_options,
    )


def online(folder, settings, *args):
    """A git call that talks to GitHub: the normal route first, then the fallback proxy of the office network."""
    result = git(folder, *args, timeout=FETCH_TIMEOUT)
    if result.returncode and settings.get("proxy_fallback"):
        result = git(folder, *args, timeout=FETCH_TIMEOUT, proxy=settings["proxy_fallback"])
    return result


def _count(folder, revision_range):
    result = git(folder, "rev-list", "--count", revision_range)
    return int(result.stdout.strip() or 0) if result.returncode == 0 else None


def _short_head(folder):
    result = git(folder, "rev-parse", "--short", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def _named(names):
    shown = ", ".join(names[:NAMES_SHOWN])
    return shown + (f" and {len(names) - NAMES_SHOWN} more" if len(names) > NAMES_SHOWN else "")


def local_edits(folder):
    """Tracked files that differ from the last commit. Untracked files (logs, state) do not count."""
    result = git(folder, "status", "--porcelain", "--untracked-files=no")
    if result.returncode:
        return None
    return [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]


def broken_files(folder, target):
    """Python and JSON files that `target` adds or changes and that do not compile or parse: [(name, why)]."""
    listed = git(folder, "diff", "--name-only", "--diff-filter=AM", "HEAD", target)
    if listed.returncode:
        return [("(the list of changed files)", listed.stderr.strip()[:120] or "git diff failed")]
    broken = []
    for name in filter(None, (line.strip() for line in listed.stdout.splitlines())):
        kind = Path(name).suffix.lower()
        if kind not in (".py", ".json"):
            continue
        shown = git(folder, "show", f"{target}:{name}", raw=True)
        if shown.returncode:
            broken.append((name, "could not be read from the new version"))
            continue
        try:
            if kind == ".py":
                compile(shown.stdout, name, "exec", dont_inherit=True)
            else:
                json.loads(shown.stdout.decode("utf-8-sig"))
        except (SyntaxError, ValueError) as error:  # ValueError covers bad JSON, bad UTF-8 and null bytes
            line = getattr(error, "lineno", None)
            broken.append((name, f"{type(error).__name__}" + (f" at line {line}" if line else "")))
    return broken


def check(folder=HERE, settings=None):
    """One full check. Returns (state, sentence); state is "off", "current", "updated" or "skipped"."""
    folder = Path(folder)
    settings = settings or config(folder / DEPARTMENTS.name)
    if not settings.get("enabled", True):
        return "off", 'switched off ("update.enabled" is false in lab_departments.json)'
    branch = settings.get("branch") or "main"
    try:
        current = git(folder, "rev-parse", "--abbrev-ref", "HEAD")
        if current.returncode:
            return "skipped", "skipped: this folder is not a git checkout"
        if current.stdout.strip() != branch:
            return "skipped", f"skipped: on branch {current.stdout.strip()}, not {branch}"
        edits = local_edits(folder)
        if edits is None:
            return "skipped", "skipped: git status failed"
        if edits:
            return "skipped", f"skipped: {len(edits)} tracked file(s) have local edits ({_named(edits)})"
        if online(folder, settings, "fetch", "--quiet", "origin", branch).returncode:
            return "skipped", "skipped: GitHub not reachable"
        target = f"origin/{branch}"
        behind = _count(folder, f"HEAD..{target}")
        ahead = _count(folder, f"{target}..HEAD")
        if behind is None or ahead is None:
            return "skipped", f"skipped: {target} could not be compared with this copy"
        if behind == 0:
            waiting = f"; {ahead} commit(s) made here are not on GitHub yet" if ahead else ""
            return "current", f"up to date at {_short_head(folder)}{waiting}"
        if ahead:
            return "skipped", (
                f"skipped: {ahead} commit(s) made here are not on GitHub and {behind} on GitHub are not here; "
                "the two have to be joined by hand (git pull --rebase, then push)"
            )
        broken = broken_files(folder, target)
        if broken:
            name, why = broken[0]
            more = f" and {len(broken) - 1} more file(s)" if len(broken) > 1 else ""
            return "skipped", f"skipped: the new version is not taken, {name} does not load ({why}){more}"
        merged = git(folder, "merge", "--ff-only", "--quiet", target)
        if merged.returncode:
            reason = (merged.stderr or merged.stdout).strip().splitlines()
            return "skipped", f"skipped: git refused the fast-forward ({reason[-1][:160] if reason else 'no reason given'})"
        return "updated", f"updated: {behind} new change(s), now at {_short_head(folder)}"
    except subprocess.TimeoutExpired as error:
        return "skipped", f"skipped: git did not answer within {int(error.timeout)} s (PC busy or network down)"
    except OSError as error:
        return "skipped", f"skipped: {error}"


def remember(state, sentence, record_path=RECORD, log_path=LOG, now=None):
    """Append the result to the log and keep the last one, with the time its state began, for other scripts."""
    now = now or dt.datetime.now()
    stamp = now.isoformat(timespec="seconds")
    record_path, log_path = Path(record_path), Path(log_path)
    try:
        previous = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    since = previous.get("since") if previous.get("state") == state and previous.get("since") else stamp
    record = {"checked": stamp, "state": state, "result": sentence, "since": since}
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{now:%Y-%m-%d %H:%M:%S}  {sentence}\n")
        temp = record_path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        temp.replace(record_path)
    except OSError:
        pass  # a full or locked disk must not turn an update check into a crash
    return record


def last_result(record_path=RECORD):
    """The record `remember` kept, or {} when the updater has never run here."""
    try:
        data = json.loads(Path(record_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def status(folder=HERE):
    """Where this copy stands, from what is already on disk. Fetches nothing and changes nothing."""
    folder = Path(folder)
    settings = config(folder / DEPARTMENTS.name)
    branch = settings.get("branch") or "main"
    lines = [f"updater: {'on' if settings.get('enabled', True) else 'off'}"]
    try:
        current = git(folder, "rev-parse", "--abbrev-ref", "HEAD")
        lines.append(f"branch: {current.stdout.strip() or 'unknown'} at {_short_head(folder) or 'unknown'}")
        edits = local_edits(folder) or []
        lines.append(f"local edits in tracked files: {len(edits)}" + (f" ({_named(edits)})" if edits else ""))
        behind = _count(folder, f"HEAD..origin/{branch}")
        ahead = _count(folder, f"origin/{branch}..HEAD")
        if behind is not None and ahead is not None:
            lines.append(f"against GitHub as last fetched: {behind} to take, {ahead} to push")
    except (subprocess.TimeoutExpired, OSError) as error:
        lines.append(f"git could not be asked: {error}")
    last = last_result(folder / "logs" / RECORD.name)
    if last:
        lines.append(f"last check {last.get('checked')}: {last.get('result')} (in this state since {last.get('since')})")
    else:
        lines.append("last check: the updater has not run in this folder yet")
    return lines


def task_script(runner, script=None):
    """The PowerShell that creates the task, replacing an older one of the same name.

    The same recipe as ReachPilot's updater, which runs on this PC: every 30 minutes and at sign-in, caught up
    after sleep, never two at once, ended after 20 minutes. A task made with plain `schtasks /Create` would
    run only on mains power, and the lab's PC is a laptop.
    """
    script = str(script or Path(__file__).resolve())

    def quoted(value):
        return "'" + str(value).replace("'", "''") + "'"  # PowerShell: a quote inside single quotes is doubled

    return f"""
$action = New-ScheduledTaskAction -Execute {quoted(runner)} -Argument {quoted(f'"{script}"')} -WorkingDirectory {quoted(ntpath.dirname(script))}
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes {TASK_MINUTES})
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes {TASK_LIMIT_MINUTES}) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName {quoted(TASK_NAME)} -Action $action -Trigger @($repeat, $logon) -Settings $settings `
    -Description 'Keeps the Millennium lab automation on the newest version from GitHub (fast-forward only, never touches local edits).' -Force | Out-Null
Get-ScheduledTask -TaskName {quoted(TASK_NAME)} | Select-Object TaskName, State
"""


def install():
    if os.name != "nt":
        print("The scheduled task is made with Windows Task Scheduler. Nothing was installed on this computer.")
        return 1
    hidden = Path(sys.executable).with_name("pythonw.exe")  # no console window every 30 minutes
    runner = hidden if hidden.exists() else Path(sys.executable)
    created = subprocess.run(["powershell", "-NoProfile", "-Command", task_script(runner)],
                             capture_output=True, text=True)
    print((created.stdout or created.stderr).strip() or f"powershell exit {created.returncode}")
    return created.returncode


def main(argv):
    if argv[:1] == ["install"]:
        return install()
    if argv[:1] == ["status"]:
        print("\n".join(status()))
        return 0
    state, sentence = check()
    remember(state, sentence)
    print(sentence)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
