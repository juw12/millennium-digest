"""Checks that the lab pool survives Windows refusing a file swap.

    python tests/test_pool_swap.py

Windows refuses Path.replace while another program has the target open (WinError 5). On 2026-11-11 that ended
two pool ticks: the free slots stayed idle for ten minutes and the next tick merged the same results again.
Nothing here touches the lab's real files: every path points into a temporary folder.

Exit code 0 = all checks passed, 1 = at least one failed (each failure is printed).
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import lab_ideas  # noqa: E402
import lab_pool  # noqa: E402

FAILURES = []


def check(name, passed, detail=""):
    print(("ok   " if passed else "FAIL ") + name + (f": {detail}" if detail and not passed else ""))
    if not passed:
        FAILURES.append(name)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Refusing:
    """Path.replace that refuses the first `refusals` swaps, as Windows does while a reader has the file open."""

    def __init__(self, refusals):
        self.refusals, self.calls, self.real = refusals, 0, Path.replace

    def __enter__(self):
        outer = self

        def replace(path, target):
            outer.calls += 1
            if outer.calls <= outer.refusals:
                raise PermissionError(5, "Access is denied", str(path))
            return outer.real(path, target)

        Path.replace = replace
        self.sleep, lab_ideas.time.sleep = lab_ideas.time.sleep, lambda seconds: None
        return self

    def __exit__(self, *unused):
        Path.replace = self.real
        lab_ideas.time.sleep = self.sleep


def attempt(action):
    """The PermissionError an action ends with, or None."""
    try:
        action()
    except PermissionError as error:
        return error
    return None


def test_swap_is_tried_again(folder):
    target = folder / "pool_state.json"
    target.write_text("{}", encoding="utf-8")
    with Refusing(3) as refusing:
        error = attempt(lambda: lab_pool.write_json(target, {"answer": 1}))
    check("write_json: a swap refused 3 times still lands", error is None and read(target) == {"answer": 1},
          f"{error!r} after {refusing.calls} tries")
    with Refusing(99) as refusing:
        error = attempt(lambda: lab_pool.write_json(target, {"answer": 2}))
    check("write_json: gives up after 8 tries and says so", error is not None and refusing.calls == 8,
          f"{error!r} after {refusing.calls} tries")
    check("write_json: a swap that never landed leaves the old file whole", read(target) == {"answer": 1})


def test_bank_and_lab_use_the_same_swap(folder):
    real_bank, lab_ideas.IDEAS = lab_ideas.IDEAS, folder / "lab_ideas.json"
    try:
        with Refusing(2) as refusing:
            lab_ideas.save({"cards": [{"id": "one"}]})
        check("lab_ideas.save: a swap refused twice still lands",
              read(lab_ideas.IDEAS) == {"cards": [{"id": "one"}]} and refusing.calls == 3, f"{refusing.calls} tries")
    finally:
        lab_ideas.IDEAS = real_bank
    source = (HERE / "lab.py").read_text(encoding="utf-8")
    check("lab.py: no file swap without the retry",
          ".replace(QUEUE)" not in source and ".replace(STATE_JS)" not in source)


def test_queue_is_saved_when_the_state_swap_fails(folder):
    """The merged mark (result_merged) lives in the queue: without it the next tick merges the result again."""
    names = ("QUEUE", "STATE", "RESULTS", "LOG_DIR", "merge_results", "write_json")
    real = {name: getattr(lab_pool, name) for name in names}
    lab_pool.QUEUE, lab_pool.STATE = folder / "research_queue.json", folder / "state" / "pool_state.json"
    lab_pool.RESULTS, lab_pool.LOG_DIR = folder / "lab_results", folder / "logs"
    lab_pool.STATE.parent.mkdir()
    lab_pool.QUEUE.write_text(json.dumps({"jobs": [{"id": "job_1", "status": "running"}]}), encoding="utf-8")

    def merge_results(queue):
        queue["jobs"][0].update({"status": "done", "result_merged": 123.5})
        return ["job_1"]

    def write_json(path, data):
        if Path(path) == lab_pool.STATE:
            raise PermissionError(5, "Access is denied", str(path))
        return real["write_json"](path, data)

    lab_pool.merge_results, lab_pool.write_json = merge_results, write_json
    try:
        error = attempt(lab_pool.collect)
        saved = read(lab_pool.QUEUE)["jobs"][0]
        check("collect: the failed state swap is still reported", error is not None)
        check("collect: the queue keeps the merged mark when the state swap fails",
              saved.get("result_merged") == 123.5, str(saved))
    finally:
        for name, value in real.items():
            setattr(lab_pool, name, value)


def test_error_line_names_type_and_place(folder):
    try:
        raise MemoryError()
    except MemoryError as error:
        text = lab_pool.describe(error)
    check("describe: an error without a message still names its type and place",
          text.startswith("MemoryError (test_pool_swap.py line ") and text.endswith("names_type_and_place)"), text)
    check("describe: an error that was never raised does not break the log line",
          lab_pool.describe(ValueError("x")) == "ValueError: x")


if __name__ == "__main__":
    for test in (test_swap_is_tried_again, test_bank_and_lab_use_the_same_swap,
                 test_queue_is_saved_when_the_state_swap_fails, test_error_line_names_type_and_place):
        with tempfile.TemporaryDirectory() as temporary:
            try:
                test(Path(temporary))
            except Exception as error:  # a check that cannot even run is a failure, not a crash
                check(test.__name__, False, repr(error))
    print(f"{len(FAILURES)} failed" if FAILURES else "all checks passed")
    sys.exit(1 if FAILURES else 0)
