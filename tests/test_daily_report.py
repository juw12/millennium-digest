"""daily_report.py: the mail names machine-checked steps beside tokens, and keeps Vector's steps apart.

    python tests/test_daily_report.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_report  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + ("" if condition else f"  {detail}"))
    if not condition:
        FAILURES.append(name)


def main():
    info = {"proved": 5, "total": 10}
    lab = {"id": "F1", "proved_by": "lab_flagship_261125_2"}
    vector = [{"id": name, "proved_by": "installed"} for name in ("F2", "F3")]

    line, card = daily_report.checked_steps([lab, *vector], info, 1_500_000_000)
    check("all three steps are counted, 5 of 10 in all", "3 on this day" in line and "5 of 10 in all" in line, line)
    check("who wrote them is named", "lab workers: F1" in line and "Vector session, installed: F2, F3" in line, line)
    check("the cost divides by the lab's own steps only", "1.50 B lab tokens per step a lab worker proved" in line, line)
    check("the card says how many were the lab's", card == "3 (1 by lab workers)", card)

    line, card = daily_report.checked_steps(vector, info, 1_500_000_000)
    check("no lab step: the tokens are said to have bought none", "bought none" in line and "per step" not in line, line)

    line, card = daily_report.checked_steps([], {"proved": 0, "total": 10}, None)
    check("an empty day is plain", "0 on this day, 0 of 10 in all; tokens not recorded" in line and card == "0 (0 by lab workers)", line)
    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
