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
import math
import re
import subprocess
import sys
import webbrowser
from urllib.parse import quote

import dashboard_charts as charts
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


ACCENT = "#4f46e5"
TEAL = "#0d9488"
AMBER = "#d97706"
ROSE = "#e11d48"
GREEN = "#16a34a"
SLATE = "#94a3b8"


def draft_buttons(address):
    encoded = quote(address)
    return (
        f'<a class="btn" href="millennium://open/{encoded}">Open draft</a>'
        f'<a class="btn ghost" href="millennium://sent/{encoded}">I sent it</a>'
    )


def render_action(action):
    if isinstance(action, tuple):
        text_part, address = action
        name, _, detail = text_part.partition(": ")
        return (
            f'<div class="todo"><span class="todo-dot mail"></span>'
            f'<div class="todo-text"><b>{html.escape(name)}</b><span>{html.escape(detail)}</span></div>'
            f'<div class="todo-actions">{draft_buttons(address)}</div></div>'
        )
    name, _, detail = action.partition(": ")
    return (
        f'<div class="todo"><span class="todo-dot info"></span>'
        f'<div class="todo-text"><b>{html.escape(name)}</b><span>{html.escape(detail)}</span></div></div>'
    )


def pretty_datetime(value):
    for pattern in ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            moment = dt.datetime.strptime(str(value), pattern)
            return moment.strftime("%a %d %b, %H:%M").replace(" 0", " ")
        except ValueError:
            continue
    return str(value)


def last_run_summary():
    lines = last_weekly_lines()
    if not lines or "No automatic" in lines[0]:
        return "Not yet", "First run Monday 09:00", "neutral"
    stamp = lines[0][:16]
    failed = any("ERROR" in line or "exit 1" in line or "BLOCKED" in line for line in lines)
    finished = any("weekly run finished" in line for line in lines)
    if failed or not finished:
        return "Check log", pretty_datetime(stamp + ":00"), "bad"
    return "OK", pretty_datetime(stamp + ":00"), "good"


def murmuration_chart():
    summary = read_json(ROOT / "bsd_project" / "theory" / "theory_summary.json")
    curves = read_json(ROOT / "bsd_project" / "theory" / "ec_parity_curves.json")
    if not summary or not curves:
        return None
    constants = summary["constants"]
    alpha, beta, gamma = constants["alpha"], constants["beta"], constants["gamma"]

    def closed_form(y, parity):
        step = math.sqrt(4 * y - 1) if y > 0.25 else 0.0
        if parity == "odd":
            return (10 / 11) * beta * math.sqrt(y) - (5 / 6) * gamma * y + (5 / 7) * alpha * step
        return (15 / 11) * beta * math.sqrt(y) - (5 / 3) * gamma * y + (15 / 7) * alpha * step

    data = curves["2000_4000"]
    centres = [0.0125 + 0.025 * i for i in range(len(data["curve_even"]))]
    fine = [0.002 + i * 0.498 / 240 for i in range(241)]
    series = [
        {"xs": fine, "ys": [closed_form(y, "even") for y in fine], "color": ACCENT, "style": "dash"},
        {"xs": fine, "ys": [closed_form(y, "odd") for y in fine], "color": TEAL, "style": "dash"},
        {"xs": centres, "ys": data["curve_even"], "color": ACCENT, "style": "line"},
        {"xs": centres, "ys": data["curve_odd"], "color": TEAL, "style": "line"},
    ]
    svg = charts.line_chart(
        series,
        x_range=(0, 0.5),
        y_range=(-3, 3.5),
        x_label="y = p / N",
        y_label="average a_p x root number",
        vlines=[(0.25, "y = 1/4")],
        aria="Murmuration density: theory vs elliptic curves",
    )
    legend = charts.legend([
        ("Elliptic curves, 2 | N", ACCENT, "line"),
        ("Elliptic curves, N odd", TEAL, "line"),
        ("Closed form, 2 | N", ACCENT, "dash"),
        ("Closed form, N odd", TEAL, "dash"),
    ])
    exact = summary["exact_vs_closed_form"]["2000_4000"]
    ec = summary["elliptic_curves_2000_4000"]
    stats = [
        ("Theory vs exact", f"{max(exact['even']['max_abs_dev'], exact['odd']['max_abs_dev']):.3f}", "max deviation"),
        ("Shape match, 2 | N", f"{ec['even']['shape_corr']:.2f}", "correlation"),
        ("Shape match, N odd", f"{ec['odd']['shape_corr']:.2f}", "correlation"),
        ("Even / odd ratio", f"{ec['ec_even_over_odd_lsq']:.2f}", f"vs {exact['theory_even_over_odd_lsq']:.2f} predicted"),
    ]
    return svg, legend, stats


def zeros_chart():
    rh = ROOT / "rh_project"
    e3 = read_json(rh / "exploratory_e3_results.json")
    analysis = read_json(rh / "analysis_results.json")
    if not e3 or not analysis:
        return None
    bins = e3["E3a_log_bins"]["bins"]
    xs = [b["N_eff"] for b in bins]
    ys = [b["amplitude_on_data_shape"] for b in bins]
    # least-squares fit of log A = log c - k log N (same law as the exploratory analysis)
    lx = [math.log(x) for x in xs]
    ly = [math.log(y) for y in ys]
    mean_x = sum(lx) / len(lx)
    mean_y = sum(ly) / len(ly)
    slope = sum((a - mean_x) * (b - mean_y) for a, b in zip(lx, ly)) / sum((a - mean_x) ** 2 for a in lx)
    intercept = mean_y - slope * mean_x
    x_low, x_high = min(xs) * 0.95, max(xs) * 1.05
    fine = [x_low + i * (x_high - x_low) / 120 for i in range(121)]
    fit = [math.exp(intercept) * x ** slope for x in fine]
    svg = charts.line_chart(
        [
            {"xs": fine, "ys": fit, "color": AMBER, "style": "dash"},
            {"xs": xs, "ys": ys, "color": ROSE, "style": "dots"},
        ],
        x_range=(x_low, x_high),
        y_range=(0, max(ys) * 1.15),
        x_label="effective matrix size N_eff (higher zeros ->)",
        y_label="correction amplitude",
        zero_line=False,
        aria="Riemann zero spacing correction vs effective size",
    )
    k = e3["E3a_log_bins"]["k"]
    low, high = e3["E3a_log_bins"]["CI95"]
    legend = charts.legend([
        ("Measured (7 height bins)", ROSE, "dots"),
        (f"Fit A ~ 1/N^{-slope:.2f}", AMBER, "dash"),
    ])
    stats = [
        ("H1 (pre-registered)", f"{analysis['H1']['A']:.2f}", "inconclusive"),
        ("H2 BBLM amplitude", f"{analysis['H2']['A']:.2f}", "failed (expected 1)"),
        ("H3 decay exponent", f"{analysis['H3']['k']:.2f}", "failed"),
        ("Exploratory k", f"{k:.2f}", f"95% CI {low:.2f}-{high:.2f}"),
    ]
    return svg, legend, stats


def short_date(iso_value):
    try:
        return dt.date.fromisoformat(iso_value).strftime("%d %b").lstrip("0")
    except ValueError:
        return iso_value


def short_next(text_value):
    match = re.match(r"Waiting for reply \((\d+) of (\d+) days\)", text_value)
    if match:
        return f"Waiting for reply, day {match.group(1)} of {match.group(2)}"
    match = re.match(r"On hold until (\d{4}-\d{2}-\d{2}) \(waiting for (.+)\)", text_value)
    if match:
        return f"On hold to {short_date(match.group(1))}, waiting for {match.group(2).split()[-1]}"
    return text_value.replace(" - ", ", ")


def stat_grid(stats):
    return '<div class="stats">' + "".join(
        f'<div class="stat"><span class="stat-label">{html.escape(label)}</span>'
        f'<span class="stat-value">{html.escape(value)}</span><span class="stat-note">{html.escape(note)}</span></div>'
        for label, value, note in stats
    ) + "</div>"


def build():
    policy = read_json(HERE / "outreach_policy.json", {})
    ledger = read_json(HERE / "contacts_log.json", {"contacts": []})
    task = task_status()
    history = digest_history()
    rows, actions, recent_new, limit = outreach_items(ledger, policy)
    latest = history[-1] if history else None

    if latest and latest["changed"]:
        actions.append("Watched page changed: " + ", ".join(latest["changed"]) + " - have a look")

    # header status
    if task:
        task_chip = f'<span class="chip good"><span class="dot"></span>Weekly job on</span>'
        next_text = pretty_datetime(task.get("next", ""))
    else:
        task_chip = '<span class="chip bad"><span class="dot"></span>Weekly job missing</span>'
        next_text = "-"

    # KPIs
    paper_total = sum(latest["counts"].values()) if latest else 0
    ready_count = sum(1 for r in rows if r["ready"])
    waiting_count = sum(1 for r in rows if r["state"] == "sent")
    run_value, run_note, run_tone = last_run_summary()
    budget_tone = "bad" if recent_new > limit else "good"
    budget_pct = min(100, 100 * recent_new / max(limit, 1))
    kpis = [
        ("New papers", str(paper_total), f"week to {latest['date']}" if latest else "no digest yet", "neutral", None),
        ("Drafts ready", str(ready_count), "need your Send", "accent" if ready_count else "neutral", None),
        ("Awaiting reply", str(waiting_count), "21-day follow-up rule", "neutral", None),
        ("New contacts, 30 days", f"{recent_new} / {limit}", "over your rule" if recent_new > limit else "within rule",
         budget_tone, budget_pct),
        ("Last weekly run", run_value, run_note, run_tone, None),
    ]
    kpi_html = ""
    for label, value, note, tone, meter in kpis:
        meter_html = (
            f'<div class="meter"><span style="width:{meter:.0f}%"></span></div>' if meter is not None else ""
        )
        kpi_html += (
            f'<div class="kpi {tone}"><span class="kpi-label">{html.escape(label)}</span>'
            f'<span class="kpi-value">{html.escape(value)}</span>{meter_html}'
            f'<span class="kpi-note">{html.escape(note)}</span></div>'
        )

    todo_html = "".join(render_action(a) for a in actions) or '<p class="empty">Nothing needs you right now.</p>'

    # papers chart
    if latest:
        short = {
            "Riemann zeros: spacing statistics (our RH study)": "Riemann zeros (our RH study)",
            "Birch and Swinnerton-Dyer": "Birch & Swinnerton-Dyer",
        }
        papers_svg = charts.hbar_chart(
            [(short.get(name, name), count) for name, count in latest["counts"].items()],
            color=ACCENT,
        )
        papers_note = (
            f"{len(history)} weekly digest so far; trends appear from next week. "
            + file_link(latest["html"], "Open full digest")
        )
    else:
        papers_svg = '<p class="empty">No digest yet.</p>'
        papers_note = ""

    # outreach pipeline
    on_hold = sum(1 for r in rows if r["state"] == "drafted" and not r["ready"])
    pipeline_svg = charts.stacked_bar([
        ("Ready to send", ready_count, ACCENT),
        ("On hold", on_hold, SLATE),
        ("Sent, waiting", waiting_count, AMBER),
        ("Replied", sum(1 for r in rows if r["state"] == "replied"), GREEN),
    ])
    state_class = {"drafted": "s-draft", "sent": "s-sent", "replied": "s-replied"}
    outreach_rows = "".join(
        f"<tr><td class='who'><b>{html.escape(r['name'])}</b></td><td><span class='tag'>{html.escape(r['topic'])}</span></td>"
        f"<td><span class='state {state_class.get(r['state'], '')}'>{html.escape(r['state'])}</span></td>"
        f"<td class='num'>{html.escape(short_date(r['date']))}</td><td class='next'>{html.escape(short_next(r['next']))}</td>"
        f"<td class='acts'>{draft_buttons(r['address']) if r['ready'] else ''}</td></tr>"
        for r in rows
    )

    # research
    research_html = ""
    murm = murmuration_chart()
    if murm:
        svg, legend, stats = murm
        research_html += (
            '<section class="card"><div class="card-head"><div><h2>Murmurations by conductor parity</h2>'
            '<p class="sub">Birch and Swinnerton-Dyer study &middot; elliptic curves with squarefree N 2000-4000</p></div>'
            '<span class="chip neutral">heuristic, not proved</span></div>'
            f'<div class="chart">{svg}</div>{legend}{stat_grid(stats)}'
            '<p class="links">'
            + file_link(ROOT / "bsd_project" / "theory" / "Parity_closed_forms_note.pdf", "Theory note (PDF)") + " &middot; "
            + file_link(ROOT / "bsd_project" / "report" / "Murmuration_amplitude_report.pdf", "Study report (PDF)") + " &middot; "
            + file_link(ROOT / "bsd_project" / "FINDINGS.md", "Findings")
            + "</p></section>"
        )
    zeros = zeros_chart()
    if zeros:
        svg, legend, stats = zeros
        research_html += (
            '<section class="card"><div class="card-head"><div><h2>Riemann zero spacing corrections</h2>'
            '<p class="sub">Riemann Hypothesis study &middot; finite-height correction vs random-matrix size</p></div>'
            '<span class="chip neutral">exploratory</span></div>'
            f'<div class="chart">{svg}</div>{legend}{stat_grid(stats)}'
            '<p class="links">'
            + file_link(ROOT / "rh_project" / "report" / "RH_spacing_corrections_report.pdf", "Study report (PDF)") + " &middot; "
            + file_link(ROOT / "rh_project" / "PREREGISTRATION.md", "Pre-registration") + " &middot; "
            + file_link(ROOT / "rh_project" / "FINDINGS.md", "Findings")
            + "</p></section>"
        )

    log_html = "".join(f"<div>{html.escape(line)}</div>" for line in last_weekly_lines())
    built = dt.datetime.now().strftime("%a %d %b %Y, %H:%M").replace(" 0", " ")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Millennium Dashboard</title>
<style>
:root {{
  --bg:#f6f7f9; --card:#fff; --ink:#0f172a; --ink2:#334155; --muted:#64748b; --line:#e5e7eb;
  --accent:{ACCENT}; --accent-soft:#eef2ff; --good:{GREEN}; --bad:{ROSE}; --amber:{AMBER};
  --radius:14px; --shadow:0 1px 2px rgba(15,23,42,.04), 0 4px 16px rgba(15,23,42,.04);
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:"Segoe UI Variable","Segoe UI",system-ui,-apple-system,sans-serif; font-size:14px; line-height:1.5; }}
a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.top {{ background:var(--card); border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5; }}
.top-in {{ max-width:1180px; margin:0 auto; padding:14px 24px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; }}
.brand {{ display:flex; align-items:center; gap:12px; margin-right:auto; }}
.logo {{ width:34px; height:34px; border-radius:9px; background:linear-gradient(135deg,#4f46e5,#7c3aed);
  color:#fff; display:grid; place-items:center; font-weight:700; font-size:15px; }}
.brand h1 {{ font-size:16px; margin:0; letter-spacing:-.01em; }}
.brand p {{ margin:0; color:var(--muted); font-size:12px; }}
.top-meta {{ display:flex; align-items:center; gap:14px; color:var(--muted); font-size:12.5px; flex-wrap:wrap; }}
.chip {{ display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:600;
  background:#f1f5f9; color:var(--ink2); white-space:nowrap; }}
.chip .dot {{ width:7px; height:7px; border-radius:50%; background:currentColor; }}
.chip.good {{ background:#ecfdf3; color:#15803d; }} .chip.bad {{ background:#fff1f2; color:#be123c; }}
main {{ max-width:1180px; margin:0 auto; padding:22px 24px 40px; display:grid; gap:18px; }}
.kpis {{ display:grid; grid-template-columns:repeat(5,1fr); gap:14px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:14px 16px; box-shadow:var(--shadow);
  display:flex; flex-direction:column; gap:2px; position:relative; overflow:hidden; }}
.kpi::before {{ content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:transparent; }}
.kpi.accent::before {{ background:var(--accent); }} .kpi.bad::before {{ background:var(--bad); }} .kpi.good::before {{ background:var(--good); }}
.kpi-label {{ color:var(--muted); font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }}
.kpi-value {{ font-size:26px; font-weight:700; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }}
.kpi.bad .kpi-value {{ color:var(--bad); }} .kpi.accent .kpi-value {{ color:var(--accent); }}
.kpi-note {{ color:var(--muted); font-size:12px; }}
.meter {{ height:5px; background:#f1f5f9; border-radius:99px; margin:4px 0 2px; overflow:hidden; }}
.meter span {{ display:block; height:100%; background:var(--bad); border-radius:99px; }}
.kpi.good .meter span {{ background:var(--good); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); padding:18px 20px; box-shadow:var(--shadow); min-width:0; }}
.card-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:12px; }}
h2 {{ font-size:15px; margin:0; letter-spacing:-.01em; }}
.sub {{ color:var(--muted); font-size:12.5px; margin:2px 0 0; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.todo {{ display:flex; align-items:center; gap:12px; padding:11px 0; border-top:1px solid var(--line); }}
.todo:first-child {{ border-top:0; padding-top:2px; }}
.todo-dot {{ width:9px; height:9px; border-radius:50%; flex:none; }}
.todo-dot.mail {{ background:var(--accent); box-shadow:0 0 0 4px var(--accent-soft); }}
.todo-dot.info {{ background:var(--amber); box-shadow:0 0 0 4px #fef3c7; }}
.todo-text {{ display:flex; flex-direction:column; flex:1; min-width:0; }}
.todo-text span {{ color:var(--muted); font-size:13px; }}
.todo-actions {{ display:flex; gap:6px; flex-wrap:wrap; }}
.btn {{ display:inline-flex; align-items:center; padding:6px 12px; border-radius:8px; background:var(--accent); color:#fff;
  font-size:12.5px; font-weight:600; white-space:nowrap; border:1px solid var(--accent); }}
.btn:hover {{ text-decoration:none; filter:brightness(1.08); }}
.btn.ghost {{ background:#fff; color:var(--accent); border-color:#c7d2fe; }}
.chart {{ width:100%; }}
.legend {{ display:flex; flex-wrap:wrap; gap:6px 16px; margin:6px 0 2px; color:var(--ink2); font-size:12px; }}
.lg {{ display:inline-flex; align-items:center; gap:6px; }}
.lg-line {{ width:20px; border-top:2.5px solid; }} .lg-line.dash {{ border-top-style:dashed; }}
.lg-dot {{ width:9px; height:9px; border-radius:50%; }}
.stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:14px; }}
.stat {{ background:#f8fafc; border:1px solid var(--line); border-radius:10px; padding:9px 11px; display:flex; flex-direction:column; }}
.stat-label {{ color:var(--muted); font-size:11.5px; }}
.stat-value {{ font-size:18px; font-weight:700; font-variant-numeric:tabular-nums; }}
.stat-note {{ color:var(--muted); font-size:11.5px; }}
.links {{ margin:12px 0 0; font-size:12.5px; color:var(--muted); }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; color:var(--muted); font-size:11.5px; font-weight:600; text-transform:uppercase; letter-spacing:.04em;
  padding:8px 10px; border-bottom:1px solid var(--line); }}
td {{ padding:11px 10px; border-bottom:1px solid #f1f5f9; vertical-align:middle; }}
tr:last-child td {{ border-bottom:0; }}
td.num {{ font-variant-numeric:tabular-nums; color:var(--ink2); white-space:nowrap; }}
td.next {{ color:var(--ink2); }} td.who {{ white-space:nowrap; }} td.acts {{ text-align:right; white-space:nowrap; }}
.tag {{ font-size:11.5px; font-weight:600; color:var(--ink2); background:#f1f5f9; padding:2px 8px; border-radius:6px; }}
.state {{ font-size:12px; font-weight:600; padding:3px 9px; border-radius:999px; background:#f1f5f9; color:var(--ink2); }}
.s-draft {{ background:#eef2ff; color:#4338ca; }} .s-sent {{ background:#fffbeb; color:#b45309; }} .s-replied {{ background:#ecfdf3; color:#15803d; }}
.note {{ color:var(--muted); font-size:12.5px; margin:12px 0 0; }}
.empty {{ color:var(--muted); margin:0; }}
details summary {{ cursor:pointer; font-weight:600; font-size:14px; }}
.log {{ font-family:"Cascadia Mono",Consolas,monospace; font-size:12px; color:var(--ink2); background:#f8fafc;
  border:1px solid var(--line); border-radius:10px; padding:10px 12px; margin-top:10px; overflow-x:auto; white-space:nowrap; }}
.hbars {{ display:grid; gap:9px; }}
.hb-row {{ display:grid; grid-template-columns:minmax(120px,210px) 1fr 28px; align-items:center; gap:10px; font-size:13px; }}
.hb-label {{ color:var(--ink2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.hb-track {{ height:14px; background:#f1f5f9; border-radius:5px; overflow:hidden; }}
.hb-bar {{ display:block; height:100%; border-radius:5px; }}
.hb-value {{ font-weight:700; font-variant-numeric:tabular-nums; }} .hb-value.zero {{ color:var(--muted); font-weight:500; }}
.sb {{ display:flex; gap:3px; height:34px; margin-bottom:8px; }}
.sb-seg {{ display:grid; place-items:center; color:#fff; font-weight:700; font-size:13px; border-radius:7px; min-width:34px; }}
footer {{ max-width:1180px; margin:0 auto; padding:0 24px 30px; color:var(--muted); font-size:12px; }}
@media (max-width:1000px) {{ .kpis {{ grid-template-columns:repeat(3,1fr); }} .grid2 {{ grid-template-columns:1fr; }} }}
@media (max-width:640px) {{
  .kpis {{ grid-template-columns:repeat(2,1fr); }} .stats {{ grid-template-columns:repeat(2,1fr); }}
  .todo {{ flex-wrap:wrap; }} .todo-actions {{ width:100%; padding-left:21px; }}
  main, .top-in {{ padding-left:14px; padding-right:14px; }}
  .table-wrap table, .table-wrap tbody, .table-wrap tr, .table-wrap td {{ display:block; width:100%; }}
  .table-wrap tr:first-child {{ display:none; }}
  .table-wrap tr {{ padding:12px 0; border-bottom:1px solid var(--line); }}
  .table-wrap td {{ border:0; padding:2px 0; text-align:left !important; }}
  .table-wrap td.who {{ font-size:15px; }}
  .table-wrap td:nth-child(2), .table-wrap td:nth-child(3), .table-wrap td:nth-child(4) {{ display:inline-block; width:auto; margin-right:8px; }}
  .hb-row {{ grid-template-columns:minmax(110px,46%) 1fr 24px; }}
}}
</style></head>
<body>
<header class="top"><div class="top-in">
  <div class="brand"><div class="logo">M</div><div><h1>Millennium Dashboard</h1><p>Research monitor &middot; private, stays on this PC</p></div></div>
  <div class="top-meta">{task_chip}<span>Next run {html.escape(next_text)}</span>
    <a href="{PUBLIC_PAGE}">Public page</a><a href="{REPO}">Code</a></div>
</div></header>
<main>
  <div class="kpis">{kpi_html}</div>

  <section class="card"><div class="card-head"><div><h2>Needs you</h2>
    <p class="sub">Open draft fills in Gmail and highlights the PDF; drag it in, press Send, then click I sent it.</p></div></div>
    {todo_html}</section>

  <div class="grid2">
    <section class="card"><div class="card-head"><div><h2>New papers this week</h2>
      <p class="sub">arXiv, last 8 days, keyword matches per topic</p></div></div>
      <div class="chart">{papers_svg}</div><p class="note">{papers_note}</p></section>
    <section class="card"><div class="card-head"><div><h2>Outreach pipeline</h2>
      <p class="sub">Drafts only &middot; you always press Send</p></div></div>
      <div class="chart">{pipeline_svg}</div>
      <div class="table-wrap"><table><tr><th>Who</th><th>Topic</th><th>State</th><th>Date</th><th>Next step</th><th></th></tr>
      {outreach_rows}</table></div></section>
  </div>

  <div class="grid2">{research_html}</div>

  <section class="card"><details><summary>Weekly job log</summary><div class="log">{log_html}</div></details></section>
</main>
<footer>Built {built} &middot; data from local digests, the outreach ledger and study results. Charts are summaries; see the reports for methods and caveats.</footer>
</body></html>
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
