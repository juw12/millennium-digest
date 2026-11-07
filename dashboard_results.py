"""Dashboard sections: research results, the evidence ladder (what each result has and has not reached,
instead of a proof), and paid math problems (prizes.json + weekly prize_watch.json link check).
Everything here is read from local result files, so the cards update when the weekly jobs finish."""
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BSD_THEORY = ROOT / "bsd_project" / "theory"
RH = ROOT / "rh_project"


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ci(values, digits=2):
    return f"[{values[0]:.{digits}f}, {values[1]:.{digits}f}]"


def _bsd_rows():
    rows = []
    discovery = _read(BSD_THEORY / "a2_moment_bootstrap.json")
    if discovery:
        rows.append(("2000-3999", "discovery", discovery))
    for path in sorted(BSD_THEORY.glob("a2_moment_bootstrap_*_*.json")):
        data = _read(path)
        if data:
            lo, hi = data.get("N_range", [0, 0])
            label = "pre-registered replication" if (lo, hi) == (4000, 8000) else "extension"
            rows.append((f"{lo}-{hi - 1}", label, data))
    html_rows = []
    for n_range, label, data in rows:
        html_rows.append(
            f"<tr><td>{n_range}</td><td>{label}</td><td class='num'>{data.get('n_curves', '-')}</td>"
            f"<td class='num'>{data['scale_a2_moment']:.2f} {_ci(data['scale_a2_moment_ci95'])}</td>"
            f"<td class='num'>{data['scale_plain']:.2f} {_ci(data['scale_plain_ci95'])}</td></tr>"
        )
    return "".join(html_rows)


def _rh_rows():
    data = _read(RH / "bblm_all_heights.json")
    if not data:
        return ""
    return "".join(
        f"<tr><td>{label}</td><td class='num'>{row['N_eff']:.2f}</td><td class='num'>{row['A_bblm']:.2f}</td>"
        f"<td class='num'>{_ci(row['ci95'])}</td></tr>"
        for label, row in data["heights"].items()
    )


def _local_type_rows():
    """F10: EC murmuration by local reduction type, scale vs the even semistable curve (largest range)."""
    data = _read(BSD_THEORY / "local_type_split.json")
    if not data or "vs_ss_even_all_classes" not in data:
        return ""
    labels = {
        "ss_odd": "semistable, N odd", "addodd_only": "additive at odd p", "add2_only": "additive at 2",
        "add_both": "additive at 2 and odd p",
    }
    block = data["vs_ss_even_all_classes"].get("10000_20000", {})
    counts = data["ranges"].get("10000_20000", {})
    return "".join(
        f"<tr><td>{labels[name]}</td><td class='num'>{counts.get(name, {}).get('n_curves', '-')}</td>"
        f"<td class='num'>{row['scale_vs_ss_even']:.2f} {_ci(row['ci95'])}</td>"
        f"<td class='num'>{row['shape_corr_vs_ss_even']:.2f}</td></tr>"
        for name, row in block.items() if name in labels
    )


def _queue_items():
    """Research queue: computer jobs (run nightly) and tasks that need the next AI session."""
    queue = _read(ROOT / "research_queue.json") or {"jobs": []}
    open_jobs = [job for job in queue["jobs"] if job.get("status") in ("pending", "running", "failed", "needs_ai")]
    if not open_jobs:
        return "<li><b>Queue empty</b> &middot; new research needed (ask the AI to add the next study)</li>"
    names = {"pending": "computer, nightly", "running": "running now", "failed": "failed, will retry",
             "needs_ai": "needs AI session"}
    return "".join(
        f"<li><span class='chip {'warn' if job['status'] == 'needs_ai' else 'neutral'}'>{names[job['status']]}</span> "
        f"{html.escape(job['title'])}</li>"
        for job in open_jobs
    )


def results_section():
    return f"""
<section class="card anchor" id="results"><div class="card-head"><div><h2>Research results</h2>
  <p class="sub">Numbers from the finished studies &middot; observations, not proofs &middot; updates when weekly jobs finish</p></div>
  <span class="chip neutral">evidence</span></div>
  <div class="grid2">
  <div><h3 style="font-size:13.5px;margin:0 0 6px">BSD &middot; a<sub>2</sub>-weighted murmuration (EC / newform scale, 1 = same)</h3>
   <div class="table-wrap"><table><tr><th>N</th><th>run</th><th>curves</th><th>&epsilon;a<sub>2</sub>a<sub>P</sub></th><th>plain &epsilon;a<sub>P</sub></th></tr>
   {_bsd_rows()}</table></div>
   <p class="note">Weighting by a<sub>2</sub> brings elliptic curves to the newform scale; the plain murmuration stays below.
   Likely explained by Sawin&ndash;Sutherland local factors (arXiv:2504.12295), so not claimed as new.</p></div>
  <div><h3 style="font-size:13.5px;margin:0 0 6px">RH &middot; BBLM amplitude by zero height (1 = BBLM exact)</h3>
   <div class="table-wrap"><table><tr><th>height</th><th>N<sub>eff</sub></th><th>A</th><th>95% CI</th></tr>
   {_rh_rows()}</table></div>
   <p class="note">Slow rise toward 1 (post-hoc, exploratory). LMFDB has no zeros above about 3&middot;10<sup>10</sup>.</p></div>
  </div>
  <div class="grid2" style="margin-top:12px">
  <div><h3 style="font-size:13.5px;margin:0 0 6px">BSD &middot; murmuration by local reduction type (vs even semistable, N 10000-19999)</h3>
   <div class="table-wrap"><table><tr><th>conductor type</th><th>curves</th><th>scale</th><th>shape corr</th></tr>
   {_local_type_rows()}</table></div>
   <p class="note">Only curves semistable everywhere and good at 2 have the shallow "odd" shape; odd conductors with
   additive reduction look like even ones (F10, exploratory, same in three N ranges).</p></div>
  <div><h3 style="font-size:13.5px;margin:0 0 6px">Research queue</h3>
   <ul class="plain">{_queue_items()}</ul>
   <p class="note">Computer jobs run every night at 02:00. "Needs AI session" items are new code or drafts for the next session.</p></div>
  </div>
  <p class="links"><a href="https://juw12.github.io/millennium-digest/results.html">Public results page</a></p>
</section>"""


# Instead of a proof: each claim climbs a ladder of evidence. done / next / out of reach.
LADDER = [
    ("BSD: a2-weighting fixes the murmuration scale", [
        ("done", "Proven ingredients", "Eichler-Selberg trace formula and a(2P) = a(2)a(P) are theorems (cited, not ours)"),
        ("done", "Exact computer check", "trace formula matched curve sums 180/180; 3,715 data alignment checks, 0 mismatches"),
        ("done", "Pre-registered replication", "N 4000-7999 passed all 3 written-in-advance criteria"),
        ("done", "Expert check", "a specialist replied that published heuristics predict this pattern"),
        ("next", "Bigger sample", "N 8000-10000 runs automatically (research tick, Sunday)"),
        ("next", "Explained prediction", "derive the a2-weighted curve from Sawin-Sutherland local factors and compare"),
        ("far", "Theorem", "murmurations are proven for modular forms (Zubrilina 2023); for elliptic curves it is open"),
    ]),
    ("RH: finite-height correction approaches BBLM", [
        ("done", "Data integrity", "300,000 consecutive LMFDB zeros per height, index-contiguous, mean spacing 1.0000"),
        ("done", "Consistent trend", "9 heights, amplitude 0.65 to 0.77, rising"),
        ("next", "Expert check", "two specialists contacted; automatic follow-up 27 Nov if no reply"),
        ("next", "Compare with the known 2nd-order term", "Bogomolny et al. 2006 / Forrester-Mays correction vs our 9 heights"),
        ("far", "Proof of RH", "these statistics cannot prove or disprove RH"),
    ]),
]
_MARK = {"done": ("&#10003;", "good"), "next": ("&#8250;", "neutral"), "far": ("&#215;", "bad")}


def evidence_section():
    blocks = []
    for claim, rungs in LADDER:
        items = "".join(
            f"<div class='todo'><span class='chip {_MARK[state][1]}'>{_MARK[state][0]}</span>"
            f"<div class='todo-text'><b>{html.escape(title)}</b><span>{html.escape(detail)}</span></div></div>"
            for state, title, detail in rungs
        )
        blocks.append(f"<div><h3 style='font-size:13.5px;margin:0 0 6px'>{html.escape(claim)}</h3>{items}</div>")
    return f"""
<section class="card anchor" id="evidence"><div class="card-head"><div><h2>Evidence ladder (instead of a proof)</h2>
  <p class="sub">A proof is out of reach, so each result climbs these steps: &#10003; done &middot; &#8250; next &middot; &#215; out of reach</p></div></div>
  <div class="grid2">{"".join(blocks)}</div></section>"""


def prizes_section():
    data = _read(HERE / "prizes.json") or {"prizes": [], "registries": []}
    watch = _read(HERE / "prize_watch.json") or {}
    order = {"high": 0, "low-medium": 1}
    prizes = sorted(data["prizes"], key=lambda p: (p["status"] != "open", order.get(p["fit"].split(" ")[0], 2)))
    rows = []
    for prize in prizes:
        check = watch.get(prize["url"], {})
        flag = ""
        if check.get("changed_on"):
            flag = f" <span class='chip bad'>page changed {check['changed_on']}</span>"
        elif check and not check.get("ok"):
            flag = " <span class='chip'>link check failed</span>"
        fit_class = "good" if prize["fit"].startswith("high") else ""
        rows.append(
            f"<tr><td class='who'><a href='{html.escape(prize['url'])}'>{html.escape(prize['name'])}</a>{flag}"
            f"<br><span class='sub'>{html.escape(prize['sponsor'])}</span></td>"
            f"<td>{html.escape(prize['amount'])}</td>"
            f"<td>{html.escape(prize['task'])}</td>"
            f"<td>{html.escape(prize['deadline'])}</td>"
            f"<td><span class='chip {fit_class}'>{html.escape(prize['fit'])}</span></td></tr>"
        )
    registries = " &middot; ".join(
        f"<a href='{html.escape(r['url'])}'>{html.escape(r['name'])}</a>" for r in data.get("registries", [])
    )
    checked = max((c.get("checked", "") for c in watch.values()), default="not yet")
    return f"""
<section class="card anchor" id="prizes"><div class="card-head"><div><h2>Paid math problems</h2>
  <p class="sub">Companies and foundations paying for solutions &middot; links re-checked weekly (last {html.escape(checked)}) &middot; best fit first</p></div>
  <span class="chip good">money</span></div>
  <div class="table-wrap"><table><tr><th style='width:22%'>Prize</th><th style='width:15%'>Amount</th><th>What you must do</th><th style='width:13%'>Deadline</th><th style='width:9%'>Fit</th></tr>
  {"".join(rows)}</table></div>
  <p class="note"><b>Best bet:</b> Squares in Squares is optimisation, not proof &mdash; computers can compete. It needs your own Kaggle account
  (identity and prize payment are tied to it). New bounties: {registries}</p></section>"""
