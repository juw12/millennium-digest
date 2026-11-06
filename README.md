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
   per 30 days, 180 days between contacts with the same person, at most one follow-up, an AI-assistance
   disclosure, a "no reply is needed" line, a specific question, and a citation of the person's own work.
   Nothing is sent automatically; you review and press Send yourself.

This is a hobby tool by an engineer, not a mathematician. It does not solve or claim progress on any
Millennium problem.

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
python outreach.py propose <email_spec.json>  check every rule; if all pass, open a Gmail draft
python outreach.py mark <address> <state>     record sent | replied | declined | do-not-contact
python outreach.py followups                  at most one polite follow-up per person when due
python outreach.py open <address>             reopen the saved Gmail draft for that person
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
  - Reply waiting: *Open reply* / *I sent it*
  - Researcher reply: *They replied*
  - Watched page changed: *Open page* / *Seen*
- **Progress per problem**: a stage bar (5 stages) and the e-mail timeline (drafted, sent, reply, answered)
  from the ledger, digests and `EVIDENCE.json`, plus "next on my end" from the local `roadmap.json`.
- **Activity log**: every button press is appended to `activity_log.jsonl`. Older events are rebuilt
  from ledger dates, digests, `weekly.log` and study reports.
- **Versions**: every commit is listed as v0.1, v0.2, ... (oldest first). The header chip shows the current version.

`activity_log.jsonl`, `ui_state.json` and `roadmap.json` stay local.
