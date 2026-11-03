"""guard.py with lib/danger.py: each M1 rule denies (or warns, or asks) on the commands it is about, passes look-alikes,
goes back to the old behavior when its key is false, and a check that raises lets the call through with a warning.
Run: python3 hooks/test_danger.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
HOME = TMP / "home"
CONFIG = HOME / ".claude" / "agent-skills.json"
STATE = TMP / "state"
BIN = TMP / "bin"
(HOME / ".claude").mkdir(parents=True)
BIN.mkdir()


def env(**extra):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("CLAUDE_", "AGENT_SKILLS_", "XDG_")) and k != "ORCA_TERMINAL_HANDLE"}
    base.update(HOME=str(HOME), AGENT_SKILLS_STATE=str(STATE), AGENT_SKILLS_CONFIG=str(CONFIG),
                PATH=f"{BIN}:/usr/bin:/bin", ORCA_TERMINAL_HANDLE="term_me")
    base.update(extra)
    return base


def hook(tool, tool_input, cwd=None, **event):
    payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": tool, "tool_input": tool_input,
               "cwd": str(cwd or TMP), **event}
    out = subprocess.run([sys.executable, HERE / "guard.py"], input=json.dumps(payload), capture_output=True,
                         text=True, env=env()).stdout
    return json.loads(out) if out.strip() else {}


def verdict(out):
    spec = out.get("hookSpecificOutput") or {}
    if spec.get("permissionDecision"):
        return spec["permissionDecision"]
    return "warn" if out.get("systemMessage") else "none"


def bash(cmd, cwd=None, **event):
    return verdict(hook("Bash", {"command": cmd}, cwd, **event))


def configure(cfg):
    CONFIG.write_text(json.dumps(cfg))


def off(key):
    *head, last = key.split(".")
    cfg = node = {}
    for part in head:
        node = node.setdefault(part, {})
    node[last] = False
    return cfg


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env())


# A repository the external-review rule can resolve, and one with an uncommitted change for reset --hard.
REPOX = TMP / "app"
REPOX.mkdir()
git("init", "-q", cwd=REPOX)
git("remote", "add", "origin", "https://github.com/acme/app.git", cwd=REPOX)
(REPOX / "a.txt").write_text("a")
git("add", "a.txt", cwd=REPOX)
git("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "a", cwd=REPOX)
(REPOX / "a.txt").write_text("changed")
CLEAN = TMP / "clean"
CLEAN.mkdir()
git("init", "-q", cwd=CLEAN)
# A stand-in `orca` that says the current worktree is the main one.
(BIN / "orca").write_text("#!/bin/sh\necho '{\"result\": {\"worktree\": {\"isMainWorktree\": true}}}'\n")
(BIN / "orca").chmod(0o755)
# An emitted settings script, anywhere, carrying the docstring line of the plugin's settings scripts.
EMITTED = TMP / "skills-config-x.py"
EMITTED.write_text('"""Change keys. The user runs --apply, never the agent."""\n')
OTHER_SETUP = TMP / "other" / "scripts" / "setup.py"
OTHER_SETUP.parent.mkdir(parents=True)
OTHER_SETUP.write_text("print('another project')\n")
SETTINGS = HOME / ".claude" / "settings.json"

configure({"repos": {"acme/app": {"external_llm": False}}})

# (key, level, [(command, cwd)] it is about, [command] that only looks like one)
CASES = [
    ("guard.deny.pattern_kill", "deny",
     ["pkill -f telegram", "killall node", "sudo pkill -9 codex", "ls && pkill x", 'echo "$(pkill x)"',
      "kill $(pgrep -f codex)", "kill -9 `pgrep node`", "pgrep -f codex | xargs kill", "/usr/bin/pkill x",
      "X=1 pkill y", "if pkill x; then echo; fi", "pk\\\nill node", "echo then case x in; pkill node; (echo ok)", "sudo -u root pkill -f node", "pgrep x | xargs -n1 kill",
      "if true; then kill $(pgrep x); fi", "/bin/kill $(pgrep node)", "if kill $(pgrep node); then :; fi",
      'echo "${HOME}"; echo "$(pkill node)"', "echo ${HOME}; cat <<EOF\n$(pkill node)\nEOF",
      "echo ${HOME}; cat <<'EOF'\nx\nEOF\npkill node"],
     ["kill 1234", "kill -9 1234 5678", "pgrep -f codex", "echo pkill", "grep -rn pkill skills",
      "git commit -m 'stop using pkill'", "man killall", "echo 'kill $(pgrep x)'", "cat <<'EOF'\npkill x\nEOF",
      "echo kill $(pgrep -f node)", "pgrep -f node | xargs echo kill", "env -u pkill echo safe",
      'echo "${x:-; pkill node;}"', "echo sudo kill $(pgrep node)", "echo ${HOME} # example; pkill node",
      """echo "${HOME}"; echo "$(printf '%s' 'example; pkill node; text')\"""",
      'echo "${HOME}"; echo "$(echo x # ; pkill node\n)"',
      "printf '%s\\n' \"${HOME}\"; cat <<'EOF'\npkill node\nEOF", "echo ${HOME}; cat <<EOF\npkill node\nEOF\necho ok",
      'case "$tool" in pkill|killall) command -v "$tool" ;; esac', "case $t in\n  pkill) echo a ;;\n  killall) echo b ;;\nesac",
      "echo ${HOME}; cat <<EOF\n\\$(pkill node)\nEOF",
      "case a in a) case pkill in pkill|killall) command -v pkill ;; esac ;; esac",
      "case x in esac*) : ;; pkill|killall) : ;; esac", "echo 'pk\\\nill'"]),
    ("guard.deny.prune", "deny",
     ["docker system prune -af", "docker volume prune -f", "docker image prune", "podman system prune",
      "docker compose down -v", "docker compose -p x down --volumes", "docker-compose down -v",
      "docker compose -f a.yml down --rmi all -v", "docker compose down -tv", "git worktree prune",
      "git -C /repo worktree prune -v", "docker container prune", "docker --context x system prune -f",
      "docker compose --project-name x down -v", "docker compose \\\n  down -v", "docker sys\\\ntem prune",
      "case $x in a) docker system prune -f ;; esac"],
     ["docker system df", "docker compose down", "docker compose up -d", "docker compose down -t 10",
      "git worktree list", "git worktree remove /x", "echo docker system prune", "git gc",
      "docker compose logs -v", "grep 'down -v' notes.md", "docker run --rm alpine echo system prune",
      "git log -- worktree prune", "docker ps # system prune", "docker compose run app down -v"]),
    ("guard.deny.worktree_rm", "deny",
     ["orca worktree rm --worktree id:1 --force", "orca worktree rm --worktree id:1 --allow-failed-archive-hook",
      "orca worktree rm --worktree current"],
     ["orca worktree rm --worktree id:1", "orca worktree list", "echo 'orca worktree rm --force'"]),
    ("guard.deny.automode_reset", "deny", ["claude auto-mode reset", "claude auto-mode reset --yes"],
     ["claude auto-mode list", "echo claude auto-mode reset"]),
    ("guard.deny.keychain", "deny",
     ["security find-generic-password -s x -w", "security find-internet-password -s x -gw", "security dump-keychain",
      "security find-generic-password -a me -s x -g"],
     ["security find-generic-password -s x", "security list-keychains", "echo security dump-keychain"]),
    ("guard.deny.url_credentials", "deny",
     ["git clone https://user:tok@example.test/acme/app.git", "git remote set-url origin https://x:$TOKEN@example.test/a",
      "curl https://u:p@example.test/api"],
     ["git clone https://example.test/acme/app.git", "git clone git@example.test:acme/app.git",
      "curl http://localhost:3000/a@b", "curl https://example.test:8443/x", "ssh://git@example.test/a"]),
    ("guard.deny.user_settings", "deny",
     [f"echo '{{}}' > {SETTINGS}", "echo x >> ~/.claude/settings.json", "jq . a > $HOME/.claude/agent-skills.json",
      "cp /tmp/s.json ~/.claude/settings.json", "mv new.json ~/.claude/settings.local.json",
      "tee ~/.claude/settings.json < x", "sed -i '' s/a/b/ ~/.claude/settings.json",
      "rm ~/.claude/agent-skills.json", f"echo x > {HOME}/.config/secure-fill/allow.json",
      'echo "$(cat /tmp/new.json)" > ~/.claude/settings.json', "mv ~/.claude/settings.json /tmp/settings.backup",
      "cp /tmp/settings.json ~/.claude/", "x=`cat a` && echo \"$x\" > ~/.claude/settings.json",
      "printf 123 > ~/.claude/settings.json", "echo x 3> ~/.claude/settings.json", "cp -t ~/.claude/ /tmp/settings.json",
      "cp --target-directory ~/.claude /tmp/settings.json", 'echo x > "$HOME/.claude/settings.json"',
      "cp x ${HOME}/.claude/settings.json", "> ~/.claude/settings.json", "echo x >& ~/.claude/settings.json"],
     ["cat ~/.claude/settings.json", "jq . ~/.claude/settings.json", "cp ~/.claude/settings.json /tmp/backup.json",
      "echo x > .claude/settings.json", "echo '> ~/.claude/settings.json'", "diff ~/.claude/settings.json x",
      'echo ">" ~/.claude/settings.json', "echo ok # > ~/.claude/settings.json", "echo \\> ~/.claude/settings.json",
      "cp ~/.claude/settings.json /tmp/", "cp -t /tmp ~/.claude/settings.json",
      "rm -f '$HOME/.claude/settings.json'", "echo x > '~/.claude/settings.json'", "echo x > \\~/.claude/settings.json",
      "echo x >&2", "echo x 2>&1 >&-"]),
    ("guard.deny.plugin_apply", "deny",
     [f"python3 {REPO}/scripts/setup.py --apply abc123", f"python3 {REPO}/scripts/bootstrap.py --write",
      f"python3 {REPO}/skills/skills-config/scripts/config_emit.py --spec s.json --apply 12ab",
      f"python3 {EMITTED} --apply 12ab", f"python3 {REPO}/skills/tune-automode/scripts/automode_rule.py --apply=1"],
     [f"python3 {REPO}/scripts/setup.py", f"python3 {EMITTED}", f"python3 {OTHER_SETUP} --apply x",
      f"python3 {REPO}/scripts/bootstrap.py", "echo --apply"]),
    ("guard.deny.slash_send", "deny",
     ["orca terminal send --terminal t1 --text '/reload-plugins'", 'orca terminal send --terminal t1 --text "/model opus"',
      "orca terminal send --terminal t1 --text /reload"],
     ["orca terminal send --terminal t1 --text 'run /reload later'", "orca terminal read --terminal t1",
      "orca terminal send --terminal t1 --text '/goal fix'"]),
    ("guard.deny.prompt_substitution", "deny",
     ['orca worktree create --repo r --name n --prompt "$(cat brief.md)\nCLOSE: x"',
      "orca worktree create --repo r --name n --prompt \"`cat b`\nCLOSE: x\""],
     ["orca worktree create --repo r --name n --prompt '/goal fix $(not run)\nCLOSE: x'",
      'orca worktree create --repo r --name n --prompt "/goal fix\nCLOSE: x"']),
    ("guard.deny.self_exempt_label", "deny",
     ["gh pr edit 12 --add-label no-visual-change", "gh pr create --title t --body b --label a,no-visual-change",
      "gh api repos/acme/app/issues/12/labels -f 'labels[]=no-visual-change'", "gh issue edit 12 --add-label=no-visual-change"],
     ["gh pr edit 12 --remove-label no-visual-change", "gh pr view 12 --json labels", "gh pr edit 12 --add-label bug",
      "gh api --method DELETE repos/acme/app/issues/12/labels/no-visual-change",
      'gh api repos/acme/app/issues/12/labels --jq ".[] | select(.name == \\"no-visual-change\\")"',
      "gh api repos/acme/app/issues/1/labels -f 'labels[]=no-visual-change-needed'"]),
    ("guard.deny.owner_only", "deny", ["orch refusal clear r1", "orch exempt visual 12"],
     ["orch refusal report", "orch status p", "echo orch exempt"]),
    ("guard.deny.external_review", "deny",
     [(f"codex exec --sandbox read-only - < p.md", REPOX), f"codex exec -C {REPOX} --sandbox read-only -",
      (f"codex exec --cd={REPOX} -", CLEAN), (f"codex exec -C{REPOX} -", CLEAN)],
     [(f"codex exec --sandbox read-only -", CLEAN), "echo codex exec", (f"git log", REPOX)]),
    ("guard.ask.pressure_bypass", "ask", ["AGENT_SKILLS_PRESSURE_GATE=0 orch heavy p -- npm test",
                                          "env AGENT_SKILLS_PRESSURE_GATE=0 orch heavy p -- x",
                                          "export AGENT_SKILLS_PRESSURE_GATE=0"],
     ["AGENT_SKILLS_PRESSURE_GATE=1 orch heavy p -- npm test", "echo AGENT_SKILLS_PRESSURE_GATE",
      "echo AGENT_SKILLS_PRESSURE_GATE=0"]),
    ("guard.warn.rm_expansion", "warn", ["rm -rf $DIR/build", "rm *.log", "rm -f /tmp/x/*"],
     ['rm -rf "${DIR:?}/build"', "rm -rf ${DIR:?}/build", "rm -f /tmp/x/a.log", 'rm "$F"']),
    ("guard.warn.reset_hard", "warn", [("git reset --hard origin/main", REPOX)],
     [("git reset --hard origin/main", CLEAN), ("git reset --soft HEAD~1", REPOX)]),
    ("guard.warn.run_download", "warn", ["gh run download 123"],
     ["gh run download 123 -D /tmp/x", "gh run download 123 --dir=/tmp/x", "gh run view 123"]),
]


def each(items):
    for item in items:
        yield item if isinstance(item, tuple) else (item, None)


for key, level, about, alike in CASES:
    for cmd, cwd in each(about):
        got = bash(cmd, cwd)
        assert got == level, (key, cmd, got)
    for cmd, cwd in each(alike):
        got = bash(cmd, cwd)
        assert got in ("none", "allow"), (key, cmd, got)
    # Off: the same commands are left to everything else, as before the rule.
    cfg = off(key)
    cfg.setdefault("repos", {"acme/app": {"external_llm": False}})
    configure(cfg)
    for cmd, cwd in each(about):
        got = bash(cmd, cwd)
        assert got != level, (key, cmd, got)
    configure({"repos": {"acme/app": {"external_llm": False}}})

# The reason names the rule's key, so the human knows what to turn off.
reason = hook("Bash", {"command": "pkill x"})["hookSpecificOutput"]["permissionDecisionReason"]
assert "guard.deny.pattern_kill" in reason and "kill <pid>" in reason, reason
# A quoted `$` is read as text but shown as `$`.
reason = hook("Bash", {"command": "docker --context '$CTX' system prune"})["hookSpecificOutput"]["permissionDecisionReason"]
assert "$CTX" in reason and "" not in reason, reason

# A warn rule can be raised to deny, and a deny rule lowered to warn.
configure({"guard": {"warn": {"run_download": "deny"}, "deny": {"pattern_kill": "warn"}}})
assert bash("gh run download 123") == "deny"
assert bash("pkill x") == "warn"
configure({})

# No repos key: codex is not judged at all.
assert bash("codex exec -", REPOX) == "none"

# A worker's `tailscale serve` warns; another session's does not.
PREAMBLE = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n"
TRANSCRIPT = TMP / "worker.jsonl"
TRANSCRIPT.write_text(json.dumps({"type": "user", "message": {"content": PREAMBLE}}))
assert bash("tailscale serve 3000", transcript_path=str(TRANSCRIPT)) == "warn"
assert bash("tailscale serve 3000") == "none"

# Edit, Write and NotebookEdit of the user's settings are denied; a project's are not.
for tool in ("Edit", "Write", "MultiEdit"):
    assert verdict(hook(tool, {"file_path": str(SETTINGS)})) == "deny", tool
    assert verdict(hook(tool, {"file_path": str(HOME / ".claude" / "agent-skills.json")})) == "deny", tool
    assert verdict(hook(tool, {"file_path": str(REPOX / ".claude" / "settings.json")})) == "none", tool
assert verdict(hook("NotebookEdit", {"notebook_path": str(SETTINGS)})) == "deny"
LINK = TMP / "link.json"
LINK.symlink_to(SETTINGS)
assert verdict(hook("Write", {"file_path": str(LINK)})) == "deny", "a symlink is judged by its target"
configure(off("guard.deny.user_settings"))
assert verdict(hook("Write", {"file_path": str(SETTINGS)})) == "none"
configure({})

# A deny and the old allow: the deny wins. Off, the old allow is back.
assert bash("orch exempt visual 12") == "deny"
configure(off("guard.deny.owner_only"))
assert bash("orch exempt visual 12") == "allow"
assert bash("orch refusal clear r1") == "allow", "off restores the old allow"
configure({})

# The hook never stops a session over its own fault: a check that raises passes with a warning and a log line.
out = hook("Bash", {"command": 123})
assert verdict(out) == "warn" and "could not finish its checks" in out["systemMessage"], out
log = [json.loads(x) for x in (STATE / "guard-errors.jsonl").read_text().splitlines()]
assert log[-1]["hook"] == "guard" and log[-1]["error"] == "TypeError" and "123" not in json.dumps(log[-1]), log
CONFIG.write_text("{not json")
assert bash("pkill x") == "deny", "an unreadable settings file means the defaults"
CONFIG.write_text(json.dumps({"guard": "not a dict"}))
assert bash("pkill x") == "deny"
configure({})

# The hook is wired for the tools these rules read.
wired = json.loads((HERE / "hooks.json").read_text())["hooks"]["PreToolUse"]
matcher = next(e["matcher"] for e in wired for h in e["hooks"] if "guard.py" in h["command"])
for tool in ("Bash", "Skill", "Edit", "Write", "MultiEdit", "NotebookEdit", "AskUserQuestion"):
    assert tool in matcher.split("|"), tool
print("danger: all pass")
