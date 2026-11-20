"""Checks the daily token target: counting, the catch-up decision, and that a hand-picked mode wins.

    python tests/test_token_pace.py

Uses a temporary database and cache; OpenCode's real database is not opened.
Exit code 0 = all checks passed, 1 = at least one failed.
"""
import datetime as dt
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import lab_pool  # noqa: E402
import pool_mode  # noqa: E402
import token_pace  # noqa: E402

FAILURES = []
POOL = {"workers": 8, "daily_token_target": 1_000_000_000,
        "work_hours": {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "17:00", "workers": 3, "catchup_workers": 5}}
MONDAY_10 = dt.datetime(2026, 11, 16, 10, 0)
MONDAY_20 = dt.datetime(2026, 11, 16, 20, 0)


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def database(folder, sessions):
    """sessions = [(datetime, tokens)]; every token column gets a fifth so the sum is checked too."""
    path = Path(folder) / "opencode.db"
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("create table session_v2 (time_created integer, tokens_input integer, tokens_output integer, "
                       "tokens_reasoning integer, tokens_cache_read integer, tokens_cache_write integer)")
    for moment, tokens in sessions:
        connection.execute("insert into session_v2 values (?, ?, ?, ?, ?, ?)",
                           (int(moment.timestamp() * 1000), *([tokens // 5] * 5)))
    connection.commit()
    connection.close()
    return path


def started_engines(folder, opencode_first, room=True):
    """Engines lab_pool.fill picks for four waiting jobs (nothing is really started)."""
    import json

    names = ("QUEUE", "STATE", "LOG_DIR", "settings", "model_reachable", "copilot_usable", "start",
             "drive_has_room", "pool_job")
    real = {name: getattr(lab_pool, name) for name in names}
    lab_pool.QUEUE, lab_pool.STATE = Path(folder) / "queue.json", Path(folder) / "pool_state.json"
    lab_pool.LOG_DIR = Path(folder)
    jobs = [{"id": f"lab_test_{number}", "status": "needs_ai", "queued": "2026-11-16T09:00:00"} for number in range(4)]
    lab_pool.QUEUE.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    lab_pool.STATE.write_text("{}", encoding="utf-8")
    engines = []

    def start(job, engine, state):
        engines.append(engine)
        state[job["id"]] = {"pid": None, "engine": engine, "started": "2026-11-16T10:00:00", "attempts": []}

    lab_pool.settings = lambda: {"workers": 4, "engines": ["opencode", "copilot"], "timeout_minutes": 100,
                                 "max_attempts": 2, "max_wait_hours": 3, "opencode_first": opencode_first}
    lab_pool.model_reachable = lambda: True
    lab_pool.copilot_usable = lambda: True
    lab_pool.drive_has_room = lambda: room
    lab_pool.pool_job = lambda job: True
    lab_pool.start = start
    try:
        lab_pool.fill()
    finally:
        for name, value in real.items():
            setattr(lab_pool, name, value)
    return engines


def main():
    with tempfile.TemporaryDirectory() as folder:
        check("on pace: jobs alternate between the engines",
              started_engines(folder, False) == ["opencode", "copilot", "opencode", "copilot"])
        check("behind the target: every new job starts on OpenCode", started_engines(folder, True) == ["opencode"] * 4)
        check("drive nearly full: no job is started", started_engines(folder, False, room=False) == [])
        token_pace.CACHE = Path(folder) / "token_pace.json"
        pool_mode.MODE_FILE = Path(folder) / "pool_mode.json"  # no hand-picked mode

        token_pace.DATABASE = database(folder, [(MONDAY_10 - dt.timedelta(hours=3), 50_000_000),
                                                (MONDAY_10 - dt.timedelta(days=1), 900_000_000)])
        slow = token_pace.status(POOL, MONDAY_10, fresh=True)
        check("only today's sessions are counted", slow["tokens"] == 50_000_000, slow["tokens"])
        check("yesterday is listed separately", slow["days"].get("2026-11-15") == 900_000_000, slow["days"])
        # schedule alone: 7 h x 3 slots + 7 h x 8 slots at 10 M = 0.77 B more
        check("expected = counted + what the schedule still adds", slow["expected"] == 50_000_000 + 770_000_000,
              slow["expected"])
        check("a slow day is behind", slow["behind"])
        check("behind in weekday Light: catch-up on", pool_mode.catching_up(POOL, MONDAY_10))
        check("catch-up runs 5 jobs instead of 3", pool_mode.scheduled_workers(POOL, MONDAY_10) == 5)
        check("evening already runs all 8", pool_mode.scheduled_workers(POOL, MONDAY_20) == 8)

        pool_mode.MODE_FILE.write_text('{"mode": "light", "until": null}', encoding="utf-8")
        check("Light chosen by hand is not overruled", pool_mode.scheduled_workers(POOL, MONDAY_10) == 3)
        pool_mode.MODE_FILE.unlink()

        token_pace.DATABASE = database(folder, [(MONDAY_10 - dt.timedelta(hours=3), 700_000_000)])
        normal = token_pace.status(POOL, MONDAY_10, fresh=True)
        check("a normal day is on pace", not normal["behind"], normal)
        check("on pace: the plain schedule (3 jobs)", pool_mode.scheduled_workers(POOL, MONDAY_10) == 3)

        off = dict(POOL, daily_token_target=0)
        token_pace.status(off, MONDAY_10, fresh=True)
        check("target 0 switches it off", not pool_mode.catching_up(off, MONDAY_10))

        token_pace.DATABASE = Path(folder) / "missing" / "opencode.db"
        gone = token_pace.status(POOL, MONDAY_10, fresh=True)
        check("unreadable database: not behind, pool unchanged", gone["tokens"] is None and not gone["behind"]
              and pool_mode.scheduled_workers(POOL, MONDAY_10) == 3)
    check("a worker's real finish time is kept", lab_pool.finished_stamp("2026-11-19T21:04:05") == "2026-11-19T21:04:05")
    stamped = lab_pool.finished_stamp(True)
    check("a finish time that is not a time becomes now", stamped[:10] == dt.date.today().isoformat(), stamped)
    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
