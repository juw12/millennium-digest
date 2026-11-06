"""Weekly Millennium Prize research monitor.

What it does (every run):
  1. Searches arXiv for papers from the last N days on each Millennium Prize problem and on the two
     topics we studied (murmurations / BSD, spacing of Riemann zeros).
  2. Checks whether the Clay Institute Millennium pages or the data sources we used (Cremona tables,
     Odlyzko zero tables) have changed since the last run; clay_watch.py follows changes up itself
     (new Clay problems are added to tracking, changed pages become notes on the Progress cards).
  3. Writes a dated digest (Markdown + HTML) and opens the HTML page in the browser.

What it deliberately does NOT do: it never emails researchers. Unsolicited automated mail to
mathematicians is spam; any message to an expert must be written and sent by Juwen.
"""
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIGESTS = HERE / "digests"
STATE_FILE = HERE / "state.json"
LOOKBACK_DAYS = 8
from settings import EMAIL as SELF_ADDRESS

TOPICS = {
    "Riemann Hypothesis": 'abs:"Riemann hypothesis" OR abs:"zeros of the Riemann zeta"',
    "Riemann zeros: spacing statistics (our RH study)": 'abs:"Riemann zeros" AND (abs:spacing OR abs:"random matrix")',
    "Birch and Swinnerton-Dyer": 'abs:"Birch and Swinnerton-Dyer"',
    "Murmurations (our BSD study)": "abs:murmuration OR abs:murmurations",
    "Navier-Stokes regularity": 'abs:"Navier-Stokes" AND (abs:blowup OR abs:"blow-up" OR abs:singularity OR abs:regularity)',
    "Yang-Mills mass gap": 'abs:"Yang-Mills" AND abs:"mass gap"',
    "P vs NP": 'abs:"P vs NP" OR abs:"P versus NP" OR abs:"P != NP"',
    "Hodge conjecture": 'abs:"Hodge conjecture"',
}

WATCHED_PAGES = {
    "Clay Millennium Problems page": "https://www.claymath.org/millennium-problems/",
    "Cremona elliptic curve tables": "https://johncremona.github.io/ecdata/",
    "Odlyzko zeta zero tables": "https://www-users.cse.umn.edu/~odlyzko/zeta_tables/index.html",
}


def fetch(url, timeout=40):
    request = urllib.request.Request(url, headers={"User-Agent": "millennium-monitor/1.0 (personal research digest)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def recent_arxiv(query, since):
    params = urllib.parse.urlencode({
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": 25,
    })
    feed = ET.fromstring(fetch(f"https://export.arxiv.org/api/query?{params}"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in feed.findall("a:entry", ns):
        published = dt.datetime.fromisoformat(entry.find("a:published", ns).text.replace("Z", "+00:00"))
        if published < since:
            continue
        papers.append({
            "title": " ".join(entry.find("a:title", ns).text.split()),
            "authors": ", ".join(a.find("a:name", ns).text for a in entry.findall("a:author", ns))[:200],
            "date": published.date().isoformat(),
            "url": entry.find("a:id", ns).text,
        })
    return papers


def page_fingerprint(url):
    content = fetch(url)
    return hashlib.sha256(content).hexdigest()


def save_outlook_draft(subject, body, attachment):
    try:
        import win32com.client  # noqa: F401  (pywin32, if available)
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)
        mail.To = SELF_ADDRESS
        mail.Subject = subject
        mail.Body = body
        mail.Attachments.Add(str(attachment))
        mail.Save()
        return "Outlook draft saved (pywin32)"
    except Exception:
        pass
    import subprocess
    script = (
        "$o = New-Object -ComObject Outlook.Application; $m = $o.CreateItem(0); "
        f"$m.To = '{SELF_ADDRESS}'; $m.Subject = '{subject}'; "
        f"$m.Body = [IO.File]::ReadAllText('{attachment}'); $null = $m.Attachments.Add('{attachment}'); $m.Save(); 'ok'"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=120)
    return "Outlook draft saved" if "ok" in result.stdout else f"Outlook draft NOT saved: {result.stderr.strip()[:200]}"


def main():
    DIGESTS.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"fingerprints": {}}
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=LOOKBACK_DAYS)
    lines = [
        f"# Millennium Prize research digest, {now.date().isoformat()}",
        "",
        f"New arXiv papers from the last {LOOKBACK_DAYS} days, and changes to the pages and data we rely on.",
        "This digest is for reading only. Nothing here was sent to anyone.",
        "",
    ]

    topics = dict(TOPICS)
    try:
        import clay_watch
        for problem in clay_watch.auto_problems():  # new problems the watch added to tracking
            topics.setdefault(problem["name"], problem["query"])
    except Exception:
        pass

    total = 0
    for topic, query in topics.items():
        time.sleep(3)  # arXiv API etiquette: at most one request every 3 seconds
        try:
            papers = recent_arxiv(query, since)
        except Exception as error:
            lines += [f"## {topic}", f"- search failed: {error}", ""]
            continue
        total += len(papers)
        lines.append(f"## {topic} ({len(papers)} new)")
        lines += [f"- {p['date']}  {p['title']}  ({p['authors']})  {p['url']}" for p in papers] or ["- nothing new"]
        lines.append("")

    lines.append("## Watched pages and data sources")
    changed_sources = []
    for name, url in WATCHED_PAGES.items():
        try:
            fingerprint = page_fingerprint(url)
        except Exception as error:
            lines.append(f"- {name}: could not check ({error})")
            continue
        previous = state["fingerprints"].get(url)
        status = "first check" if previous is None else ("CHANGED, followed up by the automation" if previous != fingerprint else "unchanged")
        if previous is not None and previous != fingerprint:
            changed_sources.append(name)
        state["fingerprints"][url] = fingerprint
        lines.append(f"- {name}: {status}  {url}")

    lines.append("")
    lines.append("## Watched by the automation: Clay problem list and data sources")
    try:
        import clay_watch
        lines += clay_watch.run(changed_sources)
    except Exception as error:
        lines.append(f"- watch step failed: {error}")

    lines.append("")
    lines.append("## Expert outreach (rule-checked; drafts only, you press Send)")
    try:
        import outreach
        for change in outreach.sync():
            lines.append(f"- ledger update: {change}")
        for address in outreach.followups(open_windows=False):
            lines.append(f"- one polite follow-up is due for {address}: open it with `python outreach.py open {address}`")
        for entry in outreach.load_ledger()["contacts"]:
            note = entry.get("note", "")
            lines.append(f"- {entry['name']} ({entry['to']}): {entry['state']} since {entry['date']} {note}")
        lines.append("- To log a Gmail reply: python outreach.py mark <address> replied")
    except Exception as error:
        lines.append(f"- outreach step failed: {error}")
    lines += [
        "",
        "## Suggested actions (manual)",
        "- Changed Clay pages and data sources are followed up by the automation (see 'Watched by the automation').",
        "- If a new paper answers one of our questions: read it before writing to anyone.",
        "- New expert email: write an email_spec.json and run `python outreach.py propose <spec>`; it drafts only if every rule passes.",
    ]

    digest_path = DIGESTS / f"digest_{now.date().isoformat()}.md"
    digest_path.write_text("\n".join(lines), encoding="utf-8")
    state["last_run"] = now.isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))

    html_path = digest_path.with_suffix(".html")
    import html as html_module
    import re
    body_html = []
    for line in lines:
        escaped = html_module.escape(line)
        escaped = re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', escaped)
        if line.startswith("# "):
            body_html.append(f"<h1>{escaped[2:]}</h1>")
        elif line.startswith("## "):
            body_html.append(f"<h2>{escaped[3:]}</h2>")
        elif line.startswith("- "):
            body_html.append(f"<li>{escaped[2:]}</li>")
        elif line:
            body_html.append(f"<p>{escaped}</p>")
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Millennium digest</title>"
        "<body style='font-family:Segoe UI,Arial;max-width:900px;margin:2em auto;line-height:1.45'>"
        + "\n".join(body_html) + "</body>",
        encoding="utf-8",
    )
    if "--no-open" in sys.argv:
        draft_status = "digest not opened (--no-open)"
    else:
        subprocess.Popen(["cmd", "/c", "start", "", str(html_path)])
        draft_status = f"digest opened in browser: {html_path}"
    print(digest_path)
    print(f"{total} new papers; {draft_status}")


if __name__ == "__main__":
    main()
