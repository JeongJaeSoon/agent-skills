"""mailbox_guard.py: own mailbox allowed, another terminal's refused. Run: python3 test_mailbox_guard.py"""
import json, os, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
CHECK = "orca orchestration check"


def decide(cmd, own="term_me"):
    env = dict(os.environ, ORCA_TERMINAL_HANDLE=own)
    out = subprocess.run([sys.executable, HERE / "mailbox_guard.py"], input=json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": cmd}}), capture_output=True, text=True, env=env).stdout
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else "pass"


# Orca's worker contract: each worker reads its own follow-ups with --terminal <own handle>.
assert decide(f"{CHECK} --terminal term_me --json") == "pass"
assert decide(f"{CHECK} --terminal=term_me --json") == "pass"
assert decide(f"{CHECK} --json") == "pass"
assert decide(f"{CHECK} --terminal $ORCA_TERMINAL_HANDLE --wait") == "pass"  # the form the brief gives workers
assert decide(f'{CHECK} --terminal "${{ORCA_TERMINAL_HANDLE}}" --ack d1') == "pass"
assert decide(f'ORCA_TERMINAL_HANDLE=term_other; {CHECK} --terminal "$ORCA_TERMINAL_HANDLE" --ack d1') == "deny"
assert decide(f"export ORCA_TERMINAL_HANDLE=term_other && {CHECK} --terminal $ORCA_TERMINAL_HANDLE") == "deny"
assert decide(f"{CHECK} --terminal term_other --ack d1") == "deny"
assert decide("orca orchestration inbox --terminal 'term_other'") == "deny"
assert decide(f"echo hi; {CHECK} --terminal term_other") == "deny"
assert decide(f"{CHECK} --terminal term_me", own="") == "deny"
print("mailbox_guard: all pass")
