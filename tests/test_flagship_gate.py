"""Flagship ledger, Lean gate verdicts, Lean-verified strong results, daily report."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_report  # noqa: E402
import flagship  # noqa: E402
import lab  # noqa: E402
import lab_useful  # noqa: E402
import lean_gate  # noqa: E402


def ledger():
    def lemma(lemma_id, kind, needs=()):
        return {"id": lemma_id, "title": lemma_id, "statement": f"statement {lemma_id}", "kind": kind,
                "needs": list(needs), "deliver": "x", "status": "open", "lean_claims": [], "verdict": None, "jobs": []}
    return {"programme": "p", "baseline": "b", "lemmas": [
        lemma("F0", "literature"), lemma("F1", "lean"), lemma("F2", "lean", ["F1"]), lemma("F4", "compute", ["F0"])]}


def job(job_id, lemma_id, status="done", **extra):
    return {"id": job_id, "dept": "flagship", "lemma_id": lemma_id, "status": status, **extra}


# ---------------------------------------------------------------- next lemma
def test_next_lemma_follows_needs_and_skips_held():
    book = ledger()
    assert flagship.next_lemma([], book)["id"] == "F0"
    held = [job("j1", "F0", "needs_ai")]
    assert flagship.next_lemma(held, book)["id"] == "F1"  # F0 held, F2 needs F1, F4 needs F0
    book["lemmas"][1]["status"] = "proved"
    assert flagship.next_lemma(held + [job("j2", "F1", "needs_ai")], book)["id"] == "F2"


# ---------------------------------------------------------------- settle
def test_lean_lemma_needs_a_checked_verdict():
    book, newly, _ = flagship.settle([job("j1", "F1", lean_claims=["a.lean"])], {"j1": "sorry"}, ledger=ledger())
    assert newly == [] and book["lemmas"][1]["status"] == "open" and book["lemmas"][1]["verdict"] == "sorry"
    book, newly, _ = flagship.settle([job("j1", "F1", lean_claims=["a.lean"])], {"j1": "checked"}, ledger=ledger())
    assert newly == ["F1"] and book["lemmas"][1]["lean_claims"] == ["a.lean"]


def test_compute_lemma_needs_lean_and_referee_and_literature_needs_referee():
    jobs = [job("c1", "F4", referee="fix - proof file missing"), job("l1", "F0", referee="pass - sources ok")]
    book, newly, _ = flagship.settle(jobs, {"c1": "checked"}, ledger=ledger())
    assert newly == ["F0"]
    jobs[0]["referee"] = "pass"
    book, newly, _ = flagship.settle(jobs, {"c1": "checked"}, ledger=book)
    assert newly == ["F4"]


def test_unfinished_and_other_desks_are_ignored():
    jobs = [job("j1", "F1", "needs_ai"), {"id": "x", "dept": "erdos", "lemma_id": "F1", "status": "done"}]
    _, newly, _ = flagship.settle(jobs, {"j1": "checked", "x": "checked"}, ledger=ledger())
    assert newly == []


def test_proposed_lemmas_go_before_the_parent_once(tmp_path):
    (tmp_path / "j1.json").write_text(json.dumps({"proposed_lemmas": [
        {"title": "half", "statement": "first half", "kind": "lean"},
        {"statement": "second half", "kind": "weird"}, "junk"]}))
    book, _, added = flagship.settle([job("j1", "F2")], {"j1": "failed"}, tmp_path, ledger=ledger())
    assert added == ["F2.1", "F2.2"]
    ids = [lemma["id"] for lemma in book["lemmas"]]
    assert ids.index("F2.1") < ids.index("F2") and set(book["lemmas"][ids.index("F2")]["needs"]) >= {"F2.1", "F2.2"}
    assert book["lemmas"][ids.index("F2.2")]["kind"] == "lean"
    _, _, again = flagship.settle([job("j1", "F2")], {"j1": "failed"}, tmp_path, ledger=book)
    assert again == []  # read once


# ---------------------------------------------------------------- lean gate
def test_relative_paths():
    assert lean_gate.relative("prize_work/x/lean/A.lean") == "x/lean/A.lean"
    assert lean_gate.relative("x\\lean\\A.lean") == "x/lean/A.lean"
    assert lean_gate.relative(str(lean_gate.PRIZE_WORK / "x" / "A.lean")) == "x/A.lean"
    assert lean_gate.relative("../secret.lean") is None
    assert lean_gate.relative("x/A.py") is None


def test_verdict_is_on_current_bytes_and_worst_file_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(lean_gate, "PRIZE_WORK", tmp_path)
    (tmp_path / "a.lean").write_text("theorem a : True := trivial")
    (tmp_path / "b.lean").write_text("theorem b : True := trivial")
    sha = lambda name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()  # noqa: E731
    cache = {"claims": {"a.lean": {"sha": sha("a.lean"), "verdict": "checked"},
                        "b.lean": {"sha": sha("b.lean"), "verdict": "kernel_trust"}}}
    assert lean_gate.job_verdict(["a.lean"], cache) == "checked"
    assert lean_gate.job_verdict(["a.lean", "b.lean"], cache) == "kernel_trust"
    (tmp_path / "a.lean").write_text("theorem a : True := sorry")  # changed after the check
    assert lean_gate.job_verdict(["a.lean"], cache) == "pending"
    assert lean_gate.job_verdict(["missing.lean"], cache) == "failed"
    assert lean_gate.job_verdict([], cache) is None


def test_gate_run_settles_alerts_once_and_writes_state(tmp_path, monkeypatch):
    work = tmp_path / "prize_work"
    work.mkdir()
    (work / "F1.lean").write_text("theorem t : True := trivial")
    sha = hashlib.sha256((work / "F1.lean").read_bytes()).hexdigest()
    (tmp_path / "cache.json").write_text(json.dumps({"claims": {"F1.lean": {"sha": sha, "verdict": "checked"}}}))
    (tmp_path / "queue.json").write_text(json.dumps({"jobs": [job("j1", "F1", lean_claims=["prize_work/F1.lean"])]}))
    (tmp_path / "ledger.json").write_text(json.dumps(ledger()))
    for name, value in (("PRIZE_WORK", work), ("CACHE", tmp_path / "cache.json"), ("QUEUE", tmp_path / "queue.json"),
                        ("STATE", tmp_path / "gate.json"), ("LOG", tmp_path / "gate.log"),
                        ("LOCK", tmp_path / "gate.lock"), ("RESULTS", tmp_path)):
        monkeypatch.setattr(lean_gate, name, value)
    monkeypatch.setattr(flagship, "LEDGER", tmp_path / "ledger.json")
    sent = []
    lean_gate.main(check=False, send=lambda key, subject, text: sent.append(key) or True)
    lean_gate.main(check=False, send=lambda key, subject, text: sent.append(key) or True)
    assert sent == ["lemma:F1"]
    assert json.loads((tmp_path / "gate.json").read_text())["jobs"] == {"j1": "checked"}
    assert json.loads((tmp_path / "ledger.json").read_text())["lemmas"][1]["status"] == "proved"
    assert not (tmp_path / "gate.lock").exists()


# ---------------------------------------------------------------- verified strong
def test_strong_counts_only_when_machine_checked():
    erdos = {"id": "e1", "dept": "erdos", "novelty": "extension"}
    squares = {"id": "s1", "dept": "squares", "novelty": "negative"}
    assert not lab_useful.is_verified_strong(erdos, {})
    assert not lab_useful.is_verified_strong(erdos, {"e1": "sorry"})
    assert lab_useful.is_verified_strong(erdos, {"e1": "checked"})
    assert lab_useful.is_verified_strong(squares, {})  # the strict validator checks packings
    assert not lab_useful.is_verified_strong({"id": "i", "dept": "erdos", "novelty": "internal"}, {"i": "checked"})


# ---------------------------------------------------------------- lab.plan_job
def test_plan_job_for_the_flagship(monkeypatch):
    book = ledger()
    monkeypatch.setattr(flagship, "load", lambda path=None: book)
    dept = {"id": "flagship", "name": "Flagship", "brief": "BRIEF."}
    planned = lab.plan_job(dept, [], __import__("datetime").date(2026, 11, 25), 1, {})
    assert planned["lemma_id"] == "F0" and planned["title"].startswith("Flagship #86 f(7): F0")
    assert "YOUR LEMMA F0" in planned["why"] and lab.RESEARCH_RULES in planned["why"]
    for lemma in book["lemmas"]:
        lemma["status"] = "proved"
    assert lab.plan_job(dept, [], __import__("datetime").date(2026, 11, 25), 2, {}) is None


# ---------------------------------------------------------------- daily report
def test_daily_report_reads_the_lab_files(tmp_path, monkeypatch):
    jobs = [job("f1", "F1", finished="2026-11-24T10:00:00", result="Cube.lean checked"),
            {"id": "s1", "dept": "squares", "status": "done", "finished": "2026-11-24T11:00:00"},
            {"id": "x1", "dept": "erdos", "status": "failed", "finished": "2026-11-24T12:00:00"}]
    (tmp_path / "queue.json").write_text(json.dumps({"jobs": jobs}))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "token_pace.json").write_text(json.dumps({"days": {"2026-11-24": 1516370374}}))
    (logs / "pool.log").write_text("2026-11-24 00:05  tick\n2026-11-24 12:00  tick\n2026-11-25 00:01  tick\n")
    book = ledger()
    book["lemmas"][1].update({"status": "proved", "verdict": "checked", "proved_at": "2026-11-24T10:05:00"})
    monkeypatch.setattr(daily_report, "QUEUE", tmp_path / "queue.json")
    monkeypatch.setattr(daily_report, "LOGS", logs)
    monkeypatch.setattr(daily_report, "ROOT", tmp_path)
    monkeypatch.setattr(flagship, "load", lambda path=None: book)
    monkeypatch.setattr(lab_useful, "lean_verdicts", lambda path=None: {})
    subject, text = daily_report.build("2026-11-24")
    assert "Flagship 1/4 lemmas proved (+1 yesterday: F1)" in subject
    assert "[x] F1" in text and "1.52 B" in text and "Failed: 1" in text
    assert "No pool activity 00:05-12:00" in text and "No pool activity 12:00-" in text
