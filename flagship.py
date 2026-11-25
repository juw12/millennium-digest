"""Flagship programme: one problem, worked lemma by lemma, and nothing counts until a machine checks it.

Started 2026-11-25 (Juwen: "work through a result like Alpoge"). The flagship is Erdos #86, the largest C4-free
subgraph f(n) of the n-cube. It began as "decide f(7)"; the prior-art lemma found f(7) = 304 settled five days earlier
(arXiv:2611.27284), so the same day it moved to f(8) (prize_work/flagship-erdos-86-f7/PLAN.md). The programme name and
the short name in job titles are read from the ledger ("programme", "short"), not written here.

The ledger (prize_work/flagship-erdos-86-f7/ledger.json) lists the lemmas in order, each with what it needs.
- lab.plan_job() gives the Flagship desk the first open lemma whose needs are proved and that no open job holds.
- Workers never edit the ledger. They list their Lean files in `lean_claims` and may propose sub-lemmas in
  `proposed_lemmas` of their result file.
- lean_gate.py (every 30 minutes) asks the Lean checker about those files and calls settle() here, which marks a
  lemma proved:  lean lemma        -> every listed Lean claim checked (no sorry, no failure)
                 compute lemma     -> Lean claims checked AND the Referee passed the job (the UNSAT proof check)
                 literature lemma  -> the Referee passed the job
- A Lean lemma may also be installed by the operator: its Lean files are written into the ledger's own `lean_claims`
  while it is still open. The gate checks those files like any other and marks the lemma proved with no job
  (proved_by "installed"). A failed installed file hands the lemma back to the workers.
"""
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LEDGER = ROOT / "prize_work" / "flagship-erdos-86-f7" / "ledger.json"
OPEN_JOB = ("needs_ai", "running")
FINISHED = ("done", "needs_review")
MACHINE_CHECKED = ("checked", "kernel_trust")
MAX_PROPOSED = 3  # sub-lemmas one job may add


def load(path=None):
    try:
        return json.loads(Path(path or LEDGER).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def save(ledger, path=None):
    path = Path(path or LEDGER)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _proved(ledger):
    return {lemma["id"] for lemma in ledger.get("lemmas", []) if lemma.get("status") == "proved"}


def next_lemma(jobs, ledger=None):
    """The first open lemma whose needs are all proved and that nothing holds, or None. A lemma is held by an open
    flagship job, by a finished job the gate has not settled yet (2026-11-25: F1 got a second worker while its first
    one's Lean file waited for the gate), and by installed Lean files the gate has not judged yet."""
    ledger = ledger if ledger is not None else load()
    if not ledger:
        return None
    proved = _proved(ledger)
    settled = {job_id for lemma in ledger.get("lemmas", []) for job_id in lemma.get("jobs") or []}
    held = set()
    for job in jobs:
        if job.get("dept") != "flagship":
            continue
        if job.get("status") in OPEN_JOB or (job.get("status") in FINISHED and job.get("id") not in settled):
            held.add(job.get("lemma_id"))
    for lemma in ledger.get("lemmas", []):
        if lemma.get("status") != "open" or lemma["id"] in held or not set(lemma.get("needs", [])) <= proved:
            continue
        if lemma.get("lean_claims") and lemma.get("verdict") in (None, "pending"):
            continue  # installed files wait for the gate
        return lemma
    return None


def job_text(lemma, ledger, jobs):
    """Title and the topic text for a flagship job on `lemma`."""
    earlier = [job for job in jobs if job.get("lemma_id") == lemma["id"] and job.get("status") in FINISHED + ("failed",)]
    history = "".join(f" Earlier attempt {job['id']} ({job.get('status')}): {str(job.get('result', ''))[:300]}"
                      for job in earlier[-2:])
    proved = [entry for entry in ledger.get("lemmas", []) if entry["id"] in lemma.get("needs", [])]
    uses = "".join(f" Proved {entry['id']} ({entry['title']}): {', '.join(entry.get('lean_claims') or [])}."
                   for entry in proved)
    drafts = (f" Files already in place for this lemma (Lean said: {lemma.get('verdict')}), repair them rather than "
              f"start again: {', '.join(lemma['lean_claims'])}." if lemma.get("lean_claims") else "")
    title = f"{ledger.get('short') or 'Flagship #86'}: {lemma['id']} {lemma['title']}"
    topic = (f" PROGRAMME: {ledger.get('programme')}. Plan: prize_work/flagship-erdos-86-f7/PLAN.md, ledger: "
             f"prize_work/flagship-erdos-86-f7/ledger.json (read-only for you). Baseline: {ledger.get('baseline')}"
             f" YOUR LEMMA {lemma['id']} ({lemma.get('kind')}): {lemma['statement']} Deliver: {lemma.get('deliver')}."
             f"{uses}{drafts}{history}")
    return title, topic


def _referee_pass(job):
    return str(job.get("referee") or "").strip().lower().startswith("pass")


def settle(jobs, job_verdicts, results_dir=None, ledger=None, claim_verdict=None):
    """Applies finished flagship jobs to the ledger. job_verdicts: {job id: Lean verdict}. claim_verdict: a function
    (list of Lean files) -> verdict, used for the Lean files installed in the ledger itself. Returns (ledger,
    newly proved lemma ids, newly added lemma ids). Does not save."""
    ledger = ledger if ledger is not None else load()
    if not ledger:
        return None, [], []
    by_id = {lemma["id"]: lemma for lemma in ledger.get("lemmas", [])}
    newly, added = [], []
    for job in jobs:
        lemma = by_id.get(job.get("lemma_id"))
        if job.get("dept") != "flagship" or not lemma or job.get("status") not in FINISHED:
            continue
        if job["id"] not in lemma.setdefault("jobs", []):
            lemma["jobs"].append(job["id"])
        verdict = job_verdicts.get(job["id"])
        if lemma.get("status") == "proved":
            continue
        kind = lemma.get("kind")
        checked = verdict in MACHINE_CHECKED
        if (kind == "lean" and checked) or (kind == "compute" and checked and _referee_pass(job)) or (
                kind == "literature" and _referee_pass(job)):
            lemma.update({"status": "proved", "verdict": verdict or "referee pass", "proved_by": job["id"],
                          "proved_at": dt.datetime.now().isoformat(timespec="seconds"),
                          "lean_claims": list(job.get("lean_claims") or [])})
            newly.append(lemma["id"])
        elif verdict:
            lemma["verdict"] = verdict  # the latest Lean word on this lemma (sorry, failed, pending)
        if results_dir and job["id"] not in ledger.setdefault("proposals_read", []):
            ledger["proposals_read"].append(job["id"])
            added += _add_proposals(ledger, lemma, Path(results_dir) / f"{job['id']}.json")
    for lemma in ledger.get("lemmas", []) if claim_verdict else []:
        if lemma.get("status") != "open" or lemma.get("kind") != "lean" or not lemma.get("lean_claims"):
            continue
        verdict = claim_verdict(lemma["lean_claims"])
        if verdict in MACHINE_CHECKED:
            lemma.update({"status": "proved", "verdict": verdict, "proved_by": "installed",
                          "proved_at": dt.datetime.now().isoformat(timespec="seconds")})
            newly.append(lemma["id"])
        elif verdict:
            lemma["verdict"] = verdict
    return ledger, newly, added


def _add_proposals(ledger, parent, result_file):
    """Sub-lemmas a worker proposed for a lemma it could not finish: added before the parent, which then needs them."""
    try:
        proposals = json.loads(result_file.read_text(encoding="utf-8-sig")).get("proposed_lemmas") or []
    except (OSError, ValueError, AttributeError):
        return []
    lemmas, added = ledger["lemmas"], []
    for proposal in proposals[:MAX_PROPOSED] if isinstance(proposals, list) else []:
        if not isinstance(proposal, dict) or not proposal.get("statement"):
            continue
        new_id = f"{parent['id']}.{sum(1 for lemma in lemmas if lemma['id'].startswith(parent['id'] + '.')) + 1}"
        kind = proposal.get("kind") if proposal.get("kind") in ("lean", "compute", "literature") else "lean"
        lemma = {"id": new_id, "title": str(proposal.get("title") or proposal["statement"])[:90],
                 "statement": str(proposal["statement"])[:1200], "kind": kind,
                 "needs": [need for need in parent.get("needs", [])], "deliver": str(proposal.get("deliver", ""))[:200],
                 "status": "open", "lean_claims": [], "verdict": None, "jobs": [], "source": result_file.stem}
        lemmas.insert(lemmas.index(parent), lemma)
        parent.setdefault("needs", []).append(new_id)
        added.append(new_id)
    return added


def summary(ledger=None):
    """{"proved": n, "total": n, "rows": [(id, title, status, verdict)]} for reports."""
    ledger = ledger if ledger is not None else load() or {"lemmas": []}
    rows = [(lemma["id"], lemma["title"], lemma.get("status"), lemma.get("verdict")) for lemma in ledger["lemmas"]]
    return {"proved": sum(1 for row in rows if row[2] == "proved"), "total": len(rows), "rows": rows}


if __name__ == "__main__":
    info = summary()
    print(f"flagship: {info['proved']}/{info['total']} lemmas proved")
    for row in info["rows"]:
        print(f"  {row[0]:6} {row[2]:7} {str(row[3] or ''):13} {row[1]}")
    sys.exit(0)
