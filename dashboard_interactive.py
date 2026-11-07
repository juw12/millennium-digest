"""Interactive parts of the dashboard: section nav, paper browser, filters, live refresh, status chips.

Everything is plain HTML + a small inline script (no internet, no libraries), so the page works the same in
the desktop app and in a browser. Inside the desktop app the script also asks the app every few seconds
whether the data changed (millennium://poll/<build stamp>); if so the page rebuilds and reloads itself.
"""
import datetime as dt
import html
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Files whose change means the page is out of date (checked by action.poll()).
WATCHED_INPUTS = [
    "contacts_log.json",
    "gmail_inbox.json",
    "activity_log.jsonl",
    "roadmap.json",
    "state.json",
    "ui_state.json",
]

TOPIC_HEADER = re.compile(r"^## (.+?) \((\d+) new\)\s*$")


def parse_digest_papers(md_path):
    """[{topic, date, title, authors, url}] from a digest .md ('- DATE  TITLE  (AUTHORS)  URL' lines)."""
    papers = []
    topic = None
    path = Path(md_path)
    if not path.exists():
        return papers
    for line in path.read_text(encoding="utf-8").splitlines():
        header = TOPIC_HEADER.match(line)
        if header:
            topic = header.group(1)
            continue
        if line.startswith("## "):
            topic = None  # e.g. the watched-pages section
            continue
        if not (topic and line.startswith("- ") and "http" in line):
            continue
        body = line[2:].strip()
        rest, _, url = body.rpartition("  http")
        url = "http" + url.strip()
        date, _, rest = rest.partition("  ")
        title, _, authors = rest.rpartition("  (")
        if not re.match(r"\d{4}-\d{2}-\d{2}$", date):
            continue
        papers.append({
            "topic": topic,
            "date": date,
            "title": (title or rest).strip(),
            "authors": authors.rstrip(")").strip(),
            "url": url,
        })
    return papers


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def papers_browser(papers, short_names=None):
    """Search box + topic chips + list of this week's papers (each links to arXiv)."""
    short_names = short_names or {}
    if not papers:
        return '<p class="empty">No papers in the latest digest.</p>'
    counts = {}
    for paper in papers:
        counts[paper["topic"]] = counts.get(paper["topic"], 0) + 1
    chips = [f'<button type="button" class="fchip on" data-filter="all">All <b>{len(papers)}</b></button>']
    for topic, count in sorted(counts.items(), key=lambda item: -item[1]):
        label = short_names.get(topic, topic)
        chips.append(
            f'<button type="button" class="fchip" data-filter="{_slug(topic)}">{html.escape(label)} <b>{count}</b></button>'
        )
    items = []
    for paper in sorted(papers, key=lambda p: p["date"], reverse=True):
        label = short_names.get(paper["topic"], paper["topic"])
        searchable = f'{paper["title"]} {paper["authors"]} {label}'.lower()
        items.append(
            f'<li class="pitem" data-kind="{_slug(paper["topic"])}" data-text="{html.escape(searchable)}">'
            f'<a href="{html.escape(paper["url"])}">{html.escape(paper["title"])}</a>'
            f'<span class="pmeta">{html.escape(paper["date"][5:])} &middot; {html.escape(paper["authors"])}'
            f' &middot; <span class="tag">{html.escape(label)}</span></span></li>'
        )
    return (
        '<div class="fbox" data-fbox="papers">'
        '<div class="ftools"><input type="search" class="fsearch" data-search placeholder="Search titles or authors  ( / )" '
        'aria-label="Search papers">'
        f'<div class="fchips" role="group" aria-label="Filter by topic">{"".join(chips)}</div></div>'
        f'<ul class="plist scroll-y">{"".join(items)}</ul>'
        '<p class="fempty muted small" hidden>No paper matches.</p></div>'
    )


def filter_chips(options, default="all"):
    """[(value, label, count)] -> chip buttons for a [data-fbox] container."""
    return '<div class="fchips" role="group" aria-label="Filter">' + "".join(
        f'<button type="button" class="fchip{" on" if value == default else ""}" data-filter="{value}">'
        f'{html.escape(label)}{f" <b>{count}</b>" if count is not None else ""}</button>'
        for value, label, count in options
    ) + "</div>"


def section_nav(entries):
    """Sticky in-page navigation. entries: [(anchor id, label, badge or '')]."""
    links = "".join(
        f'<a href="#{anchor}" data-nav="{anchor}">{html.escape(label)}'
        + (f' <span class="nbadge">{html.escape(str(badge))}</span>' if badge else "")
        + "</a>"
        for anchor, label, badge in entries
    )
    return f'<nav class="snav" aria-label="Sections"><div class="snav-in">{links}</div></nav>'


# ---------------------------------------------------------------- status chips for the header

def unpushed_commits():
    """Commits on this PC that GitHub does not have yet (0 if git is unavailable)."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "@{u}..HEAD"],
            cwd=HERE, capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int(result.stdout.strip() or 0) if result.returncode == 0 else 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def status_chips():
    chips = []
    try:
        import gmail_inbox

        state = gmail_inbox.load_state()
    except Exception:
        state = {}
    if state.get("at"):
        at = dt.datetime.fromisoformat(state["at"])
        age_h = (dt.datetime.now() - at).total_seconds() / 3600
        when = at.strftime("%H:%M") if age_h < 20 else at.strftime("%d %b")
        if not state.get("ok"):
            chips.append(f'<span class="chip bad" title="{html.escape(state.get("note", ""))}"><span class="dot"></span>Gmail check failed {when}</span>')
        elif age_h > 3:
            chips.append(f'<span class="chip warn"><span class="dot"></span>Gmail checked {when}</span>')
        else:
            chips.append(f'<span class="chip good"><span class="dot"></span>Gmail checked {when}</span>')
    waiting = unpushed_commits()
    if waiting:
        chips.append(
            f'<span class="chip warn" title="Saved on this PC; pushed automatically when the network allows">'
            f'<span class="dot"></span>{waiting} change{"s" if waiting > 1 else ""} not on GitHub yet</span>'
        )
    return "".join(chips)


def inputs_changed_since(stamp):
    """True when a data file the page is built from changed after `stamp` (epoch seconds)."""
    paths = [HERE / name for name in WATCHED_INPUTS]
    digests = HERE / "digests"
    if digests.exists():
        paths += list(digests.glob("*.md"))
    for path in paths:
        try:
            if path.stat().st_mtime > stamp + 1:
                return True
        except OSError:
            continue
    return False


CSS = """
.top { position:relative; }
.snav { position:sticky; top:0; z-index:5; background:rgba(246,247,249,.94); backdrop-filter:blur(6px);
  border-bottom:1px solid var(--line); }
.snav-in { max-width:1180px; margin:0 auto; padding:7px 24px; display:flex; gap:4px; overflow-x:auto; scrollbar-width:none; }
.snav a { padding:5px 11px; border-radius:999px; color:var(--ink2); font-size:12.5px; font-weight:600; white-space:nowrap; }
.snav a:hover { background:#eef2ff; text-decoration:none; }
.snav a.on { background:var(--accent); color:#fff; }
.nbadge { display:inline-block; min-width:18px; padding:0 5px; border-radius:99px; background:var(--bad); color:#fff;
  font-size:11px; text-align:center; margin-left:3px; }
.snav a.on .nbadge { background:#fff; color:var(--accent); }
section[id], div[id].anchor { scroll-margin-top:56px; }
.chip.warn { background:#fffbeb; color:#b45309; }
.ftools { display:flex; flex-direction:column; gap:8px; margin-bottom:8px; }
.fsearch { width:100%; padding:7px 11px; border:1px solid var(--line); border-radius:9px; font:inherit; font-size:13px;
  background:#fff; color:var(--ink); }
.fsearch:focus { outline:2px solid #c7d2fe; border-color:var(--accent); }
.fchips { display:flex; flex-wrap:wrap; gap:5px; }
.fchip { border:1px solid var(--line); background:#fff; color:var(--ink2); border-radius:999px; padding:3px 10px;
  font:inherit; font-size:12px; font-weight:600; cursor:pointer; min-height:28px; }
.fchip b { color:var(--muted); font-weight:600; margin-left:2px; }
.fchip:hover { border-color:#c7d2fe; }
.fchip.on { background:var(--accent); border-color:var(--accent); color:#fff; }
.fchip.on b { color:#e0e7ff; }
.fchip:focus-visible, .snav a:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.plist { list-style:none; margin:0; padding:0; max-height:360px; }
.pitem { padding:8px 2px; border-top:1px solid #f1f5f9; display:flex; flex-direction:column; gap:2px; }
.pitem:first-child { border-top:0; }
.pitem a { font-weight:600; font-size:13px; line-height:1.4; color:var(--ink); }
.pitem a:hover { color:var(--accent); }
.pmeta { color:var(--muted); font-size:12px; }
.hb-row[data-filter-target] { cursor:pointer; border-radius:6px; }
.hb-row[data-filter-target]:hover .hb-label { color:var(--accent); }
.more-row { text-align:center; padding-top:8px; }
.live-note { position:fixed; right:18px; bottom:18px; background:var(--ink); color:#fff; padding:8px 14px; border-radius:10px;
  font-size:12.5px; box-shadow:var(--shadow); opacity:0; transition:opacity .25s; pointer-events:none; z-index:9; }
.live-note.show { opacity:.92; }
svg .hit { fill:transparent; cursor:crosshair; }
svg .hit:hover { fill:rgba(79,70,229,.18); }
@media (max-width:640px) { .snav-in { padding:7px 14px; } }
"""

JS = r"""
(function () {
  var store = window.localStorage;
  function save(key, value) { try { store.setItem('md:' + key, value); } catch (e) {} }
  function load(key) { try { return store.getItem('md:' + key); } catch (e) { return null; } }

  // Filter boxes: chips [data-filter] + optional search [data-search] over items [data-kind] / [data-text].
  document.querySelectorAll('[data-fbox]').forEach(function (box) {
    var name = box.getAttribute('data-fbox');
    var chips = box.querySelectorAll('.fchip');
    var search = box.querySelector('[data-search]');
    var items = box.querySelectorAll('[data-kind]');
    var empty = box.querySelector('.fempty');
    var limit = parseInt(box.getAttribute('data-limit') || '0', 10);
    var expanded = false;
    var moreRow = box.querySelector('.more-row');
    function apply() {
      var active = box.querySelector('.fchip.on');
      var want = active ? active.getAttribute('data-filter') : 'all';
      var text = search ? search.value.trim().toLowerCase() : '';
      var shown = 0, matching = 0;
      items.forEach(function (item) {
        var kinds = (item.getAttribute('data-kind') || '').split(' ');
        var ok = (want === 'all' || kinds.indexOf(want) >= 0) &&
                 (!text || (item.getAttribute('data-text') || item.textContent.toLowerCase()).indexOf(text) >= 0);
        if (ok) { matching += 1; }
        var visible = ok && (expanded || !limit || matching <= limit || text);
        item.hidden = !visible;
        if (visible) { shown += 1; }
      });
      if (empty) { empty.hidden = matching > 0; }
      if (moreRow) {
        moreRow.hidden = expanded || !limit || matching <= limit || !!text;
        var button = moreRow.querySelector('button');
        if (button) { button.textContent = 'Show all ' + matching; }
      }
      save(name + ':filter', want);
    }
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        chips.forEach(function (c) { c.classList.remove('on'); c.setAttribute('aria-pressed', 'false'); });
        chip.classList.add('on'); chip.setAttribute('aria-pressed', 'true');
        apply();
      });
    });
    if (search) { search.addEventListener('input', apply); }
    if (moreRow) {
      moreRow.querySelector('button').addEventListener('click', function () { expanded = true; apply(); });
    }
    var remembered = load(name + ':filter');
    if (remembered) {
      chips.forEach(function (c) { c.classList.toggle('on', c.getAttribute('data-filter') === remembered); });
      if (!box.querySelector('.fchip.on') && chips[0]) { chips[0].classList.add('on'); }
    }
    apply();
    box.applyFilter = function (value) {
      chips.forEach(function (c) { c.classList.toggle('on', c.getAttribute('data-filter') === value); });
      apply();
    };
  });

  // Clicking a bar in "New papers" filters the paper list to that topic.
  document.querySelectorAll('[data-filter-target]').forEach(function (row) {
    row.addEventListener('click', function () {
      var box = document.querySelector('[data-fbox="papers"]');
      if (box && box.applyFilter) {
        box.applyFilter(row.getAttribute('data-filter-target'));
        box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    });
  });

  // "/" jumps to the paper search.
  document.addEventListener('keydown', function (event) {
    var tag = (event.target.tagName || '').toLowerCase();
    if (event.key === '/' && tag !== 'input' && tag !== 'textarea') {
      var input = document.querySelector('[data-fbox="papers"] [data-search]');
      if (input) { event.preventDefault(); input.focus(); }
    }
  });

  // Section nav: highlight the section in view.
  var links = document.querySelectorAll('.snav a[data-nav]');
  var targets = [];
  links.forEach(function (link) {
    var el = document.getElementById(link.getAttribute('data-nav'));
    if (el) { targets.push([el, link]); }
  });
  function spy() {
    var current = null;
    targets.forEach(function (pair) {
      if (pair[0].getBoundingClientRect().top < 140) { current = pair[1]; }
    });
    links.forEach(function (link) { link.classList.toggle('on', link === current); });
  }
  window.addEventListener('scroll', spy, { passive: true });
  spy();


  // Desktop app only: ask the app whether the data changed; it rebuilds and reloads the page if so.
  var built = document.body.getAttribute('data-built');
  function poll() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.handle && built) {
      window.pywebview.api.handle('millennium://poll/' + built);
    }
  }
  setInterval(poll, 10000);
  setTimeout(poll, 3000);

  // Keep the scroll position across the automatic reloads.
  var y = load('scrollY');
  if (y && load('scrollAt') && Date.now() - parseInt(load('scrollAt'), 10) < 15000) { window.scrollTo(0, parseInt(y, 10)); }
  window.addEventListener('beforeunload', function () { save('scrollY', String(window.scrollY)); save('scrollAt', String(Date.now())); });
})();
"""
