"""Daily OpenCode token target for the lab pool (pool.daily_token_target in lab_departments.json).

    python token_pace.py     today's count, what the day is expected to reach, and the last 14 days

The pool normally reaches the target on its own. On a day that is falling behind (the model was unreachable
for hours, the drive was full) the pool catches up while it is in its weekday Light schedule:
    - it runs pool.work_hours.catchup_workers jobs instead of pool.work_hours.workers, and
    - every new job starts on OpenCode (a retry still switches engine).
A Light or Full mode chosen by hand on the dashboard is never overruled.

Counted = input + output + reasoning + cache read + cache write tokens of every OpenCode session created
today (local time), read from OpenCode's own database, which is opened read-only.
"""
import datetime as dt
import json
import sqlite3
import time
from pathlib import Path

import pool_mode

HERE = Path(__file__).resolve().parent
DATABASE = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
CACHE = HERE / "logs" / "token_pace.json"
TALOS_DATABASE = Path.home() / ".talos" / "talos.db"  # Talos lab workers (lab_pool engine "talos")
LAB_ROOT = HERE.parent
CACHE_SECONDS = 300
SLOT_HOUR_TOKENS = 10_000_000  # per job slot and hour, both engines mixed (measured 12 M; kept low on purpose)
MARGIN = 1.1                   # catch up unless the day is expected to pass the target by 10 %
TOTAL = "tokens_input + tokens_output + tokens_reasoning + tokens_cache_read + tokens_cache_write"


def tokens_by_day(days=14, now=None, database=None):
    """{"YYYY-MM-DD": tokens} for the last `days` local days, or None when the database cannot be read."""
    now = now or dt.datetime.now()
    first = dt.datetime.combine(now.date() - dt.timedelta(days=days - 1), dt.time())
    try:
        connection = sqlite3.connect(f"file:{Path(database or DATABASE).as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            rows = connection.execute(
                f"select date(time_created / 1000, 'unixepoch', 'localtime'), sum({TOTAL}) from session_v2 "
                "where time_created >= ? group by 1", (first.timestamp() * 1000,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return {day: int(total or 0) for day, total in rows if day}


def talos_tokens_by_day(days=14, now=None, database=None, root=None):
    """{"YYYY-MM-DD": tokens} of Talos sessions run in the lab folder (input + output), or {} when unreadable.
    Like OpenCode's, these calls go through the AICC gateway with Juwen's key, so AICC records them too."""
    now = now or dt.datetime.now()
    first = dt.datetime.combine(now.date() - dt.timedelta(days=days - 1), dt.time())
    folder = str(root or LAB_ROOT).rstrip("\\/")
    try:
        connection = sqlite3.connect(f"file:{Path(database or TALOS_DATABASE).as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            rows = connection.execute(
                "select date(created, 'unixepoch', 'localtime'), sum(tokens_in + tokens_out) from sessions "
                "where created >= ? and (cwd = ? or cwd like ?) group by 1",
                (first.timestamp(), folder, folder + "%")).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return {day: int(total or 0) for day, total in rows if day}


def expected_rest(pool, now):
    """Tokens the plain schedule (no catch-up) should still add before midnight."""
    midnight = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time())
    rate = float(pool.get("tokens_per_slot_hour", SLOT_HOUR_TOKENS))
    override = pool_mode.load_override(now)
    total, moment, step = 0.0, now, dt.timedelta(minutes=10)
    while moment < midnight:
        chunk = min(step, midnight - moment)
        total += pool_mode.workers_at(pool, moment, override)[0] * rate * chunk.total_seconds() / 3600
        moment += chunk
    return total


def status(pool=None, now=None, fresh=False):
    """{"target", "tokens", "expected", "behind", "days"}; target 0 = no target set. Cached for five minutes."""
    pool = pool_mode.pool_config() if pool is None else pool
    now = now or dt.datetime.now()
    target = int(pool.get("daily_token_target") or 0)
    if not fresh:
        try:
            saved = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - saved["checked"] < CACHE_SECONDS and saved["target"] == target:
                return saved
        except (OSError, ValueError, KeyError):
            pass
    days = tokens_by_day(now=now)
    talos = talos_tokens_by_day(now=now)
    if days is not None:  # the lab's daily count = OpenCode + Talos workers
        days = {day: days.get(day, 0) + talos.get(day, 0) for day in sorted(set(days) | set(talos))}
    result = {"target": target, "tokens": None, "expected": None, "behind": False, "days": days or {},
              "talos_days": talos, "checked": time.time()}
    if days is not None:
        result["tokens"] = days.get(now.date().isoformat(), 0)
        result["expected"] = int(result["tokens"] + expected_rest(pool, now))
        result["behind"] = bool(target) and result["expected"] < target * MARGIN
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(result, indent=1), encoding="utf-8")
    except OSError:
        pass
    return result


def behind(pool=None, now=None):
    """True when the day needs the catch-up. Never raises: an unreadable database means "not behind"."""
    try:
        return bool(status(pool, now)["behind"])
    except Exception:
        return False


if __name__ == "__main__":
    report = status(fresh=True)
    if report["tokens"] is None:
        print("OpenCode's database could not be read")
    else:
        print(f"today {report['tokens'] / 1e9:.2f} B tokens, expected by midnight {report['expected'] / 1e9:.2f} B, "
              f"target {report['target'] / 1e9:.2f} B -> {'catching up' if report['behind'] else 'on pace'}")
        for day, total in sorted(report["days"].items()):
            print(f"  {day}  {total / 1e9:6.2f} B")
