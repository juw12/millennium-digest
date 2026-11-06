"""Private local dashboard: one page with everything the research automation knows.

Reads local files only (digests, outreach ledger, study evidence, scheduled-task state) and writes
dashboard/index.html, which is git-ignored and never uploaded.

Usage:
  python dashboard.py          rebuild
  python dashboard.py --open   rebuild and open in the browser
"""
import datetime as dt
import html
import json
import re
import subprocess
import sys
import webbrowser
from urllib.parse import quote
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "dashboard" / "index.html"
TASK_NAME = "Millennium research digest"
PUBLIC_PAGE = "https://juw12.github.io/millennium-digest/"
REPO = "https://github.com/juw12/millennium-digest"


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def task_status():
    script = (
        f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; "
        "if ($t) { $i = $t | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{state=\"$($t.State)\"; last=\"$($i.LastRunTime)\"; "
        "result=$i.LastTaskResult; next=\"$($i.NextRunTime)\"} | ConvertTo-Json }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return json.loads(result.stdout) if result.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def digest_history():
    history = []
    for path in sorted((HERE / "digests").glob("digest_*.md")):
        counts = {}
        changed_pages = []
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"## (.+?) \((\d+) new\)", line)
            if match:
                counts[match.group(1)] = int(match.group(2))
            if "CHANGED" in line:
                changed_pages.append(line.split(":")[0].lstrip("- ").strip())
        history.append({
            "date": path.stem.replace("digest_", ""),
            "counts": counts,
            "changed": changed_pages,
            "html": path.with_suffix(".html"),
        })
    return history


def outreach_items(ledger, policy):
    today = dt.date.today()
    rows = []
    actions = []
    recent_new = 0
    for contact in ledger.get("contacts", []):
        state = contact.get("state", "")
        first_date = dt.date.fromisoformat(contact["date"])
        if (today - first_date).days < 30 and not contact.get("follow_up"):
            recent_new += 1
        hold = contact.get("hold_until")
        on_hold = bool(hold) and dt.date.fromisoformat(hold) > today
        # same rule as outreach.py send: an active hold on anyone else in the topic blocks this draft
        topic_holds = [
            other for other in ledger.get("contacts", [])
            if other.get("topic") == contact.get("topic")
            and other["to"] != contact["to"]
            and other.get("hold_until")
            and dt.date.fromisoformat(other["hold_until"]) > today
        ]
        sent_date = contact.get("sent_date")
        days_waiting = (today - dt.date.fromisoformat(sent_date)).days if sent_date else None

        next_step = ""
        if state == "replied":
            next_step = "Replied - read and answer when ready"
        elif state == "sent" and days_waiting is not None:
            due = policy.get("follow_up_after_days_without_reply", 21)
            if days_waiting >= due:
                next_step = "Follow-up allowed now (one only)"
                actions.append(f"{contact['name']}: no reply for {days_waiting} days - one polite follow-up allowed")
            else:
                next_step = f"Waiting for reply ({days_waiting} of {due} days)"
        elif state == "drafted":
            if topic_holds:
                waiting_for = topic_holds[0]
                next_step = (
                    f"On hold until {waiting_for['hold_until']} "
                    f"(waiting for {waiting_for.get('name', waiting_for['to'])})"
                )
            elif on_hold:
                next_step = f"On hold until {hold}"
            else:
                next_step = "Draft ready - review and send yourself"
                actions.append((f"{contact['name']}: draft is ready - open it, attach the PDF, press Send", contact["to"]))
        elif state in ("declined", "do-not-contact"):
            next_step = "Closed"
        rows.append({
            "address": contact["to"],
            "ready": state == "drafted" and not topic_holds and not on_hold,
            "name": contact.get("name", contact["to"]),
            "topic": contact.get("topic", ""),
            "state": state,
            "date": sent_date or contact["date"],
            "next": next_step,
        })
        if hold and on_hold and (dt.date.fromisoformat(hold) - today).days <= 7:
            actions.append(f"{contact['name']}: hold ends {hold}")
    limit = policy.get("max_new_drafts_per_30_days", 2)
    return rows, actions, recent_new, limit


def studies():
    found = []
    for evidence_path in sorted(ROOT.glob("*_project/EVIDENCE.json")):
        evidence = read_json(evidence_path, {})
        found.append({
            "name": evidence.get("study", evidence_path.parent.name),
            "gates": evidence.get("gates", ""),
            "passed": evidence.get("all_gates_passed"),
            "report": evidence.get("report", ""),
            "findings": evidence_path.parent / "FINDINGS.md",
        })
    return found


def theory_lines():
    summary = read_json(ROOT / "bsd_project" / "theory" / "theory_summary.json")
    if not summary:
        return []
    exact = summary["exact_vs_closed_form"]["2000_4000"]
    curves = summary["elliptic_curves_2000_4000"]
    return [
        f"Closed forms vs exact trace formula (N 2000-4000): max deviation "
        f"{exact['even']['max_abs_dev']:.3f} (even N), {exact['odd']['max_abs_dev']:.3f} (odd N)",
        f"Elliptic curves follow the even-N shape (correlation {curves['even']['shape_corr']:.2f}); "
        f"odd-N match is weaker ({curves['odd']['shape_corr']:.2f})",
        f"Open: elliptic-curve even/odd ratio {curves['ec_even_over_odd_lsq']:.2f} vs "
        f"{exact['theory_even_over_odd_lsq']:.2f} predicted",
    ]


def last_weekly_lines():
    log_path = HERE / "weekly.log"
    if not log_path.exists():
        return ["No automatic weekly run yet (first one is the next Monday 09:00)."]
    lines = log_path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if "weekly run started" in line]
    return lines[starts[-1]:] if starts else lines[-8:]


def file_link(path, label):
    path = Path(path)
    if not path.exists():
        return html.escape(label) + ' <span class="muted">(missing)</span>'
    return f'<a href="{path.resolve().as_uri()}">{html.escape(label)}</a>'


def draft_buttons(address):
    encoded = quote(address)
    return (
        f'<a class="btn" href="millennium://open/{encoded}">Open draft</a>'
        f'<a class="btn ghost" href="millennium://sent/{encoded}">I sent it</a>'
    )


def render_action(action):
    if isinstance(action, tuple):
        text_part, address = action
        return f"<li>{html.escape(text_part)} {draft_buttons(address)}</li>"
    return f"<li>{html.escape(action)}</li>"


def build():
    policy = read_json(HERE / "outreach_policy.json", {})
    ledger = read_json(HERE / "contacts_log.json", {"contacts": []})
    task = task_status()
    history = digest_history()
    rows, actions, recent_new, limit = outreach_items(ledger, policy)
    latest = history[-1] if history else None

    if latest and latest["changed"]:
        actions.append("Watched page changed: " + ", ".join(latest["changed"]) + " - have a look")
    if task and str(task.get("state")) != "Ready" and str(task.get("state")) != "3":
        actions.append(f"Weekly task state is {task.get('state')} - check it")

    if task:
        result_code = task.get("result")
        never_ran = result_code == 267011
        result_text = "not run yet" if never_ran else ("OK" if result_code == 0 else f"code {result_code}")
        last_text = "-" if never_ran else task.get("last", "-")
        task_html = (
            f'<span class="pill ok">Weekly task: on</span>'
            f'<span class="pill">Last run: {html.escape(str(last_text))} ({result_text})</span>'
            f'<span class="pill">Next run: {html.escape(str(task.get("next", "-")))}</span>'
        )
    else:
        task_html = '<span class="pill bad">Weekly task not found</span>'

    action_html = "".join(render_action(a) for a in actions) or "<li>Nothing needs you right now.</li>"

    paper_html = ""
    if latest:
        topics = list(latest["counts"].keys())
        peak = max([1] + [c for d in history for c in d["counts"].values()])
        for topic in topics:
            series = [d["counts"].get(topic, 0) for d in history[-8:]]
            bars = "".join(
                f'<span class="bar" style="height:{4 + 36 * value / peak:.0f}px" title="{value}"></span>'
                for value in series
            )
            paper_html += (
                f"<tr><td>{html.escape(topic)}</td><td class='num'>{latest['counts'][topic]}</td>"
                f"<td><div class='spark'>{bars}</div></td></tr>"
            )
    weeks_note = f"{len(history)} digest(s) so far; bars show up to the last 8 weeks." if history else "No digest yet."

    outreach_html = "".join(
        f"<tr><td>{html.escape(r['name'])}</td><td>{html.escape(r['topic'])}</td>"
        f"<td><span class='state {html.escape(r['state'])}'>{html.escape(r['state'])}</span></td>"
        f"<td>{html.escape(r['date'])}</td><td>{html.escape(r['next'])}</td>"
        f"<td>{draft_buttons(r['address']) if r['ready'] else ''}</td></tr>"
        for r in rows
    )
    budget_class = "bad" if recent_new > limit else "ok"
    budget_html = (
        f'<span class="pill {budget_class}">New contacts in last 30 days: {recent_new} (rule: max {limit})</span>'
    )

    study_html = ""
    for study in studies():
        status = "gates passed" if study["passed"] else "gates not passed"
        study_html += (
            f"<div class='card'><h3>{html.escape(study['name'])}</h3>"
            f"<p class='muted'>{html.escape(status)}: {html.escape(study['gates'])}</p>"
            f"<p>{file_link(study['report'], 'Open report (PDF)')} &middot; {file_link(study['findings'], 'Findings')}</p></div>"
        )
    theory = theory_lines()
    if theory:
        study_html += (
            "<div class='card'><h3>Latest theory check (murmurations, parity of the conductor)</h3><ul>"
            + "".join(f"<li>{html.escape(t)}</li>" for t in theory)
            + "</ul><p>"
            + file_link(ROOT / "bsd_project" / "theory" / "Parity_closed_forms_note.pdf", "Open note (PDF)")
            + "</p></div>"
        )

    weekly_html = "".join(f"<div>{html.escape(line)}</div>" for line in last_weekly_lines())
    latest_link = file_link(latest["html"], f"Open full digest {latest['date']}") if latest else ""
    built = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Millennium Dashboard</title>
<style>
 :root {{ --ink:#1c2230; --muted:#5b6475; --line:#e3e6ec; --bg:#f6f5f2; --accent:#5b3cc4; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; font-family:"Segoe UI",system-ui,Arial,sans-serif; background:var(--bg); color:var(--ink); }}
 header {{ background:#14111f; color:#f4f1ff; padding:22px 24px; }}
 header h1 {{ margin:0 0 10px; font-size:1.6rem; }}
 main {{ max-width:1100px; margin:0 auto; padding:20px; display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); }}
 section {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
 section.wide {{ grid-column:1/-1; }}
 h2 {{ font-size:1.05rem; margin:0 0 10px; color:var(--accent); }}
 h3 {{ font-size:.98rem; margin:0 0 4px; }}
 .pill {{ display:inline-block; padding:4px 10px; border-radius:999px; margin:0 6px 6px 0; font-size:.85rem; background:#2a2440; color:#e6e0ff; }}
 section .pill {{ background:#eef0f4; color:var(--ink); }}
 .pill.ok {{ background:#1f6b46; color:#fff; }} .pill.bad {{ background:#a3261b; color:#fff; }}
 table {{ width:100%; border-collapse:collapse; font-size:.92rem; }}
 td, th {{ text-align:left; padding:6px 4px; border-top:1px solid #f0f1f4; vertical-align:middle; }}
 th {{ color:var(--muted); font-weight:600; border-top:0; }}
 .num {{ text-align:right; font-variant-numeric:tabular-nums; width:3em; }}
 .spark {{ display:flex; align-items:flex-end; gap:3px; height:40px; }}
 .bar {{ width:10px; background:#8f78e6; border-radius:2px; }}
 .state {{ padding:2px 8px; border-radius:6px; font-size:.82rem; background:#eef0f4; }}
 .state.replied {{ background:#d8f3e3; }} .state.sent {{ background:#e3e9ff; }} .state.drafted {{ background:#fff1cc; }}
 .card {{ border:1px solid var(--line); border-radius:10px; padding:12px; margin:0 0 10px; }}
 .muted {{ color:var(--muted); font-size:.88rem; }}
 .log {{ font-family:Consolas,monospace; font-size:.82rem; color:var(--muted); }}
 ul {{ margin:6px 0; padding-left:20px; }}
 a {{ color:#4b2aa8; }}
 .btn {{ display:inline-block; padding:4px 10px; margin:2px 4px 2px 0; border-radius:7px; background:#5b3cc4; color:#fff; text-decoration:none; font-size:.85rem; font-weight:600; white-space:nowrap; }}
 .btn.ghost {{ background:#fff; color:#5b3cc4; border:1px solid #5b3cc4; }}
 .btn:hover {{ filter:brightness(1.1); }}
</style></head>
<body>
<header><h1>Millennium Dashboard</h1>{task_html}
<div class="muted" style="color:#bdb3e6">Built {built}. Private: this page stays on your PC.
<a style="color:#d6ccff" href="{PUBLIC_PAGE}">Public page</a> &middot; <a style="color:#d6ccff" href="{REPO}">Code</a></div></header>
<main>
 <section class="wide"><h2>Needs you</h2><ul>{action_html}</ul></section>
 <section><h2>New papers this week</h2><table><tr><th>Topic</th><th class="num">New</th><th>Trend</th></tr>{paper_html}</table>
  <p class="muted">{weeks_note} {latest_link}</p></section>
 <section class="wide"><h2>Expert outreach</h2>{budget_html}
  <table><tr><th>Who</th><th>Topic</th><th>State</th><th>Date</th><th>Next step</th><th></th></tr>{outreach_html}</table>
  <p class="muted"><b>Open draft</b>: Gmail opens filled in and the PDF is highlighted in File Explorer - drag it into the email and press Send yourself. Then click <b>I sent it</b> to log it. Nothing is ever sent automatically. Rules: outreach_policy.json.</p></section>
 <section class="wide"><h2>Research studies</h2>{study_html}</section>
 <section class="wide"><h2>Last automatic run</h2><div class="log">{weekly_html}</div></section>
</main></body></html>
"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT}: {len(rows)} contacts, {len(history)} digests, {len(actions)} action(s)")


if __name__ == "__main__":
    build()
    if "--open" in sys.argv:
        edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        if edge.exists():
            subprocess.Popen([str(edge), "--new-window", OUT.resolve().as_uri()])
        else:
            webbrowser.open(OUT.resolve().as_uri())
