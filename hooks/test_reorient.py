"""reorient.py: after a compaction, only the coordinator of a registered program is pointed back at it.
Run: python3 hooks/test_reorient.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
(TMP / "bin").mkdir()
(TMP / "programs/p").mkdir(parents=True)
(TMP / "programs/p/program.json").write_text(json.dumps({"slug": "p", "run": "run_a", "note": "Project/p/note.md"}))
orca = TMP / "bin/orca"
orca.write_text("#!/bin/sh\ncat \"$FAKE_RUN\"\n")
orca.chmod(0o755)


def hook(source="compact", own="term_me", reply=None, programs="programs"):
    (TMP / "reply.json").write_text(reply if reply is not None else json.dumps(
        {"ok": True, "result": {"run": {"id": "run_a", "coordinator_handle": "term_me"}}}))
    env = {"PATH": f"{TMP / 'bin'}:/usr/bin:/bin", "PROGRAMS_HOME": str(TMP / programs), "FAKE_RUN": str(TMP / "reply.json")}
    if own:
        env["ORCA_TERMINAL_HANDLE"] = own
    out = subprocess.run([sys.executable, HERE / "reorient.py"], input=json.dumps({"source": source}),
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout.strip() else None


text = hook()
assert "orch status p" in text and "orch wait p" in text and "Project/p/note.md" in text and "run_a" in text, text
assert hook(source="startup") is None
assert hook(own="term_other") is None, "a worker or another coordinator is not this program's coordinator"
assert hook(own=None) is None, "not an Orca terminal"
assert hook(reply=json.dumps({"ok": True, "result": {"run": {"id": "run_b", "coordinator_handle": "term_me"}}})) is None, \
    "a Run no program was registered for"
assert hook(reply=json.dumps({"ok": False, "error": {"code": "no_run"}})) is None
assert hook(reply="not json") is None
assert hook(programs="none") is None, "no programs on this machine"
print("reorient: all pass")
