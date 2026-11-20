"""Keeps the lab from filling its drive: removes what finished jobs leave behind.

    python disk_guard.py             one pass (the scheduled task "Millennium disk guard" runs this every 15 min)
    python disk_guard.py --force     a full pass now, even if the last one was a few minutes ago
    python disk_guard.py --dry-run   print what a full pass would remove; changes nothing
    python disk_guard.py --status    free space and the limits
    (--root <folder> points it at another lab folder; the tests use that)

What a full pass removes (lab = the folder above this one). A file that git tracks is never removed, a junction
is never followed (only the link is removed), and a file that is in use is left for the next pass.

 1. Finished scratch. Scratch = a folder named .scratch_*, _scratch_*, scratch_*, *_scratch, review_scratch*,
    review_panel_scratch*, and every entry inside a folder named .scratch or scratch. It is finished when
    nothing in it was written or created for `grace_hours` (lab level) or `dossier_grace_hours` (inside
    prize_work, where a dossier keeps working files longer) and no running job is named in it. Untracked small
    text files that are not copies of a tracked file go into logs/scratch_archive/<day>_<name>.zip first
    (kept `archive_days`, at most `archive_mb` per folder, newest first).
 2. Copies inside review_panel/<job>/: an untracked file that is byte-for-byte the same as a lab file outside
    review_panel and outside scratch. Each removal is written to logs/disk_guard_removed.log.
 3. Worker logs (logs/pool_*.log) older than `log_days`, except the logs of useful jobs (lab_useful.py).
 4. Only when free space is under `low_free_gb`: the shorter `pressure_*` waiting times apply, and build
    caches that can be downloaded again (.lake folders) and were not touched for `cache_cold_days` go too.

Under `stop_free_gb` the pool starts no new jobs (lab_pool.fill asks pool_may_start) and one e-mail is sent.
Limits live in lab_departments.json under "disk_guard"; the defaults are below.
"""
import ctypes
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEPARTMENTS = HERE / "lab_departments.json"

DEFAULTS = {
    "grace_hours": 6,              # lab-level scratch: untouched this long = finished (a job may run 100 min)
    "dossier_grace_hours": 48,     # scratch inside prize_work/<dossier>
    "pressure_grace_hours": 2.5,   # both waiting times when free space is under low_free_gb ...
    "pressure_dossier_grace_hours": 6,
    "low_free_gb": 15,
    "stop_free_gb": 5,
    "log_days": 14,
    "archive_days": 30,
    "archive_mb": 20,
    "cache_cold_days": 3,
    "full_pass_minutes": 60,       # a full pass at most this often while there is enough room
}
SCRATCH_PREFIXES = (".scratch_", "_scratch_", "scratch_", "review_scratch", "review_panel_scratch")
SCRATCH_PARENTS = (".scratch", "scratch")
NEVER_ENTER = {".git", ".lake", "node_modules", "automation", "pylibs", "inbox", "outbox"}
CACHE_NAMES = {".lake"}
TEXT = {".md", ".py", ".json", ".jsonl", ".txt", ".tex", ".lean", ".csv", ".log", ".out", ".cert", ".toml", ".yml",
        ".yaml", ".cfg", ".ini", ".sh", ".ps1"}
SMALL = 256 * 1024       # a "small text file" is at most this big
DEDUPE_FROM = 64 * 1024  # review_panel copies smaller than this are not worth hashing
MAX_DEPTH = 6


# ------------------------------------------------------------------ helpers

def config():
    try:
        saved = json.loads(DEPARTMENTS.read_text(encoding="utf-8")).get("disk_guard", {})
    except (OSError, ValueError):
        saved = {}
    return {key: float(saved.get(key, default)) for key, default in DEFAULTS.items()}


def free_gb(root=ROOT):
    return shutil.disk_usage(root).free / 1e9


def pool_may_start(root=ROOT):
    """False when the drive is so full that a new job would only fail with "No space left"."""
    try:
        return free_gb(root) >= config()["stop_free_gb"]
    except OSError:
        return True  # cannot tell: do not stop the lab for it


def is_link(entry):
    return entry.is_junction() or entry.is_symlink()


def is_link_path(path):
    return os.path.islink(path) or os.path.isjunction(path)


def key_of(path):
    return os.path.normcase(os.path.normpath(str(path)))


def tracked_files(root):
    """Every path git tracks in the lab, or None when git cannot say (then nothing may be removed)."""
    try:
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    return {key_of(Path(root) / name.decode("utf-8", "replace")) for name in listed.stdout.split(b"\0") if name}


def digest(path):
    sha = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def walk(top):
    """(files, links, folders, newest) below top without following links. files = [(path, size, mtime)].
    newest = the latest write OR creation time of anything inside, top itself included: a byte copy keeps the
    old write times of its source, but its creation times are new."""
    files, links, folders = [], [], []
    try:
        own = os.stat(top)
        newest = max(own.st_mtime, own.st_ctime)
    except OSError:
        newest = 0.0
    stack = [str(top)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if is_link(entry):
                    links.append(entry.path)
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            newest = max(newest, info.st_mtime, info.st_ctime)
            if entry.is_dir(follow_symlinks=False):
                folders.append(entry.path)
                stack.append(entry.path)
            else:
                files.append((entry.path, info.st_size, info.st_mtime))
    return files, links, folders, newest


def remove_file(path):
    """True when the file is gone. A read-only file (git pack) is made writable first; a file in use stays."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return True
        except OSError:
            return False
    except OSError:
        return False


def remove_link(path):
    """Removes a junction or symlink itself; what it points at is not touched."""
    for remover in (os.rmdir, os.unlink):
        try:
            remover(path)
            return True
        except OSError:
            continue
    return False


def remove_empty(folders, top=None):
    for folder in sorted(folders, key=len, reverse=True):  # deepest first
        try:
            os.rmdir(folder)
        except OSError:
            pass
    if top:
        try:
            os.rmdir(top)
        except OSError:
            pass


def running_job_names():
    """Compact names of the jobs the pool is running now ("lab_panel_261119_16" -> "panel26111916")."""
    try:
        import pool_mode

        ids = [job["id"] for job in pool_mode.running_jobs()]
    except Exception:
        return None  # unknown: the caller then trusts the waiting time alone
    return {compact(job_id[4:] if job_id.startswith("lab_") else job_id) for job_id in ids}


def compact(text):
    return "".join(character for character in text.lower() if character.isalnum())


# ------------------------------------------------------------------ 1. finished scratch

def scratch_units(root):
    """[(path, is_dossier)] for every scratch folder, and every entry inside a .scratch / scratch folder."""
    units = []
    stack = [(str(root), 0, False)]
    while stack:
        current, depth, dossier = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            name = entry.name.lower()
            if is_link(entry) or not entry.is_dir(follow_symlinks=False) or name in NEVER_ENTER:
                continue
            inside_dossier = dossier or (depth == 0 and name == "prize_work")
            if name in SCRATCH_PARENTS:
                try:
                    units += [(child.path, inside_dossier) for child in os.scandir(entry.path)]
                except OSError:
                    pass
            elif name.startswith(SCRATCH_PREFIXES) or name.endswith("_scratch"):
                units.append((entry.path, inside_dossier))
            elif depth < MAX_DEPTH:
                stack.append((entry.path, depth + 1, inside_dossier))
    return units


class Tracked:
    """The tracked paths, plus "is this small file a copy of a tracked file?" (sizes first, hashes on demand)."""

    def __init__(self, paths):
        self.paths = paths
        self._by_size = None
        self._digests = {}

    def __contains__(self, path):
        return key_of(path) in self.paths

    def has_copy_of(self, path, size):
        if self._by_size is None:
            self._by_size = {}
            for tracked in self.paths:
                try:
                    tracked_size = os.path.getsize(tracked)
                except OSError:
                    continue
                if tracked_size <= SMALL:
                    self._by_size.setdefault(tracked_size, []).append(tracked)
        twins = self._by_size.get(size)
        if not twins:
            return False
        if size not in self._digests:
            found = set()
            for twin in twins:
                try:
                    found.add(digest(twin))
                except OSError:
                    pass
            self._digests[size] = found
        try:
            return digest(path) in self._digests[size]
        except OSError:
            return True  # unreadable: nothing to archive


def clean_scratch(unit, tracked, archive_dir, limits, dry_run):
    """Empties one finished scratch entry. Returns {"freed", "removed", "kept", "archived", "busy"}."""
    report = {"freed": 0, "removed": 0, "kept": 0, "archived": 0, "busy": 0}
    if os.path.isdir(unit):
        files, links, folders, _ = walk(unit)
    else:
        info = os.stat(unit)
        files, links, folders = [(unit, info.st_size, info.st_mtime)], [], []
    doomed, keepsakes = [], []
    for path, size, modified in files:
        if path in tracked:
            report["kept"] += 1
        elif size <= SMALL and os.path.splitext(path)[1].lower() in TEXT and not tracked.has_copy_of(path, size):
            keepsakes.append((path, size, modified))
        else:
            doomed.append((path, size))
    budget = limits["archive_mb"] * 1e6
    archive = None
    for path, size, modified in sorted(keepsakes, key=lambda item: item[2], reverse=True):  # newest first
        if budget >= size:
            budget -= size
            report["archived"] += 1
            if not dry_run:
                if archive is None:
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    # the hash keeps two entries with the same name (other folders) in separate zips
                    label = compact(Path(unit).name)[:60] + "_" + hashlib.sha1(str(unit).lower().encode()).hexdigest()[:8]
                    archive = zipfile.ZipFile(archive_dir / f"{dt.date.today():%Y%m%d}_{label}_{os.getpid()}.zip", "a",
                                              zipfile.ZIP_DEFLATED)
                try:
                    archive.write(path, os.path.relpath(path, Path(unit).parent))
                except (OSError, ValueError):
                    report["busy"] += 1
                    continue
        doomed.append((path, size))
    if archive is not None:
        archive.close()
    for path, size in doomed:
        if dry_run or remove_file(path):
            report["freed"] += size
            report["removed"] += 1
        else:
            report["busy"] += 1
    if not dry_run:
        for link in links:
            remove_link(link)
        remove_empty(folders, unit if os.path.isdir(unit) else None)
    return report


def finished(path, grace_hours, now, running):
    """(True/False, newest timestamp). For a link only the link's own times count: it is never followed."""
    name = compact(os.path.basename(path))
    if running and any(job and job in name for job in running):
        return False, now
    if os.path.isdir(path) and not is_link_path(path):
        newest = walk(path)[3]
    else:
        try:
            info = os.lstat(path)
        except OSError:
            return False, now
        newest = max(info.st_mtime, info.st_ctime)
    return (now - newest) >= grace_hours * 3600, newest


# ------------------------------------------------------------------ 2. copies inside review_panel

def keeper_index(root, skip):
    """{size: [paths]} of lab files that a review_panel copy may be a duplicate of."""
    index = {}
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if is_link(entry) or key_of(entry.path) in skip:
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name.lower() not in NEVER_ENTER:
                    stack.append(entry.path)
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            if size >= DEDUPE_FROM:
                index.setdefault(size, []).append(entry.path)
    return index


def dedupe_panel(root, tracked, scratch_paths, grace_hours, now, running, removed_log, dry_run):
    report = {"freed": 0, "removed": 0, "busy": 0}
    panel_root = Path(root) / "review_panel"
    if not panel_root.is_dir():
        return report
    index = None
    digests = {}

    def cached(path):
        if path not in digests:
            try:
                digests[path] = digest(path)
            except OSError:
                digests[path] = None
        return digests[path]

    for job_dir in os.scandir(panel_root):
        if is_link(job_dir) or not job_dir.is_dir(follow_symlinks=False):
            continue
        done, _ = finished(job_dir.path, grace_hours, now, running)
        if not done:
            continue
        for path, size, _ in walk(job_dir.path)[0]:
            if size < DEDUPE_FROM or path in tracked:
                continue
            if index is None:  # built once, and only when a candidate exists
                index = keeper_index(root, {key_of(panel_root)} | scratch_paths)
            twins = index.get(size)
            if not twins:
                continue
            own = cached(path)
            twin = next((other for other in twins if own and cached(other) == own and os.path.exists(other)), None)
            if not twin:
                continue
            if dry_run or remove_file(path):
                report["freed"] += size
                report["removed"] += 1
                if not dry_run:
                    removed_log.append(f"copy\t{path}\t{size}\tsame as\t{twin}")
            else:
                report["busy"] += 1
    return report


# ------------------------------------------------------------------ 3. old worker logs, old archives

def old_logs(log_dir, limits, now, dry_run):
    report = {"freed": 0, "removed": 0}
    try:
        import lab_useful

        useful = lab_useful.useful_ids()
    except Exception:
        return report  # cannot tell which logs belong to useful jobs: keep them all
    for pattern, days, protect in (("pool_*.log", limits["log_days"], True),
                                   ("scratch_archive/*.zip", limits["archive_days"], False)):
        for path in Path(log_dir).glob(pattern):
            try:
                info = path.stat()
            except OSError:
                continue
            if now - info.st_mtime < days * 86400:
                continue
            if protect and any(path.name.startswith(f"pool_{job_id}_") for job_id in useful):
                continue
            if dry_run or remove_file(path):
                report["freed"] += info.st_size
                report["removed"] += 1
    return report


# ------------------------------------------------------------------ 4. cold build caches (low space only)

def cold_caches(root, tracked, limits, now, dry_run):
    report = {"freed": 0, "removed": 0, "busy": 0, "folders": []}
    stack = [(str(root), 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            name = entry.name.lower()
            if is_link(entry) or not entry.is_dir(follow_symlinks=False):
                continue
            if name in CACHE_NAMES:
                files, links, folders, newest = walk(entry.path)
                if now - newest < limits["cache_cold_days"] * 86400:
                    continue
                report["folders"].append(entry.path)
                for path, size, _ in files:
                    if path in tracked:
                        continue
                    if dry_run or remove_file(path):
                        report["freed"] += size
                        report["removed"] += 1
                    else:
                        report["busy"] += 1
                if not dry_run:
                    for link in links:
                        remove_link(link)
                    remove_empty(folders, entry.path)
            elif name not in NEVER_ENTER and depth < MAX_DEPTH:
                stack.append((entry.path, depth + 1))
    return report


# ------------------------------------------------------------------ one pass

def full_pass(root=ROOT, log_dir=None, limits=None, dry_run=False, now=None, say=print):
    """Returns the summary dict; `say` gets one line per step."""
    root = Path(root)
    log_dir = Path(log_dir) if log_dir else HERE / "logs"
    limits = limits or config()
    now = now or time.time()
    before = free_gb(root)
    pressure = before < limits["low_free_gb"]
    summary = {"free_before_gb": round(before, 2), "pressure": pressure, "freed": 0, "steps": {}}
    tracked_paths = tracked_files(root)
    if tracked_paths is None:
        say("git could not list the tracked files (busy or not a repository): nothing removed this pass")
        summary["skipped"] = "git"
        return summary
    tracked = Tracked(tracked_paths)
    running = running_job_names()
    lab_grace = limits["pressure_grace_hours" if pressure else "grace_hours"]
    dossier_grace = limits["pressure_dossier_grace_hours" if pressure else "dossier_grace_hours"]

    units = scratch_units(root)
    scratch = {"freed": 0, "removed": 0, "kept": 0, "archived": 0, "busy": 0, "folders": 0, "waiting": 0}
    for unit, dossier in units:
        done, _ = finished(unit, dossier_grace if dossier else lab_grace, now, running)
        if not done:
            scratch["waiting"] += 1
            continue
        if is_link_path(unit):
            if not dry_run:
                remove_link(unit)
            scratch["folders"] += 1
            continue
        if not os.path.isdir(unit) and unit in tracked:
            continue
        result = clean_scratch(unit, tracked, log_dir / "scratch_archive", limits, dry_run)
        scratch["folders"] += 1
        for name in ("freed", "removed", "kept", "archived", "busy"):
            scratch[name] += result[name]
    summary["steps"]["scratch"] = scratch
    say(f"scratch: {scratch['folders']} finished entries, {scratch['removed']} files, {scratch['freed'] / 1e9:.2f} GB; "
        f"{scratch['archived']} small files archived, {scratch['kept']} tracked kept, {scratch['busy']} in use, "
        f"{scratch['waiting']} entries still inside the waiting time")

    removed_log = []
    panel = dedupe_panel(root, tracked, {key_of(unit) for unit, _ in units}, lab_grace, now, running, removed_log,
                         dry_run)
    summary["steps"]["panel_copies"] = panel
    say(f"review_panel copies: {panel['removed']} files, {panel['freed'] / 1e9:.2f} GB")
    if removed_log:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "disk_guard_removed.log", "a", encoding="utf-8") as handle:
            stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M}"
            handle.writelines(f"{stamp}\t{line}\n" for line in removed_log)

    logs = old_logs(log_dir, limits, now, dry_run)
    summary["steps"]["logs"] = logs
    say(f"old worker logs and archives: {logs['removed']} files, {logs['freed'] / 1e9:.2f} GB")

    freed = scratch["freed"] + panel["freed"] + logs["freed"]
    if pressure and before + freed / 1e9 < limits["low_free_gb"]:
        caches = cold_caches(root, tracked, limits, now, dry_run)
        summary["steps"]["caches"] = caches
        freed += caches["freed"]
        say(f"cold build caches: {len(caches['folders'])} folders, {caches['freed'] / 1e9:.2f} GB")
    summary["freed"] = freed
    summary["free_after_gb"] = round(before + freed / 1e9 if dry_run else free_gb(root), 2)
    return summary


def _alive(pid):
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259


def main(arguments):
    root = Path(arguments[arguments.index("--root") + 1]) if "--root" in arguments else ROOT
    log_dir = HERE / "logs"
    limits = config()
    free = free_gb(root)
    if "--status" in arguments:
        print(f"free {free:.1f} GB; shorter waiting times under {limits['low_free_gb']:.0f} GB; "
              f"no new jobs under {limits['stop_free_gb']:.0f} GB")
        return
    dry_run = "--dry-run" in arguments
    if dry_run:
        summary = full_pass(root, log_dir, limits, dry_run=True)
        print(f"would free {summary['freed'] / 1e9:.2f} GB (free now {free:.1f} GB)")
        return
    log_dir.mkdir(exist_ok=True)
    state_file = log_dir / "disk_guard_state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    due = time.time() - state.get("last_pass", 0) >= limits["full_pass_minutes"] * 60
    if not ("--force" in arguments or due or free < limits["low_free_gb"]):
        return
    lock = log_dir / "disk_guard.lock"
    try:
        if _alive(int(lock.read_text().strip() or 0)):
            return  # the previous pass is still working
    except (OSError, ValueError):
        pass
    lock.write_text(str(os.getpid()))
    lines = []
    try:
        summary = full_pass(root, log_dir, limits, say=lines.append)
        state.update({"last_pass": time.time(), "free_gb": summary.get("free_after_gb"),
                      "freed_gb": round(summary["freed"] / 1e9, 2), "pressure": summary["pressure"]})
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        after = summary.get("free_after_gb", free)
        with open(log_dir / "disk_guard.log", "a", encoding="utf-8") as handle:
            handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  free {summary['free_before_gb']} -> {after} GB"
                         f"{' (low space)' if summary['pressure'] else ''}; " + "; ".join(lines) + "\n")
        if after < limits["stop_free_gb"]:
            try:
                import notify_juwen

                notify_juwen.notify(
                    "disk_low", "Drive almost full: the lab is paused",
                    f"Only {after:.1f} GB is free on the lab's drive after the automatic clean-up, so the lab "
                    f"starts no new jobs until at least {limits['stop_free_gb']:.0f} GB is free.\n\n"
                    "The clean-up already removed finished scratch folders, duplicate review copies, old logs and "
                    "cold build caches. What is left is real data: please free space on the drive by hand.")
            except Exception as error:
                lines.append(f"notice not sent: {error!r}")
        print("\n".join(lines))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main(sys.argv[1:])
