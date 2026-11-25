"""Talos as a pool engine: engine choice, command line, outage detection, token counting."""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab_pool  # noqa: E402
import token_pace  # noqa: E402


def test_weighted_engine_cycle():
    picks = [lab_pool.pick_engine(["opencode", "talos"], {"opencode": 2, "talos": 1}, slot) for slot in range(6)]
    assert picks == ["opencode", "opencode", "talos"] * 2
    assert lab_pool.pick_engine(["opencode", "talos"], {}, 1) == "talos"  # no weights: plain turns
    assert lab_pool.pick_engine(["opencode"], {"opencode": 0}, 3) == "opencode"  # all weights 0: still an engine


def test_talos_command_uses_console_python_and_json(monkeypatch):
    monkeypatch.setattr(lab_pool.sys, "executable", r"C:\Python313\pythonw.exe")
    line = lab_pool.talos_run("do the job")
    assert line[0].lower().endswith("python.exe") and line[1:4] == ["-m", "talos", "run"]
    assert line[4] == "do the job" and "--yes" in line and "--json" in line


def test_talos_model_error_is_an_outage_not_a_lost_attempt(tmp_path):
    log = tmp_path / "pool_x_talos.log"
    log.write_text(json.dumps({"text": "Connection refused", "stopped": "model error", "usage": {}}), encoding="utf-8")
    assert lab_pool._no_connection(log)
    log.write_text(json.dumps({"text": "done", "stopped": "", "usage": {"in": 5}}), encoding="utf-8")
    assert not lab_pool._no_connection(log)


def test_talos_tokens_count_lab_sessions_only(tmp_path):
    database = tmp_path / "talos.db"
    connection = sqlite3.connect(database)
    connection.execute("create table sessions (id text, created real, cwd text, tokens_in int, tokens_out int)")
    today = dt.datetime(2026, 11, 25, 10, 0)
    rows = [("a", today.timestamp(), r"E:\Projects\MillenniumResearch", 1000, 10),
            ("b", today.timestamp(), r"E:\Projects\MillenniumResearch\prize_work", 500, 5),
            ("c", today.timestamp(), r"C:\elsewhere", 9999, 9),
            ("d", (today - dt.timedelta(days=40)).timestamp(), r"E:\Projects\MillenniumResearch", 7, 7)]
    connection.executemany("insert into sessions values (?, ?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()
    days = token_pace.talos_tokens_by_day(now=today, database=database, root=r"E:\Projects\MillenniumResearch")
    assert days == {"2026-11-25": 1515}
    assert token_pace.talos_tokens_by_day(now=today, database=tmp_path / "missing.db") == {}


def test_turns_continue_across_ticks():
    """One start per tick (work hours) must still reach Talos: the turn index is the pool state size + slot."""
    state = {}
    engines = []
    for tick in range(6):
        engines.append(lab_pool.pick_engine(["opencode", "talos"], {"opencode": 2, "talos": 1}, len(state) + 0))
        state[f"job{tick}"] = {"attempts": []}
    assert engines.count("talos") == 2


def _fill(tmp_path, monkeypatch, jobs, alive_ids, workers=3):
    """Runs lab_pool.fill() on a fake queue; returns [(job id, engine)] it started."""
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
    state = {job_id: {"pid": 1000 + index, "attempts": []} for index, job_id in enumerate(alive_ids)}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    started = []

    def start(job, engine, state):
        state[job["id"]] = {"pid": 1, "engine": engine, "started": "2026-11-25T09:00:00", "attempts": []}
        started.append((job["id"], engine))

    monkeypatch.setattr(lab_pool, "QUEUE", queue)
    monkeypatch.setattr(lab_pool, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(lab_pool, "settings", lambda: {
        "workers": workers, "engines": ["opencode", "talos"], "engine_weights": {}, "opencode_first": False,
        "max_attempts": 2, "max_wait_hours": 1, "dept_engines": {"flagship": "talos"}, "reserved": {"flagship": 2},
        "talos_dir": "x"})
    monkeypatch.setattr(lab_pool, "alive", lambda pid: pid >= 1000)
    monkeypatch.setattr(lab_pool, "model_reachable", lambda: True)
    monkeypatch.setattr(lab_pool, "drive_has_room", lambda: True)
    monkeypatch.setattr(lab_pool, "talos_usable", lambda: True)
    monkeypatch.setattr(lab_pool, "start", start)
    monkeypatch.setattr(lab_pool, "save_queue_merged", lambda queue, original: None)
    lab_pool.fill()
    return started


def _job(job_id, dept):
    return {"id": job_id, "dept": dept, "status": "needs_ai", "queued": "2026-11-25T08:00:00"}


def test_flagship_starts_on_talos_in_its_reserved_slots_when_the_cap_is_full(tmp_path, monkeypatch):
    busy = [f"lab_squares_261125_{i}" for i in range(7)]
    jobs = [_job(job_id, "squares") for job_id in busy]
    jobs += [_job("lab_flagship_261125_1", "flagship"), _job("lab_flagship_261125_2", "flagship"),
             _job("lab_flagship_261125_3", "flagship"), _job("lab_erdos_261125_9", "erdos")]
    for job in jobs[:7]:
        job["status"] = "running"
    started = _fill(tmp_path, monkeypatch, jobs, busy)
    assert started == [("lab_flagship_261125_1", "talos"), ("lab_flagship_261125_2", "talos")]


def test_running_flagship_workers_do_not_fill_the_shared_slots(tmp_path, monkeypatch):
    flagship = [{**_job(f"lab_flagship_261125_{i}", "flagship"), "status": "running"} for i in (1, 2)]
    squares = [{**_job(f"lab_squares_261125_{i}", "squares"), "status": "running"} for i in range(2)]
    waiting = [_job("lab_erdos_261125_8", "erdos"), _job("lab_erdos_261125_9", "erdos")]
    alive = [job["id"] for job in flagship + squares]
    started = _fill(tmp_path, monkeypatch, flagship + squares + waiting, alive)
    assert [job_id for job_id, _ in started] == ["lab_erdos_261125_8"]  # 2 of 3 shared slots were in use
    # a third Flagship job has no reserved slot left: it waits for a shared one like every other desk
    third = _job("lab_flagship_261125_3", "flagship")
    full = squares + [{**_job("lab_squares_261125_5", "squares"), "status": "running"}]
    started = _fill(tmp_path, monkeypatch, flagship + full + [third], alive + ["lab_squares_261125_5"])
    assert started == []
    started = _fill(tmp_path, monkeypatch, flagship + squares + [third], alive)
    assert started == [("lab_flagship_261125_3", "talos")]


def test_other_desks_still_respect_the_cap(tmp_path, monkeypatch):
    busy = [{**_job(f"lab_squares_261125_{i}", "squares"), "status": "running"} for i in range(3)]
    started = _fill(tmp_path, monkeypatch, busy + [_job("lab_erdos_261125_9", "erdos")], [job["id"] for job in busy])
    assert started == []  # 3 running = the cap
    started = _fill(tmp_path, monkeypatch, busy[:1] + [_job("lab_erdos_261125_9", "erdos")], [busy[0]["id"]])
    assert [job_id for job_id, _ in started] == ["lab_erdos_261125_9"]
