"""Offline tests for `orch hold` / `orch release` (holds.py through prog.py), on invented data.

Run: python3 test_holds.py
"""
import json, os, pathlib, subprocess, sys, tempfile

home = pathlib.Path(tempfile.mkdtemp(prefix="test-holds-"))
env = {**os.environ, "PROGRAMS_HOME": str(home), "ORCH_FLEET_STATE": str(home / "state"), "ORCA_TERMINAL_HANDLE": "term_a"}
PROG = pathlib.Path(__file__).parent / "prog.py"
locks = home / "_locks"


def orch(*args, **extra):
    r = subprocess.run([sys.executable, str(PROG), *args], capture_output=True, text=True, env={**env, **extra})
    return r.returncode, r.stdout.strip(), r.stderr.strip()


assert orch("hold", "--list") == (0, "nothing held", "")
assert orch("hold", "browser", "--note", "staging 로그인")[1] == "browser held by term_a (ttl 120m)"
# Someone else is refused and told who has it; the holder refreshing keeps the note.
code, _, err = orch("hold", "browser", ORCA_TERMINAL_HANDLE="term_b")
assert code == 1 and err.startswith("browser is held by term_a for 0m (staging 로그인)"), err
assert orch("hold", "browser")[0] == 0
assert json.loads((locks / "res__browser.lock").read_text())["note"] == "staging 로그인"
assert orch("release", "browser", ORCA_TERMINAL_HANDLE="term_b")[0] == 1
assert orch("hold", "Bad Name")[0] == 1

# A lane lock prog.py wrote sits beside the holds and is listed with them.
(locks / "acme__web@main.lock").write_text(json.dumps({"pr": 45, "program": "search", "ts": "2026-10-09T11:00:00+00:00"}))
rows = json.loads(orch("hold", "--list", "--json")[1])
assert [(r["kind"], r["resource"], r["by"], r["note"]) for r in rows] == [
    ("lane", "acme/web@main", "#45", "search"),
    ("resource", "browser", "term_a", "staging 로그인"),
], rows

# A hold past its ttl is broken by the next reader and the ledger says so.
held = json.loads((locks / "res__browser.lock").read_text())
(locks / "res__browser.lock").write_text(json.dumps({**held, "ts": "2020-01-01T00:00:00Z"}))
assert [r["kind"] for r in json.loads(orch("hold", "--list", "--json")[1])] == ["lane"]
events = [json.loads(l) for l in (locks / "holds.jsonl").read_text().splitlines()]
assert [e["ev"] for e in events] == ["lock_acquired", "lock_released"], events
assert events[1]["note"].startswith("broken after"), events[1]

assert orch("hold", "browser", "--ttl", "5")[0] == 0
assert orch("release", "browser", "--force", ORCA_TERMINAL_HANDLE="term_b")[1] == "released browser"
assert json.loads((locks / "holds.jsonl").read_text().splitlines()[-1])["note"] == "forced by term_b"
assert orch("release", "browser")[1] == "browser was not held"

print("test_holds: ok")
