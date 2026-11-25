"""Publishes reviewed lab notes to the public notes repo, so the monthly release there picks them up.

The lab writes public-ready notes to outbox/public/. This script copies a note to the public repo only
when every gate below holds; everything else stays where it is and is listed with the reason.

    python publish_notes.py              publish what passes (scheduled once a day)
    python publish_notes.py --dry-run    print the decision for every note, change nothing

Gates (all must hold):
  1. a dated research note (YYYY-MM-DD-name.md), not an audit, correction or competition note
  2. the note's header names at least one research job, and every one named is useful
     (lab_useful: Referee pass + Review Panel pass) and none is an internal check;
     or the header has a line "Flagship: F4, F5" and every lemma named is proved in the flagship
     ledger with Lean files whose bytes today are the bytes the Lean gate marked `checked`
  3. nothing in the note says it must wait (panel FIX, "do not post", superseded, retracted)
  4. it has rested for `cooloff_days` and no later correction note exists for the same problem
  5. it is not on the private hold list (outbox/public_decisions.json, "hold": {note: reason})
  6. the note, the referee reports and the scripts that go with it contain nothing private

What goes up: the note with the standing "AI lab, no human review" banner, the referee reports of the
jobs it names, the tracked scripts and outputs of its gate folder, and one README row. A note that rests
on flagship lemmas only gets the Lean banner instead, and its Lean files go up in place of a gate folder.
Published notes are never overwritten here; corrections are made by hand in the public repo.
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import flagship
import lab_facts
import lab_useful
import lean_gate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUTBOX = ROOT / "outbox" / "public"
DECISIONS = ROOT / "outbox" / "public_decisions.json"
LOG = HERE / "logs" / "publish_notes.log"
USEFUL_INDEX = Path.home() / "Documents" / "Obsidian Vault" / "notes" / "millennium-lab-useful-results.md"
DEFAULTS = {"enabled": True, "repo": "../../open-problems-lab", "cooloff_days": 3, "max_per_run": 3,
            "proxy_fallback": "http://localhost:3128"}

DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9]+)[a-z0-9-]*\.md$")
NOT_RESEARCH = re.compile(r"audit|correction|retraction|erratum|kaggle|squares|competition")
MUST_WAIT = re.compile(r"panel\s+FIX|do not post|must not be posted|superseded|retracted", re.I)
JOB_ID = re.compile(r"lab_([a-z]+)_[0-9a-f]{6,}_\d+")
HELPERS = ("panel", "writer", "director", "referee", "review")  # jobs that judge or write, not research
FLAGSHIP_LINE = re.compile(r"^Flagship:(.*)$", re.M)
LEMMA_ID = re.compile(r"\bF\d+(?:\.\d+)*\b")
LEAN_KIND = "Machine-checked proof (Lean 4)"
GATE_DIR = re.compile(r"prize_work/([A-Za-z0-9_.\-]+)/")
SCRIPT_TYPES = {".py", ".json", ".md", ".txt", ".csv", ".lean", ".tex", ".out", ".log", ".cert"}
FILE_LIMIT, GATE_LIMIT, GATE_FILES = 400_000, 4_000_000, 60
KINDS = [("negative", "**Negative result**"), ("new", "New computation"), ("extension", "Extension"),
         ("reproduction", "Reproduction")]
PRIVATE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"      # e-mail address
    r"|[A-Za-z]:[\\/]+(?:Users|Projects)[\\/]"            # a path on this PC (any slash style)
    r"|\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}|\bsk-[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}"
    r"|password\s*[:=]|api[_ ]?key\s*[:=]", re.I)
BANNER = """> **Status (published {day}).** This note comes from an AI-agent research lab run by
> Juwen Lie. The numbers were computed and re-run by AI agents (scripts plus an independent
> AI referee re-run). **No human mathematician has reviewed it**, and it has not been posted
> to the problem's own site. "I" below means the lab. Statements such as "first" or "not
> published" reflect the lab's literature search on the note's date and may be wrong.
> Nothing here is a proof or a solution. Files under `review_panel/` are simulated
> adversarial reviews and are not part of this repository. Corrections are welcome: please
> open an issue."""
BANNER_LEAN = """> **Status (published {day}).** This note comes from an AI-agent research lab run by
> Juwen Lie. The proof was written by an AI agent and checked by the Lean 4 kernel with
> Mathlib (standard axioms only); the Lean files are in this repository under `prize_work/`.
> The kernel checks the proof, not whether the formal statement says what this note says:
> please read the definitions. **No human mathematician has reviewed it**, the lab does not
> know whether the result is new, and it has not been posted to the problem's own site.
> "I" below means the lab. Nothing here settles the open problem itself. Corrections and
> references are welcome: please open an issue."""


def log(text):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {text}\n")


def config():
    try:
        saved = json.loads((HERE / "lab_departments.json").read_text(encoding="utf-8")).get("publish", {})
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{key: value for key, value in saved.items() if not key.startswith("_")}}


def decisions():
    try:
        data = json.loads(DECISIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"hold": dict(data.get("hold") or {}), "published": dict(data.get("published") or {})}


def private_words():
    """Names of the people the lab may write to: they never belong in a public file."""
    try:
        experts = json.loads((HERE / "experts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    names = [str(entry.get("name") or "") for entry in experts.values() if isinstance(entry, dict)]
    return [name.strip().lower() for name in names if len(name.strip()) >= 6]


def private_hits(content, words=()):
    """What makes a text unfit for a public repo (empty list = clean)."""
    hits = [match.group(0)[:40] for match in PRIVATE.finditer(content)]
    low = content.lower()
    return hits[:5] + [word for word in words if word in low][:5]


def research_jobs(note_text):
    """Ids of the research jobs named in the note's header (the first 16 lines)."""
    header = "\n".join(note_text.splitlines()[:16])
    return sorted({match.group(0) for match in JOB_ID.finditer(header) if match.group(1) not in HELPERS})


def flagship_lemmas(note_text):
    """Ids of the flagship lemmas named on the header's "Flagship:" line (the first 16 lines)."""
    header = "\n".join(note_text.splitlines()[:16])
    return sorted({lemma_id for line in FLAGSHIP_LINE.findall(header) for lemma_id in LEMMA_ID.findall(line)})


def lean_state():
    """The flagship ledger's lemmas by id and the Lean gate's cache, read from this lab."""
    ledger = lean_gate.read_json(ROOT / flagship.LEDGER.relative_to(flagship.ROOT), {})
    cache = lean_gate.read_json(ROOT / "prize_work" / "lean" / "claim_cache.json", {})
    return {"lemmas": {lemma.get("id"): lemma for lemma in ledger.get("lemmas", [])}, "cache": cache}


def lemma_files(lemma, cache, words=()):
    """(Lean files of a lemma as paths inside the lab, None) when it may go up as machine-checked, else
    (None, reason). Stricter than the ledger: only `checked` counts (no compiler trust), on today's bytes."""
    if lemma is None:
        return None, "not in the ledger"
    if lemma.get("status") != "proved":
        return None, "not proved in the ledger"
    files = []
    for claim in lemma.get("lean_claims") or []:
        key = lean_gate.relative(claim)
        try:
            data = (ROOT / "prize_work" / key).read_bytes() if key else None
        except OSError:
            data = None
        if data is None:
            return None, f"Lean file missing ({claim})"
        entry = (cache.get("claims") or {}).get(key) or {}
        if entry.get("sha") != hashlib.sha256(data).hexdigest():
            return None, f"{Path(key).name} changed since the Lean gate checked it"
        if entry.get("verdict") != "checked":
            return None, f"{Path(key).name}: Lean verdict is {entry.get('verdict')}, not checked"
        if len(data) > FILE_LIMIT or private_hits(data.decode("utf-8", errors="replace"), words):
            return None, f"{Path(key).name} is too large or has private text"
        files.append(f"prize_work/{key}")
    return (files, None) if files else (None, "no Lean file in the ledger")


def body_lines(note_text):
    """The note without its addressee line (a word to the reader in the lab, never part of the public text)."""
    return [line for index, line in enumerate(note_text.splitlines()) if not (index < 10 and line.startswith("To:"))]


def public_text(note_text, day, banner=BANNER):
    """The note as it goes up: no addressee line, the standing banner under the header."""
    lines = body_lines(note_text)
    spot = next((index for index, line in enumerate(lines) if index >= 2 and not line.strip()), len(lines))
    lines[spot:spot] = ["", banner.format(day=day)]
    return "\n".join(lines).rstrip("\n") + "\n"


def verdict(path, jobs_by_id, useful, held, published_names, outbox_files, cooloff_days, now, words=(), lean=None):
    """(True, info) when the note may go up, else (False, [reasons]). `lean` = lean_state()."""
    name = path.name
    match = DATED.match(name)
    if name in published_names:
        return False, ["already published"]
    if name in held:
        return False, [f"on the hold list: {held[name]}"]
    if not match:
        return False, ["not a dated research note"]
    if NOT_RESEARCH.search(name):
        return False, ["audit, correction or competition note"]
    reasons = []
    content = path.read_text(encoding="utf-8", errors="replace")
    waiting = MUST_WAIT.search("\n".join(body_lines(content)))
    if waiting:
        reasons.append(f"the note says it must wait ({waiting.group(0).lower()!r})")
    named = research_jobs(content)
    lemmas = flagship_lemmas(content)
    if not named and not lemmas:
        reasons.append("no research job named in the header")
    lean_files = []
    for lemma_id in lemmas:
        files, why = lemma_files(((lean or {}).get("lemmas") or {}).get(lemma_id), (lean or {}).get("cache") or {}, words)
        if why:
            reasons.append(f"flagship {lemma_id}: {why}")
        else:
            lean_files += [name for name in files if name not in lean_files]
    for job_id in named:
        job = jobs_by_id.get(job_id)
        if job is None:
            reasons.append(f"{job_id}: not in the queue")
        elif job_id not in useful:
            reasons.append(f"{job_id}: not Referee pass + Review Panel pass")
        elif lab_useful._first_word(job.get("novelty")) == "internal":
            reasons.append(f"{job_id}: an internal check")
    age_days = (now - dt.datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 86400
    if age_days < cooloff_days:
        reasons.append(f"resting: {age_days:.1f} of {cooloff_days} days")
    problem = match.group(2)
    for other in outbox_files:
        if (other.name != name and problem in other.name.lower() and re.search(r"correction|retraction|erratum", other.name)
                and other.stat().st_mtime >= path.stat().st_mtime - 86400):
            reasons.append(f"a later correction exists: {other.name}")
    lean_only = bool(lemmas) and not named  # no referee job: the Lean kernel is the judge, and the banner says so
    leaks = private_hits(public_text(content, now.date().isoformat()), words)
    if leaks:
        reasons.append(f"private text in the note ({len(leaks)} place(s))")
    if reasons:
        return False, reasons
    novelties = {lab_useful._first_word(jobs_by_id[job_id].get("novelty")) for job_id in named}
    kind = LEAN_KIND if lean_only else next((label for key, label in KINDS if key in novelties), "Computation")
    title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ")), name)
    number = re.fullmatch(r"erdos(\d+)", problem)
    return True, {"jobs": named, "kind": kind, "title": title, "content": content,
                  "problem": f"Erdős #{number.group(1)}" if number else "-",
                  "gates": [] if lean_only else sorted(set(GATE_DIR.findall(content))),
                  "lemmas": lemmas, "lean_files": lean_files, "banner": BANNER_LEAN if lean_only else BANNER}


def review():
    """[(path, ok, info or reasons)] for every note in the outbox, plus the settings used."""
    settings = config()
    repo = (HERE / settings["repo"]).resolve()
    try:
        jobs = json.loads(lab_useful.QUEUE.read_text(encoding="utf-8")).get("jobs", [])
    except (OSError, ValueError):
        jobs = []
    saved = decisions()
    public = {path.name for path in (repo / "notes").glob("*.md")} | set(saved["published"])
    files = sorted(OUTBOX.glob("*.md"))
    words = private_words()
    now = dt.datetime.now()
    lean = lean_state()
    rows = [(path, *verdict(path, {str(job.get("id")): job for job in jobs}, lab_useful.useful_ids(lab_useful.QUEUE), saved["hold"],
                            public, files, settings["cooloff_days"], now, words, lean)) for path in files]
    return rows, settings, repo, words


def git(folder, *args, timeout=120, proxy=None):
    command = ["git", *(["-c", f"http.proxy={proxy}"] if proxy else []), *args]
    return subprocess.run(command, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def online(folder, settings, *args):
    """A git call that talks to GitHub: the normal route first, then the fallback proxy."""
    result = git(folder, *args)
    if result.returncode and settings.get("proxy_fallback"):
        result = git(folder, *args, proxy=settings["proxy_fallback"])
    return result


def gate_files(gate, words):
    """Tracked, small, clean text files of one gate folder: [(relative path, bytes)]."""
    listed = git(ROOT, "ls-files", "-z", "--", f"prize_work/{gate}")
    if listed.returncode:
        return []
    chosen, total = [], 0
    for relative in sorted(filter(None, listed.stdout.split("\0"))):
        source = ROOT / relative
        parts = Path(relative).parts
        if any("scratch" in part.lower() for part in parts) or source.suffix.lower() not in SCRIPT_TYPES:
            continue
        try:
            data = source.read_bytes()
        except OSError:
            continue
        if len(data) > FILE_LIMIT or total + len(data) > GATE_LIMIT or len(chosen) >= GATE_FILES:
            continue
        if private_hits(data.decode("utf-8", errors="replace"), words):
            continue
        chosen.append((relative, data))
        total += len(data)
    return chosen


def add_readme_row(readme, row):
    """Appends one row to the notes table (the table whose header starts with | Note |)."""
    lines = readme.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("| Note |")), None)
    if start is None:
        return readme
    end = start
    while end + 1 < len(lines) and lines[end + 1].startswith("|"):
        end += 1
    lines.insert(end + 1, row)
    return "\n".join(lines) + "\n"


def stage(tree, path, info, day, words):
    """Writes one note and what belongs to it into the work tree. Returns the files written."""
    written = []
    target = tree / "notes" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(public_text(info["content"], day, info.get("banner", BANNER)), encoding="utf-8", newline="\n")
    written.append(f"notes/{path.name}")
    for job_id in info["jobs"]:
        report = ROOT / "review_notes" / f"{job_id}.md"
        public_report = tree / "review_notes" / report.name
        if report.exists() and not public_report.exists():
            body = report.read_text(encoding="utf-8", errors="replace")
            if not private_hits(body, words):
                public_report.parent.mkdir(parents=True, exist_ok=True)
                public_report.write_text(body, encoding="utf-8", newline="\n")
                written.append(f"review_notes/{report.name}")
    for gate in info["gates"]:
        for relative, data in gate_files(gate, words):
            public_file = tree / relative
            if not public_file.exists():  # a published file is never replaced from here
                public_file.parent.mkdir(parents=True, exist_ok=True)
                public_file.write_bytes(data)
                written.append(relative.replace("\\", "/"))
    for relative in info.get("lean_files", []):  # the bytes the Lean gate checked (verdict() compared the hash)
        public_file = tree / relative
        if not public_file.exists():
            public_file.parent.mkdir(parents=True, exist_ok=True)
            public_file.write_bytes((ROOT / relative).read_bytes())
            written.append(relative)
    slug = path.stem[11:]
    one_line = re.sub(r"\s+", " ", info["title"]).replace("|", "/")
    row = f"| [{slug}](notes/{path.name}) | {info['problem']} | {info['kind']} | {one_line} |"
    readme = tree / "README.md"
    if readme.exists():
        readme.write_text(add_readme_row(readme.read_text(encoding="utf-8"), row), encoding="utf-8", newline="\n")
        written.append("README.md")
    return written


def publish(ready, settings, repo, words):
    """Commits the notes in a short-lived work tree of the public repo and pushes. Returns the names that went up."""
    day = dt.date.today().isoformat()
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    tree = repo.parent / ".worktrees" / repo.name / f"publish-{stamp}"
    branch = f"agent/publish-{stamp}"
    if online(repo, settings, "fetch", "--quiet", "origin").returncode:
        log("publish: GitHub not reachable, nothing changed")
        return []
    tree.parent.mkdir(parents=True, exist_ok=True)
    added = git(repo, "worktree", "add", "--quiet", "-b", branch, str(tree), "origin/main")
    if added.returncode:
        log(f"publish: could not open a work tree ({added.stderr.strip()[:200]})")
        return []
    done = []
    try:
        for path, info in ready:
            if (tree / "notes" / path.name).exists():
                continue
            files = stage(tree, path, info, day, words)
            git(tree, "add", "--", *files)
            done.append(path.name)
        if not done:
            return []
        subject = f"Publish {len(done)} reviewed note(s): " + ", ".join(name[11:-3] for name in done)
        committed = git(tree, "commit", "--quiet", "--no-verify", "-m", subject[:200])
        pushed = committed if committed.returncode else online(tree, settings, "push", "--quiet", "origin", "HEAD:main")
        if pushed.returncode:
            log(f"publish: not pushed ({(pushed.stderr or pushed.stdout).strip()[:200]})")
            return []
    finally:
        git(repo, "worktree", "remove", "--force", str(tree))
        git(repo, "branch", "-D", branch)
    if not git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        online(repo, settings, "pull", "--quiet", "--ff-only")
    return done


def main(argv):
    dry_run = "--dry-run" in argv
    rows, settings, repo, words = review()
    ready = [(path, info) for path, ok, info in rows if ok][: settings["max_per_run"]]
    if dry_run:
        for path, ok, info in rows:
            print(f"{'PUBLISH' if ok else 'keep   '}  {path.name}" + ("" if ok else f"  <- {'; '.join(info)}"))
        print(f"\n{len(ready)} note(s) would be published, {len(rows) - len(ready)} stay")
        return 0
    listed = lab_useful.write_index(USEFUL_INDEX, lab_useful.QUEUE)  # the private list, on this PC only
    try:  # the lab's facts and self-check, in the same private folder; publishing does not wait for it
        lab_facts.write_note(USEFUL_INDEX.with_name(lab_facts.NOTE_NAME), HERE)
    except Exception as error:
        log(f"facts note not written ({type(error).__name__}: {str(error)[:120]})")
    if not settings["enabled"] or not ready:
        log(f"nothing to publish ({len(rows)} note(s) checked; {listed} useful result(s) listed)")
        return 0
    if not (repo / ".git").exists():
        log(f"public repo not found at {repo}")
        return 1
    done = publish(ready, settings, repo, words)
    if done:
        saved = decisions()
        for name in done:
            saved["published"][name] = dt.date.today().isoformat()
        DECISIONS.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
        log(f"published {len(done)}: {', '.join(done)}")
        try:
            import notify_juwen
            notify_juwen.notify(
                "notes_published", f"Lab: {len(done)} note(s) published",
                "Published to the public notes repo (next monthly release will archive them):\n- " + "\n- ".join(done)
                + "\n\nTo take one back: remove it in the public repo and add its name under \"hold\" in "
                "outbox/public_decisions.json.", min_hours=0)
        except Exception as error:  # the e-mail is a courtesy; the publish already happened
            log(f"publish: notice not sent ({type(error).__name__})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
