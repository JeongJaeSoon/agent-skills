"""New signal kinds, the main_flake event and the decisions-gap warning of prog.py. Run: python3 test_signal_kinds.py"""
import json, os, pathlib, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prog

PROG = pathlib.Path(prog.__file__)


def ev(ts, name, **kw):
    return {"ts": ts, "ev": name, **kw}


# --- L-466: loss, wait and orchestrator-correction kinds -----------------------------------------------------
for kind in ("loss", "wait", "orchestrator_correction"):
    assert kind in prog.SIGNAL_KINDS, kind
assert prog.SIGNAL_KINDS[:4] == ("human_correction", "brief_gap", "stall", "tooling")  # existing kinds keep their names

home = pathlib.Path(tempfile.mkdtemp(prefix="test-signal-kinds-"))
env = {**os.environ, "HOME": str(home), "PROGRAMS_HOME": str(home / "programs"), "AGENT_SKILLS_STATE": str(home / "state")}
(home / "programs" / "demo").mkdir(parents=True)
(home / "programs" / "demo" / "program.json").write_text(json.dumps({"repo": "acme/app", "created_at": "2026-11-01T00:00:00+00:00"}))


def orch(*argv):
    return subprocess.run([sys.executable, str(PROG), *argv], capture_output=True, text=True, env=env)


def ledger():
    return [json.loads(l) for l in (home / "programs" / "demo" / "ledger.jsonl").read_text().splitlines()]


for kind in ("loss", "wait", "orchestrator_correction"):
    r = orch("record", "demo", "signal", "--kind", kind, "--evidence", "msg_1", "--note", "n")
    assert r.returncode == 0, (kind, r.stderr)
assert [e["kind"] for e in ledger()] == ["loss", "wait", "orchestrator_correction"]
r = orch("record", "demo", "signal", "--kind", "bogus", "--evidence", "msg_1")
assert r.returncode != 0 and "orchestrator_correction" in r.stderr, r.stderr  # the refusal lists the new kinds

# --- L-463: main_flake keeps the run id and attempt, and never changes main's state --------------------------
r = orch("record", "demo", "main_flake", "--run", "123456", "--attempt", "2", "--sha", "abc", "--note", "rerun went green")
assert r.returncode == 0, r.stderr
row = ledger()[-1]
assert row["ev"] == "main_flake" and row["run"] == "123456" and row["attempt"] == 2 and row["sha"] == "abc", row
assert prog.main_state(ledger()) == "green"  # a flake is not a red
for bad in (("--attempt", "2"), ("--run", "9"), ("--run", "9", "--attempt", "x"), ("--run", "9", "--attempt", "1")):
    r = orch("record", "demo", "main_flake", *bad)
    assert r.returncode != 0 and "main_flake" in r.stderr, (bad, r.stderr)  # attempt 1 is the run itself, not a rerun
assert len(ledger()) == 4

# --- L-464: decisions gap ------------------------------------------------------------------------------------
tsv = "2026-11-01T01:16:00Z\tframe\tpicked X\twhy\tevidence\tdone\n"
events = [ev("2026-11-01T00:00:00+00:00", "landed", pr=1, sha="a"),          # before the last decision: not counted
          ev("2026-11-01T02:00:00+00:00", "landed", pr=2, sha="b"),
          ev("2026-11-01T02:05:00+00:00", "main_green", pr=2, sha="b"),
          ev("2026-11-01T03:00:00+00:00", "main_red", pr=3, sha="c"),
          ev("2026-11-01T03:30:00+00:00", "predicate_verified"),
          ev("2026-11-01T03:31:00+00:00", "signal", kind="stall", evidence="x")]  # not a delivery event
gap = prog.decision_gap(events, tsv)
assert gap == {"count": 4, "since": "2026-11-01T01:16:00Z", "kinds": {"landed": 1, "main_green": 1, "main_red": 1, "predicate_verified": 1}}, gap
line = prog.decision_gap_line(events, tsv)
assert line.startswith("WARN decisions:") and "4 event(s)" in line and "2026-11-01T01:16:00Z" in line, line
# Below the threshold, or with nothing after the last decision, there is no warning.
assert prog.decision_gap_line(events[:2], tsv) is None
assert prog.decision_gap_line(events, "2026-11-02T00:00:00Z\tx\ty\tz\te\tr\n") is None
# A header row, blank lines and a row with a broken timestamp do not become the "last decision".
messy = "ts\tphase\tdecision\twhy\tevidence\tresult\n" + tsv + "\nnot-a-time\tx\ty\tz\te\tr\n"
assert prog.decision_gap(events, messy)["since"] == "2026-11-01T01:16:00Z"
# Review of #609: a bare timestamp is not a decision row (it needs ts, phase and decision cells)...
assert prog.decision_gap_line(events, "2099-01-01T00:00:00Z\n").startswith("WARN decisions: no decision row")
# ...the last row decides, not the largest stamp...
out_of_order = "2026-11-02T00:00:00Z\ta\tb\tc\td\te\n2026-11-01T01:00:00Z\ta\tb\tc\td\te\n"
assert prog.decision_gap(events, out_of_order)["since"] == "2026-11-01T01:00:00Z"
# ...and an event stamp without an offset does not crash the comparison.
naive = [ev("2026-11-01T02:00:00", "landed", pr=n, sha=str(n)) for n in range(3)]
assert prog.decision_gap(naive, tsv)["count"] == 3
# No trail at all: every delivery event is unrecorded, and the warning says the file is missing.
none_line = prog.decision_gap_line(events, None)
assert none_line and "no decisions.tsv" in none_line and "5 event(s)" in none_line, none_line
assert prog.decision_gap_line([ev("2026-11-01T00:00:00+00:00", "landed", pr=1, sha="a")], None) is None
# An empty-table digest with only a header counts like a missing trail.
assert prog.decision_gap_line(events, "ts\tphase\tdecision\twhy\tevidence\tresult\n").startswith("WARN decisions: no decision row")
print("prog.py signal kinds, main_flake, decisions gap: all pass")
