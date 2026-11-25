"""Which lab results count as useful: the job's Referee said pass AND a Review Panel said pass.

Used by disk_guard.py (worker logs of useful jobs are kept), publish_notes.py (the public gate) and the
dashboard. Reads research_queue.json only; never changes it.

    python lab_useful.py            counts
    python lab_useful.py --list     one line per useful job (id, novelty, title)
"""
import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUEUE = HERE.parent / "research_queue.json"
STRONG = ("extension", "new", "negative")  # novelty classes that go past a reproduction


def _first_word(text):
    words = str(text or "").strip().lower().replace("-", " ").split()
    return words[0].strip(".,:;") if words else ""


def panel_verdicts(jobs):
    """{job id: "pass" | "fix" | "reject"}: the newest Review Panel verdict on each reviewed job."""
    verdicts = {}
    panels = [job for job in jobs if job.get("panel_verdict")]
    for panel in sorted(panels, key=lambda job: str(job.get("finished") or job.get("queued") or "")):
        raw = panel["panel_verdict"]
        if isinstance(raw, str) and raw.lstrip().startswith("{"):
            try:
                raw = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue  # an unreadable verdict counts as no verdict
        if isinstance(raw, dict):
            for job_id, verdict in raw.items():
                verdicts[str(job_id)] = _first_word(verdict)
            continue
        reviews = panel.get("reviews") or []
        if isinstance(reviews, str):
            try:
                reviews = ast.literal_eval(reviews)
            except (ValueError, SyntaxError):
                reviews = []
        for job_id in reviews:  # one sentence for every job the panel looked at
            verdicts[str(job_id)] = _first_word(raw)
    return verdicts


def useful_jobs(queue_path=QUEUE):
    """Jobs whose own referee field is pass and whose newest panel verdict is pass."""
    try:
        jobs = json.loads(Path(queue_path).read_text(encoding="utf-8")).get("jobs", [])
    except (OSError, ValueError):
        return []
    return useful_from(jobs)


def useful_from(jobs):
    """useful_jobs() for a job list that is already loaded."""
    verdicts = panel_verdicts(jobs)
    return [job for job in jobs
            if _first_word(job.get("referee")) == "pass" and verdicts.get(str(job.get("id"))) == "pass"]


def useful_ids(queue_path=QUEUE):
    return {str(job["id"]) for job in useful_jobs(queue_path)}


def is_strong(job):
    return _first_word(job.get("novelty")) in STRONG


LEAN_GATE = HERE / "logs" / "lean_gate.json"
MACHINE_CHECKED = ("checked", "kernel_trust")


def lean_verdicts(path=None):
    """{job id: verdict} from lean_gate.py (checked, kernel_trust, sorry, failed, pending)."""
    try:
        return json.loads(Path(path or LEAN_GATE).read_text(encoding="utf-8-sig")).get("jobs", {}) or {}
    except (OSError, ValueError, AttributeError):
        return {}


def is_verified(job, verdicts=None):
    """Machine-checked (Juwen, 2026-11-25): a Packing Shop result is checked by its strict validator (sis.py);
    every other result needs its Lean claims checked by the Lean gate."""
    if job.get("dept") == "squares":
        return True
    verdicts = lean_verdicts() if verdicts is None else verdicts
    return verdicts.get(str(job.get("id"))) in MACHINE_CHECKED


def is_verified_strong(job, verdicts=None):
    return is_strong(job) and is_verified(job, verdicts)


def write_index(path, queue_path=None):
    """Writes the list of useful results as one private note (newest first). Returns the count, 0 if not written."""
    found = sorted(useful_jobs(queue_path or QUEUE), key=lambda job: str(job.get("finished") or ""), reverse=True)
    path = Path(path)
    if not found or not path.parent.exists():
        return 0
    lines = [
        "---", 'title: "Millennium lab: useful results"', "type: note", "tags: [millennium, lab, research]",
        f"updated: {found[0].get('finished', '')[:10]}", "publish: false", "---", "",
        f"{len(found)} results passed both checks (Referee pass + Review Panel pass); "
        f"{sum(map(is_strong, found))} go past a reproduction. Private list: research ideas, not for sharing.",
        "Rewritten by the lab once a day. Reports: `review_notes/<job>.md` in the lab folder.", "",
        "| Finished | Kind | Job | Result |", "|---|---|---|---|",
    ]
    for job in found:
        title = " ".join(str(job.get("title") or "").split()).replace("|", "/")[:160]
        lines.append(f"| {str(job.get('finished') or '')[:10]} | {_first_word(job.get('novelty')) or '-'} | `{job['id']}` | {title} |")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return 0
    return len(found)


if __name__ == "__main__":
    found = useful_jobs()
    if "--list" in sys.argv:
        for job in found:
            print(f"{job['id']:34} {str(job.get('novelty') or '-'):13} {str(job.get('title') or '')[:110]}")
    print(f"{len(found)} useful job(s) (referee pass + panel pass), {sum(map(is_strong, found))} past a reproduction")
