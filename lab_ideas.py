"""Idea bank and lab notebook: what the Millennium Lab tries next, and what it has already learned.

Two data files live in the research repo root (private; this module is only the code):
  lab_ideas.json      idea cards - one concrete, falsifiable approach each, ranked by value x odds / cost
  lab_notebook.jsonl  one line per finished job: method, result, novelty class, verdict

lab.py takes the best open card of a department when it plans a job; lab_pool.py puts that card and the relevant
notebook lines into the worker's prompt. When the result file comes back the pool settles the card (won, back to
open, parked or dead), stores the worker's follow-up ideas as new cards and appends a notebook line. Workers never
edit either file. The Director department reads both and refills the bank.

Quality gate: a worker's follow-up idea is an opinion. Its rating is capped, a re-wording of a card the bank
already has is dropped, and it counts half in the ranking until the Director has reviewed it. A card that names
no verifier or no kill test is marked down, whoever wrote it.

A card only counts as "worked" on evidence: for the packing score that is the scoreboard (best_score.json
"holders"), for frontier cards a result classed extension or new. Reproducing a known value is the gate, not
progress.

Classes: internal (a tool, dataset, audit or fix, or a gain on the lab's own number only), reproduction, extension,
new, negative. Only a card that makes a frontier claim can yield "extension" or "new": on any other card the pool
records such a result as internal and keeps the worker's word next to it (honest_class), so a real record found
on a tool card reaches the Director as a lead instead of being counted as an extension.

The Referee re-runs finished jobs, and its verdict outranks the worker's own claim (apply_referee): the verdict is
written on the job and on its notebook line, a claimed class the re-run does not support is lowered, and a card
that "worked" on such a claim has that win taken back.

Usage: python lab_ideas.py           ranked idea bank
       python lab_ideas.py --notes   the last notebook lines
"""
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IDEAS = ROOT / "lab_ideas.json"
NOTEBOOK = ROOT / "lab_notebook.jsonl"
SQUARES_SCORE = ROOT / "prize_work" / "squares-in-squares" / "best_score.json"
CARD_DEPTS = ("squares", "erdos", "prize", "bsd", "rh")  # departments that work from idea cards
FRONTIER_DEPTS = ("erdos", "prize", "bsd", "rh")  # a card here only works when the result passes the published frontier
OPEN_JOB = ("needs_ai", "running")
PROGRESS = ("extension", "new")
VERDICTS = ("worked", "partial", "failed", "blocked", "no result")  # how a job can leave its card
MAX_OPEN_PER_DEPT = 30
TEXT_FIELDS = {"title": 140, "idea": 1500, "why": 600, "verifier": 400, "kill": 400}
WORKER_ODDS_CAP = 0.35  # a worker rating its own follow-up idea gives an opinion, not evidence
WORKER_CARDS_PER_JOB = 2  # follow-up ideas one worker job may bank: jobs proposed far more than the lab could test
REVIEW_BATCH = 8  # open cards the Director is asked to review per job; at this backlog a worker job banks one idea
UNREVIEWED = 0.5  # ranking factor for a worker idea until the Director has re-scored it
UNTESTABLE = 0.6  # ranking factor for a card that names no verifier or no kill test
NEEDS_LABEL = {"verifier": "verifier", "kill": "kill criterion"}
NEEDS_TEXT = {"verifier": "an independent check that would confirm the result",
              "kill": "a test of 5 minutes or less that could show the idea cannot work"}
STOP_WORDS = frozenset("a an and are as at be by for from in is of on or the to via with".split())


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def slug(text, length=60):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:length]


def _number(value, low, high, default):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ the idea bank
def load():
    """The bank. A file that exists but cannot be read raises, so a broken file is never replaced by an empty one."""
    if not IDEAS.exists():
        return {"cards": []}
    bank = json.loads(IDEAS.read_text(encoding="utf-8"))
    if not isinstance(bank, dict) or not isinstance(bank.get("cards"), list):
        raise ValueError(f"{IDEAS.name}: expected an object with a 'cards' list")
    return bank


def _load_quiet():
    try:
        return load()
    except (OSError, ValueError):
        return {"cards": []}


def swap_in(temp, target):
    """Swap a finished file in whole. Windows refuses the swap while another program has the target open, so try
    again (2026-11-11: a refused swap of the queue and of the pool state each ended a pool tick)."""
    for attempt in range(8):
        try:
            temp.replace(target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.25)


def save(bank):
    """Swap the new bank in whole."""
    temp = IDEAS.with_suffix(".tmp")
    temp.write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    swap_in(temp, IDEAS)


def _find(bank, card_id):
    return next((card for card in bank["cards"] if card_id and card.get("id") == card_id), None)


def get(card_id):
    return _find(_load_quiet(), card_id)


def _missing(card):
    """Which of verifier and kill test the card does not name."""
    return [key for key in NEEDS_LABEL if not str(card.get(key) or "").strip()]


def _unreviewed(card):
    """A worker's follow-up idea that the Director has not re-scored yet."""
    return str(card.get("source", "")).startswith("worker:") and not card.get("reviewed_by")


def _owed(card):
    """The Director still owes this card a review: rewrite it with a verifier and a kill test, or kill it."""
    return bool(_unreviewed(card) or _missing(card))


def priority(card):
    """Expected value per job: value (0-10) x odds (0-1) / cost (jobs). A worker's own rating counts half until
    the Director has reviewed the card, and a card nobody could check (no verifier or no kill test) is marked
    down as well."""
    score = card.get("value", 0) * card.get("odds", 0) / max(0.5, card.get("cost", 1))
    if _unreviewed(card):
        score *= UNREVIEWED
    return score * UNTESTABLE if _missing(card) else score


def ranked(dept=None, statuses=("open",), bank=None):
    cards = [card for card in (bank or _load_quiet())["cards"]
             if card.get("status") in statuses and (dept is None or card.get("dept") == dept)]
    cards.sort(key=lambda card: (-priority(card), card.get("attempts", 0), card.get("created", ""), card["id"]))
    return cards


def next_card(dept, held=(), taken=(), working=()):
    """(card, wait): the department's best open card that no open job holds (`held`: card ids) and whose problem
    nobody is working on (`taken`: topic ids). An idea the Director has not reviewed yet is an opinion, so it may
    fill only one of the department's jobs (`working`: the card ids its open jobs hold). When such ideas are all
    that is left, wait is True: the slot goes to a department with reviewed work, and director_target sends the
    Director here."""
    bank = _load_quiet()
    free = [card for card in ranked(dept, bank=bank) if card["id"] not in held and card.get("topic_id") not in taken]
    if free and any(_owed(card) for card in bank["cards"] if card["id"] in working):
        reviewed = [card for card in free if not _owed(card)]
        return (reviewed[0], False) if reviewed else (None, True)
    return (free[0] if free else None), False


def _record(card, job_id, verdict):
    card.setdefault("history", []).append({"job": job_id, "verdict": verdict, "time": _now()})
    card["history"] = card["history"][-20:]


def claim(card_id, job_id):
    bank = load()
    card = _find(bank, card_id)
    if not card or card.get("status") != "open":
        return False
    card.update({"status": "claimed", "claimed_by": job_id, "claimed": _now()})
    save(bank)
    return True


def release(job):
    """The job died without a result: its card goes back to the bank (parked after two such losses)."""
    bank = load()
    card = _find(bank, job.get("idea_id"))
    if not card or card.get("claimed_by") != job.get("id"):
        return None
    _record(card, job["id"], "no result")
    card["attempts"] = card.get("attempts", 0) + 1
    card["status"] = "parked" if card["attempts"] >= 2 else "open"
    card.pop("claimed_by", None)
    save(bank)
    return card["status"]


def reconcile(jobs):
    """Self-heal: a card still claimed by a job that is no longer open goes back to the bank. Returns the ids.
    A card claimed by hand ("manual:<who>") is left alone until that person settles or releases it, and a claim
    younger than 15 minutes is left alone too (another script may have queued its job a moment ago)."""
    open_ids = {job["id"] for job in jobs if job.get("status") in OPEN_JOB}
    bank = load()
    freed = []
    settled_before = (dt.datetime.now() - dt.timedelta(minutes=15)).isoformat(timespec="seconds")
    for card in bank["cards"]:
        if str(card.get("claimed_by", "")).startswith("manual:"):
            continue
        if (card.get("status") == "claimed" and card.get("claimed_by") not in open_ids
                and str(card.get("claimed", "")) < settled_before):
            card["status"] = "open"
            card.pop("claimed_by", None)
            freed.append(card["id"])
    if freed:
        save(bank)
    return freed


def _new_card(dept, proposal, source, trusted):
    """A card from a proposal. Worker proposals are capped (value 7, odds 0.35, cost at least one job) until
    evidence or the Director raises them."""
    text = {key: str(proposal.get(key, "")).strip()[:size] for key, size in TEXT_FIELDS.items()}
    if not text["title"] or not text["idea"]:
        return None
    card = {
        "id": slug(proposal.get("id") or f"{dept}-{text['title']}"),
        "dept": dept,
        **text,
        "value": _number(proposal.get("value"), 0, 10 if trusted else 7, 4),
        "odds": _number(proposal.get("odds"), 0.01, 0.95 if trusted else WORKER_ODDS_CAP, 0.2),
        "cost": _number(proposal.get("cost"), 0.5 if trusted else 1, 10, 1),
        "repeat": bool(proposal.get("repeat")) if trusted else False,
        "frontier": bool(proposal.get("frontier", dept in FRONTIER_DEPTS)),
        "status": "open",
        "source": source,
        "created": _now(),
        "attempts": 0,
        "runs": 0,
        "history": [],
    }
    if proposal.get("topic_id"):
        card["topic_id"] = str(proposal["topic_id"])[:60]
    return card if card["id"] else None


def _blocked(card):
    """Parked on something outside the lab (a login wall, a missing file). Only a person releases such a card."""
    return card.get("status") == "parked" and str(card.get("parked_reason", "")).lower().startswith("blocked")


def _waiting(card):
    """Parked because a worker could not test it: an input was missing that the lab itself can produce (another
    job's output, a file to fetch). The idea has not failed. The Director releases the card by rewriting it once
    the input exists."""
    return card.get("status") == "parked" and str(card.get("parked_reason", "")).lower().startswith("waiting")


def _copy(card):
    """Killed as a duplicate of another card. That is not a dead end: the card that was kept says what is known."""
    return str(card.get("dead_reason", "")).lower().startswith(("duplicate", "same as"))


def _park_overflow(bank):
    for dept in CARD_DEPTS:
        for card in ranked(dept, bank=bank)[MAX_OPEN_PER_DEPT:]:
            card["status"] = "parked"
            card["parked_reason"] = "bank full: lowest priority"


def _words(title):
    """The title's content words; a plural counts as its singular."""
    words = set()
    for word in re.findall(r"[a-z]+|\d+", str(title).lower()):
        if word in STOP_WORDS or (len(word) < 3 and not word.isdigit()):
            continue
        words.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return words


def same_idea(title, other):
    """A cheap copy test for worker proposals: one title only adds words to the other, or the two share nearly
    all words and exactly the same numbers. It only catches re-wordings; two ideas that mean the same in
    different words are for the Director to merge."""
    mine, theirs = _words(title), _words(other)
    shared = mine & theirs
    if len(shared) >= 4 and shared in (mine, theirs):
        return True
    if len(shared) < 3 or any(word.isdigit() for word in mine ^ theirs):
        return False
    return len(shared) / len(mine | theirs) >= 0.8


def add_cards(dept, proposals, source, trusted=False):
    """Add proposed ideas as open cards. An id that already exists (in any state) is skipped, and so is a worker
    proposal that re-words a card the department already has. A worker job banks the ideas the bank ranks highest:
    WORKER_CARDS_PER_JOB of them, and one while REVIEW_BATCH or more open cards of its department still wait for
    the Director's review. Returns new ids."""
    if dept not in CARD_DEPTS or not isinstance(proposals, list):
        return []
    bank = load()
    fresh = []  # this job's new cards, in the order the worker gave them
    for proposal in proposals[:12 if trusted else 4]:
        card = _new_card(dept, proposal, source, trusted) if isinstance(proposal, dict) else None
        known = bank["cards"] + fresh
        if not card or any(other.get("id") == card["id"] for other in known):
            continue
        if not trusted and any(same_idea(card["title"], other.get("title", ""))
                               for other in known if other.get("dept") == dept):
            continue
        fresh.append(card)
    if not trusted:
        backlog = sum(1 for card in ranked(dept, bank=bank) if _owed(card))
        best = sorted(fresh, key=priority, reverse=True)[:1 if backlog >= REVIEW_BATCH else WORKER_CARDS_PER_JOB]
        fresh = [card for card in fresh if any(card is kept for kept in best)]  # equal ratings keep the worker's order
    if fresh:
        bank["cards"] += fresh
        _park_overflow(bank)
        save(bank)
    return [card["id"] for card in fresh]


def apply_director(job, data):
    """A Director result: 'cards' are added or re-scored, 'kill' entries become dead. Evidence outranks opinion:
    cards that are claimed, won or dead are not touched by 'cards', and a blocked card waits for a person. A
    re-scored card counts as reviewed. Returns (added or updated ids, killed ids)."""
    bank = load()
    source = f"director:{job['id']}"
    changed, killed = [], []
    for proposal in (data.get("cards") or [])[:16]:
        if not isinstance(proposal, dict):
            continue
        dept = proposal.get("dept") or job.get("focus_dept")
        card = _new_card(dept, proposal, source, True) if dept in CARD_DEPTS else None
        if not card:
            continue
        existing = _find(bank, card["id"])
        if not existing:
            bank["cards"].append(card)
        elif existing.get("status") in ("open", "parked") and not _blocked(existing):
            for key in (*TEXT_FIELDS, "value", "odds", "cost", "repeat", "frontier"):
                if card[key] != "":  # a text field the Director left out keeps what the card already says
                    existing[key] = card[key]
            existing.update({"status": "open", "attempts": 0, "reviewed_by": job["id"]})
            for key in ("needs", "parked_reason"):
                existing.pop(key, None)
            _record(existing, job["id"], "re-scored by director")
        else:
            continue
        changed.append(card["id"])
    for entry in (data.get("kill") or [])[:20]:
        card = _find(bank, entry.get("id")) if isinstance(entry, dict) else None
        if card and card.get("status") in ("open", "parked") and not _blocked(card):
            card.update({"status": "dead", "dead_reason": str(entry.get("reason", ""))[:300]})
            _record(card, job["id"], "killed by director")
            killed.append(card["id"])
    if changed or killed:
        _park_overflow(bank)
        save(bank)
    return changed, killed


def verdict_of(job, data, card):
    """worked | partial | failed | blocked. Measured where possible, otherwise the worker's own classification,
    never above what the evidence class allows. blocked: an input was missing, so nobody tested the card.
    A tool or audit card (frontier false) that did its job has worked, also when it found nothing new."""
    stated = str(data.get("idea_verdict", "")).lower()
    novelty = str(data.get("novelty", "")).lower()
    strategy = str(data.get("strategy") or "").strip().lower()
    if stated.strip().startswith("blocked"):
        return "blocked"
    if job.get("dept") == "squares" and strategy:
        try:
            holders = json.loads(SQUARES_SCORE.read_text(encoding="utf-8")).get("holders")
        except (OSError, ValueError):
            holders = None
        if isinstance(holders, dict):  # the scoreboard decides: does this strategy hold the best packing for any n?
            if sum(count for name, count in holders.items() if strategy in name.lower()) > 0:
                return "worked"
            return "failed" if stated == "failed" or novelty == "negative" else "partial"
    frontier = card.get("frontier", job.get("dept") in FRONTIER_DEPTS)
    delivered = stated == "worked" and not frontier  # the audit was done: "no news" is its result, not a failure
    if data.get("status") == "failed" or stated == "failed" or (novelty == "negative" and not delivered):
        return "failed"
    if frontier:
        return "worked" if novelty in PROGRESS and stated != "partial" else "partial"
    return "worked" if stated == "worked" else "partial"


def settle(job, data):
    """Update the job's card from its result. Returns the verdict (None when the job had no card)."""
    bank = load()
    card = _find(bank, job.get("idea_id"))
    if not card:
        return None
    verdict = verdict_of(job, data, card)
    settled = next((entry for entry in card.get("history", []) if entry.get("job") == job["id"]
                    and entry.get("verdict") in ("worked", "partial", "failed", "blocked")), None)
    if settled:  # settled before (the worker rewrote its result file): count it once; the Referee's revert stands
        return settled["verdict"] if settled.get("referee") else verdict
    other_holder = card.get("status") == "claimed" and card.get("claimed_by") not in (None, job["id"])
    _record(card, job["id"], verdict)
    if card.get("claimed_by") == job["id"]:
        card.pop("claimed_by")
    repeat = card.get("repeat")
    if verdict == "blocked":  # nobody tested the idea: odds, attempts and runs stay as they were
        if not other_holder and card.get("status") in ("claimed", "open"):
            reason = " ".join(str(data.get("blocked_by") or "").split())[:200]
            card["status"] = "parked"
            card["parked_reason"] = "waiting: " + (reason or "the worker named no blocker")
    elif verdict == "worked":
        card["runs"] = card.get("runs", 0) + 1
        card["attempts"] = 0
        card["odds"] = round(min(0.9, card.get("odds", 0.2) * 1.25), 3)
        card["status"] = "open" if repeat else "won"
    elif verdict == "partial":
        card["attempts"] = card.get("attempts", 0) + 1
        card["status"] = "parked" if card["attempts"] >= 2 else "open"
    else:
        card["attempts"] = card.get("attempts", 0) + 1
        card["odds"] = round(card.get("odds", 0.2) * (0.7 if repeat else 0.6), 3)
        if repeat:
            card["status"] = "parked" if card["odds"] < 0.08 else "open"
        else:
            card["status"] = "dead" if card["attempts"] >= 2 else "open"
    if other_holder and card["status"] in ("open", "parked"):
        card["status"] = "claimed"  # a late result: another job holds this card now, so it is not issued again
    save(bank)
    return verdict


def honest_class(job, data):
    """A card that makes no frontier claim cannot yield "extension" or "new": such a result is recorded as
    internal and the worker's word is kept in data["claimed_novelty"]. Returns the worker's claim when it was
    lowered, otherwise None. A job without a card is left alone, and a bank that cannot be read raises, so the
    caller can say that the class was not checked."""
    claim = str(data.get("novelty") or "").strip().lower()
    if claim not in PROGRESS:
        return None
    card = _find(load(), job.get("idea_id"))
    if not card or card.get("frontier", job.get("dept") in FRONTIER_DEPTS):
        return None
    data["novelty"], data["claimed_novelty"] = "internal", claim
    return claim


# ------------------------------------------------------------------ the notebook
def note(job, data, verdict=None):
    """Append one notebook line for a finished job."""
    metric = data.get("metric") if isinstance(data.get("metric"), dict) else None
    ideas = data.get("next_ideas") if isinstance(data.get("next_ideas"), list) else []
    files = data.get("files") if isinstance(data.get("files"), list) else []
    entry = {
        "time": _now(),
        "job": job["id"],
        "dept": job.get("dept"),
        "title": str(job.get("title", ""))[:140],
        "topic": job.get("topic_id"),
        "idea_id": job.get("idea_id"),
        "reviews": job.get("reviews") or job.get("focus_dept"),
        "status": data.get("status"),
        "novelty": str(data.get("novelty", ""))[:20] or None,
        "verdict": verdict,
        "method": str(data.get("method", ""))[:300],
        "result": str(data.get("result", ""))[:600],
        "baseline": str(data.get("baseline", ""))[:300],
        "metric": {key: str(value)[:80] for key, value in metric.items()} if metric else None,
        "next_ideas": [str(idea.get("title", ""))[:140] for idea in ideas[:4] if isinstance(idea, dict)],
        "files": [str(path)[:160] for path in files[:8]],
    }
    if data.get("claimed_novelty"):  # the lab lowered the class (honest_class): the worker's word stays visible
        entry["claimed"] = str(data["claimed_novelty"])[:20]
    if job.get("referee"):  # a worker that rewrites its result file does not shake off the Referee's verdict
        entry.update({key: job[key] for key in ("referee", "referee_by", "referee_note") if key in job})
        if job.get("claimed_novelty"):
            entry.update({"novelty": job.get("novelty"), "claimed": job["claimed_novelty"]})
    with NOTEBOOK.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def notes(dept=None):
    """Notebook entries, oldest first. A job that was noted twice keeps its last line."""
    try:
        lines = NOTEBOOK.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    latest, order = {}, []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not entry.get("job"):
            continue
        if entry["job"] not in latest:
            order.append(entry["job"])
        latest[entry["job"]] = entry
    return [latest[job_id] for job_id in order if dept is None or latest[job_id].get("dept") == dept]


# ------------------------------------------------------------------ the Referee's verdict
CLASS_RANK = {"negative": 0, "internal": 1, "reproduction": 1, "extension": 2, "new": 3}
_DOWNGRADE = re.compile(r"\bdowngrad(?:e|ed|es|ing)\b[^.,;:!?]{0,40}?\bto\W+(?:an?\W+)?(internal|reproduction|negative)\b", re.I)
_NOT_BEFORE = re.compile(r"(?:\b(?:no|not|never|without|cannot)\b|n't)"
                         r"(?:\s+(?:need(?:s|ed)?|necessary|reason|to|be|been|a|any))*\s*$", re.I)
_NOT_AFTER = re.compile(r"\s*(?:is|was|would be|seems)?\s*(?:not\b|unnecessary|unwarranted|unjustified)", re.I)


def referee_verdicts(review, job_ids):
    """{job id: (pass | fix | fail, note)} from the "review" field of a Referee result. Referees write it as an
    object keyed by job id or as one string "<job id>: pass - note. <job id>: ...". A job whose verdict word
    cannot be read is left out, so the caller can report it."""
    pieces = {}
    if isinstance(review, dict):
        pieces = {job_id: str(review[job_id]) for job_id in job_ids if job_id in review}
    elif isinstance(review, str):
        marks = sorted((review.index(f"{job_id}:"), job_id) for job_id in job_ids if f"{job_id}:" in review)
        for (start, job_id), (stop, _) in zip(marks, marks[1:] + [(len(review), None)]):
            pieces[job_id] = review[start + len(job_id) + 1:stop]
    verdicts = {}
    for job_id, text in pieces.items():
        match = re.match(r"\W*(pass|fix|fail)[a-z]*", text, re.I)
        if match:
            note = " ".join(text[match.end():].split()).lstrip("-:;,.|\u2013\u2014 ")
            verdicts[job_id] = (match.group(1).lower(), note[:300])
    return verdicts


def _supported_class(word, note, claimed, stated):
    """The class the Referee's re-run supports when that is below the worker's claim, otherwise None. `stated` is
    the Referee's own "supported_class" entry; without one, "downgrade to reproduction" in its note counts unless
    the note negates it. A fail leaves the claim unconfirmed. The Referee never raises a claim."""
    if claimed not in CLASS_RANK:
        return None
    named = re.match(r"\W*(internal|reproduction|extension|new|negative)\b", str(stated or ""), re.I)
    named = named.group(1).lower() if named else None
    if not named:
        for match in _DOWNGRADE.finditer(note):
            if not _NOT_BEFORE.search(note[:match.start()]) and not _NOT_AFTER.match(note[match.end():]):
                named = match.group(1).lower()
                break
    if named and CLASS_RANK[named] < CLASS_RANK[claimed]:
        return named
    return "unconfirmed" if word == "fail" else None


def apply_referee(referee_job, data, jobs):
    """Carry a Referee's verdicts into the record: the reviewed queue jobs, their idea cards and the notebook.
    The Referee re-ran the work, so its verdict outranks the worker's own claim. It can lower a claimed class,
    never raise it, and a card that "worked" on a result the re-run does not support has that win taken back
    (packing cards are left to the scoreboard). `jobs` maps job id to queue job; the caller saves the queue.
    Returns the log lines. Safe to call twice: a verdict that is already on the job is skipped."""
    listed = [job_id for job_id in data.get("reviewed_jobs") or [] if isinstance(job_id, str)]
    targets = [job_id for job_id in dict.fromkeys([*(referee_job.get("reviews") or []), *listed]) if job_id in jobs]
    verdicts = referee_verdicts(data.get("review"), targets)
    stated = data.get("supported_class") if isinstance(data.get("supported_class"), dict) else {}
    lines = [f"{referee_job['id']}: no verdict understood for {job_id}"
             for job_id in targets if job_id in listed and job_id not in verdicts]
    last = {entry["job"]: entry for entry in notes()}
    bank, bank_changed, appended, updates = load(), False, [], []
    for job_id in targets:
        if job_id not in verdicts:
            continue
        word, text = verdicts[job_id]
        job = jobs[job_id]
        fields = {"referee": word, "referee_by": referee_job["id"], "referee_note": text}
        if all(job.get(key) == value for key, value in fields.items()):
            continue
        claimed = str(job.get("claimed_novelty") or job.get("novelty") or "").strip().lower()
        supported = _supported_class(word, text, claimed, stated.get(job_id))
        card = _find(bank, job.get("idea_id"))
        won = next((entry for entry in (card or {}).get("history", [])
                    if entry.get("job") == job_id and entry.get("verdict") == "worked"), None)
        reverted = False
        if won and job.get("dept") != "squares":
            frontier = card.get("frontier", job.get("dept") in FRONTIER_DEPTS)
            if word == "fail" or (frontier and supported and supported not in PROGRESS):
                won.update({"verdict": "partial", "was": "worked", "referee": referee_job["id"]})
                card["runs"] = max(0, card.get("runs", 0) - 1)
                card["odds"] = round(card.get("odds", 0.2) / 1.25, 3)
                card["attempts"] = card.get("attempts", 0) + 1
                if card.get("status") in ("won", "open"):
                    card["status"] = "parked" if card["attempts"] >= 2 else "open"
                reverted = bank_changed = True
        if supported:
            fields.update({"claimed_novelty": claimed, "novelty": supported})
        updates.append((job, fields))
        if job_id in last:
            entry = {**last[job_id], "referee": word, "referee_by": referee_job["id"], "referee_note": text}
            if supported:
                entry.update({"novelty": supported, "claimed": claimed})
            if reverted:
                entry["verdict"] = "partial"
            appended.append(entry)
        lines.append(f"{job_id}: referee {word}" + (f", class {claimed} -> {supported}" if supported else "")
                     + (f", idea {card['id']} worked -> partial" if reverted else ""))
    if bank_changed:
        save(bank)
    if appended:
        with NOTEBOOK.open("a", encoding="utf-8") as handle:
            handle.writelines(json.dumps(entry, ensure_ascii=False) + "\n" for entry in appended)
    for job, fields in updates:  # last, so a job is only marked once the bank and the notebook hold the verdict
        job.update(fields)
    return lines


# ------------------------------------------------------------------ text for the worker prompt
def card_text(card):
    lines = [
        f"IDEA CARD {card['id']} (value {card.get('value', 0):g}/10, odds {card.get('odds', 0):g}, "
        f"cost {card.get('cost', 1):g} job(s), tried {card.get('attempts', 0) + card.get('runs', 0)}x before)",
        f"Title: {card.get('title', '')}",
        f"Idea: {card.get('idea', '')}",
    ]
    for label, key in (("Why it should work", "why"), ("Verifier", "verifier"), ("Kill criterion", "kill")):
        if card.get(key):
            lines.append(f"{label}: {card[key]}")
    missing = _missing(card)
    if missing:
        lines.append(f"This card names no {' and no '.join(NEEDS_LABEL[key] for key in missing)}. Before any other "
                     f"work, write in your notes {' and '.join(NEEDS_TEXT[key] for key in missing)}"
                     + ("; run that test first." if "kill" in missing else "."))
    if card.get("repeat"):
        lines.append("This card is a programme: pick a target nobody in the lab memory has done yet.")
    return "\n".join(lines)


def _note_line(entry, size=280):
    """One notebook line for a prompt. What the Referee found wrong comes before the worker's own account."""
    novelty = entry.get("novelty")
    if novelty and entry.get("claimed"):
        novelty = f"{novelty}, worker claimed {entry['claimed']}"
    referee = f"referee {entry['referee']}" if entry.get("referee") else None
    tags = " / ".join(tag for tag in (novelty, entry.get("verdict") or entry.get("status"), referee) if tag)
    warning = ""
    if entry.get("referee") in ("fix", "fail") and entry.get("referee_note"):
        warning = f"REFEREE: {entry['referee_note'][:170]} | "
    method = f"{entry['method']}: " if entry.get("method") else ""
    result = str(entry.get("result", ""))[:max(120, size - len(warning))]
    return f"- {entry['job']} [{tags or 'no class'}] {warning}{method}{result}"


def memory_text(job, limit=3500):
    """What the lab already knows that matters for this job: notebook lines plus the do-not-repeat list."""
    dept = job.get("dept")
    entries = notes()
    bank = _load_quiet()
    director = dept == "director"
    focus = job.get("focus_dept") if director else dept
    wanted = set(job.get("reviews") or [])
    chosen = {entry["job"] for entry in entries if entry["job"] in wanted}
    proposer = str((_find(bank, job.get("idea_id")) or {}).get("source", "")).partition(":")[2]  # "worker:<job id>"
    related = [entry for entry in entries
               if (job.get("idea_id") and entry.get("idea_id") == job["idea_id"])
               or (job.get("topic_id") and entry.get("topic") == job["topic_id"])
               or entry["job"] == proposer]
    chosen.update(entry["job"] for entry in related[-3:])
    chosen.update(entry["job"] for entry in [e for e in entries if e.get("dept") == focus][-(10 if director else 6):])
    if focus in CARD_DEPTS:  # what the Referee and the Review Panel said about this department's results
        marker = f"lab_{focus}_"
        referee = [entry for entry in entries if entry.get("dept") in ("review", "panel")
                   and marker in f"{entry.get('title', '')} {entry.get('reviews') or ''}"]
        chosen.update(entry["job"] for entry in referee[-2:])
    if director:
        chosen.update(entry["job"] for entry in entries[-6:])
    shown = [entry for entry in entries if entry["job"] in chosen]
    core = wanted | {entry["job"] for entry in related[-3:]}  # lines about this job's own card or review targets
    mine = [card for card in bank["cards"] if card.get("dept") == focus]
    open_cards = ranked(focus, bank=bank)
    extra = []  # most important first: when the text is too long its tail is what gets cut
    if director:
        counts = {}
        for card in bank["cards"]:
            key = (card.get("dept"), card.get("status"))
            counts[key] = counts.get(key, 0) + 1
        extra.append("Idea bank (dept: open / claimed / won / dead / parked): " + "; ".join(
            f"{name} " + " / ".join(str(counts.get((name, status), 0))
                                    for status in ("open", "claimed", "won", "dead", "parked"))
            for name in CARD_DEPTS))
        owed = [card for card in open_cards if _owed(card)]
        if owed:
            extra.append(
                "BANK HYGIENE - do this first. These open cards are worker ideas nobody has reviewed, or they name "
                "no verifier or kill test. Read each one in the idea bank, then either rewrite it in 'cards' under "
                "the same id (your own value, odds and cost, a verifier and a kill test) or list it in 'kill' with a "
                "reason that starts with duplicate, done, blocked or refuted: "
                + "; ".join(f"{card['id']} ({priority(card):.2f})" for card in owed[:REVIEW_BATCH]))
        waiting = [card for card in mine if _waiting(card)][-6:]
        if waiting:
            extra.append(
                "WAITING - parked because an input was missing when a worker tried them; nobody has tested them. "
                "Check whether each input exists now. If it does, rewrite the card in 'cards' under the same id, "
                "which releases it; if it never will, list it in 'kill' with a reason that starts with blocked: "
                + "; ".join(
                    f"{card['id']} ({str(card.get('parked_reason', '')).partition(':')[2].strip()[:140]})"
                    for card in waiting))
        ready = [card for card in open_cards if not _owed(card)][:12]
        if ready:
            extra.append(f"Reviewed open cards for {focus}, best first: " + "; ".join(
                f"{card['id']} ({priority(card):.2f})" for card in ready))
        record = track_record(bank)
        if record:
            extra.append("Track record of settled cards by who wrote them (worked / partial / failed / blocked / "
                         "no result; a blocked card was never tested): "
                         + "; ".join(f"{kind} " + " / ".join(str(tally[verdict]) for verdict in VERDICTS)
                                     for kind, tally in sorted(record.items()))
                         + ". Set odds against this record, not against hope.")
    busy = [card["title"] for card in mine
            if card.get("status") == "claimed" and card.get("claimed_by") != job.get("id")]
    if busy:
        extra.append("Other workers are on these ideas right now - do not duplicate them: " + "; ".join(busy[:6]))
    blocked = [card for card in mine if _blocked(card) or (_waiting(card) and not director)][-4:]
    if blocked:
        extra.append("Blocked - do not retry or re-propose until the blocker is gone: " + "; ".join(
            f"{card['title']} ({str(card.get('parked_reason', ''))[:170]})" for card in blocked))
    dead = [card for card in mine if card.get("status") == "dead" and not _copy(card)][-6:]
    if dead:
        extra.append("Dead ends (do not retry without a new argument): " + "; ".join(
            card["title"] + (f" ({card['dead_reason'][:90]})" if card.get("dead_reason") else "") for card in dead))
    won = [card["title"] for card in mine if card.get("status") == "won"][-6:]
    if won:
        extra.append("Already done: " + "; ".join(won))
    banked = [] if director else [card["title"][:90] for card in open_cards if card["id"] != job.get("idea_id")][:8]
    if banked:
        extra.append("Ideas already in the bank - do not propose them again: " + "; ".join(banked))
    head = ("LAB MEMORY - what the lab already knows (oldest first). Build on it; never repeat it. "
            "A line tagged 'referee pass' was re-run independently; any other line is its worker's own claim. "
            f"Full notebook (read-only): {NOTEBOOK}. Idea bank (read-only): {IDEAS}.")
    if not shown and not extra:
        return f"{head}\n(nothing recorded for this department yet)"

    def text(width=None):
        return "\n".join([head, *(_note_line(entry)[:width] for entry in shown), *extra])

    while len(shown) > 2 and len(text()) > limit:  # too long: the oldest line that is not about this job goes first
        shown.remove(next((entry for entry in shown if entry["job"] not in core), shown[0]))
    room = limit + 400
    while shown and len(text()) > room:  # still too long: notebook lines give way, the do-not-repeat lists stay whole
        width = (room - len(text(0))) // len(shown)
        if width >= 200:
            return text(width)[:room]
        shown.remove(next((entry for entry in shown if entry["job"] not in core), shown[0]))
    return text()[:room]


def director_target(config, avoid=None):
    """(department id, its open cards): the card department with the fewest days of ready ideas left. A programme
    card (repeat) feeds about five jobs, any other card one; a card that still waits for the Director's review
    feeds none, so a department that is full of unreviewed worker ideas gets a Director as well. `avoid` is the
    department the Director refilled last time, so one hungry department cannot take every Director job."""
    rows = []
    for dept in config.get("departments", []):
        if dept.get("id") not in CARD_DEPTS or dept.get("paused"):
            continue
        cards = ranked(dept["id"])
        supply = sum(5 if card.get("repeat") else 1 for card in cards if not _owed(card))
        jobs_per_day = dept.get("slots", 1) * 24 / max(2, dept.get("every_hours", 2))
        rows.append((supply / jobs_per_day, dept["id"], len(cards)))
    if not rows:
        return None, 0
    rows.sort()
    choice = next((row for row in rows if row[1] != avoid), rows[0])
    return choice[1], choice[2]


def track_record(bank):
    """Settled verdicts by who wrote the card (seed, director, worker): {"seed": {"worked": 2, ...}, ...}."""
    record = {}
    for card in bank["cards"]:
        kind = str(card.get("source", "?")).partition(":")[0]
        for entry in card.get("history", []):
            if entry.get("verdict") in VERDICTS:
                record.setdefault(kind, dict.fromkeys(VERDICTS, 0))[entry["verdict"]] += 1
    return record


def summary():
    """Counts for the lab page: cards by status and notebook lines."""
    counts = {}
    for card in _load_quiet()["cards"]:
        counts[card.get("status", "?")] = counts.get(card.get("status", "?"), 0) + 1
    return {"cards": counts, "notes": len(notes())}


def main():
    if "--notes" in sys.argv:
        for entry in notes()[-15:]:
            print(_note_line(entry, 200))
        return
    bank = _load_quiet()
    for dept in CARD_DEPTS:
        cards = [card for card in bank["cards"] if card.get("dept") == dept]
        if not cards:
            continue
        print(f"{dept}: {len(cards)} cards")
        for card in sorted(cards, key=lambda card: (card.get("status") != "open", -priority(card))):
            print(f"  {priority(card):5.2f}  {card.get('status', '?'):8} {card['id'][:44]:44} "
                  f"{card.get('title', '')[:60]}")
    print(f"notebook: {len(notes())} lines")


if __name__ == "__main__":
    main()
