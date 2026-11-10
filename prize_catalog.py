"""World catalogue of unsolved problems that carry a cash prize (read-only, weekly).

Sources
1. Prize Problem Ledger (https://prizeproblems.org/): an audited index of open maths / CS / crypto problems with
   active cash awards (Millennium, Beal, Collatz, every open Erdos prize, Wolfram, Certicom, EFF, ...).
   Every card is parsed: PPL number, title, field, sponsor, reward, open-since year, verification label.
2. prizes.json "extra_catalog": verified open challenges that the ledger does not cover (Kaggle, Hutter,
   Vesuvius, XPRIZE, Conjectures.io).

Output: prize_catalog.json (local). If the ledger cannot be fetched, the previous catalogue is kept and the
error is recorded, so the dashboard never loses the list. Nothing is ever submitted anywhere.
Usage: python prize_catalog.py
"""
import datetime as dt
import html
import json
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIZES = HERE / "prizes.json"
OUT = HERE / "prize_catalog.json"
LEDGER = "https://prizeproblems.org/"

# Approximate USD rates, only used to sort by size (amounts are always shown in their own currency).
USD_PER = {"$": 1.0, "€": 1.1, "£": 1.3, "¥": 0.0067, "₹": 0.012, "C$": 0.73}

# Fields where a computer search / record / counterexample can earn the prize without a full proof.
COMPUTE_FIELDS = {"Computational number theory", "Cryptography", "Recreational mathematics"}
COMPUTE_WORDS = re.compile(
    r"\b(find|compute|discover|record|construct|counterexample|key recovery|collision|attack|digit prime|"
    r"magic square|pack)\b", re.I)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (millennium prize catalogue; weekly)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def clean(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


def reward_usd(text):
    """'$1,000,000' -> 1e6, '€3,000' -> 3300, '¥120,000,000' -> 804000, 'Share of $40,000 pool' -> 40000."""
    match = re.search(r"(C\$|[$€£¥₹])\s?([\d,.]+)", text)
    if not match:
        return 0.0
    number = float(match.group(2).replace(",", "").rstrip("."))
    return round(number * USD_PER[match.group(1)], 2)


def parse_ledger(page):
    items = []
    for card in re.findall(r'<article class="problem-card.*?</article>', page, flags=re.S):
        def grab(pattern):
            found = re.search(pattern, card, flags=re.S)
            return found.group(1) if found else ""

        metrics = dict(
            (clean(label), clean(value))
            for label, value in re.findall(r'<span class="metric-label">(.*?)</span><strong>(.*?)</strong>', card, re.S)
        )
        family_label = clean(grab(r'<span class="family-label">(.*?)</span>'))
        family = grab(r'class="problem-card family-([^"\s]+)').replace("erdÅ‘s", "erdos")
        field = clean(grab(r'<p class="field-kicker">(.*?)</p>'))
        title = clean(grab(r"<h3>(.*?)</h3>"))
        statement = clean(grab(r'<p class="statement">(.*?)</p>'))
        reward = metrics.get("Reward", "")
        items.append({
            "id": clean(grab(r'class="catalog-number"[^>]*>(.*?)</a>')),
            "title": title,
            "family": family or "independent",
            "sponsor": family_label.split("·", 1)[1].strip() if "·" in family_label else "",
            "field": field,
            "statement": statement,
            "reward": reward,
            "reward_usd": reward_usd(reward),
            "open_since": metrics.get("Open since", ""),
            "verification": clean(grab(r'<span class="verification[^"]*">(.*?)</span></div>')),
            "url": LEDGER.rstrip("/") + grab(r'href="(/problems/\d+/)"'),
            "computable": field in COMPUTE_FIELDS or (bool(COMPUTE_WORDS.search(f"{title} {statement}"))
                                                     and not re.search(r"\bprove\b|existence", f"{title} {statement}", re.I)),
            "source": "Prize Problem Ledger",
        })
    return items


def extra_items():
    data = json.loads(PRIZES.read_text(encoding="utf-8"))
    items = []
    for extra in data.get("extra_catalog", []):
        item = dict(extra)
        item.setdefault("reward_usd", reward_usd(item.get("reward", "")))
        item.setdefault("source", "checked by hand")
        items.append(item)
    return items


def build():
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    today = dt.date.today().isoformat()
    try:
        ledger = parse_ledger(fetch(LEDGER))
        if len(ledger) < 50:  # page layout changed: keep the last good list rather than show a broken one
            raise ValueError(f"only {len(ledger)} cards parsed")
        error = None
    except Exception as exc:  # network or layout problem: keep the previous ledger part
        ledger = [item for item in old.get("items", []) if item.get("source") == "Prize Problem Ledger"]
        error = str(exc)[:200]
    previous_ids = {item["id"] for item in old.get("items", [])}
    items = ledger + extra_items()
    for item in items:
        item["new_since_last_check"] = bool(previous_ids) and item["id"] not in previous_ids
    items.sort(key=lambda item: -item["reward_usd"])
    catalog = {
        "checked": today if error is None else old.get("checked"),
        "attempted": today,
        "error": error,
        "sources": [LEDGER, "prizes.json extra_catalog"],
        "count": len(items),
        "total_usd": round(sum(item["reward_usd"] for item in items)),
        "items": items,
    }
    OUT.write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding="utf-8")
    note = f" (ledger fetch failed: {error}; kept last list)" if error else ""
    print(f"catalogued {len(items)} open prize problems, about ${catalog['total_usd']:,} in total{note}")
    return catalog


if __name__ == "__main__":
    build()
