"""Hands Millennium jobs that need an AI to the always-on Precedent Agent, so nobody has to open a chat.

Every research-queue job with status "needs_ai" whose "not_before" date has passed is posted once to the
Obsidian queue (~/Documents/Obsidian Vault/Precedent/Queue.md) as an "#auto" goal. The Precedent Agent
(started with Windows) picks it up and runs OpenCode round after round until the job is done; the agent
follows E:\\Projects\\MillenniumResearch\\AI_JOB_PROTOCOL.md and marks the job done in research_queue.json.

Called by research_tick.py (daily) and gmail_inbox.py (every 2 h). Run by hand: python ai_bridge.py
"""
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESEARCH_DIR = HERE.parent
QUEUE_JSON = RESEARCH_DIR / "research_queue.json"
PROTOCOL = RESEARCH_DIR / "AI_JOB_PROTOCOL.md"
OBSIDIAN_QUEUE = Path.home() / "Documents" / "Obsidian Vault" / "Precedent" / "Queue.md"
SECTION = "## Now"


def due_jobs(queue, today):
    for job in queue.get("jobs", []):
        if job.get("status") != "needs_ai" or job.get("bridged"):
            continue
        not_before = job.get("not_before")
        if not_before and today < dt.date.fromisoformat(not_before):
            continue
        yield job


def goal_line(job):
    return (
        f"- [ ] Millennium research job `{job['id']}`: {job['title']}. "
        f"Follow {PROTOCOL} exactly (sending the email is pre-approved by Juwen; not a hard stop). #auto"
    )


def post_to_obsidian(lines):
    text = OBSIDIAN_QUEUE.read_text(encoding="utf-8")
    if SECTION in text:
        head, rest = text.split(SECTION, 1)
        text = head + SECTION + "\n" + "\n".join(lines) + rest
    else:
        text = text.rstrip("\n") + f"\n\n{SECTION}\n" + "\n".join(lines) + "\n"
    OBSIDIAN_QUEUE.write_text(text, encoding="utf-8")


def bridge(today=None):
    """Posts due jobs; returns their ids. Safe to call often: each job is posted once."""
    today = today or dt.date.today()
    if not QUEUE_JSON.exists() or not OBSIDIAN_QUEUE.exists():
        return []
    queue = json.loads(QUEUE_JSON.read_text(encoding="utf-8"))
    jobs = list(due_jobs(queue, today))
    if not jobs:
        return []
    post_to_obsidian([goal_line(job) for job in jobs])
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    for job in jobs:
        job["bridged"] = stamp
    QUEUE_JSON.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return [job["id"] for job in jobs]


if __name__ == "__main__":
    posted = bridge()
    print("posted to the Precedent queue:", ", ".join(posted) if posted else "nothing due")
