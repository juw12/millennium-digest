"""Daily slot balance for the research desks, and the cap on internal-check jobs.

Why (Juwen, 2026-11-25): from 11-14 to 11-25 the lab burnt 1-1.8 B tokens a day, but only one result went past a
reproduction (extension, new or negative with a Referee pass and a Review Panel pass). Most jobs were the Packing
Shop's internal checks. So slots now follow what each desk delivers, once a day:

    yield = (strong results + PRIOR_HITS) / (finished jobs + PRIOR_JOBS)    over the last `window_days`

A strong result is a useful job (lab_useful: Referee pass + Review Panel pass) whose novelty is extension, new or
negative. The Packing Shop also earns one hit per `squares_gain_per_hit` of relative score gain, because its Kaggle
score is paid without a frontier claim. The prior gives a desk with few jobs the benefit of the doubt, so a desk
that has not had its chance yet is not starved. Each desk moves at most `max_step` slot a day towards its share
of the total and stays inside its bounds; the total number of slots stays the same.

The cap: when `internal_share` of all research slots already hold internal-check jobs (a card without a frontier
claim, or the Packing Shop's standing brief), new research jobs must take a frontier card.

The plan lives in logs/slot_plan.json (not tracked, so the self-updater keeps working); lab_departments.json keeps
the base slots and the bounds. lab.ensure_jobs() calls ensure_today() on every pool tick; the plan is worked out
once a day. Delete the plan file to go back to the base slots; "enabled": false in "rebalance" switches it off.

    python slot_balance.py            work out today's plan (again), write it, print it
    python slot_balance.py --dry-run  print the plan, write nothing
"""
import datetime as dt
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import lab_useful  # noqa: E402

PLAN = HERE / "logs" / "slot_plan.json"
SCORE_LOG = HERE / "logs" / "squares_score_history.json"
SQUARES_SCORE = HERE.parent / "prize_work" / "squares-in-squares" / "best_score.json"
PRIOR_HITS = 0.5
PRIOR_JOBS = 5
DEFAULTS = {
    "enabled": True,
    "bounds": {"squares": [2, 5], "erdos": [1, 4], "prize": [1, 4]},
    "window_days": 7,
    "max_step": 1,
    "internal_share": 0.5,
    "squares_gain_per_hit": 0.001,
    "plan_valid_days": 3,
}


def settings(config):
    merged = dict(DEFAULTS)
    merged.update({key: value for key, value in (config.get("rebalance") or {}).items() if not key.startswith("_")})
    return merged


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))  # a hand-written file may start with a BOM
    except (OSError, ValueError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _dept(job):
    if job.get("dept"):
        return job["dept"]
    parts = str(job.get("id", "")).split("_")
    return parts[1] if len(parts) > 2 and parts[0] == "lab" else None


def _finished(job):
    value = job.get("finished")
    return value[:19] if isinstance(value, str) and len(value) >= 10 else ""


def desk_record(jobs, depts, since):
    """{dept: {"finished": n, "strong": n}}: jobs finished at or after `since` (ISO text), and the strong ones."""
    useful = {str(job.get("id")) for job in lab_useful.useful_from(jobs)}
    record = {dept: {"finished": 0, "strong": 0} for dept in depts}
    for job in jobs:
        dept = _dept(job)
        if dept not in record or job.get("status") not in ("done", "needs_review") or _finished(job) < since:
            continue
        record[dept]["finished"] += 1
        if str(job.get("id")) in useful and lab_useful.is_strong(job):
            record[dept]["strong"] += 1
    return record


def squares_hits(history, today, window_days, gain_per_hit):
    """Hits for the Packing Shop's score gain over the window: relative gain / gain_per_hit (lower score is better).
    `history`: {"YYYY-MM-DD": score}. No hits without a score from before the window."""
    start = (today - dt.timedelta(days=window_days)).isoformat()
    before = [day for day in history if day <= start]
    if not before or not history:
        return 0.0
    old = history[max(before)]
    new = history[max(history)]
    if not old or old <= 0 or new >= old:
        return 0.0
    return (old - new) / old / gain_per_hit


def decide(current, bounds, weights, max_step=1):
    """New slots: each desk moves towards total x its weight share, at most max_step, inside its bounds; the
    total stays the same. Pure function (tested)."""
    total = sum(current.values())
    weight_sum = sum(weights.values()) or 1.0
    ideal = {dept: total * weights[dept] / weight_sum for dept in current}

    def clamp(dept, value):
        low, high = bounds.get(dept, (1, total))
        return max(low, min(high, value))

    new = {}
    for dept, slots in current.items():
        step = max(-max_step, min(max_step, round(ideal[dept] - slots)))
        new[dept] = clamp(dept, slots + step)
    for _ in range(4 * total):  # put the total back: take from the most over-served, give to the most under-served
        gap = sum(new.values()) - total
        if gap == 0:
            break
        if gap > 0:
            movable = [dept for dept in new if new[dept] > bounds.get(dept, (1, total))[0]]
            in_step = [dept for dept in movable if new[dept] > current[dept] - max_step]
            if not (in_step or movable):
                break
            dept = max(in_step or movable, key=lambda name: (new[name] - ideal[name], name))
            new[dept] -= 1
        else:
            movable = [dept for dept in new if new[dept] < bounds.get(dept, (1, total))[1]]
            in_step = [dept for dept in movable if new[dept] < current[dept] + max_step]
            if not (in_step or movable):
                break
            dept = max(in_step or movable, key=lambda name: (ideal[name] - new[name], name))
            new[dept] += 1
    return new


def base_slots(config, depts):
    by_id = {dept["id"]: dept for dept in config.get("departments", [])}
    return {dept: int(by_id[dept].get("slots", 1)) for dept in depts if dept in by_id and not by_id[dept].get("paused")}


def make_plan(config, jobs, today, previous=None, score_history=None):
    """Today's plan from the jobs, the previous plan (or the base slots) and the Packing Shop's score history."""
    rules = settings(config)
    base = base_slots(config, rules["bounds"])
    old = (previous or {}).get("slots") or {}
    current = {dept: int(old.get(dept, slots)) for dept, slots in base.items()}
    total = sum(base.values())
    if sum(current.values()) != total:  # the base slots were changed by hand: start again from them
        current = dict(base)
    since = (today - dt.timedelta(days=rules["window_days"])).isoformat()
    record = desk_record(jobs, current, since)
    hits = {dept: float(row["strong"]) for dept, row in record.items()}
    if "squares" in hits:
        hits["squares"] += squares_hits(score_history or {}, today, rules["window_days"], rules["squares_gain_per_hit"])
    weights = {dept: (hits[dept] + PRIOR_HITS) / (record[dept]["finished"] + PRIOR_JOBS) for dept in current}
    bounds = {dept: tuple(rules["bounds"][dept]) for dept in current}
    slots = decide(current, bounds, weights, rules["max_step"])
    return {
        "date": today.isoformat(),
        "slots": slots,
        "before": current,
        "window_days": rules["window_days"],
        "desks": {dept: {"finished": record[dept]["finished"], "strong": record[dept]["strong"],
                         "hits": round(hits[dept], 3), "yield": round(weights[dept], 4)} for dept in current},
        "rule": "yield = (strong + 0.5) / (finished + 5); at most one slot a day per desk; total kept",
    }


def record_score(today):
    """Keeps one Packing Shop score a day (the last one seen that day) in logs/squares_score_history.json."""
    best = _read(SQUARES_SCORE, {})
    history = _read(SCORE_LOG, {})
    if best.get("valid") and isinstance(best.get("score"), (int, float)):
        history[today.isoformat()] = float(best["score"])
        _write(SCORE_LOG, dict(sorted(history.items())[-120:]))
    return history


def load_plan(config, today):
    """The plan's slots if it is switched on and recent enough, else {}."""
    rules = settings(config)
    if not rules["enabled"]:
        return {}
    plan = _read(PLAN, {})
    try:
        age = (today - dt.date.fromisoformat(plan.get("date", ""))).days
    except ValueError:
        return {}
    if age < 0 or age > rules["plan_valid_days"]:
        return {}
    return {dept: int(slots) for dept, slots in (plan.get("slots") or {}).items() if dept in rules["bounds"]}


def apply(config, today):
    """Puts today's planned slots into `config` (in memory only) and returns it."""
    plan = load_plan(config, today)
    rules = settings(config)
    for dept in config.get("departments", []):
        if dept.get("id") in plan:
            low, high = rules["bounds"][dept["id"]]
            dept["slots"] = max(low, min(high, plan[dept["id"]]))
    return config


def ensure_today(config, jobs, today, dry_run=False):
    """Works out today's plan once a day (on the first pool tick of the day). Returns the plan in force."""
    rules = settings(config)
    if not rules["enabled"]:
        return {}
    previous = _read(PLAN, {})
    if previous.get("date") == today.isoformat():
        return previous
    history = _read(SCORE_LOG, {}) if dry_run else record_score(today)
    plan = make_plan(config, jobs, today, previous, history)
    if not dry_run:
        _write(PLAN, plan)
    return plan


def internal_cap(config):
    """How many research jobs may hold internal-check work at once: internal_share of all research slots."""
    rules = settings(config)
    research = [dept for dept in config.get("departments", [])
                if dept.get("id") in ("squares", "erdos", "prize", "bsd", "rh") and not dept.get("paused")]
    return math.floor(rules["internal_share"] * sum(int(dept.get("slots", 1)) for dept in research))


def main():
    config = _read(HERE / "lab_departments.json", {})
    jobs = _read(HERE.parent / "research_queue.json", {}).get("jobs", [])
    today = dt.date.today()
    dry = "--dry-run" in sys.argv
    history = _read(SCORE_LOG, {}) if dry else record_score(today)
    plan = make_plan(config, jobs, today, _read(PLAN, {}), history)
    if not dry:
        _write(PLAN, plan)
    print(f"slot plan {plan['date']} ({'dry run' if dry else 'written'}): {plan['before']} -> {plan['slots']}")
    for dept, row in plan["desks"].items():
        print(f"  {dept:8} finished {row['finished']:4}  strong {row['strong']:3}  hits {row['hits']:6}  "
              f"yield {row['yield']}")
    print(f"internal-check cap: {internal_cap(apply(config, today))} open jobs")


if __name__ == "__main__":
    main()
