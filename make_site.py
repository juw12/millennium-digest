"""Build docs/index.html (the GitHub Pages project page) from the latest digest.

Only the public parts are copied: the arXiv paper lists and the watched-page checks. The outreach
section (researchers' names and addresses, contact history) is never written to the page.

Usage: python make_site.py
"""
import datetime as dt
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIGESTS = HERE / "digests"
OUT = HERE / "docs" / "index.html"
PUBLIC_SECTIONS_END = ("## Expert outreach", "## Suggested actions")


def latest_digest():
    digests = sorted(DIGESTS.glob("digest_*.md"))
    if not digests:
        raise SystemExit("no digest yet: run millennium_monitor.py first")
    return digests[-1]


def parse_public_sections(markdown):
    sections = []
    current = None
    for line in markdown.splitlines():
        if line.startswith(PUBLIC_SECTIONS_END):
            break
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "items": []}
            sections.append(current)
        elif line.startswith("- ") and current is not None:
            current["items"].append(line[2:].strip())
    return sections


def render_item(item):
    match = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.*?)\s+\((.*)\)\s+(https?://\S+)$", item)
    if match:
        date, title, authors, url = match.groups()
        return (
            f'<li><span class="date">{date}</span> <a href="{html.escape(url)}">{html.escape(title)}</a>'
            f'<span class="authors">{html.escape(authors)}</span></li>'
        )
    link = re.search(r"(https?://\S+)$", item)
    if link:
        label = html.escape(item[: link.start()].strip())
        return f'<li>{label} <a href="{html.escape(link.group(1))}">source</a></li>'
    return f"<li class=\"muted\">{html.escape(item)}</li>"


def build():
    digest_path = latest_digest()
    digest_date = digest_path.stem.replace("digest_", "")
    sections = parse_public_sections(digest_path.read_text(encoding="utf-8"))
    policy = json.loads((HERE / "outreach_policy.json").read_text(encoding="utf-8"))
    paper_count = sum(
        len([item for item in section["items"] if item != "nothing new"])
        for section in sections
        if not section["title"].startswith("Watched")
    )

    section_html = []
    for section in sections:
        items = "\n".join(render_item(item) for item in section["items"])
        section_html.append(f"<section><h3>{html.escape(section['title'])}</h3><ul>{items}</ul></section>")

    rules = [
        "Never sends on its own: it opens a draft, you press Send",
        "One recipient per email",
        f"At most {policy['max_new_drafts_per_30_days']} new contacts per 30 days",
        f"{policy['min_days_between_contacts_same_person']} days between contacts with the same person",
        f"At most {policy['max_follow_ups_per_person']} follow-up, after {policy['follow_up_after_days_without_reply']} days",
        f"Body under {policy['max_body_words']} words, with an AI-assistance disclosure",
        'A "no reply is needed" line and one specific question',
        "Must cite the researcher's own work and a public source for their address",
    ]
    rules_html = "\n".join(f"<li>{html.escape(rule)}</li>" for rule in rules)

    built_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Millennium Digest - weekly arXiv watch on the Millennium Prize problems</title>
<meta name="description" content="A weekly digest of new arXiv papers on the seven Millennium Prize problems, with a drafts-only, rule-checked outreach helper.">
<style>
  :root {{ --ink:#1c2230; --muted:#5b6475; --line:#e3e6ec; --accent:#6d3fd1; --bg:#fbfaf7; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:"Segoe UI",system-ui,-apple-system,Arial,sans-serif; color:var(--ink); background:var(--bg); line-height:1.5; }}
  header {{ background:#14111f; color:#f4f1ff; padding:56px 20px 44px; }}
  .wrap {{ max-width:960px; margin:0 auto; padding:0 20px; }}
  h1 {{ font-size:clamp(2rem,5vw,3rem); margin:0 0 8px; letter-spacing:-0.02em; }}
  .tag {{ font-size:1.15rem; color:#cfc6f5; max-width:640px; margin:0 0 20px; }}
  .pill {{ display:inline-block; border:1px solid #4a3d7a; border-radius:999px; padding:4px 12px; margin:0 6px 6px 0; font-size:.9rem; color:#e4dcff; }}
  .cta {{ display:inline-block; margin-top:14px; background:#b9a5ff; color:#14111f; padding:10px 18px; border-radius:8px; font-weight:600; text-decoration:none; }}
  main {{ padding:32px 0 56px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; margin:8px 0 32px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:18px; }}
  .card h2 {{ font-size:1.1rem; margin:0 0 6px; }}
  .card p, .card li {{ color:var(--muted); font-size:.97rem; }}
  .card ul {{ padding-left:18px; margin:6px 0 0; }}
  h2.section {{ font-size:1.4rem; margin:36px 0 4px; }}
  .sub {{ color:var(--muted); margin:0 0 12px; }}
  section {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 18px; margin:12px 0; }}
  section h3 {{ font-size:1.02rem; margin:0 0 6px; color:var(--accent); }}
  section ul {{ list-style:none; padding:0; margin:0; }}
  section li {{ padding:6px 0; border-top:1px solid #f0f1f4; }}
  section li:first-child {{ border-top:0; }}
  .date {{ font-variant-numeric:tabular-nums; color:var(--muted); font-size:.88rem; margin-right:6px; }}
  .authors {{ display:block; color:var(--muted); font-size:.88rem; }}
  .muted {{ color:var(--muted); }}
  a {{ color:#4b2aa8; }}
  pre {{ background:#14111f; color:#eae6ff; padding:14px; border-radius:10px; overflow-x:auto; font-size:.9rem; }}
  footer {{ color:var(--muted); font-size:.9rem; padding:24px 0 40px; border-top:1px solid var(--line); }}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>Millennium Digest</h1><p><a href="results.html"><b>Research results</b></a> &middot; numerical experiments on BSD and RH statistics (observations, not proofs)</p>
    <p class="tag">Every Monday: the new arXiv papers on the seven Millennium Prize problems, on one page.
    Plus a helper that keeps emails to researchers polite, rare and hand-sent.</p>
    <span class="pill">Python, standard library only</span>
    <span class="pill">Windows Task Scheduler</span>
    <span class="pill">arXiv API</span>
    <span class="pill">Drafts only, never auto-sends</span>
    <br><a class="cta" href="https://github.com/juw12/millennium-digest">View the code on GitHub</a>
  </div>
</header>
<main class="wrap">
  <div class="grid">
    <div class="card"><h2>Weekly digest</h2>
      <p>Searches arXiv for the last 8 days on each problem, plus two narrower topics (elliptic-curve
      murmurations, Riemann-zero spacing), and flags changes on the Clay problems page and two data sources.</p></div>
    <div class="card"><h2>Outreach guard</h2>
      <p>Checks every draft to a researcher against written rules before opening a Gmail compose window,
      and keeps a ledger of who was contacted and when.</p></div>
    <div class="card"><h2>Honest scope</h2>
      <p>A hobby tool by an engineer, not a mathematician. It collects and organises; it does not solve or
      claim progress on any Millennium problem.</p></div>
  </div>

  <h2 class="section">Latest digest: {digest_date}</h2>
  <p class="sub">{paper_count} new papers in the 8 days before the run. Outreach details are private and not shown here.</p>
  {''.join(section_html)}

  <h2 class="section">Outreach rules</h2>
  <p class="sub">From outreach_policy.json. A draft that breaks any rule is refused.</p>
  <div class="card"><ul>{rules_html}</ul></div>

  <h2 class="section">Run it</h2>
  <pre>copy config.example.json config.json
python millennium_monitor.py      # writes and opens digests/digest_&lt;date&gt;.html
python outreach.py status         # ledger + what the rules allow next
python make_site.py               # rebuilds this page from the latest digest</pre>
</main>
<footer class="wrap">Page built {built_at} by the weekly job. Built by Juwen Lie. Paper titles and links come from the arXiv API; all papers belong to their authors.
Code: <a href="https://github.com/juw12/millennium-digest">github.com/juw12/millennium-digest</a> (MIT licence).</footer>
</body>
</html>
"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT} from {digest_path.name}: {len(sections)} sections, {paper_count} papers")


if __name__ == "__main__":
    build()
