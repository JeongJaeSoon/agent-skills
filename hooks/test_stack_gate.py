"""stack_gate.py: `gh pr create` on stdin fixtures, against a real git repository and a fake gh.
Run: python3 hooks/test_stack_gate.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
REPO, BIN, ROWS, CONFIG = TMP / "repo", TMP / "bin", TMP / "rows.json", TMP / "config.json"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.test", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(*args):
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True,
                       env={**os.environ, **GIT_ENV})
    return r.stdout.strip()


def commit(name):
    (REPO / name).write_text(name)
    git("add", name)
    git("commit", "-q", "-m", name)
    return git("rev-parse", "HEAD")


REPO.mkdir()
BIN.mkdir()
git("init", "-q", "-b", "main")
commit("base")
git("update-ref", "refs/remotes/origin/main", "HEAD")
git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
# main → lower → upper; `side` is off main with nothing in common.
git("checkout", "-q", "-b", "lower")
LOWER = commit("lower")
git("update-ref", "refs/remotes/origin/lower", "HEAD")
git("checkout", "-q", "-b", "upper")
UPPER = commit("upper")
git("checkout", "-q", "-b", "side", "main")
SIDE = commit("side")
MAIN = git("rev-parse", "main")
git("checkout", "-q", "upper")

(BIN / "gh").write_text(f"""#!/bin/sh
[ -n "$FAKE_GH_FAIL" ] && exit 1
cat "{ROWS}"
""")
(BIN / "gh").chmod(0o755)


def rows(*items):
    ROWS.write_text(json.dumps(list(items)))


def row(n, head, sha, base="main", title="t"):
    return {"number": n, "title": title, "headRefName": head, "headRefOid": sha, "baseRefName": base}


def transcript(prompt):
    path = TMP / "t.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n")
    return str(path)


def hook(command, level=None, prompt=None, path=None, env=None, cwd=None):
    CONFIG.write_text(json.dumps({"orchestrate": {"stack_dependent": level}}) if level is not None else "{}")
    ev = {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(cwd or REPO), "tool_name": "Bash",
          "tool_input": {"command": command}}
    if prompt is not None:
        ev["transcript_path"] = transcript(prompt)
    if path:
        ev["transcript_path"] = path
    r = subprocess.run([sys.executable, HERE / "stack_gate.py"], input=json.dumps(ev), capture_output=True, text=True,
                       env={"PATH": f"{BIN}:/usr/bin:/bin", "AGENT_SKILLS_CONFIG": str(CONFIG), "HOME": str(TMP),
                            "AGENT_SKILLS_STATE": str(TMP / "state"), **(env or {})})
    assert r.returncode == 0 and r.stderr == "", r
    if not r.stdout:
        return None, ""
    out = json.loads(r.stdout)
    spec = out["hookSpecificOutput"]
    return spec.get("permissionDecision"), spec.get("permissionDecisionReason", "") + out.get("systemMessage", "")


BRIEF = "TASK\nORDER       Starts after: none. Lands after: ABC-7(a note on it) and PROJ-1. Then the rest XYZ-9.\n"

# A branch holding another open PR's head, based on main: a warning by default, a deny once promoted.
rows(row(5, "lower", LOWER))
decision, text = hook("gh pr create --base main --title t --body b")
assert decision is None and "#5" in text and "gh stack link" in text and "orchestrate.stack_dependent" in text, text
assert text.count("\n") == 0, text
decision, text = hook("gh pr create --base main --title t --body b", "deny")
assert decision == "deny" and text.startswith("[gate:stack]") and "#5" in text, text
# `true` is the default level and `false` is off, as for every other key.
assert hook("gh pr create --base main", True)[0] is None and "#5" in hook("gh pr create --base main", True)[1]
assert hook("gh pr create --base main", False) == (None, "")
assert hook("gh pr create --base main", "off") == (None, "")
# No --base goes to the default branch.
assert hook("gh pr create --title t", "deny")[0] == "deny"
# gh's own choice of base when the flag is left out: the branch's gh-merge-base.
git("config", "branch.upper.gh-merge-base", "lower")
assert hook("gh pr create --title t", "deny")[0] is None
git("config", "--unset", "branch.upper.gh-merge-base")

# Stacked on it: passes under deny, with the reminder to link, since no PR exists yet to check.
decision, text = hook("gh pr create --base lower --title t", "deny")
assert decision is None and "gh stack link" in text and "#5" in text, text
for form in ("gh pr create -B lower", "gh pr create -Blower", "gh pr create --base=lower",
             "cd . && gh pr create --base lower", "/usr/local/bin/gh pr create --base lower"):
    assert hook(form, "deny")[0] is None, form
# Two layers below: everything in the chain under the base counts.
git("checkout", "-q", "-b", "third")
THIRD = commit("third")
git("update-ref", "refs/remotes/origin/upper", UPPER)
rows(row(5, "lower", LOWER), row(6, "upper", UPPER, base="lower"))
assert hook("gh pr create --base upper", "deny")[0] is None
assert hook("gh pr create --base lower", "deny")[0] == "deny"  # #6 is above that base and this branch holds its head
git("checkout", "-q", "upper")

# A dependency far down a long branch is still seen.
stream = "".join(f"commit refs/heads/long\nmark :{i}\ncommitter t <t@example.test> 0 +0000\ndata 1\nx\n"
                 + (f"from :{i - 1}\n" if i > 1 else f"from {LOWER}\n") for i in range(1, 2102))
subprocess.run(["git", "fast-import", "--quiet"], cwd=REPO, input=stream, text=True, check=True)
rows(row(5, "lower", LOWER))
assert hook("gh pr create --head long --base main", "deny")[0] == "deny"

# --head decides, not what is checked out.
rows(row(5, "lower", LOWER))
assert hook("gh pr create --head side --base main", "deny")[0] is None
assert hook("gh pr create -H upper --base main", "deny")[0] == "deny"
git("checkout", "-q", "side")
assert hook("gh pr create --head upper --base main", "deny")[0] == "deny"
assert hook("gh pr create --base main", "deny") == (None, "")
git("checkout", "-q", "upper")
# A long-lived branch's own PR (main into a release branch) is on every branch: its head is on the base, no dependency.
rows(row(9, "main", MAIN, base="release"))
assert hook("gh pr create --base main", "deny") == (None, "")
# Not narrowed to the author, and this PR's own branch is not a dependency of itself.
rows(row(6, "upper", UPPER, base="main"))
assert hook("gh pr create --base main", "deny") == (None, "")

# The brief's ORDER: a named open PR off main, by ticket in the title or the branch, in any of the value's forms.
git("checkout", "-q", "side")
rows(row(7, "x", "0" * 40, title="fix(abc): ABC-7 it"), row(8, "you/proj-1", "1" * 40, base="other"))
decision, text = hook("gh pr create --base main", "deny", BRIEF)
assert decision == "deny" and "#7" in text and "ABC-7" in text and "#8" in text, text
assert "XYZ" not in text, text  # past the sentence end
# Its branch as base satisfies the brief.
rows(row(7, "x", "0" * 40, title="ABC-7"), row(8, "y", "1" * 40, title="PROJ-1", base="x"))
git("update-ref", "refs/remotes/origin/y", SIDE)
decision, text = hook("gh pr create --base y", "deny", BRIEF)
assert decision is None and "gh stack link" in text and "could not read" not in text, text
git("update-ref", "-d", "refs/remotes/origin/y")
# #N works too, and a template's placeholder or `none` names nothing.
rows(row(7, "x", "0" * 40))
assert hook("gh pr create --base main", "deny", "ORDER  Lands after: #7.\n")[0] == "deny"
for line in ("ORDER  Starts after <TICKET…> · lands after <TICKET…> (stack / orch dep).", "ORDER  Lands after: none.",
             "ORDER  Lands after: 없음.", "Nothing about order here, lands after ABC-7"):
    assert hook("gh pr create --base main", "deny", line) == (None, ""), line
# A named PR that no open PR answers to may have landed since dispatch: one warning line, never a deny.
rows(row(99, "z", "2" * 40))
decision, text = hook("gh pr create --base main", "deny", BRIEF)
assert decision is None and "ABC-7, PROJ-1" in text and text.count("\n") == 0, text
git("checkout", "-q", "upper")

# What cannot be read passes with one warning line.
rows(row(5, "lower", LOWER))
for command in ("gh pr create --repo acme/app --base main", "gh pr create -R acme/app", "gh pr create --head fork:x",
                'gh pr create --head "$B"', 'gh pr create --base "$B"'):
    decision, text = hook(command, "deny")
    assert decision is None and "could not read" in text and text.count("\n") == 0, (command, text)
decision, text = hook("gh pr create --base main", "deny", env={"FAKE_GH_FAIL": "1"})
assert decision is None and "gh pr list" in text and text.count("\n") == 0, text
decision, text = hook("gh pr create --base nowhere", "deny")
assert decision is None and "could not read" in text, text
decision, text = hook("gh pr create --base main", "deny", path=str(TMP / "gone.jsonl"))
assert decision is None and "first prompt" in text, text
git("checkout", "-q", "--detach")
decision, text = hook("gh pr create --base main", "deny")
assert decision is None and "head branch" in text, text
git("checkout", "-q", "upper")
outside = TMP / "not-a-repo"
outside.mkdir()
decision, text = hook("gh pr create --base main", "deny", cwd=outside)
assert decision is None and "could not read" in text, text
# A list at gh's limit is said to be partial.
rows(*[row(1000 + i, f"h{i}", "3" * 40) for i in range(200)])
assert "first 200" in hook("gh pr create --base main")[1]

# Only `gh pr create` reads anything, and text about it is not the command.
ROWS.write_text("not json")
for command in ("gh pr view 5", "git status", "gh stack submit", "gh stack link 5 6 --base main", 'echo "gh pr create"',
                "gh pr edit 5 --base main"):
    assert hook(command, "deny") == (None, ""), command
for event in ('{"tool_name": "Read", "tool_input": {}}', '{"tool_name": "Bash", "tool_input": {"command": 3}}'):
    r = subprocess.run([sys.executable, HERE / "stack_gate.py"], input=event, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "", r
# A fault of its own never stops the call, and leaves a line.
state = TMP / "state"
r = subprocess.run([sys.executable, HERE / "stack_gate.py"], input="not json", capture_output=True, text=True,
                   env={"PATH": "/usr/bin:/bin", "AGENT_SKILLS_STATE": str(state)})
assert r.returncode == 0 and r.stdout == "" and "stack_gate" in (state / "guard-errors.jsonl").read_text(), r
print("stack_gate: all pass")
