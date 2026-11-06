"""lib/discipline.py (design M4, six `worker.*` keys): each check fires on its case, stays silent on the near miss,
is silent while its key is off, and reaches only a card (and its subagents).
Run: python3 hooks/test_discipline.py"""
import atexit, json, os, pathlib, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
# The fixtures must not lie under a temp directory, or every path in them would be scratch: they live beside the tests.
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp(prefix=".t-discipline-", dir=HERE.parent)))
atexit.register(shutil.rmtree, TMP, True)
SCRATCH = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
atexit.register(shutil.rmtree, SCRATCH, True)
STATE = TMP / "state"
os.environ.update(AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(TMP / "cfg.json"), HOME=str(TMP))
sys.path.insert(0, str(HERE))

import roles  # noqa: E402
from lib import config, discipline  # noqa: E402

WORKER = roles.Session("worker", "main", supervised=True)
SUBAGENT = roles.Session("worker", "subagent", supervised=True)
GENERAL, LEAD, ORCH = roles.Session("general"), roles.Session("lead", declared=True), roles.Session("orchestrator", declared=True)


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"})


MAIN, TREE, REMOTE = TMP / "main-checkout", TMP / "work" / "tree", TMP / "remote.git"
sh("git", "init", "-q", "-b", "main", str(MAIN))
(MAIN / "a.txt").write_text("a\n")
sh("git", "add", "a.txt", cwd=MAIN)
sh("git", "commit", "-q", "-m", "init", cwd=MAIN)
sh("git", "worktree", "add", "-q", "-b", "card", str(TREE), cwd=MAIN)
OUTSIDE = TMP / "elsewhere"
OUTSIDE.mkdir()
TRANSCRIPT = TMP / "t.jsonl"


def transcript(*commands):
    TRANSCRIPT.write_text("".join(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]}}) + "\n"
        for i, c in enumerate(commands)))


def rows():
    f = STATE / "turn-warnings.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def reset(cfg=None):
    (STATE / "turn-warnings.jsonl").unlink(missing_ok=True)
    pathlib.Path(os.environ["AGENT_SKILLS_CONFIG"]).write_text(json.dumps(cfg or {}))


def bash(cmd, s=WORKER, cwd=TREE, cfg=None, tr=None, tool_use_id=None):
    reset(cfg)
    event = {"cwd": str(cwd), "session_id": "s", "transcript_path": str(tr or TRANSCRIPT), "tool_use_id": tool_use_id}
    return [f.key for f in discipline.check_bash(cmd, event, s, config.load())]


def edit(path, s=WORKER, cwd=TREE, cfg=None, tool="Write"):
    reset(cfg)
    event = {"cwd": str(cwd), "session_id": "s"}
    return [f.key for f in discipline.check_write({"file_path": str(path)}, event, s, config.load())]


transcript("ls")
# ---- worker.edit_tool: the file writes a card made with Bash --------------------------------------------------------
EDIT = ["worker.edit_tool"]
for cmd in ("echo x > hooks/a.py", "echo x >> notes.md", "cat > f.py <<'EOF'\nprint(1)\nEOF", "printf x | tee out.txt",
            "sed -i 's/a/b/' hooks/a.py", "sed -i.bak 's/a/b/' a.py", "sed -ni 's/a/b/p' a.py", "sed --in-place s/a/b/ a.py",
            "python3 -c \"open('a.py','w').write('x')\"", "python3 -c \"import pathlib; pathlib.Path('a').write_text('x')\"",
            "python3 - <<'PY'\nimport pathlib\npathlib.Path('a.py').write_text('x')\nPY",
            "python3 - <<'PY'\nwith open('a.py', 'w') as f:\n    f.write('x')\nPY",
            "make build &> build.log", "make 2> err.log", "echo x > \"$OUT\""):
    assert bash(cmd) == EDIT, cmd
for cmd in ("echo x > /dev/null", "ls 2>&1", "make >&2", "echo x > /tmp/a.txt", f"echo x > {SCRATCH}/scratch.txt",
            "echo x > $TMPDIR/a", "echo x > ${TMPDIR}/a", "cat f | tee /dev/null", "sed 's/a/b/' a.py", "sed -n 1,5p a.py",
            "sed -e s/i/j/ a.py", "echo '>' file", "grep -n '>' a.py", "git commit -m 'sed -i is banned'",
            "python3 -c \"print(open('a.py').read())\"", "python3 -c \"open('/tmp/x','w').write('x')\"",
            "python3 - <<'PY'\nprint(open('a.py').read())\nPY", "cat a.py | head", "tee",
            "echo x > /private/tmp/claude-1/scratchpad/n.txt", "echo r > /var/folders/ab/cd/T/orch-refusal/tree/r.md",
            "echo x # > a.py"):
    assert bash(cmd) == [], cmd
orch_refusal = pathlib.Path(os.environ.get("TMPDIR") or "/tmp") / "orch-refusal" / "tree" / "r.md"
assert bash(f"echo x > {orch_refusal}") == []
# a redirect in a subshell or substitution is still a write
assert bash("echo $(echo x > a.py)") == EDIT
# sed -i reads every file operand, not the last one only; -e gives the script, so the first operand is a file
assert bash("sed -i 's/a/b/' a.py /tmp/last.py") == EDIT
assert bash("sed -i -e s/a/b/ a.py") == EDIT
assert bash("sed -i s/a/b/ /tmp/x /tmp/y") == []
assert bash("sed -i -e s/a/b/ /tmp/x") == []
# python: any path expression, Path.open, and a temp word elsewhere in the code excuses nothing
assert bash("python3 -c \"from pathlib import Path; open(Path('a.py'), 'w').write('x')\"") == EDIT
assert bash("python3 -c \"from pathlib import Path; Path('a.py').open('w').write('x')\"") == EDIT
assert bash("python3 -c \"import tempfile; open('a.py','w').write('x')\"") == EDIT
assert bash("python3 -c \"open('/tmp/x.txt', 'w').write('x')\"") == []
assert bash("python3 -c \"import pathlib; pathlib.Path('/tmp/x').write_text('x')\"") == []
# a python heredoc is a command only where bash runs it; its stdout going to /dev/null changes nothing
assert bash("cat <<'EOF'\npython3 - <<'PY'\nopen('a.py','w').write('x')\nPY\nEOF") == []
assert bash("python3 - <<'PY' > /dev/null\nopen('a.py','w').write('x')\nPY") == EDIT
assert bash("python3 -c \"open('a.py').read(); print('x', 'w')\"") == []  # a read; the 'w' belongs to print
assert bash("python3 -c \"import os; open(os.path.join('d', 'a.py'), 'w').write('x')\"") == EDIT
assert bash("sed -i '' 's/x/y/' /tmp/x") == [] and bash("sed -i '' s/x/y/ a.py") == EDIT  # BSD empty suffix
slow = __import__("time").monotonic()
assert bash("python3 -c " + __import__("shlex").quote("# open('a.py', '" + "w" * 100000 + "z')")) == []
assert __import__("time").monotonic() - slow < 1.5  # no quadratic backtracking on a long literal
# a `cd` in a subshell, a substitution or a pipeline leaves the rest where it was
assert bash("(cd /tmp); echo x > a.py") == EDIT
assert bash("echo \"$(cd /tmp)\"; echo x > a.py") == EDIT
assert bash("(cd /tmp; echo x > a.py)") == []  # the subshell's cd holds inside it
assert bash("(true; cd /tmp); echo x > a.py") == EDIT  # and ends with it
assert bash("cd /tmp |& cat; echo x > a.py") == EDIT
assert "worker.chain" not in bash("git status; git push --dry-run") and "worker.chain" not in bash("ls\ngit commit --dry-run")
assert bash("python3 -c \"scratch='/tmp/x'; open('a.py','w').write('x=3')\"") == EDIT  # the temp word is not the target
assert bash("python3 -c \"open('/tmp/x.txt','w').write('x')\"") == []
assert bash("python3 -c \"print(\\\"open('a.py', 'w')\\\")\"") == []  # text, not a call
assert bash("python3 -c \"# open('a.py','w')\"") == []
slow = __import__("time").monotonic()
assert bash("cat <<'EOF'\n" + "python3 <<PY\n" * 8000 + "EOF\n") == []
assert __import__("time").monotonic() - slow < 1.5  # candidates are filtered by what bash runs, not searched for
assert bash("python3 -c 'print(1)' <<'PY'\nopen('a.py','w').write('x')\nPY") == []  # stdin is not the program
assert bash("python3 - 3<<'PY'\nopen('a.py','w').write('x')\nPY") == []
assert bash("python3 <<'PY'\nopen('a.py','w').write('x')\nPY") == EDIT
assert bash("(cd /tmp); (echo x > code.py)") == EDIT  # the first subshell's cd ended with it
assert bash("python3 -c 'from pathlib import Path; print(\"/tmp/x\", Path(\"a.py\").write_text(\"x\"))'") == EDIT
assert bash("python3 -c 'from pathlib import Path; Path(\"/tmp/x\").write_text(\"x\")'") == []
assert bash("echo x > ~a_user_that_does_not_exist_999/file") == EDIT  # an unknown home is left as written
assert "worker.chain" in bash("git status; git commit -am --dry-run")  # the message of -am, no flag
# a `cd` moves where a relative target lands
assert bash("cd /tmp && echo x > a.py") == []
assert bash(f"cd {TREE} && echo x > a.py", cwd=SCRATCH) == EDIT

# ---- keys: off silences, true/warn/deny act, every firing is a row ---------------------------------------------------
assert bash("echo x > a.py", cfg={"worker": {"edit_tool": "off"}}) == []
assert bash("echo x > a.py", cfg={"worker": {"edit_tool": False}}) == []
assert bash("echo x > a.py", cfg={"worker": {"edit_tool": True}}) == EDIT
assert bash("echo x > a.py", cfg={"worker": {"chain": "off"}}) == EDIT  # keys are separate
assert bash("echo x > a.py") == EDIT and [r["check"] for r in rows()] == ["worker.edit_tool"]
assert rows()[0]["level"] == "warn" and rows()[0]["event"] == "PreToolUse"
reset({"worker": {"edit_tool": "deny"}})
event = {"cwd": str(TREE), "session_id": "s", "transcript_path": str(TRANSCRIPT)}
assert [f.level for f in discipline.check_bash("echo x > a.py", event, WORKER, config.load())] == ["deny"]
assert bash("echo x > a.py", cfg={"worker": {"edit_tool": "off"}}) == [] and rows() == []

# ---- who is reached ------------------------------------------------------------------------------------------------
for s in (GENERAL, LEAD, ORCH):
    assert bash("echo x > a.py", s=s) == [], s.role
    assert edit(OUTSIDE / "x", s=s) == [], s.role
assert bash("echo x > a.py", s=SUBAGENT) == EDIT  # a card's subagent
assert roles.applies("worker_discipline", roles.Session("worker", "main", close=True))

# ---- worker.chain ----------------------------------------------------------------------------------------------------
CHAIN = ["worker.chain"]
for cmd in ("git add a && git commit -m x", "rm -f a.txt; ls", "ls && git push origin HEAD", "mv a b && ls",
            "git status\ngit commit -m x", "orca orchestration send --type worker_done --subject s && echo ok",
            "git -C /x commit -m y && ls", "cd sub && rm a"):
    assert "worker.chain" in bash(cmd), cmd  # pre_push may fire beside it
assert "worker.chain" in bash("echo message | git commit -F - && git status")  # the commit ends a pipeline
for cmd in ("git commit -m x", "git commit -m \"$(cat <<'EOF'\nmsg\nEOF\n)\"", "rm -f a.txt", "ls && git status",
            "git status && git diff", "git log | head", "echo done; ls", "git push origin HEAD", "echo 'a && rm x'",
            "git diff | git commit -F -"):
    assert "worker.chain" not in bash(cmd), cmd

# ---- worker.paths / worker.main_checkout / worker.memory ---------------------------------------------------------
assert edit(TREE / "hooks" / "new.py") == []
assert edit("hooks/new.py") == []
assert edit(SCRATCH / "x.md") == []
assert edit("/tmp/x.md") == [] and edit("/private/tmp/claude-1/scratchpad/x.md") == []
assert edit(pathlib.Path(os.environ.get("TMPDIR") or "/tmp") / "orch-refusal" / "tree" / "r.md") == []
OTHER = TMP / "other-work"
sh("git", "worktree", "add", "-q", "-b", "other", str(OTHER), cwd=MAIN)
assert edit(OTHER / "a.txt") == ["worker.paths"]  # a sibling worktree is not this card's
assert edit(OUTSIDE / "x.py") == ["worker.paths"]
assert edit(MAIN / "a.txt") == ["worker.paths", "worker.main_checkout"]
assert edit(MAIN / "a.txt", cwd=MAIN) == ["worker.main_checkout"]  # a card running in the main checkout itself
assert edit(MAIN / "new" / "dir" / "f.py", cwd=MAIN) == ["worker.main_checkout"]  # a file that does not exist yet
assert edit(OUTSIDE / "x.py", cfg={"worker": {"paths": "off"}}) == []
assert edit(MAIN / "a.txt", cwd=MAIN, cfg={"worker": {"main_checkout": "off"}}) == []
CACHE = TMP / ".claude" / "plugins" / "cache" / "agent-skills" / "hooks"
assert edit(CACHE / "guard.py") == ["worker.paths"]
MEM = TMP / ".claude" / "projects" / "-x-y" / "memory" / "note.md"
assert edit(MEM) == ["worker.memory"]
assert edit(MEM, cfg={"worker": {"memory": "off"}}) == []
assert edit(TMP / ".claude" / "projects" / "-x-y" / "other.md") == ["worker.paths"]
assert bash(f"echo n > {MEM}") == ["worker.edit_tool", "worker.memory"]
assert bash("echo n > ~/.claude/projects/-x-y/memory/n.md") == ["worker.edit_tool", "worker.memory"]
assert edit(OUTSIDE / "x.py", cwd=OUTSIDE) == []  # no repository to judge by: silent

# ---- worker.pre_push ---------------------------------------------------------------------------------------------
PUSH = ["worker.pre_push"]
transcript("ls", "git status")
assert bash("git push origin HEAD") == PUSH
transcript("python3 hooks/test_guard.py", "git status")
assert bash("git push origin HEAD") == []
transcript("orch heavy x -- make test")
assert bash("git push origin HEAD") == []
transcript("git push origin HEAD", "ls")
assert bash("git push origin HEAD") == []  # not the first push
assert bash("git push origin HEAD", tr=TMP / "missing.jsonl") == []  # unreadable transcript: unknown, silent
assert bash("git push --dry-run origin HEAD") == [] and bash("git push -n origin HEAD") == []
# the call being judged is in its own transcript; a mention of a test is not a run
transcript("git push origin HEAD")
assert bash("git push origin HEAD", tool_use_id="t0") == PUSH
transcript("echo pytest", "git diff -- hooks/test_guard.py")
assert bash("git push origin HEAD") == PUSH
transcript("ls")
(TREE / "code.py").write_text("x = 1\n")
(TREE / "test_code.py").write_text("assert 1\n")
sh("git", "add", "code.py", cwd=TREE)
assert bash("git commit -m x") == PUSH  # code, no test, none run
sh("git", "add", "test_code.py", cwd=TREE)
assert bash("git commit -m x") == []  # the test is in the commit
sh("git", "reset", "-q", "test_code.py", cwd=TREE)
transcript("python3 hooks/test_x.py")
assert bash("git commit -m x") == []  # a test ran in this session
sh("git", "reset", "-q", cwd=TREE)
(TREE / "notes.md").write_text("n\n")
sh("git", "add", "notes.md", cwd=TREE)
transcript("ls")
assert bash("git commit -m docs") == []  # no code in it
assert bash(f"git -C {MAIN} commit -m x") == []  # judged in the repository it names, not the card's
sh("git", "reset", "-q", cwd=TREE)
sh("git", "add", "code.py", cwd=TREE)
sh("git", "commit", "-q", "-m", "code", cwd=TREE)
(TREE / "code.py").write_text("x = 2\n")
assert bash("git commit -am fix") == PUSH  # -a takes the tracked change the index does not hold yet
assert bash("git commit code.py -m fix") == PUSH  # so does a pathspec
assert bash("git commit --dry-run -am fix") == []
assert bash("git commit -m --dry-run -a") == PUSH  # `--dry-run` as a message is no flag
assert bash("git commit -C HEAD -a") == PUSH  # `-C <commit>` after the verb is no directory
transcript("find . -name test_x.py -exec python3 {} \\;")
assert bash("git commit -am fix") == []  # a test did run
transcript("ls")
sh("git", "checkout", "-q", "--", "code.py", cwd=TREE)
sh("git", "add", "notes.md", cwd=TREE)
sh("git", "branch", "-q", "--set-upstream-to=main", cwd=TREE)
sh("git", "update-ref", "refs/remotes/origin/card", "HEAD", cwd=TREE)
assert bash("git rebase main") == PUSH  # the branch is on origin
assert bash("git rebase --abort") == []
sh("git", "update-ref", "-d", "refs/remotes/origin/card", cwd=TREE)
assert bash("git rebase main") == []  # never pushed
assert bash("git push origin HEAD", cfg={"worker": {"pre_push": "off"}}) == []

# ---- through guard.py: a dispatched card sees the warning, a person's session sees nothing -------------------------
def guard(tool, tool_input, first_prompt):
    path = TMP / "g.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": first_prompt}}) + "\n")
    reset()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    out = subprocess.run([sys.executable, HERE / "guard.py"], capture_output=True, text=True, env=env, input=json.dumps(
        {"tool_name": tool, "tool_input": tool_input, "cwd": str(TREE), "transcript_path": str(path), "session_id": "g"})).stdout
    return json.loads(out) if out.strip() else {}


CARD = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\nTASK"
out = guard("Bash", {"command": "sed -i s/a/b/ a.txt"}, CARD)
assert "worker.edit_tool" in out["systemMessage"] and "permissionDecision" not in out["hookSpecificOutput"], out
assert [r["check"] for r in rows()] == ["worker.edit_tool"]
assert guard("Bash", {"command": "sed -i s/a/b/ a.txt"}, "fix the bug") == {}
out = guard("Write", {"file_path": str(OUTSIDE / "x.py"), "content": "x"}, CARD)
assert "worker.paths" in out["systemMessage"], out
assert guard("Write", {"file_path": str(TREE / "x.py"), "content": "x"}, CARD).get("systemMessage", "").find("worker.") < 0
print("ok")
