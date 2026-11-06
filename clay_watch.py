"""The automation's own watch on the Clay Institute pages and our data sources. Nothing here is for Juwen to do.

Every weekly run (called from millennium_monitor.py, or `python clay_watch.py` by hand):
  * Reads the Clay Millennium Problems page and every problem it links to.
  * A problem on that list that we do not track yet is added automatically (clay_watch.json): from the
    next run on it gets its own arXiv search in the digest and its own card under Progress.
  * When a tracked problem's Clay page changes, or starts to talk about a solution/claimed resolution,
    the problem gets a "watcher" note on its Progress card (read the page, update our notes).
  * When a data source we computed with changes (Cremona tables -> BSD, Odlyzko zeros -> RH), that
    problem gets a "re-run the study before trusting old numbers" note.
Every change also goes to the activity log. Only status words decide nothing by themselves: a problem is
never marked solved automatically, it is flagged for a check.
"""
import datetime as dt
import hashlib
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "clay_watch.json"  # local
CLAY_LIST = "https://www.claymath.org/millennium-problems/"
PROBLEM_LINK = re.compile(r'<a[^>]+href="(https://www\.claymath\.org/millennium/[^"/]+/)"[^>]*>(.*?)</a>', re.S)
SOLUTION_WORDS = re.compile(
    r"\b(has been|was|were|is now) (solved|resolved|proved|proven|settled)\b"
    r"|\bprize (has been|was) awarded\b|\bclaimed (resolution|solution|proof)\b",
    re.I,
)
# Our problem keys, matched by words in Clay's own titles (robust to their URL slugs changing).
KNOWN_KEYS = [
    ("BSD", r"swinnerton"),
    ("HODGE", r"hodge"),
    ("NS", r"navier"),
    ("PNP", r"\bnp\b"),
    ("POINCARE", r"poincar"),
    ("RH", r"riemann"),
    ("YM", r"yang"),
]
DATA_SOURCES = {
    "Cremona elliptic curve tables": "BSD",
    "Odlyzko zeta zero tables": "RH",
}
MAX_NOTES = 5


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "millennium-monitor/1.0 (personal research digest)"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read().decode("utf-8", "replace")


def _text(raw):
    raw = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pages": {}, "auto_problems": [], "notes": {}, "last_check": None, "clay_list": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def key_for(name, state):
    lowered = name.lower()
    for key, pattern in KNOWN_KEYS:
        if re.search(pattern, lowered):
            return key
    for problem in state.get("auto_problems", []):
        if problem["name"].lower() == lowered:
            return problem["key"]
    return None


def _new_key(name, state):
    taken = {key for key, _ in KNOWN_KEYS} | {p["key"] for p in state.get("auto_problems", [])}
    base = re.sub(r"[^A-Z]", "", "".join(word[:1] for word in name.upper().split())) or "NEW"
    key, number = base, 2
    while key in taken:
        key, number = f"{base}{number}", number + 1
    return key


def _note(state, key, text, today):
    notes = state.setdefault("notes", {}).setdefault(key, [])
    if notes and notes[-1]["text"] == text:
        return False
    notes.append({"date": today, "text": text})
    del notes[:-MAX_NOTES]
    return True


def clay_problems():
    """[(name, url)] in the order Clay lists them, without duplicates."""
    seen = {}
    for url, label in PROBLEM_LINK.findall(_fetch(CLAY_LIST)):
        name = _text(label)
        if url not in seen and name and len(name) < 80:
            seen[url] = name
    return [(name, url) for url, name in seen.items()]


def run(changed_sources=(), today=None):
    """Check everything once. Returns digest lines describing what the automation did."""
    import history

    today = (today or dt.date.today()).isoformat()
    state = load_state()
    first_run = not state.get("pages")
    lines = []
    try:
        listed = clay_problems()
    except Exception as error:  # network trouble: try again next week
        return [f"- Clay problem list could not be read ({error}); retried next run"]
    if not listed:
        return ["- Clay problem list: no problem links found (page layout changed?); retried next run"]

    state["clay_list"] = [{"name": name, "url": url} for name, url in listed]
    for name, url in listed:
        key = key_for(name, state)
        if key is None:
            key = _new_key(name, state)
            state["auto_problems"].append({
                "key": key,
                "name": name,
                "url": url,
                "added": today,
                "query": f'abs:"{name}"',
            })
            text = f"New on the Clay list: {name}. Added to tracking (weekly arXiv search, Progress card)."
            _note(state, key, text, today)
            history.record("watch", text)
            lines.append(f"- {text}")
        try:
            page_text = _text(_fetch(url))
        except Exception as error:
            lines.append(f"- {name}: Clay page could not be read ({error})")
            continue
        fingerprint = hashlib.sha256(page_text.encode("utf-8")).hexdigest()
        previous = state["pages"].get(url)
        state["pages"][url] = fingerprint
        if previous == fingerprint:
            continue
        mention = SOLUTION_WORDS.search(page_text) if key != "POINCARE" else None
        if previous is None and not mention:
            continue  # first look at this page: just remember it
        if mention:
            verb = "changed and now says" if previous else "says"
            text = (f"Clay's {name} page {verb} \"{mention.group(0)}\": "
                    "read it, check whether the status changed, and update our notes.")
        else:
            text = f"Clay's {name} page changed: read it and update our notes if anything matters for our work."
        if _note(state, key, text, today):
            history.record("watch", text)
        lines.append(f"- {text}")

    for source in changed_sources:
        key = DATA_SOURCES.get(source)
        if key:
            text = f"{source} changed: re-run the study scripts on the new tables before relying on old numbers."
            if _note(state, key, text, today):
                history.record("watch", text)
            lines.append(f"- {text}")

    state["last_check"] = today
    save_state(state)
    tracked = sum(1 for name, _ in listed if key_for(name, state))
    summary = f"- Clay list checked: {len(listed)} problems, {tracked} tracked"
    if first_run:
        summary += " (first check: page fingerprints recorded)"
    return [summary] + lines


def auto_problems():
    return load_state().get("auto_problems", [])


def notes(key):
    return load_state().get("notes", {}).get(key, [])


if __name__ == "__main__":
    changed = sys.argv[1:]
    print("\n".join(run(changed)))
