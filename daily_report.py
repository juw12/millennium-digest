"""Daily report to Juwen (task "Millennium daily report", 07:30): what the lab did yesterday, in one email.

Sections: flagship ledger (the one result that matters), machine-checked results, all output by desk, the Kaggle
score, tokens, slot plan, anything that went wrong (outages, the PC off, failures), and the usage part from
usage_report.py: tokens, jobs and results per AI app and per model. Every number is read from the lab's own
files; nothing is estimated. The mail is HTML in the look of the AICC leaderboard mail (cards, tables, 14-day
token bars); the note and the history file hold the same text as Markdown.

Goes to: the email relay (juwpegatron@gmail.com, notify_juwen.py), the Obsidian note
notes/millennium-daily-report.md (rewritten daily, so Cairn finds it), and logs/daily_reports/<date>.md (history).
Newly proved flagship lemmas and new Lean-checked results are emailed at once by lean_gate.py, not only here.

    python daily_report.py             yesterday's report: write, save, email
    python daily_report.py --print     print it, send nothing
"""
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import flagship  # noqa: E402
import lab_useful  # noqa: E402
import usage_report  # noqa: E402

ROOT = HERE.parent
QUEUE = ROOT / "research_queue.json"
LOGS = HERE / "logs"
VAULT_NOTE = Path.home() / "Documents" / "Obsidian Vault" / "notes" / "millennium-daily-report.md"
HISTORY = LOGS / "daily_reports"


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _day(job):
    """The local day a job finished on (the queue stores some finish times in UTC)."""
    return usage_report.local_day(job.get("finished"))


def _gaps(day, minutes=60):
    """Stretches of the day longer than `minutes` with no pool tick (the PC off or asleep, or the pool stuck)."""
    try:
        lines = (LOGS / "pool.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    stamps = []
    for line in lines:
        match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", line)
        if match and match.group(1)[:10] in (day, (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()):
            stamps.append(dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M"))
    start = dt.datetime.fromisoformat(day)
    points = [start] + sorted(stamp for stamp in stamps if start <= stamp <= start + dt.timedelta(days=1)) + [
        min(start + dt.timedelta(days=1), dt.datetime.now())]
    return [(a, b) for a, b in zip(points, points[1:]) if (b - a).total_seconds() > minutes * 60]


def checked_steps(newly, info, tokens):
    """The lab's second number beside tokens: flagship lemmas the Lean gate accepted on the day, who wrote them,
    and what a lab-written step cost. (report line, card value). Steps written in a Vector session are named
    apart: their tokens do not pass the AICC gateway, so they must not make the lab's tokens look cheaper."""
    by_lab = [lemma["id"] for lemma in newly if lemma.get("proved_by") != "installed"]
    installed = [lemma["id"] for lemma in newly if lemma.get("proved_by") == "installed"]
    who = "; ".join(filter(None, [f"lab workers: {', '.join(by_lab)}" if by_lab else "",
                                  f"Vector session, installed: {', '.join(installed)}" if installed else ""]))
    if by_lab and tokens:
        cost = f"{usage_report.short(tokens / len(by_lab))} lab tokens per step a lab worker proved"
    elif tokens:
        cost = f"no step by a lab worker, so {usage_report.short(tokens)} lab tokens bought none"
    else:
        cost = "tokens not recorded"
    line = (f"- Machine-checked steps (flagship lemmas the Lean gate accepted): {len(newly)} on this day"
            + (f" ({who})" if who else "") + f", {info['proved']} of {info['total']} in all; {cost}")
    return line, f"{len(newly)} ({len(by_lab)} by lab workers)"


def build(day=None, usage=None, parts=None):
    """(subject, markdown text) for `day` (default: yesterday). `usage` = usage_report.collect(day) when the
    caller has it (the usage part is left out when it is None); `parts`, when given, receives the headline and
    the cards for the HTML mail."""
    day = day or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    jobs = _read(QUEUE, {}).get("jobs", [])
    verdicts = lab_useful.lean_verdicts()
    finished = [job for job in jobs if _day(job) == day and job.get("status") in ("done", "needs_review")]
    marked_failed = set(usage_report.pool_events(day, LOGS / "pool.log")["failed"])  # failed jobs have no finish time
    failed = [job for job in jobs if job.get("status") == "failed"
              and (str(job.get("id")) in marked_failed or _day(job) == day)]
    useful_ids = {job["id"] for job in lab_useful.useful_from(jobs)}
    verified_strong = [job for job in finished if job["id"] in useful_ids
                       and lab_useful.is_verified_strong(job, verdicts)]
    claimed_strong = [job for job in finished if lab_useful.is_strong(job)]
    desks = collections.Counter(job.get("dept") or "other" for job in finished)
    ledger = flagship.load() or {"lemmas": []}
    info = flagship.summary(ledger)
    pace = _read(LOGS / "token_pace.json", {})
    tokens = (pace.get("days") or {}).get(day)
    plan = _read(LOGS / "slot_plan.json", {})
    scores = _read(LOGS / "squares_score_history.json", {})
    best = _read(ROOT / "prize_work" / "squares-in-squares" / "best_score.json", {})
    gaps = _gaps(day)
    flagship_jobs = [job for job in jobs if job.get("dept") == "flagship" and _day(job) == day]
    newly = [lemma for lemma in ledger["lemmas"] if str(lemma.get("proved_at", ""))[:10] == day]

    lines = [f"# Millennium lab - {day}", ""]
    headline = (f"Flagship {info['proved']}/{info['total']} lemmas proved"
                + (f" (+{len(newly)} yesterday: {', '.join(lemma['id'] for lemma in newly)})" if newly else "")
                + f"; {len(verified_strong)} machine-checked strong result(s); {len(finished)} jobs finished")
    lines += [f"**{headline}.**", ""]
    lines += [f"## Flagship: {ledger.get('programme') or 'Erdos #86'}", f"Baseline: {ledger.get('baseline', '-')}", ""]
    for lemma_id, title, status, verdict in info["rows"]:
        mark = {"proved": "[x]", "open": "[ ]"}.get(status, "[~]")
        lines.append(f"- {mark} {lemma_id} {title}" + (f" - Lean: {verdict}" if verdict else ""))
    for job in flagship_jobs:
        lines.append(f"  - {job['id']} on {job.get('lemma_id')}: {str(job.get('result', ''))[:300]}")
    next_open = flagship.next_lemma(jobs, ledger)
    lines += ["", f"Next lemma: {next_open['id']} {next_open['title']}" if next_open else "Next lemma: none ready", ""]
    lines += ["## Machine-checked results (Lean gate, or the Kaggle validator)"]
    lines += [f"- {job['id']}: {str(job.get('result', ''))[:300]}" for job in verified_strong] or ["- none"]
    if len(claimed_strong) > len(verified_strong):
        lines.append(f"- {len(claimed_strong) - len(verified_strong)} more claimed new/extension/negative, "
                     "not machine-checked yet (not counted)")
    lines += ["", "## Output"]
    lines.append("- Finished by desk: " + (", ".join(f"{name} {count}" for name, count in desks.most_common()) or "none"))
    lines.append(f"- Failed: {len(failed)}")
    steps_line, steps_card = checked_steps(newly, info, tokens)
    lines.append(steps_line)
    if best.get("valid"):
        week = (dt.date.fromisoformat(day) - dt.timedelta(days=7)).isoformat()
        older = [scores[key] for key in sorted(scores) if key <= week]
        lines.append(f"- Kaggle Squares score {best['score']:.6f} (lower is better)"
                     + (f", {older[-1]:.6f} a week before" if older else ""))
    talos_tokens = (pace.get("talos_days") or {}).get(day, 0)
    lines.append(f"- Tokens (OpenCode + Talos, all through the AICC gateway): {tokens / 1e9:.2f} B, of which Talos "
                 f"{talos_tokens / 1e9:.2f} B (target 1 B)" if tokens else "- Tokens: not recorded")
    if plan.get("slots"):
        lines.append(f"- Slots ({plan.get('date')}): " + ", ".join(f"{k} {v}" for k, v in plan["slots"].items()))
    lines += ["", "## Problems"]
    problems = [f"- No pool activity {a:%H:%M}-{b:%H:%M} ({(b - a).total_seconds() / 3600:.1f} h): PC off, asleep "
                "or the pool stuck" for a, b in gaps]
    if failed:
        problems.append(f"- {len(failed)} failed job(s): " + ", ".join(job["id"] for job in failed[:6]))
    lines += problems or ["- none"]
    if usage is not None:
        try:
            lines += ["", usage_report.markdown(usage, jobs).rstrip()]
        except Exception as error:  # the research part must go out even when the usage part cannot be written
            lines += ["", "## AI apps and models", f"- could not be written: {error!r}"]
    if parts is not None:
        switching = (usage or {}).get("switching") or {}
        parts.update(headline=headline, days=pace.get("days") or {}, target=pace.get("target") or 0, cards=[
            ("Flagship lemmas proved", f"{info['proved']} / {info['total']}"),
            ("Checked steps on the day", steps_card),
            ("Machine-checked results", len(verified_strong)),
            ("Jobs finished / failed", f"{len(finished)} / {len(failed)}"),
            ("Tokens", usage_report.short(tokens) if tokens else "-"),
            ("AICC recorded", usage_report.short(((usage or {}).get("aicc") or {}).get("total") or None)),
            ("Workers that left the pinned model", f"{switching['switched']} / {switching['sessions']}"
             if switching.get("sessions") else "-"),
        ])
    lines += ["", "_Written by daily_report.py from the lab's own files. No claim here is a proof of a prize problem; "
              "a lemma counts only when the Lean gate marks it._"]
    subject = f"Daily {day}: {headline}"
    return subject, "\n".join(lines) + "\n"


def save(day, text):
    HISTORY.mkdir(parents=True, exist_ok=True)
    (HISTORY / f"{day}.md").write_text(text, encoding="utf-8")
    if VAULT_NOTE.parent.exists():
        front = (f"---\ntitle: \"Millennium lab: daily report\"\ntype: note\ntags: [millennium, lab, daily]\n"
                 f"updated: {dt.date.today().isoformat()}\npublish: false\n---\n\n")
        VAULT_NOTE.write_text(front + text, encoding="utf-8")


def main():
    day = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    try:
        usage = usage_report.collect(day)
    except Exception:  # the usage part is extra: the research report goes out without it
        usage = None
    parts = {}
    subject, text = build(day, usage, parts)
    if "--print" in sys.argv:
        print(subject)
        print(text)
        return
    save(day, text)
    if usage is not None:
        usage_report.save(usage)
    import notify_juwen
    try:
        body = usage_report.mail_html(day, parts["headline"], parts["cards"], text, parts["days"], parts["target"])
        (HISTORY / f"{day}.html").write_text(body, encoding="utf-8")
    except Exception:
        body = None
    if body is None or not notify_juwen.notify_html(f"daily-report-{day}", subject, body, min_hours=0):
        notify_juwen.notify(f"daily-report-{day}", subject, text.replace("**", ""), min_hours=0)


if __name__ == "__main__":
    main()
