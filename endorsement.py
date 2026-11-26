"""arXiv endorsement requests as a standing step: one person at a time, through the outreach queue.

arXiv asks a first-time submitter to a category for an endorsement and tells them to forward a code to
somebody who may endorse. This module does that forwarding under the lab's outreach rules. It never sends
anything itself: it writes ONE spec into outbox/outreach/ when the rules below allow it, and the daily
outreach queue (outreach.run_queue) sends that spec only if every rule of outreach_policy.json passes.

A "need" is a private JSON file in outbox/endorsement/ (outside the public repo), written by an AI session
that has read arXiv's own pages: the category, the code, the paper, and a short list of candidates. A
candidate is on the list only if arXiv's "which authors can endorse" page names them for the category, the
paper cites their work, and their address is printed in public.

Rules (numbers in outreach_policy.json under endorsement_requests):
  * one request at a time; the next person is asked only after `days_between_requests` days of silence
    (or at once when the last person declined);
  * at most `max_people_per_need` people for one need; then it stops and tells Juwen;
  * the moment anybody answers (on this topic or on one the need names), nothing more is written: an AI
    session reads the answer first (a needs_ai job is queued once);
  * a later request says how many people were asked before it.

Commands
  python endorsement.py status           what was asked, what happens next; changes nothing
  python endorsement.py tick             the daily step (also run by followup_drafter.py before the queue)
  python endorsement.py endorsed <id>    arXiv has the endorsement: stop asking, reopen the upload job
  python endorsement.py stop <id>        stop asking for this need (for example the paper was withdrawn)
  python endorsement.py read <id>        an AI session has read the answers that paused the need: go on

status.json next to the needs is a summary without names or addresses, for dashboards.
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEEDS_DIR = HERE.parent / "outbox" / "endorsement"
QUEUE_DIR = HERE.parent / "outbox" / "outreach"
LEDGER_FILE = HERE / "contacts_log.json"
POLICY_FILE = HERE / "outreach_policy.json"
RESEARCH_QUEUE = HERE.parent / "research_queue.json"
STATUS_FILE_NAME = "status.json"
DEFAULT_RULES = {"days_between_requests": 7, "max_people_per_need": 3, "pause_when_anyone_replies": True}
ANSWERED = ("replied", "answered")
REFUSED = ("declined", "do-not-contact")
REQUIRED_KEYS = ("id", "category", "code", "endorse_url", "topic", "subject", "body", "evidence", "candidates")


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, data, indent=1):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def rules():
    policy = _read(POLICY_FILE, {})
    return {**DEFAULT_RULES, **{key: value for key, value in (policy.get("endorsement_requests") or {}).items()
                                if not key.startswith("_")}}


def load_needs():
    """Every readable need file, oldest name first. A file without the required keys is skipped."""
    needs = []
    for path in sorted(NEEDS_DIR.glob("*.json")):
        if path.name == STATUS_FILE_NAME:
            continue
        need = _read(path, None)
        if isinstance(need, dict) and all(key in need for key in REQUIRED_KEYS):
            needs.append((path, need))
    return needs


def requests_so_far(need, ledger):
    """Ledger entries that are endorsement requests of this need (first mails only), oldest first."""
    addresses = {candidate["to"].strip().lower() for candidate in need["candidates"]}
    return [entry for entry in ledger.get("contacts", [])
            if entry.get("topic") == need["topic"] and entry.get("to") in addresses and not entry.get("follow_up")]


def answers(need, ledger):
    """People who answered on this need's topic or on a topic the need names (for example the same result)."""
    topics = {need["topic"], *need.get("pause_on_reply_topics", [])}
    read = set(need.get("answers_read", []))
    return [entry for entry in ledger.get("contacts", [])
            if entry.get("topic") in topics and entry.get("state") in ANSWERED and answer_key(entry) not in read]


def answer_key(entry):
    return f"{entry['to']}|{entry['date']}"


def waiting_spec(need):
    """The spec of this need that is in the outreach queue and has not gone out, if there is one."""
    for path in sorted(QUEUE_DIR.glob("*.json")):
        spec = _read(path, None)
        if not isinstance(spec, dict) or spec.get("_endorsement_need") != need["id"]:
            continue
        if not (spec.get("_sent") or spec.get("_drafted") or spec.get("_withdrawn")):
            return path
    return None


def next_candidate(need, ledger):
    asked = {entry["to"] for entry in ledger.get("contacts", []) if entry.get("topic") == need["topic"]}
    for candidate in need["candidates"]:
        if candidate["to"].strip().lower() not in asked:
            return candidate
    return None


def body_for(need, candidate, earlier):
    """The need's body with this person's greeting and reason. From the second request on it says, in one
    sentence, how many people were asked before, so nobody is asked as if they were the first."""
    asked_before = ""
    if earlier:
        last = max(entry.get("sent_date") or entry["date"] for entry in earlier)
        people = "one other researcher" if len(earlier) == 1 else f"{len(earlier)} other researchers"
        asked_before = (f" I asked {people} for this endorsement before you, last on {last}, "
                        "and have had no answer.")
    return (need["body"]
            .replace("{salutation}", candidate["salutation"])
            .replace("{why_you}", candidate["why_you"])
            .replace("{asked_before}", asked_before)
            .replace("{code}", need["code"])
            .replace("{endorse_url}", need["endorse_url"]))


def spec_for(need, candidate, earlier, today):
    number = len(earlier) + 1
    slug = re.sub(r"[^a-z0-9]+", "-", candidate["to"].split("@")[0].lower()).strip("-")
    name = f"{today.isoformat()}-{need['id']}-{number}-{slug}.json"
    spec = {
        "_status": f"waiting: written {today.isoformat()} by endorsement.py (request {number} of this need)",
        "_endorsement_need": need["id"],
        "_expert_entry": {candidate["to"].strip().lower(): {
            "name": candidate["name"],
            "topics": [f"arXiv endorsement {need['category']}"],
            "email_source": candidate["email_source"],
            "their_work": candidate["their_work"],
        }},
        "to": candidate["to"].strip().lower(),
        "topic": need["topic"],
        "subject": need["subject"].replace("{code}", need["code"]),
        "body": body_for(need, candidate, earlier),
        "evidence": need["evidence"],
    }
    return name, spec


def _queue_job(job):
    """Adds one job to the research queue unless a job with that id is there. Returns True when added."""
    queue = _read(RESEARCH_QUEUE, None)
    if not isinstance(queue, dict):
        return False
    if any(existing.get("id") == job["id"] for existing in queue.get("jobs", [])):
        return False
    queue.setdefault("jobs", []).append(job)
    _write(RESEARCH_QUEUE, queue, indent=2)
    return True


def _tell_juwen(key, subject, text):
    try:
        import notify_juwen

        return notify_juwen.notify(key, subject, text, min_hours=24 * 30)
    except Exception:  # a notice must never break the daily step
        return False


def decide(need, ledger, today, limits):
    """What the daily step would do for one need. Returns (action, plain sentence, detail).
    Actions: done, paused, waiting_in_queue, stop, wait, ask."""
    if need.get("status") in ("endorsed", "stopped"):
        return "done", f"{need['id']}: {need['status']}, nothing to do", None
    earlier = requests_so_far(need, ledger)
    if limits.get("pause_when_anyone_replies", True):
        answered = answers(need, ledger)
        if answered:
            return "paused", (f"{need['id']}: somebody answered ({len(answered)}); no new request until an AI "
                              "session has read the answer"), answered
    queued = waiting_spec(need)
    if queued:
        return "waiting_in_queue", f"{need['id']}: {queued.name} is in the outreach queue", queued
    candidate = next_candidate(need, ledger)
    if len(earlier) >= limits["max_people_per_need"] or candidate is None:
        reason = (f"{len(earlier)} people were asked (limit {limits['max_people_per_need']})"
                  if len(earlier) >= limits["max_people_per_need"] else "nobody is left on the list")
        return "stop", f"{need['id']}: {reason}; stopping", reason
    if earlier:
        last = earlier[-1]
        if last.get("state") not in REFUSED:
            asked_on = dt.date.fromisoformat(last.get("sent_date") or last["date"])
            next_day = asked_on + dt.timedelta(days=limits["days_between_requests"])
            if today < next_day:
                return "wait", f"{need['id']}: request {len(earlier)} went out {asked_on}; next not before {next_day}", next_day
    return "ask", f"{need['id']}: request {len(earlier) + 1} is due", candidate


def tick(today=None, status_only=False):
    """The daily step. Writes at most one spec per need; the outreach queue sends it under its own rules."""
    today = today or dt.date.today()
    limits = rules()
    ledger = _read(LEDGER_FILE, {"contacts": []})
    lines = []
    for path, need in load_needs():
        action, line, detail = decide(need, ledger, today, limits)
        lines.append(line)
        if status_only:
            continue
        if action == "paused" and not need.get("answer_job"):
            job_id = f"endorsement_answer_{re.sub(r'[^a-z0-9]+', '_', need['id'].lower())}"
            _queue_job({
                "id": job_id,
                "title": f"arXiv endorsement {need['category']}: somebody answered; read it before anything else goes out",
                "project": "outreach",
                "status": "needs_ai",
                "why": (
                    f"endorsement.py paused the need {need['id']} (outbox/endorsement/{path.name}) because a researcher "
                    "answered. Read the answer (gmail_inbox, contacts_log.json). If it says the endorsement was given: "
                    f"`python endorsement.py endorsed {need['id']}`. If it finds an error in the paper: "
                    f"`python endorsement.py stop {need['id']}` and queue the fix; ask nobody else until the paper is "
                    "right. If it is a no, or an answer about something else: `python outreach.py mark <address> "
                    "declined` where that is what was said, make sure the need's body is still true (it says who was "
                    f"asked and what is unchecked), then `python endorsement.py read {need['id']}` so the daily step goes "
                    "on. Answer the researcher under the outreach rules."
                ),
            })
            need["answer_job"] = job_id
            _write(path, need)
        elif action == "stop":
            need["status"] = "stopped"
            need["stopped"] = {"date": today.isoformat(), "reason": detail}
            _write(path, need)
            _tell_juwen(
                f"endorsement_stopped_{need['id']}",
                f"arXiv endorsement for {need['category']}: nobody endorsed",
                f"The lab asked for an arXiv endorsement for {need['category']} and stopped: {detail}.\n\n"
                "What helps most: a mathematician you know who posts on arXiv. Forward them arXiv's e-mail with the "
                f"code {need['code']}, or open Vector and say who to ask.",
            )
        elif action == "ask":
            earlier = requests_so_far(need, ledger)
            name, spec = spec_for(need, detail, earlier, today)
            QUEUE_DIR.mkdir(parents=True, exist_ok=True)
            _write(QUEUE_DIR / name, spec)
            lines[-1] = f"{need['id']}: wrote {name}; the outreach queue sends it if every rule passes"
    if not status_only:
        write_status(today)
    return lines


def mark(need_id, status, today=None):
    """endorsed or stopped by hand. Waiting specs of the need are withdrawn so nothing more goes out."""
    today = today or dt.date.today()
    for path, need in load_needs():
        if need["id"] != need_id:
            continue
        need["status"] = status
        need[status] = {"date": today.isoformat()}
        _write(path, need)
        while True:
            queued = waiting_spec(need)
            if not queued:
                break
            spec = _read(queued, {})
            spec["_withdrawn"] = today.isoformat()
            spec["_status"] = f"WITHDRAWN {today.isoformat()}: the need is {status}"
            _write(queued, spec)
        if status == "endorsed":
            reopen_upload_job(need, today)
            _tell_juwen(
                f"endorsement_given_{need_id}",
                f"arXiv endorsement for {need['category']} is given",
                "arXiv has the endorsement. The upload needs a session that can use your browser: "
                "log in to arXiv in Edge, open Vector and type: go",
            )
        write_status(today)
        return True
    return False


def mark_answers_read(need_id, today=None):
    """An AI session has read the answers: the daily step may go on. Returns how many answers were marked."""
    ledger = _read(LEDGER_FILE, {"contacts": []})
    for path, need in load_needs():
        if need["id"] != need_id:
            continue
        unread = answers(need, ledger)
        need["answers_read"] = sorted(set(need.get("answers_read", [])) | {answer_key(entry) for entry in unread})
        need.pop("answer_job", None)
        _write(path, need)
        write_status(today)
        return len(unread)
    return None


def reopen_upload_job(need, today):
    """The parked upload job named by the need becomes due today, so the next AI session takes it."""
    job_id = need.get("queue_job")
    queue = _read(RESEARCH_QUEUE, None)
    if not job_id or not isinstance(queue, dict):
        return False
    for job in queue.get("jobs", []):
        if job.get("id") == job_id and job.get("status") != "done":
            job["status"] = "needs_ai"
            job["not_before"] = today.isoformat()
            job.pop("bridged", None)
            job.pop("bridged_via", None)
            job["why"] = (f"ENDORSED on {today.isoformat()} (endorsement.py): the arXiv upload can go on; it needs a "
                          "session that can drive the browser. " + job.get("why", ""))
            _write(RESEARCH_QUEUE, queue, indent=2)
            return True
    return False


def summary(today=None):
    """What dashboards show: counts and dates, no names and no addresses."""
    today = today or dt.date.today()
    limits = rules()
    policy = _read(POLICY_FILE, {})
    ledger = _read(LEDGER_FILE, {"contacts": []})
    needs = []
    for _, need in load_needs():
        action, line, detail = decide(need, ledger, today, limits)
        earlier = requests_so_far(need, ledger)
        asked_addresses = {entry["to"] for entry in earlier}
        needs.append({
            "id": need["id"],
            "category": need["category"],
            "paper": need.get("paper_title", ""),
            "paper_url": need.get("paper_url", ""),
            "status": need.get("status", "asking"),
            "action": action,
            "line": plain_line(need, action, earlier, detail),
            "asked": len(earlier),
            "max_people": limits["max_people_per_need"],
            "on_the_list": len(need["candidates"]),
            "left_on_the_list": len([c for c in need["candidates"] if c["to"].strip().lower() not in asked_addresses]),
            "requests": [{"number": index + 1, "date": entry.get("sent_date") or entry["date"], "state": entry["state"]}
                         for index, entry in enumerate(earlier)],
            "next_not_before": detail.isoformat() if action == "wait" else None,
            "draft_expires": need.get("draft_expires"),
            "days_between_requests": limits["days_between_requests"],
        })
    first_mails = [entry for entry in ledger.get("contacts", []) if not entry.get("follow_up")]
    age = lambda entry: (today - dt.date.fromisoformat(entry["date"])).days  # noqa: E731
    return {
        "updated": dt.datetime.now().isoformat(timespec="seconds"),
        "needs": needs,
        "outreach": {
            "new_7_days": len([entry for entry in first_mails if age(entry) < 7]),
            "limit_7_days": policy.get("max_new_contacts_per_7_days"),
            "new_30_days": len([entry for entry in first_mails if age(entry) < 30]),
            "limit_30_days": policy.get("max_new_drafts_per_30_days"),
            "answered": len([entry for entry in first_mails if entry.get("state") in ANSWERED]),
            "people": len({entry["to"] for entry in first_mails}),
        },
    }


def plain_line(need, action, earlier, detail):
    """One sentence for a dashboard. It never carries a name, an address, a file name or the code."""
    if need.get("status") == "endorsed":
        return "arXiv has the endorsement. The upload is the next step."
    if need.get("status") == "stopped" or action == "stop":
        return f"Stopped after {len(earlier)} request(s) without an endorsement. Somebody you know is the next best way."
    if action == "paused":
        return "Somebody answered. Nothing more goes out until the answer has been read."
    if action == "waiting_in_queue":
        return f"Request {len(earlier) + 1} is written and waits for the daily outreach run."
    if action == "wait":
        last = earlier[-1].get("sent_date") or earlier[-1]["date"]
        return f"Request {len(earlier)} went out on {last}. With no answer, the next person is asked on {detail.isoformat()} or later."
    return f"Request {len(earlier) + 1} is due at the next daily run."


def write_status(today=None):
    if not NEEDS_DIR.exists():
        return None
    data = summary(today)
    _write(NEEDS_DIR / STATUS_FILE_NAME, data)
    return data


def main(arguments):
    command = arguments[0] if arguments else "status"
    if command == "status":
        lines = tick(status_only=True)
    elif command == "tick":
        lines = tick()
    elif command in ("endorsed", "stop") and len(arguments) == 2:
        status = "endorsed" if command == "endorsed" else "stopped"
        lines = [f"{arguments[1]}: {status}" if mark(arguments[1], status) else f"no need with id {arguments[1]}"]
    elif command == "read" and len(arguments) == 2:
        marked = mark_answers_read(arguments[1])
        lines = [f"no need with id {arguments[1]}" if marked is None else f"{arguments[1]}: {marked} answer(s) marked as read"]
    else:
        print(__doc__)
        return 1
    for line in lines or ["no endorsement need is on file"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
