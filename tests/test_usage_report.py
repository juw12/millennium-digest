"""Checks the usage report: tokens per AI app and per model, model switching, the job tables, the gateway record,
and the HTML mail.

    python tests/test_usage_report.py

Uses temporary OpenCode and Talos databases, a made-up pool.log and a fake gateway; nothing real is opened and no
mail is sent. Exit code 0 = all checks passed, 1 = at least one failed.
"""
import datetime as dt
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import usage_report  # noqa: E402

FAILURES = []
DAY = "2026-11-24"
LAB = "E:/Lab"
NOON = dt.datetime(2026, 11, 24, 12, 0)


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def message(model, tokens_in, tokens_out, cache_read=0):
    return json.dumps({"model": {"id": model, "providerID": "pegaai"},
                       "tokens": {"input": tokens_in, "output": tokens_out, "reasoning": 0,
                                  "cache": {"read": cache_read, "write": 0}}})


def opencode_database(folder):
    """Three sessions on the day: a lab worker that switches model, a lab worker that stays, one of Juwen's own
    in another project; and one lab worker from the day before."""
    path = Path(folder) / "opencode.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "create table session_v2 (id text, title text, directory text, time_created integer, time_updated integer, "
        "tokens_input integer, tokens_output integer, tokens_reasoning integer, tokens_cache_read integer, "
        "tokens_cache_write integer)")
    connection.execute("create table session_message (session_id text, type text, seq integer, data text)")
    stamp = NOON.timestamp() * 1000
    sessions = [
        ("s_switch", "Lab: lab_a_1", "E:\\Lab", stamp, 1000, 100, 0, 500, 0),
        ("s_stay", "Lab: lab_a_2", "E:\\Lab", stamp, 300, 50, 0, 0, 0),
        ("s_other", "Fix the app", "E:\\Projects\\App", stamp, 70, 30, 0, 0, 0),
        ("s_before", "Lab: lab_a_3", "E:\\Lab", stamp - 86_400_000, 400, 0, 0, 0, 0),
    ]
    for row in sessions:
        connection.execute("insert into session_v2 values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (row[0], row[1], row[2], row[3], row[3] + 60_000, *row[4:]))
    messages = [
        ("s_switch", "user", 1, "{}"),
        ("s_switch", "assistant", 2, message("Pega-Coding", 400, 40, 500)),
        ("s_switch", "assistant", 3, message("Pega-Chat", 600, 60)),
        ("s_stay", "assistant", 1, message("Pega-Coding", 300, 50)),
        ("s_other", "assistant", 1, message("Pega-Chat", 60, 30)),  # the session row says 100: 10 not per message
        ("s_before", "assistant", 1, message("Pega-Coding", 400, 0)),
    ]
    connection.executemany("insert into session_message values (?, ?, ?, ?)", messages)
    connection.commit()
    connection.close()
    return path


def talos_database(folder):
    path = Path(folder) / "talos.db"
    connection = sqlite3.connect(path)
    connection.execute("create table sessions (id text, model text, cwd text, created real, updated real, "
                       "tokens_in integer, tokens_out integer)")
    start = NOON.timestamp()
    connection.executemany("insert into sessions values (?, ?, ?, ?, ?, ?, ?)", [
        ("t1", "pegaai/Pega-Coding", "E:\\Lab", start + 30, start + 900, 90_000, 2_000),
        ("t2", "pegaai/Pega-Coding", "C:\\Users\\me", start, start + 60, 500, 50),
    ])
    connection.commit()
    connection.close()
    return path


def jobs():
    return [
        {"id": "lab_a_1", "dept": "squares", "status": "done", "referee": "pass", "novelty": "new",
         "finished": "2026-11-24T12:40:00", "pool": {"engine": "opencode", "started": "2026-11-24T12:00:00"}},
        {"id": "lab_a_2", "dept": "erdos", "status": "done", "referee": "fix: gap in step 2",
         "finished": "2026-11-24T12:20:00", "pool": {"engine": "opencode", "started": "2026-11-24T12:00:00"}},
        # finished 23:30 UTC on the 23rd = the 24th in any zone east of +00:30; given in local time through a helper
        {"id": "lab_a_3", "dept": "erdos", "status": "needs_review", "referee": "pass",
         "finished": utc_stamp(dt.datetime(2026, 11, 24, 1, 0)), "pool": {"engine": "opencode"}},
        {"id": "lab_t_1", "dept": "flagship", "status": "done", "referee": "pass",
         "finished": "2026-11-24T12:15:00", "pool": {"engine": "talos", "started": "2026-11-24T12:00:00"}},
        {"id": "lab_f_1", "dept": "erdos", "status": "failed", "pool": {"engine": "opencode"}},
        {"id": "lab_f_old", "dept": "erdos", "status": "failed", "pool": {"engine": "opencode"}},
        {"id": "lab_next", "dept": "erdos", "status": "done", "referee": "pass",
         "finished": "2026-11-25T03:00:00", "pool": {"engine": "opencode"}},
        {"id": "panel_1", "dept": "panel", "status": "done", "finished": "2026-11-24T13:00:00",
         "panel_verdict": "{'lab_a_1': 'pass', 'lab_a_3': 'fix'}", "pool": {"engine": "opencode"}},
    ]


def utc_stamp(local):
    """A local time written the way some workers write it: in UTC with +00:00."""
    return local.astimezone(dt.timezone.utc).isoformat()


POOL_LOG = """\
2026-11-23 23:00  lab_f_old: marked failed
2026-11-24 12:00  start lab_a_1 on opencode (pid 1)
2026-11-24 12:00  start lab_a_2 on opencode (pid 2)
2026-11-24 12:00  start lab_t_1 on talos (pid 3)
2026-11-24 12:20  lab_a_2: opencode exited
2026-11-24 12:30  lab_a_1: opencode timeout after 30 min
2026-11-24 12:31  start lab_a_1 on opencode (pid 4)
2026-11-24 12:40  lab_a_1: opencode exited
2026-11-24 12:15  lab_t_1: talos exited
2026-11-24 12:50  lab_f_1: opencode no connection
2026-11-24 12:51  lab_f_1: marked failed
2026-11-24 12:52  model unreachable: no OpenCode job started
"""


def fake_gateway(path):
    if path == "/token-usage/daily":
        return {"date": "2026-11-24", "total_tokens": 5000, "breakdown": [
            {"ai_model": "Qwen/Qwen3.8-27B", "prompt_tokens": 1500, "completion_tokens": 100, "total_tokens": 1600},
            {"ai_model": "pegaai/Pega-Coding", "prompt_tokens": 3000, "completion_tokens": 400, "total_tokens": 3400}]}
    return {"data": [{"date": "2026-11-23", "total_tokens": 1}, {"date": "2026-11-24", "total_tokens": 5200}]}


def dead_gateway(path):
    raise OSError("no network")


def main():
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        opencode, talos = opencode_database(folder), talos_database(folder)
        log = folder / "pool.log"
        log.write_text(POOL_LOG, encoding="utf-8")
        aicc = folder / "aicc"

        # ---- times
        check("a UTC stamp is read as the local day", usage_report.local_day(
            utc_stamp(dt.datetime(2026, 11, 24, 1, 0))) == DAY)
        check("a local stamp keeps its day", usage_report.local_day("2026-11-24T23:59:00") == DAY)
        check("no stamp gives no day", usage_report.local_day(None) == "" and usage_report.local_day("soon") == "")
        check("short() rounds to B, M, K", (usage_report.short(1_516_370_374), usage_report.short(2_500_000),
                                           usage_report.short(950), usage_report.short(None))
              == ("1.52 B", "2.50 M", "950", "-"))

        # ---- pool.log
        events = usage_report.pool_events(DAY, log)
        check("failed jobs are dated by the log", events["failed"] == ["lab_f_1"], str(events["failed"]))
        check("time limits are found with their engine", events["timeouts"] == {"lab_a_1": "opencode"})
        check("worker runs are counted per engine",
              events["runs"]["opencode"] == {"started": 3, "exited": 2, "timeout": 1, "no connection": 1}
              and events["runs"]["talos"] == {"started": 1, "exited": 1}, str(events["runs"]))
        check("a missing log gives empty events", usage_report.pool_events(DAY, folder / "none.log")
              == {"timeouts": {}, "failed": [], "runs": {}})

        # ---- the gateway record
        record = usage_report.aicc_snapshot(fake_gateway, aicc, now=dt.datetime(2026, 11, 24, 23, 40))
        check("the snapshot is saved by day", record and (aicc / f"{DAY}.json").exists()
              and json.loads((aicc / f"{DAY}.json").read_text(encoding="utf-8"))["total"] == 5000)
        check("the snapshot keeps the model split", [item["model"] for item in record["models"]]
              == ["Qwen/Qwen3.8-27B", "pegaai/Pega-Coding"])
        check("a snapshot of the gateway's old day is not saved under the new date", usage_report.aicc_snapshot(
            fake_gateway, folder / "late", now=dt.datetime(2026, 11, 25, 0, 5)) is None
            and not (folder / "late").exists())
        check("a dead gateway gives no snapshot", usage_report.aicc_snapshot(dead_gateway, aicc) is None)
        final = usage_report.aicc_record(DAY, fake_gateway, aicc)
        check("the day's final total replaces the snapshot total", final["total"] == 5200 and final["final"]
              and len(final["models"]) == 2)
        offline = usage_report.aicc_record(DAY, dead_gateway, aicc)
        check("without the gateway the snapshot is used", offline["total"] == 5000 and not offline.get("final"))
        check("no snapshot and no gateway gives nothing", usage_report.aicc_record("2026-11-01", dead_gateway, aicc) == {})

        # ---- the day's summary
        summary = usage_report.collect(DAY, jobs(), opencode, talos, LAB, fake_gateway, aicc, log)
        apps = {app["app"]: app for app in summary["apps"]}
        check("apps: lab workers, Juwen's own, Talos lab, Talos other",
              set(apps) == {"OpenCode: lab workers", "OpenCode: Juwen's other projects", "Talos: lab workers",
                            "Talos: other"}, str(sorted(apps)))
        workers = apps.get("OpenCode: lab workers", {})
        check("lab worker tokens are summed over both models",
              workers.get("tokens") == 1950 and workers.get("sessions") == 2 and workers.get("cache_read") == 500,
              str(workers))
        check("a session of the day before is not counted in the day's tokens",
              summary["total"] == 1950 + 100 + 92_000 + 550, str(summary["total"]))
        models = {model["model"]: model for model in summary["models"]}
        check("tokens per model follow the messages, not the session's model",
              models["Pega-Coding"]["opencode"] == 940 + 350 and models["Pega-Chat"]["opencode"] == 660 + 90,
              str(models))
        check("Talos tokens are on its model", models["Pega-Coding"]["talos"] == 92_550)
        check("tokens that are not stored per message are shown, not dropped",
              models["(not stored per message)"]["opencode"] == 10)
        check("the real model name is attached", models["Pega-Coding"]["real"] == "DeepSeek-V4.1-Flash")
        switching = summary["switching"]
        check("one of two lab workers switched", (switching["switched"], switching["sessions"]) == (1, 2))
        check("the fallback model's share of lab tokens", (switching["fallback_tokens"], switching["tokens"]) == (660, 1950))
        check("the gateway record is in the summary", summary["aicc"]["total"] == 5200)

        rows = {row["id"]: row for row in summary["jobs"]}
        check("jobs of the day: finished by local day, failed by the log",
              set(rows) == {"lab_a_1", "lab_a_2", "lab_a_3", "lab_t_1", "lab_f_1", "panel_1"}, str(sorted(rows)))
        check("the job's model is the one that did most of it",
              rows["lab_a_1"]["model"] == "Pega-Coding" and rows["lab_a_1"]["switched"]
              and rows["lab_a_1"]["tokens"] == 1600 and rows["lab_a_1"]["minutes"] == 40.0, str(rows["lab_a_1"]))
        check("a job that ran over midnight still finds its session",
              rows["lab_a_3"]["model"] == "Pega-Coding" and rows["lab_a_3"]["tokens"] == 400, str(rows["lab_a_3"]))
        check("a Talos job is matched to the session that started with it",
              rows["lab_t_1"]["model"] == "Pega-Coding" and rows["lab_t_1"]["tokens"] == 92_000, str(rows["lab_t_1"]))
        check("a failed job is a failed row", rows["lab_f_1"]["failed"] and not rows["lab_a_1"]["failed"])

        # ---- tables
        verdicts = {"lab_a_1": "checked", "lab_a_2": "sorry"}
        by_app = {row["name"]: row for row in usage_report.table(
            summary["jobs"], jobs(), lambda row: row["engine"], verdicts, summary["timeouts"])}
        opencode_row = by_app["opencode"]
        check("per app: finished, failed, time limit",
              (opencode_row["finished"], opencode_row["failed"], opencode_row["timeouts"]) == (4, 1, 1), str(opencode_row))
        check("per app: referee pass / fix", (opencode_row["referee_pass"], opencode_row["referee_fix"]) == (2, 1))
        check("per app: useful needs referee pass and panel pass", opencode_row["useful"] == 1)
        check("per app: Lean-checked and machine-checked",
              opencode_row["lean_checked"] == 1 and opencode_row["machine_checked"] == 1)
        check("per app: tokens per finished job", opencode_row["tokens_per_finished"] == 2350 / 4
              and opencode_row["median_minutes"] == 30.0, str(opencode_row))
        check("Talos has its own row", by_app["talos"]["finished"] == 1 and by_app["talos"]["median_minutes"] == 15.0)

        # ---- saved days and the 7-day table
        reports = folder / "usage_reports"
        usage_report.save(summary, reports)
        earlier = dict(summary, day="2026-11-23", jobs=[dict(rows["lab_a_2"], id="lab_week")], timeouts={})
        usage_report.save(earlier, reports)
        check("the summary is saved as JSON", json.loads((reports / f"{DAY}.json").read_text(encoding="utf-8"))["day"] == DAY)
        week, found, _ = usage_report.week_rows(DAY, reports)
        check("the week reads the saved days", found == 2 and len(week) == len(summary["jobs"]) + 1)
        parts = usage_report.sections(summary, jobs(), reports, bench={})
        check("the 7-day table adds the days before", parts["week_days"] == 2
              and sum(row["finished"] + row["failed"] for row in parts["week"]) == len(summary["jobs"]) + 1)

        # ---- text and HTML
        bench = {"finished": "2026-11-25T10:40:00", "problems": ["t1", "t2"], "models": {
            "Pega-Coding": {"pass_first": 1, "pass_after_repair": 2, "of": 2, "tokens_in": 900, "tokens_out": 300,
                            "median_seconds": 12.5},
            "Pega-Chat": {"pass_first": 0, "pass_after_repair": 1, "of": 2, "tokens_in": 800, "tokens_out": 200,
                          "median_seconds": 4.0}}}
        text = usage_report.markdown(summary, jobs(), reports, bench)
        check("the text has every part", all(part in text for part in (
            "## AI apps and models", "### Tokens by AI app", "### Tokens by model", "### Worker runs by AI app",
            "### Jobs by AI app", "### Jobs by app and model", "### Last 7 days by app and model",
            "### Lean writing test")))
        check("the text names the switching", "1 of 2 lab worker sessions (50%)" in text)
        check("the text shows the gateway record", "The AICC gateway recorded 5.20 K" in text
              and "Qwen/Qwen3.8-27B 1.60 K" in text)
        check("the bench is sorted by proofs", text.index("| Pega-Coding | DeepSeek-V4.1-Flash | 1/2 | 2/2")
              < text.index("| Pega-Chat | Qwen3.8-27B | 0/2 | 1/2"))
        check("every table row has as many cells as its header", all(
            block[0].count("|") == line.count("|") for block in _tables(text) for line in block), "ragged table")
        unreadable = usage_report.collect(DAY, jobs(), folder / "missing.db", folder / "missing2.db", LAB,
                                          dead_gateway, folder / "empty", log)
        check("an unreadable OpenCode database is said, not hidden", not unreadable["opencode_readable"]
              and "could not be read" in usage_report.markdown(unreadable, jobs(), reports, {}))
        check("the jobs are still listed without the databases", len(unreadable["jobs"]) == len(summary["jobs"]))

        page = usage_report.to_html("# Title\n## Part\n- **bold** <b> a_b_c\n\n| A | B |\n|---|---|\n| 1 | x < y |\n")
        check("HTML: tables, lists, bold, escaping", "<table" in page and "<li" in page and "x &lt; y" in page
              and "&lt;b&gt;" in page and "<b style" in page and "a_b_c" in page, page)
        mail = usage_report.mail_html(DAY, "1 result", [("Tokens", "1.36 B"), ("Jobs", "5 / 1")], "# Daily\n" + text,
                                      {"2026-11-23": 600_000_000, DAY: 1_360_000_000}, 1_000_000_000)
        check("mail: cards, report and the token bars", "1.36 B" in mail and "Tokens per day, last 14 days" in mail
              and "Millennium lab - 2026-11-24" in mail and "#F2495C" in mail and "#F2CC0C" in mail)
        check("mail: no script, no style sheet (mail clients drop them)", "<script" not in mail and "<style" not in mail)
        check("mail: the report's own title is not repeated", "Daily</div>" not in mail)

    print(f"\n{len(FAILURES)} failed" if FAILURES else "\nall checks passed")
    return 1 if FAILURES else 0


def _tables(text):
    blocks, block = [], []
    for line in text.splitlines():
        if line.startswith("|"):
            block.append(line)
        elif block:
            blocks.append(block)
            block = []
    return blocks + ([block] if block else [])


if __name__ == "__main__":
    sys.exit(main())
