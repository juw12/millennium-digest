# millennium-digest

Project page: https://juw12.github.io/millennium-digest/

A small personal research tool around the Clay Millennium Prize problems. It has two parts:

1. **Weekly digest** (`millennium_monitor.py`): searches arXiv for papers from the last 8 days on each
   Millennium problem (plus two narrower topics: elliptic-curve murmurations and Riemann-zero spacing),
   checks whether a few watched pages changed (the Clay problems page, Cremona's elliptic curve tables,
   Odlyzko's zeta zero tables), and writes a dated Markdown + HTML digest that opens in the browser.
   It reads only: it never emails anyone.
2. **Outreach guard** (`outreach.py`): helps write to researchers without spamming them. It checks every
   draft against the rules in `outreach_policy.json` before opening a Gmail compose window, and keeps a
   ledger of who was contacted and when. Rules include: one recipient per email, at most 2 new contacts
   per 7 days and 8 per 30 days, 60 days before the same person hears of a new result, at most one
   follow-up, one unanswered email per person, an AI-assistance disclosure, a "no reply is needed" line,
   a specific question, and a citation of the person's own work. An email that only asks whether
   something is known or right may go out before the claim is settled, if it says what is unchecked.
   Outreach is recurring: specs wait in `outbox/outreach/`, the daily task sends the next one the rules
   allow (`python outreach.py queue`), and once a week an AI session checks whether there is a new
   checked result worth one question to one researcher. Anything that fails a rule waits; a send Gmail
   does not confirm stays a draft.
   arXiv endorsement requests are a standing step too (`endorsement.py`): arXiv asks a first-time
   submitter to a category to forward a code to somebody who may endorse. The daily task asks one person
   at a time, the next one only after 7 days of silence, three people at most, and nobody once anybody
   has answered. Only people arXiv itself lists as endorsers for the category, whose work the paper
   cites and whose address is printed in public, are asked.

This is a hobby tool by an engineer, not a mathematician. It does not solve or claim progress on any
Millennium problem.

## Get it on your device

- **Windows:** [`MillenniumDigest-Windows-Setup.exe`](https://github.com/juw12/millennium-digest/releases/download/desktop/MillenniumDigest-Windows-Setup.exe) (no admin needed; if Windows says it protected your PC, choose *More info → Run anyway*), or without installing [`MillenniumDigest-Windows-Portable.zip`](https://github.com/juw12/millennium-digest/releases/download/desktop/MillenniumDigest-Windows-Portable.zip).
- **Mac:** [`MillenniumDigest-macOS-AppleSilicon.dmg`](https://github.com/juw12/millennium-digest/releases/download/desktop/MillenniumDigest-macOS-AppleSilicon.dmg) (M1 and newer) or [`MillenniumDigest-macOS-Intel.dmg`](https://github.com/juw12/millennium-digest/releases/download/desktop/MillenniumDigest-macOS-Intel.dmg). The first time, macOS blocks it because it is not notarized: **System Settings → Privacy & Security → Open Anyway**.
- **Android:** [`MillenniumDigest-android.apk`](https://github.com/juw12/millennium-digest/releases/download/android/MillenniumDigest-android.apk): open the file on the phone and allow your browser to install apps once.
- **iPhone:** open https://juw12.github.io/millennium-digest/ in Safari, tap Share, then **Add to Home Screen**.

The Windows, Mac and Android apps open the Millennium Digest site in a window of its own ([`desktop/`](desktop/README.md), [`android/`](android/README.md)) and change with it. Each build is opened on GitHub's machines before it is published: on Windows and on a Mac the page loads, the app starts and can store data; on an Android emulator it installs, opens and stays up. They are not code-signed or notarised, and no real phone or PC has run them yet.

## Numerical results

Results page: https://juw12.github.io/millennium-digest/results.html (source: `docs/results.html`, last updated 2026-11-07).
Numerical observations only, not proofs; nothing here proves or disproves BSD or the Riemann Hypothesis.

- **BSD side, a2-weighted murmuration:** at odd squarefree conductor, weighting by a2 brings the
  elliptic-curve murmuration to the newform scale (ratio 1.007 [0.91, 1.10] in the pre-registered
  4000-7999 replication; plain murmuration 0.495). Plausibly explained by Sawin-Sutherland local-factor
  heuristics (arXiv:2504.12295), so not claimed as new.
- **Local type at 2, pre-registered on unseen conductors 20,000-29,999:** H1 and H2 passed, H3' missed its
  threshold by 0.005, so the result is reported as *not replicated* rather than moving the threshold. A
  separate pre-registered square-root kink at P/N = 1/16 for 4 | N was confirmed.
- **RH side, finite-height GUE corrections:** using ~300,000 LMFDB zeros per height, the BBLM amplitude
  rises from about 0.65 at t = 1e8 to about 0.77 at t = 3e10, still below the predicted 1 (post-hoc,
  exploratory).

## Setup

Needs Python 3.10+ (standard library only) on Windows.

```
copy config.example.json config.json        # your email and name
copy experts.example.json experts.json      # researchers you may contact, with a public source for each address
python millennium_monitor.py                # writes digests/digest_<date>.html and opens it
python outreach.py status                   # ledger + what the rules allow next
python make_site.py                         # rebuilds docs/index.html (public parts of the latest digest only)
```

To run the digest every Monday at 09:00 (Windows Task Scheduler):

```
schtasks /Create /TN "Millennium research digest" /SC WEEKLY /D MON /ST 09:00 ^
  /TR "\"C:\path\to\python.exe\" \"C:\path\to\millennium_monitor.py\""
```

## Weekly job and dashboard

`weekly.py` is what the Monday task runs: digest, then `make_site.py`, then it commits and pushes only
`docs/index.html` (and refuses if the page contains a private marker), then rebuilds the dashboard.
Each step is logged to `weekly.log`.

`dashboard.py --open` builds a private local page (`dashboard/index.html`, never committed) with:
what needs you this week, new papers per topic with a trend, the outreach ledger with holds and
follow-up dates, study status and report links, and the result of the last automatic run.

## Outreach commands

```
python outreach.py status                     show the ledger and what is allowed next
python outreach.py propose <email_spec.json>  check every rule; if all pass, send it (draft if not confirmed)
python outreach.py mark <address> <state>     record sent | replied | declined | do-not-contact
python outreach.py followups                  at most one polite follow-up per person when due
python outreach.py open <address>             reopen the saved Gmail draft for that person
python endorsement.py status                  arXiv endorsement requests: who was asked when, what is next
python endorsement.py endorsed <id>           arXiv has the endorsement: stop asking, reopen the upload job
python endorsement.py stop <id>               stop asking for this need
```

`gmail_send.py` (optional `send` command) needs a Gmail app password stored locally with Windows DPAPI;
without it, the tool works in drafts-only mode.

## Not in this repo

`config.json`, `experts.json`, `contacts_log.json`, `state.json` and `digests/` stay local
(see `.gitignore`): they hold personal settings, researchers' addresses and the outreach history.

## Licence

MIT, see `LICENSE`.

## Desktop app

`app/millennium_app.py` shows the dashboard in its own window (pywebview / WebView2), with working buttons.
Every `millennium://action/argument` link is passed to `action.handle()`, and the app reloads `action`,
`dashboard`, `dashboard_extras`, `history` and `progress` on each click. So new buttons only need
Python changes, not a rebuilt .exe.

Build the .exe (from this folder):

```
python -m PyInstaller --noconfirm --onedir --windowed --name "Millennium Dashboard" --icon ..\icon.ico --distpath app --workpath app\build --specpath app\build app\millennium_app.py
```

## Dashboard sections

- **Needs you**: every item has a button.
  - Draft to send: *Open draft + PDF* / *I sent it*
  - Someone replied: *Open in Gmail* (your answer is already a draft in the thread) / *Their reply* / *I sent it*
  - Follow-up due (21-day rule): drafted in the thread, *Open in Gmail*
  - Researcher reply: *They replied*
  - *Check Gmail now* runs the Gmail check immediately.
- **Millennium Lab** (`lab.py`, `lab/index.html`): eight bot researchers in departments (BSD, RH, Erdős bench,
  prize bounties, Kaggle Squares-in-Squares packing, library scout for NS/YM/PvNP/Hodge, referee, press room).
  Each department always has one AI job open in `research_queue.json`; `ai_watchdog.py` refills it every 30 min,
  so the lab works continuously (target 8 finished steps a day, `lab_departments.json`). Milestones and dates live
  in `lab_targets.json`. `lab/index.html` is a live three.js scene (Paper style, like Cairn Founder Studio) that
  re-reads `lab/lab_state.js` every minute; the dashboard header links to it ("3D Lab").
- **Parallel lab pool** (`lab_pool.py`, task "Millennium lab pool" every 10 min): lab jobs no longer go to the
  one-at-a-time Cairn autopilot. Up to `pool.workers` jobs (default 3) run at once as separate OpenCode / Copilot
  CLI processes; workers write `lab_results/<id>.json`, the pool merges them, retries a failed run once with the
  other engine and skips Copilot for 24 h when it reports it is not logged in. Departments have `slots` and
  `every_hours`; the Review Panel (simulated expert review from published papers, never shown to real people) gates
  outreach; the Workshop sets up Lean 4 + Mathlib. Packing search for Kaggle Squares in Squares runs every night
  (`prize_work/squares-in-squares/nightly.py`, task "Millennium squares search"), and "Millennium insomnia" keeps
  the laptop awake and online 22:30-08:30.
- **Daily progress** (`daily_progress.py`): a 14-day strip (research step / upkeep only / idle), the streak, and
  today's step. Every day gets at least one research step: the nightly computer job if the research queue has one,
  otherwise an AI job `daily_YYYYMMDD` rotating BSD -> RH -> "prize problem of the day". It is queued by the 02:00
  research tick and, if that run was missed, by the 30-minute AI watchdog; `ai_bridge.py` hands it to the agent
  through the Cairn autopilot queue (`cairn autopilot add`).
- **All open prize problems in the world** (`prize_catalog.py`, weekly): every unsolved problem with a cash prize
  we could find: the full [Prize Problem Ledger](https://prizeproblems.org/) (Millennium, Beal, Collatz, every open
  Erdős prize, Wolfram, Certicom, EFF, ...) plus hand-checked open challenges in `prizes.json` → `extra_catalog`
  (Kaggle Squares in Squares, Hutter Prize, Vesuvius Challenge, XPRIZE). Searchable, filterable, with a
  "computer can attack" tag for problems where a search or record can win without a proof.
- **Progress per problem**: a stage bar (5 stages) and the e-mail timeline (drafted, sent, reply, answered)
  from the ledger, digests and `EVIDENCE.json`, plus "next on my end" from the local `roadmap.json`.
- **Activity log**: every button press is appended to `activity_log.jsonl`. Older events are rebuilt
  from ledger dates, digests, `weekly.log` and study reports.
- **Versions**: every commit is listed as v0.1, v0.2, ... (oldest first). The header chip shows the current version.

`activity_log.jsonl`, `ui_state.json` and `roadmap.json` stay local.

## Gmail drafts in your pinned Gmail app

Draft buttons (and `outreach.py propose` / `open`) use `gmail_app.py`. It opens the email in the same Chrome
as the Gmail shortcut pinned to the taskbar, so no separate sign-in is needed. To / Subject / Body come in
filled, and the PDF is put on the clipboard and pasted into the message (Ctrl+V). Keys are only sent while
that new Gmail window is in front; otherwise a note asks you to press Ctrl+V. Nothing is ever sent:
you press Send. `gmail_compose.py` (separate Edge profile) remains only as a fallback.

## Replies and follow-ups (`gmail_inbox.py`)

The scheduled task "Millennium Gmail check" runs `gmail_inbox.py` every 2 hours (headless Edge with the
helper Gmail profile in `edge_gmail_profile`; nothing appears on screen). For every contact in the ledger
that is drafted, sent or replied, it reads the Gmail threads with that address and:

- marks a drafted email **sent** once your message shows up in Gmail (no "I sent it" click needed);
- saves a **new reply** to `../inbox/`, sets the contact to *replied*, and drafts your answer as a reply
  **inside that Gmail thread** (so it threads properly). A hand-written `../outbox/<TOPIC>_<Surname>_reply_<YYYYMMDD>.txt`
  is used if it exists; otherwise a short thank-you is generated from the arXiv ids and sections they mention;
- marks the contact **answered** once your answer is in the thread;
- drafts the single allowed **follow-up** (21-day rule, `outreach.followups`) inside the thread as well.

When a draft was made, a message box offers to open Gmail Drafts and the dashboard is rebuilt. Answers to
replies are queued for the next AI session, which writes and sends them; the checker itself never sends generated text. Run it by hand with `python gmail_inbox.py`
(`--dry-run` reads only, `--quiet` skips the message box). Last result: `gmail_inbox.json`, log:
`gmail_inbox.log` (both local).

## The automation's own watch (`clay_watch.py`)

Watched pages are not a to-do for you. Every weekly run, `clay_watch.py`:

- reads the Clay Millennium Problems list. Any unsolved problem we do not track yet is added on its own
  (weekly arXiv search plus a Progress card, stored in the local `clay_watch.json`);
- fingerprints every Clay problem page. A changed page, or one that talks about a solved / claimed
  resolution, becomes a *Watcher* note on that problem's Progress card. Nothing is marked solved
  automatically;
- turns changed data sources (Cremona tables -> BSD, Odlyzko zeros -> RH) into a "re-run the study"
  note.

Everything it does is also written to the Activity log.

## Staying up to date (`selfupdate.py`)

A change pushed to `main` from any computer reaches the lab's PC by itself. The scheduled task
"Millennium updates" runs `selfupdate.py` every 30 minutes; create it once with `python selfupdate.py install`.

- It only fast-forwards: on `main`, no local edits in tracked files, nothing unpushed. It never merges,
  rebases or resets, and untracked files (logs, state) do not count as edits.
- Before it switches, every Python and JSON file the new version changes has to compile or parse. A version
  that does not is left alone and named in the log, so a typo cannot stop the lab unattended.
- When both sides have commits it stops and says so; join them by hand (`git pull --rebase`, then push).
- `python selfupdate.py status` shows where the copy stands. `"update": {"enabled": false}` in
  `lab_departments.json` switches it off. Log: `logs/selfupdate.log`.

The scheduled tasks are short runs and use the new code at their next start; the desktop app uses it the
next time it is opened.

## The lab's facts and self-check (`lab_facts.py`)

Once a day, with the publish check, the lab writes one private note next to the useful-results list:
`millennium-lab-facts.md`. It is the place to read before saying what the lab does or what it can earn:
every desk with what it works on and how many jobs it finished, the prizes in `prizes.json` with their
amounts and fit, the milestones, the prize problems the desks worked on with the catalogue's prize, and the
results. Every section names its source file; what no file says is shown as "not stated".

The note ends with a self-check: places where the lab's own files disagree or something is late (a prize a
desk names that `prizes.json` lacks, a job text with a different prize than the catalogue, an open prize
past its date, a desk with nothing finished and nothing open, a stale catalogue, a stuck updater). It reads
only and changes nothing. `python lab_facts.py --check` prints the self-check alone (exit code 1 when it
found something); `--print` shows the note without writing it.
