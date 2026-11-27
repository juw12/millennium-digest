"""Daily desk slot balance and the internal-check cap (slot_balance.py, lab.ensure_jobs)."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab  # noqa: E402
import lab_ideas  # noqa: E402
import slot_balance  # noqa: E402

BOUNDS = {"squares": (2, 5), "erdos": (1, 4), "prize": (1, 4)}
TODAY = dt.date(2026, 11, 25)


def config(squares=3, erdos=3, prize=2, **rebalance):
    return {
        "max_open": 18,
        "rebalance": rebalance,
        "departments": [
            {"id": "squares", "name": "Packing Shop", "slots": squares, "brief": "b"},
            {"id": "erdos", "name": "Erdos Bench", "slots": erdos, "brief": "b"},
            {"id": "prize", "name": "Bounty Hunters", "slots": prize, "brief": "b"},
            {"id": "bsd", "name": "Murmuration Desk", "brief": "b", "every_hours": 20},
            {"id": "rh", "name": "Zeta Desk", "brief": "b", "every_hours": 20},
        ],
    }


def finished_job(job_id, dept, day, strong=False):
    job = {"id": job_id, "dept": dept, "status": "done", "finished": f"{day}T10:00:00"}
    if strong:
        job.update({"referee": "pass", "novelty": "extension"})
    return job


def panel(job_ids, day):
    return {"id": f"lab_panel_{day}", "dept": "panel", "status": "done", "finished": f"{day}T12:00:00",
            "panel_verdict": {job_id: "pass" for job_id in job_ids}}


# ---------------------------------------------------------------- decide (pure)
def test_equal_weights_keep_slots_close_to_even_and_total_fixed():
    new = slot_balance.decide({"squares": 3, "erdos": 3, "prize": 2}, BOUNDS, {"squares": 1, "erdos": 1, "prize": 1})
    assert sum(new.values()) == 8
    assert all(abs(new[d] - old) <= 1 for d, old in {"squares": 3, "erdos": 3, "prize": 2}.items())


def test_moves_at_most_one_slot_a_day():
    new = slot_balance.decide({"squares": 5, "erdos": 2, "prize": 1}, BOUNDS, {"squares": 0.01, "erdos": 1, "prize": 1})
    assert new["squares"] == 4 and sum(new.values()) == 8


def test_bounds_hold_even_against_a_strong_desk():
    new = slot_balance.decide({"squares": 2, "erdos": 4, "prize": 2}, BOUNDS, {"squares": 0.0, "erdos": 100, "prize": 0.0})
    assert new["squares"] >= 2 and new["erdos"] <= 4 and new["prize"] >= 1
    assert sum(new.values()) == 8


def test_productive_desk_grows():
    new = slot_balance.decide({"squares": 3, "erdos": 3, "prize": 2}, BOUNDS, {"squares": 0.01, "erdos": 0.02, "prize": 0.5})
    assert new["prize"] == 3 and new["squares"] == 2


# ---------------------------------------------------------------- record and plan
def test_strong_needs_referee_and_panel_pass_and_strong_novelty(monkeypatch):
    import lab_useful
    monkeypatch.setattr(lab_useful, "lean_verdicts", lambda path=None: {"e1": "checked", "e2": "checked",
                                                                         "e3": "checked", "old": "checked"})
    jobs = [
        finished_job("e1", "erdos", "2026-11-24", strong=True),
        finished_job("e2", "erdos", "2026-11-24", strong=True),  # no panel pass
        {**finished_job("e3", "erdos", "2026-11-24"), "referee": "pass", "novelty": "internal"},
        finished_job("old", "erdos", "2026-11-01", strong=True),  # outside the window
        panel(["e1", "e3", "old"], "2026-11-24"),
    ]
    record = slot_balance.desk_record(jobs, ["erdos"], "2026-11-18")
    assert record["erdos"] == {"finished": 3, "strong": 1}


def test_squares_hits_from_score_gain():
    history = {"2026-11-17": 22.1062, "2026-11-25": 22.0841}  # 0.1 % better
    hits = slot_balance.squares_hits(history, TODAY, 7, 0.001)
    assert 0.99 < hits < 1.01
    assert slot_balance.squares_hits({"2026-11-24": 22.0}, TODAY, 7, 0.001) == 0.0  # nothing from before the window
    assert slot_balance.squares_hits({"2026-11-10": 22.0, "2026-11-25": 22.5}, TODAY, 7, 0.001) == 0.0  # worse


def test_plan_shifts_a_busy_desk_without_strong_results_towards_the_others():
    jobs = [finished_job(f"s{i}", "squares", "2026-11-24") for i in range(300)]
    jobs += [finished_job(f"e{i}", "erdos", "2026-11-24") for i in range(20)]
    jobs += [finished_job(f"p{i}", "prize", "2026-11-24") for i in range(10)]
    plan = slot_balance.make_plan(config(), jobs, TODAY)
    assert plan["before"] == {"squares": 3, "erdos": 3, "prize": 2}
    assert plan["slots"]["squares"] == 2 and sum(plan["slots"].values()) == 8
    assert plan["desks"]["squares"]["finished"] == 300


def test_plan_starts_again_from_base_when_the_base_total_changes():
    plan = slot_balance.make_plan(config(), [], TODAY, previous={"slots": {"squares": 5, "erdos": 2, "prize": 1, }})
    assert plan["before"] == {"squares": 5, "erdos": 2, "prize": 1}  # same total 8: carried on
    plan = slot_balance.make_plan(config(), [], TODAY, previous={"slots": {"squares": 5, "erdos": 4, "prize": 4}})
    assert plan["before"] == {"squares": 3, "erdos": 3, "prize": 2}


def test_apply_uses_a_fresh_plan_only(tmp_path, monkeypatch):
    monkeypatch.setattr(slot_balance, "PLAN", tmp_path / "slot_plan.json")
    (tmp_path / "slot_plan.json").write_text(json.dumps({"date": "2026-11-24", "slots": {"squares": 2, "erdos": 4}}))
    cfg = slot_balance.apply(config(), TODAY)
    slots = {d["id"]: d.get("slots") for d in cfg["departments"]}
    assert slots["squares"] == 2 and slots["erdos"] == 4 and slots["prize"] == 2
    cfg = slot_balance.apply(config(), TODAY + dt.timedelta(days=10))  # stale plan: base slots
    assert {d["id"]: d.get("slots") for d in cfg["departments"]}["squares"] == 3
    cfg = slot_balance.apply(config(enabled=False), TODAY)  # switched off
    assert {d["id"]: d.get("slots") for d in cfg["departments"]}["squares"] == 3


def test_ensure_today_works_the_plan_out_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(slot_balance, "PLAN", tmp_path / "slot_plan.json")
    monkeypatch.setattr(slot_balance, "SCORE_LOG", tmp_path / "hist.json")
    monkeypatch.setattr(slot_balance, "SQUARES_SCORE", tmp_path / "best.json")
    (tmp_path / "best.json").write_text(json.dumps({"valid": True, "score": 22.09}))
    first = slot_balance.ensure_today(config(), [], TODAY)
    assert first["date"] == TODAY.isoformat() and (tmp_path / "slot_plan.json").exists()
    assert json.loads((tmp_path / "hist.json").read_text()) == {"2026-11-25": 22.09}
    (tmp_path / "slot_plan.json").write_text(json.dumps({**first, "marker": 1}))
    assert slot_balance.ensure_today(config(), [], TODAY).get("marker") == 1  # not worked out again


def test_internal_cap_is_half_of_research_slots():
    assert slot_balance.internal_cap(config()) == 5  # 3 + 3 + 2 + bsd 1 + rh 1 = 10
    with_mech = config()
    with_mech["departments"].append({"id": "mech", "name": "Mechanics Bench", "slots": 2, "brief": "b"})
    assert slot_balance.internal_cap(with_mech) == 6  # the Mechanics Bench is a research desk: 12 slots


# ---------------------------------------------------------------- lab.py hooks
def test_is_internal():
    cards = {"c_front": {"frontier": True}, "c_audit": {"frontier": False}, "c_sq": {}}
    assert lab.is_internal({"dept": "squares"}, cards)  # standing brief
    assert lab.is_internal({"dept": "squares", "idea_id": "c_sq"}, cards)  # squares default: no frontier claim
    assert not lab.is_internal({"dept": "squares", "idea_id": "c_front"}, cards)
    assert lab.is_internal({"dept": "erdos", "idea_id": "c_audit"}, cards)
    assert not lab.is_internal({"dept": "erdos"}, cards)  # catalogue work in a frontier department
    assert not lab.is_internal({"dept": "review"}, cards)


def test_ensure_jobs_respects_the_cap(tmp_path, monkeypatch):
    bank = {"cards": [
        {"id": f"sq_audit_{i}", "dept": "squares", "status": "open", "frontier": False, "value": 5, "odds": 0.5,
         "cost": 1, "title": f"audit {i}", "verifier": "v", "kill": "k", "source": "director:x"} for i in range(5)
    ] + [
        {"id": "sq_front", "dept": "squares", "status": "open", "frontier": True, "value": 2, "odds": 0.5,
         "cost": 1, "title": "frontier", "verifier": "v", "kill": "k", "source": "director:x"},
    ]}
    queue = tmp_path / "research_queue.json"
    open_internal = [{"id": f"lab_squares_261124_{i}", "dept": "squares", "status": "needs_ai",
                      "queued": "2026-11-24T10:00:00", "idea_id": None} for i in range(5)]
    queue.write_text(json.dumps({"jobs": open_internal}), encoding="utf-8")
    cfg = config(squares=5, erdos=1, prize=1)  # research slots 9 -> cap 4; 5 internal already open
    cfg["departments"] = [d for d in cfg["departments"] if d["id"] == "squares"]
    cfg["departments"][0]["slots"] = 7
    departments = tmp_path / "lab_departments.json"
    departments.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(lab, "QUEUE", queue)
    monkeypatch.setattr(lab, "DEPARTMENTS", departments)
    monkeypatch.setattr(lab, "_taken_topics", lambda jobs: set())
    monkeypatch.setattr(lab_ideas, "_load_quiet", lambda: bank)
    monkeypatch.setattr(slot_balance, "PLAN", tmp_path / "plan.json")
    monkeypatch.setattr(slot_balance, "settings", lambda c: {**slot_balance.DEFAULTS, "bounds": {}})
    created = lab.ensure_jobs(TODAY, dry_run=True)
    assert len(created) == 1  # only the frontier card; no audit card, no standing brief
    added = [job for job in json.loads(queue.read_text())["jobs"] if job["id"] in created]
    assert added == []  # dry run wrote nothing


def test_programme_card_without_slate_is_skipped_not_the_desk(monkeypatch):
    programme = {"id": "erdos-prog", "dept": "erdos", "status": "open", "repeat": True, "frontier": True,
                 "value": 9, "odds": 0.9, "cost": 1, "title": "programme", "verifier": "v", "kill": "k",
                 "source": "director:x"}
    named = {"id": "erdos-named", "dept": "erdos", "status": "open", "frontier": True, "topic_id": "E-1",
             "value": 5, "odds": 0.5, "cost": 1, "title": "named", "verifier": "v", "kill": "k",
             "source": "director:x"}
    bank = {"cards": [programme, named]}
    monkeypatch.setattr(lab_ideas, "_load_quiet", lambda: bank)
    monkeypatch.setattr(lab, "_catalog", lambda: [{"id": "E-1", "family": "erdos", "title": "E1", "reward": "$1",
                                                  "url": "u"}])
    monkeypatch.setattr(lab, "_done_dossiers", lambda: {lab._slug("E-1")})  # every problem has a dossier
    monkeypatch.setattr(lab, "_taken_topics", lambda jobs: set())
    dept = {"id": "erdos", "name": "Erdos Bench", "brief": "b"}
    job = lab.plan_job(dept, [], TODAY, 1, {})
    assert job and job["idea_id"] == "erdos-named"


def test_plan_written_by_powershell_with_a_bom_is_read(tmp_path, monkeypatch):
    plan = tmp_path / "slot_plan.json"
    plan.write_bytes(b"\xef\xbb\xbf" + json.dumps({"date": "2026-11-25", "slots": {"squares": 3}}).encode())
    monkeypatch.setattr(slot_balance, "PLAN", plan)
    assert slot_balance.load_plan(config(), TODAY) == {"squares": 3}


def test_strong_without_a_lean_check_does_not_count(monkeypatch):
    import lab_useful
    monkeypatch.setattr(lab_useful, "lean_verdicts", lambda path=None: {"e1": "sorry"})
    jobs = [finished_job("e1", "erdos", "2026-11-24", strong=True), panel(["e1"], "2026-11-24")]
    assert slot_balance.desk_record(jobs, ["erdos"], "2026-11-18")["erdos"]["strong"] == 0


# ---------------------------------------------------------------- Mechanics Bench (2026-11-27)
MECH = {"id": "mech", "name": "Mechanics Bench", "slots": 1, "every_hours": 4, "brief": "Mechanics brief."}


def test_mechanics_bench_is_in_the_lab_settings():
    import lab_pool
    settings = json.loads((Path(lab.__file__).parent / "lab_departments.json").read_text(encoding="utf-8"))
    desk = next(dept for dept in settings["departments"] if dept["id"] == "mech")
    assert desk["slots"] == 1 and desk["every_hours"] == 4 and not desk.get("paused")
    assert "prize_work/mech-<slug>.md" in desk["brief"]
    assert "$" not in desk["brief"]  # lab_facts.py asks for every named sum in prizes.json; this desk has no prize
    assert "mech" in lab_ideas.CARD_DEPTS and "mech" in lab_ideas.FRONTIER_DEPTS and "mech" in lab_pool.PRIORITY
    for desk_id in lab_ideas.FRONTIER_DEPTS:
        assert desk_id in lab_ideas.CARD_DEPTS


def test_mechanics_bench_without_a_card_works_its_standing_brief(monkeypatch):
    monkeypatch.setattr(lab_ideas, "_load_quiet", lambda: {"cards": []})
    job = lab.plan_job(MECH, [], TODAY, 1, {})
    assert job["id"] == "lab_mech_261125_1" and job["dept"] == "mech" and "idea_id" not in job
    assert job["why"].startswith("Mechanics brief.") and job["why"].endswith(lab.RESEARCH_RULES)
    assert not lab.is_internal(job, {})  # a frontier desk: its standing brief is not an internal check
    assert lab.plan_job(MECH, [], TODAY, 1, {}, frontier_only=True)  # so the internal-check cap does not stop it


def test_mechanics_bench_works_its_idea_card_and_the_director_refills_it(monkeypatch):
    card = {"id": "mech-monostable", "dept": "mech", "status": "open", "frontier": True, "value": 8, "odds": 0.2,
            "cost": 1, "title": "monostable polyhedron with 13 faces", "verifier": "v", "kill": "k",
            "source": "director:x"}
    bank = {"cards": [card]}
    monkeypatch.setattr(lab_ideas, "_load_quiet", lambda: bank)
    monkeypatch.setattr(lab, "_taken_topics", lambda jobs: set())
    job = lab.plan_job(MECH, [], TODAY, 1, {})
    assert job["idea_id"] == "mech-monostable" and job["title"] == "Mechanics Bench: monostable polyhedron with 13 faces"
    bank["cards"] = []  # an empty bank: the Director's next job is for this desk
    assert lab_ideas.director_target({"departments": [MECH]}) == ("mech", 0)


def test_mechanics_bench_result_file_asks_for_novelty_and_a_checked_claim():
    import lab_pool
    schema = lab_pool.result_schema({"id": "lab_mech_261127_1", "dept": "mech"})
    assert '"novelty"' in schema and '"baseline"' in schema and '"lean_claims"' in schema
