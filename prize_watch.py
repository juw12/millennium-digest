"""Weekly check of the paid-problem links in prizes.json (and the two registries that list new bounties).
Records whether each page is reachable and whether its text changed since last week, so the dashboard
can flag "page changed - check for new bounties / rule changes". Read-only: never submits anything.
Output: prize_watch.json (local state)
"""
import datetime as dt
import hashlib
import json
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIZES = HERE / "prizes.json"
STATE = HERE / "prize_watch.json"


def page_fingerprint(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (prize watch; weekly)"})
    with urllib.request.urlopen(request, timeout=40) as response:
        text = response.read().decode("utf-8", "replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def main():
    data = json.loads(PRIZES.read_text(encoding="utf-8"))
    old = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    today = dt.date.today().isoformat()
    new = {}
    urls = [p["url"] for p in data["prizes"]] + [r["url"] for r in data["registries"]]
    for url in urls:
        previous = old.get(url, {})
        try:
            fingerprint = page_fingerprint(url)
            changed = bool(previous.get("hash")) and previous["hash"] != fingerprint
            new[url] = {"checked": today, "ok": True, "hash": fingerprint,
                        "changed_on": today if changed else previous.get("changed_on")}
        except Exception as error:
            new[url] = {"checked": today, "ok": False, "error": str(error)[:120],
                        "hash": previous.get("hash"), "changed_on": previous.get("changed_on")}
    STATE.write_text(json.dumps(new, indent=2), encoding="utf-8")
    print(f"checked {len(urls)} prize pages")


if __name__ == "__main__":
    main()
