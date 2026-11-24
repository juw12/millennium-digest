"""Copilot that cannot reach its own API (office proxy 502) is an outage, not a lost attempt."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab_pool  # noqa: E402

PROXY_502 = """Error: Failed to load models

Error: error sending request for url (https://api.individual.githubcopilot.com/meta): client error (Connect): \
tunnel error: unsuccessful: proxy CONNECT failed with status 502 Bad Gateway

Copilot could not connect to the Copilot API to retrieve the list of available models.
"""


def test_proxy_502_log_is_copilot_offline(tmp_path):
    log = tmp_path / "pool_x_copilot.log"
    log.write_text(PROXY_502, encoding="utf-8")
    assert lab_pool._copilot_offline(log)


def test_long_run_quoting_the_message_is_not_offline(tmp_path):
    log = tmp_path / "pool_x_copilot.log"
    log.write_text("real work\n" * 500 + PROXY_502, encoding="utf-8")
    assert not lab_pool._copilot_offline(log)


def test_missing_log_is_not_offline(tmp_path):
    assert not lab_pool._copilot_offline(tmp_path / "none.log")
    assert not lab_pool._copilot_offline(None)


def test_offline_attempts_do_not_use_up_the_job():
    entry = {"attempts": [{"outcome": "copilot offline"}] * 5 + [{"outcome": "exited"}]}
    assert not lab_pool.exhausted(entry, 2)


def test_fresh_offline_flag_pauses_copilot(tmp_path, monkeypatch):
    exe = tmp_path / "copilot.exe"
    exe.write_text("", encoding="utf-8")
    flag = tmp_path / "copilot_offline.txt"
    monkeypatch.setattr(lab_pool, "COPILOT", exe)
    monkeypatch.setattr(lab_pool, "COPILOT_FLAG", tmp_path / "a.txt")
    monkeypatch.setattr(lab_pool, "COPILOT_QUOTA_FLAG", tmp_path / "b.txt")
    monkeypatch.setattr(lab_pool, "COPILOT_OFFLINE_FLAG", flag)
    assert lab_pool.copilot_usable()
    flag.write_text(dt.datetime.now().isoformat(), encoding="utf-8")
    assert not lab_pool.copilot_usable()
