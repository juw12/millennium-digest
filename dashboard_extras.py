"""Extra dashboard sections: "Needs you" with a button on every item, progress per problem, versions and
activity. Buttons are millennium:// links; the desktop app (and action.py in a browser) handles them.
"""
import datetime as dt
import html
import json
import re
from pathlib import Path
from urllib.parse import quote

import history
import progress

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UI_STATE_FILE = HERE / "ui_state.json"  # local: which page changes you already looked at


def ui_state():
    try:
        return json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_ui_state(state):
    UI_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def find_reply_draft(address):
    """Your newest drafted answer to someone who replied: outbox/*reply*.txt whose first line is 'To: <address>'."""
    candidates = sorted((ROOT / "outbox").glob("*reply*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        first = path.read_text(encoding="utf-8").splitlines()[:1]
        if first and first[0].lower().strip() == f"to: {address.lower()}":
            return path
    return None


def _their_words(contact, limit=170):
    """The start of their latest reply (saved by gmail_inbox.py), for the Needs-you card."""
    incoming = (contact.get("reply_draft") or {}).get("incoming")
    if not incoming or not Path(incoming).exists():
        return ""
    body = Path(incoming).read_text(encoding="utf-8").split("\n\n", 1)[-1]
    text = " ".join(body.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def _gmail_check_line():
    try:
        import gmail_inbox

        state = gmail_inbox.load_state()
    except Exception:
        return "Gmail is checked every 2 hours."
    if not state.get("at"):
        return "Gmail is checked every 2 hours."
    at = dt.datetime.fromisoformat(state["at"]).strftime("%d %b %H:%M")
    if not state.get("ok"):
        return f"Gmail check failed at {at}: {state.get('note', '')}"
    return f"Gmail checked {at}; checked again every 2 hours."


def button(label, href, ghost=False, title=""):
    css = "btn ghost" if ghost else "btn"
    tip = f' title="{html.escape(title)}"' if title else ""
    return f'<a class="{css}" href="{html.escape(href)}"{tip}>{html.escape(label)}</a>'


def _item(kind, title, detail, buttons):
    return (
        f'<div class="todo"><span class="todo-dot {kind}"></span>'
        f'<div class="todo-text"><b>{html.escape(title)}</b><span>{html.escape(detail)}</span></div>'
        f'<div class="todo-actions">{"".join(buttons)}</div></div>'
    )


def watched_page_urls(digest_html_path):
    """{page name: url} from the digest's 'Watched pages' lines ('- Name: CHANGED, have a look  https://...')."""
    urls = {}
    md_path = Path(digest_html_path).with_suffix(".md")
    if md_path.exists():
        for line in md_path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"- (.+?): .*?(https?://\S+)\s*$", line)
            if match:
                urls[match.group(1).strip()] = match.group(2)
    return urls


def needs_you(rows, ledger, policy, latest_digest):
    """Every item gets at least one button. rows = dashboard.outreach_items rows."""
    today = dt.date.today()
    items = []
    by_address = {c["to"]: c for c in ledger.get("contacts", [])}
    for row in rows:
        contact = by_address.get(row["address"], {})
        encoded = quote(row["address"])
        if contact.get("follow_up") and contact.get("state") == "drafted" and contact.get("gmail_draft"):
            items.append(_item("mail", f"{row['name']}: follow-up drafted in Gmail",
                               "One polite follow-up (21-day rule), saved as a draft in your thread. "
                               "Read it and press Send, or delete the draft to skip it.", [
                                   button("Open in Gmail", f"millennium://thread/{encoded}"),
                               ]))
        elif row["ready"]:
            items.append(_item("mail", f"{row['name']}: draft ready",
                               "Opens in Gmail with the PDF attached; read it and press Send. "
                               "Sending is noticed automatically.", [
                button("Open draft + PDF", f"millennium://open/{encoded}"),
            ]))
        elif row["state"] == "replied" and not contact.get("answer_sent_date"):
            reply_draft = contact.get("reply_draft") or {}
            if reply_draft.get("gmail"):
                words = _their_words(contact)
                detail = (f"\u201c{html.escape(words)}\u201d " if words else "") + (
                    "Your answer is saved as a draft in that Gmail thread"
                    + (" (auto-written: they asked a question, check it answers)" if reply_draft.get("generated") and reply_draft.get("question") else "")
                    + ". Read it and press Send; sending is noticed automatically."
                )
                incoming = reply_draft.get("incoming")
                buttons = [button("Open in Gmail", f"millennium://reply/{encoded}")]
                if incoming and Path(incoming).exists():
                    buttons.append(button("Their reply", Path(incoming).resolve().as_uri(), ghost=True))
                items.append(_item("mail", f"{row['name']} replied: your answer is drafted", detail, buttons))
                continue
            draft = find_reply_draft(row["address"])
            if draft:
                items.append(_item("mail", f"{row['name']} replied: send your thank-you",
                                   "Your answer is drafted. It opens in Gmail filled in; read it and press Send. "
                                   "Sending is noticed automatically.", [
                                       button("Open reply", f"millennium://reply/{encoded}"),
                                   ]))
            else:
                items.append(_item("mail", f"{row['name']} replied",
                                   "Read the reply in Gmail and answer when ready; your answer is noticed automatically.", [
                    button("Open in Gmail", f"millennium://thread/{encoded}"),
                ]))
        elif row["state"] == "sent" and contact.get("sent_date"):
            due_days = policy.get("follow_up_after_days_without_reply", 21)
            waited = (today - dt.date.fromisoformat(contact["sent_date"])).days
            if waited >= due_days:
                items.append(_item("mail", f"{row['name']}: no reply for {waited} days",
                                   "One polite follow-up is allowed. A reply from them is noticed automatically.", [
                                       button("Open in Gmail", f"millennium://thread/{encoded}"),
                                   ]))
    if not items:
        return (f'<p class="empty">Nothing needs you right now. {html.escape(_gmail_check_line())} '
                f'The next digest runs Monday 09:00. {button("Check Gmail now", "millennium://checkmail/now", ghost=True)}</p>')
    return "".join(items) + (f'<p class="empty">{html.escape(_gmail_check_line())} '
                             f'{button("Check Gmail now", "millennium://checkmail/now", ghost=True)}</p>')


def _stage_bar(stage):
    if stage < 0:
        return '<div class="stages done-all">Solved</div>'
    cells = "".join(
        f'<span class="stage {"on" if index <= stage else ""}" title="{html.escape(label)}"></span>'
        for index, label in enumerate(progress.STAGES)
    )
    return f'<div class="stages">{cells}</div><div class="stage-label">{html.escape(progress.STAGES[stage])}</div>'


def _file_link(path, label):
    path = Path(path) if path else None
    if not path or not path.exists():
        return ""
    return f'<a href="{path.resolve().as_uri()}">{html.escape(label)}</a>'


def _watch_status():
    """One line on what the automation's Clay watch saw last time."""
    try:
        import clay_watch

        state = clay_watch.load_state()
    except Exception:
        return ""
    if not state.get("last_check"):
        return '<p class="watch-line muted small">Clay watch: first check runs with the next digest.</p>'
    listed = state.get("clay_list", [])
    tracked = sum(1 for p in listed if clay_watch.key_for(p["name"], state))
    added = len(state.get("auto_problems", []))
    return (
        f'<p class="watch-line muted small">Watched for you: Clay problem list checked {html.escape(state["last_check"])}, '
        f'{len(listed)} problems listed, {tracked} tracked'
        + (f", {added} added automatically" if added else "")
        + ". New unsolved problems are added here by themselves; page changes show up as Watcher notes.</p>"
    )


def problems_section(ledger, policy):
    import dashboard_interactive as ui

    cards = []
    kinds = {"active": 0, "watching": 0, "solved": 0}
    for problem in progress.problems(ledger, policy):
        if problem["solved"]:
            kinds["solved"] += 1
            cards.append(
                f'<div class="prob solved" data-kind="solved"><div class="prob-head"><b>{html.escape(problem["name"])}</b>'
                f'<span class="chip neutral">solved</span></div><p class="muted">{html.escape(problem["solved"])}</p></div>'
            )
            continue
        kind = "active" if (problem["study"] or problem["emails"] or problem["stage"] > 0) else "watching"
        kinds[kind] += 1
        papers = (
            f'{problem["papers_latest"]} new this week &middot; {problem["papers_total"]} since tracking started'
            if problem["weeks"] else "No digest yet"
        )
        study_html = '<p class="muted">No own study yet: watching papers only.</p>'
        if problem["study"]:
            study = problem["study"]
            gate = '<span class="chip good">gates passed</span>' if study["gates_passed"] else '<span class="chip bad">gates open</span>'
            links = " &middot; ".join(filter(None, [_file_link(study["report"], "Report"), _file_link(study["findings"], "Findings")]))
            study_html = (
                f'<div class="prob-study"><b>{html.escape(study["name"])}</b> {gate}'
                f'<div class="muted small">{html.escape(study["gates"])}</div><div class="small">{links}</div></div>'
            )
        email_html = ""
        for email in problem["emails"]:
            steps = "".join(
                f'<li class="{"ok" if done else "todo-step"}">{html.escape(label)}'
                f'{" &middot; " + html.escape(day.strftime("%d %b").lstrip("0")) if day else ""}</li>'
                for label, day, done in email["steps"]
            )
            email_html += (
                f'<div class="mail"><div class="mail-head"><b>{html.escape(email["name"])}</b>'
                f'<span class="state s-{html.escape(email["state"])}">{html.escape(email["state"])}</span></div>'
                f'<ol class="steps">{steps}</ol>'
                + (f'<div class="muted small">{html.escape(email["note"])}</div>' if email["note"] else "")
                + "</div>"
            )
        if not email_html:
            email_html = '<p class="muted small">No emails on this topic.</p>'
        next_html = (
            f'<div class="next-up"><span>Next on my end</span>{html.escape(problem["next"])}</div>' if problem["next"] else ""
        )
        for note in reversed(problem.get("watch_notes", [])[-2:]):
            next_html += (
                f'<div class="next-up watch"><span>Watcher &middot; {html.escape(note["date"])}</span>'
                f'{html.escape(note["text"])}</div>'
            )
        cards.append(
            f'<div class="prob" data-kind="{kind}"><div class="prob-head"><b>{html.escape(problem["name"])}</b></div>'
            f'{_stage_bar(problem["stage"])}<p class="small">{papers}</p>{study_html}'
            f'<div class="prob-mails"><div class="mini-h">Emails</div>{email_html}</div>{next_html}</div>'
        )
    chips = ui.filter_chips([
        ("all", "All", len(cards)),
        ("active", "Our work", kinds["active"]),
        ("watching", "Watching papers", kinds["watching"]),
        ("solved", "Solved", kinds["solved"]),
    ])
    return (
        '<section class="card" id="progress"><div class="card-head"><div><h2>Progress per problem</h2>'
        '<p class="sub">Papers tracked, our studies, every email step and what happens next. '
        'Nothing here claims progress on a proof.</p></div></div>'
        f'{_watch_status()}'
        f'<div data-fbox="problems"><div class="ftools">{chips}</div>'
        f'<div class="probs">{"".join(cards)}</div></div></section>'
    )


KIND_LABEL = {"email": "Email", "digest": "Digest", "job": "Weekly job", "study": "Study", "app": "App", "button": "You"}


def history_section(ledger):
    import dashboard_interactive as ui

    version_rows = "".join(
        f'<tr><td class="num"><b>{html.escape(v["version"])}</b></td><td class="num">{html.escape(v["date"])}</td>'
        f'<td>{html.escape(v["subject"])}</td><td class="num muted">{html.escape(v["hash"])}</td></tr>'
        for v in history.versions()
    ) or '<tr><td colspan="4" class="muted">git not found</td></tr>'
    events = history.activity(ledger)
    mine = sum(1 for e in events if e.get("kind") == "button")
    activity_rows = "".join(
        f'<tr data-kind="{"you" if e.get("kind") == "button" else "auto"} {html.escape(str(e.get("kind", "")))}" '
        f'data-text="{html.escape(e["text"].lower())}"><td class="num">{html.escape(str(e["at"]).replace("T", " ")[:16])}</td>'
        f'<td><span class="tag">{html.escape(KIND_LABEL.get(e.get("kind"), e.get("kind", "")))}</span></td>'
        f'<td>{html.escape(e["text"])}</td></tr>'
        for e in events
    )
    activity_chips = ui.filter_chips([
        ("all", "All", len(events)),
        ("you", "You", mine),
        ("auto", "Automatic", len(events) - mine),
    ])
    return (
        '<div class="grid2">'
        '<section class="card" id="activity"><div class="card-head"><div><h2>Activity log</h2>'
        '<p class="sub">Everything done so far, newest first: emails, digests, studies, weekly runs and your button presses</p>'
        f'</div></div><div data-fbox="activity" data-limit="8">'
        f'<div class="ftools"><input type="search" class="fsearch" data-search placeholder="Search the log" aria-label="Search the activity log">{activity_chips}</div>'
        f'<div class="table-wrap scroll-y"><table><tr><th>When</th><th>What</th><th></th></tr>{activity_rows}</table></div>'
        '<div class="more-row"><button type="button" class="btn ghost">Show all</button></div>'
        '<p class="fempty muted small" hidden>Nothing matches.</p></div></section>'
        '<section class="card" id="versions"><div class="card-head"><div><h2>Versions</h2>'
        '<p class="sub">Every published change of this tool (github.com/juw12/millennium-digest)</p></div></div>'
        f'<div class="table-wrap scroll-y"><table><tr><th>Version</th><th>Date</th><th>Change</th><th></th></tr>{version_rows}</table></div></section>'
        '</div>'
    )


EXTRA_CSS = """
.probs { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:14px; }
.prob { border:1px solid var(--line); border-radius:12px; padding:14px; background:#fcfcfd; display:flex; flex-direction:column; gap:8px; min-width:0; }
.prob.solved { opacity:.75; }
.next-up.watch { border-left-color: var(--warn, #b7791f); }
.watch-line { margin: 0 0 10px; }
.prob-head { display:flex; justify-content:space-between; align-items:center; gap:8px; font-size:14.5px; }
.stages { display:grid; grid-template-columns:repeat(5,1fr); gap:4px; }
.stage { height:6px; border-radius:99px; background:#e2e8f0; }
.stage.on { background:var(--accent); }
.stage-label { font-size:12px; color:var(--accent); font-weight:600; margin-top:-2px; }
.small { font-size:12.5px; margin:0; }
.muted { color:var(--muted); }
.prob-study { background:#fff; border:1px solid var(--line); border-radius:9px; padding:8px 10px; }
.mini-h { font-size:11.5px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; margin-bottom:4px; }
.mail { border-top:1px dashed var(--line); padding:7px 0; }
.mail:first-of-type { border-top:0; }
.mail-head { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.steps { margin:5px 0 2px; padding-left:18px; font-size:12.5px; }
.steps li.ok { color:var(--ink2); }
.steps li.ok::marker { color:var(--good); }
.steps li.todo-step { color:var(--muted); }
.next-up { background:var(--accent-soft); border-radius:9px; padding:8px 10px; font-size:12.5px; color:var(--ink2); }
.next-up span { display:block; font-size:11.5px; font-weight:700; color:var(--accent); text-transform:uppercase; letter-spacing:.04em; }
.s-sent { background:#fffbeb; color:#b45309; } .s-replied { background:#ecfdf3; color:#15803d; } .s-drafted { background:#f0fdfa; color:#115e59; }
.scroll-y { max-height:420px; overflow-y:auto; }
.btn { cursor:pointer; min-height:32px; }
"""
