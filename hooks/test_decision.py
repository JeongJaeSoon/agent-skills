"""decision.py: closes a decision the human answered in the terminal that registered it, and only then.
Run: python3 hooks/test_decision.py"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path(tempfile.mkdtemp()) / "dashboard"
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
import os  # noqa: E402
os.environ["ORCH_FLEET_STATE"] = str(STATE)
import fleet  # noqa: E402


def hook(prompt, handle="term_coord", raw=None):
    env = {"PATH": "/usr/bin:/bin", "ORCH_FLEET_STATE": str(STATE)}
    if handle is not None:
        env["ORCA_TERMINAL_HANDLE"] = handle
    out = subprocess.run([sys.executable, HERE / "decision.py"], input=raw if raw is not None else json.dumps({"prompt": prompt}),
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stderr == "", out  # never blocks a prompt, never a loud failure
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout else None


def status(did):
    return json.loads((STATE / "decisions.json").read_text())["decisions"][did]


fmt = fleet.decision_add("Export format", options=["CSV::sheets", "JSON"], handle="term_coord")["id"]      # d1
ship = fleet.decision_add("Ship today?", options=["Yes", "Yes, but later", "No"], handle="term_coord")["id"]  # d2
other = fleet.decision_add("A worker's own", options=["Yes"], handle="term_worker")["id"]                 # d3

# Ambiguous or unrelated input closes nothing; the registering terminal gets a reminder, other terminals nothing.
text = hook("how is the build going?")
assert "d1 Export format (options: CSV, JSON)" in text and "d2 Ship today?" in text and "orch decide done" in text, text
assert "d3" not in text
assert hook("Yesterday's run failed") and status(ship)["status"] == "open"   # "Yes" is not a word of its own there
assert hook("d9 CSV") and status(fmt)["status"] == "open"                    # no such open decision of this terminal
assert hook("d3 Yes") and status(other)["status"] == "open"                  # someone else's decision
text = hook("CSV", handle="term_worker")  # a worker's prompt sees only its own decision and cannot close the coordinator's
assert "d3" in text and "d1" not in text and status(fmt)["status"] == "open", text
assert hook("CSV", handle=None) is None and hook("", raw="not json") is None and hook("CSV", handle="../x") is None
assert status(fmt)["status"] == "open"

# An option label at the start of the first line, owned by exactly one open decision of this terminal.
text = hook("csv, and keep the header row")
assert status(fmt)["status"] == "done" and status(fmt)["answer"] == "CSV", status(fmt)
assert "d1" in text and "recorded as done" in text, text
# The longest matching label wins; the id form takes the rest of the line as the answer.
fleet_d4 = fleet.decision_add("Ship tomorrow?", options=["Go"], handle="term_coord")["id"]
assert hook("Yes, but later: after the release notes") and status(ship)["answer"] == "Yes, but later", status(ship)
assert hook("decision d4: 허락합니다\nmore context") and status(fleet_d4)["answer"] == "허락합니다", status(fleet_d4)
d5 = fleet.decision_add("Two with the same label", options=["Retry"], handle="term_coord")["id"]
d6 = fleet.decision_add("Another", options=["Retry"], handle="term_coord")["id"]
assert "orch decide done" in hook("Retry") and status(d5)["status"] == status(d6)["status"] == "open"  # ambiguous
assert hook("d6 Retry") and status(d6)["status"] == "done" and status(d5)["status"] == "open"
assert hook("d5:skip it") and status(d5)["answer"] == "skip it"
assert hook("anything") is None  # this terminal has nothing open: no context at all
print("test_decision: ok")
