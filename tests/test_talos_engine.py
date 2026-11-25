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
