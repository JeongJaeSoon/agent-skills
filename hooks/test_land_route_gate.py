"""land_route_gate.py from stdin fixtures against a fake gh: a role session's `gh pr merge` in a repo the land executor
covers is denied with a runnable `orch land` line; a same-owner repo outside the scope is denied with the skills-config
spec that adds it; an excluded repo, another owner, a general session and the executor off pass.
Run: python3 hooks/test_land_route_gate.py"""
import json, os, pathlib, shutil, stat, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(os.path.realpath(tempfile.mkdtemp()))
HOME, CONFIG, PROGRAMS, BIN, CARD = TMP / "home", TMP / "agent-skills.json", TMP / "programs", TMP / "bin", TMP / "card"
for d in (BIN, CARD, HOME / ".claude"):
    d.mkdir(parents=True)

FAKE_GH = """#!/bin/sh
echo "$PWD $@" >> "$FAKE_GH_LOG"
[ -n "$FAKE_GH_FAIL" ] && exit 1
if [ "$1" = api ]; then
  [ -s "$FAKE_GH_API" ] || exit 1
  cat "$FAKE_GH_API"; exit 0
fi
cat "$FAKE_GH_PR"
"""
(BIN / "gh").write_text(FAKE_GH)
(BIN / "gh").chmod((BIN / "gh").stat().st_mode | stat.S_IEXEC)
PR, LOG = TMP / "pr.json", TMP / "gh.log"
HEAD = "0123456789abcdef0123456789abcdef01234567"
APP = json.dumps({"number": 7, "headRefOid": HEAD, "url": "https://github.com/acme/app/pull/7"})

WORKER = "You are working inside Orca, a multi-agent IDE.\nYou are a dispatched worker\n"
CARD_BRIEF = "/goal fix it\nCLOSE: when merged\n"
PROGRAM_BRIEF = WORKER + "PROGRAM: demo\nGOAL  x\n"
FREE = "fix the typo please\n"


def transcript(text):
    path = TMP / "t.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
    return str(path)


def config(level=None, **ex):
    cfg = {"orchestrate": {"land_executor": {"enabled": True, "repos": ["acme/*"], **ex}}}
    if level:
        cfg["orchestrate"]["land_route"] = level
    CONFIG.write_text(json.dumps(cfg))


def program(slug, repo="acme/app", policy="autonomous", prs=()):
    shutil.rmtree(PROGRAMS / slug, ignore_errors=True)
    (PROGRAMS / slug).mkdir(parents=True)
    (PROGRAMS / slug / "program.json").write_text(json.dumps({"repo": repo, "merge_policy": policy}))
    (PROGRAMS / slug / "ledger.jsonl").write_text("".join(json.dumps({"ev": "ready", "pr": n}) + "\n" for n in prs))


API = TMP / "api.json"


def run(cmd, prompt=CARD_BRIEF, gh=APP, fail=False, api="", **fields):
    PR.write_text(gh)
    API.write_text(api)
    LOG.write_text("")
    env = {"PATH": f"{BIN}:/usr/bin:/bin", "HOME": str(HOME), "AGENT_SKILLS_CONFIG": str(CONFIG),
           "PROGRAMS_HOME": str(PROGRAMS), "FAKE_GH_PR": str(PR), "FAKE_GH_LOG": str(LOG), "FAKE_GH_API": str(API),
           "AGENT_SKILLS_STATE": str(TMP / "state")}
    if fail:
        env["FAKE_GH_FAIL"] = "1"
    payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash", "cwd": str(CARD),
               "tool_input": {"command": cmd}, "transcript_path": transcript(prompt), **fields}
    r = subprocess.run([sys.executable, HERE / "land_route_gate.py"], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def reason(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


config()

# In scope, a standalone card: denied with the land line to run and the verdict only after a review of that head.
out = run("gh pr merge 7 --repo acme/app --squash")
assert decision(out) == "deny", out
text = reason(out)
assert text.startswith("[gate:land-route]"), text
assert "`orch land app-standalone --repo acme/app --pr 7 --wait-minutes 50`" in text, text
assert f"`orch verdict app-standalone --repo acme/app --pr 7 --sha {HEAD} --source <who reviewed>`" in text, text
assert "only then" in text and "review:codex" in text, "the verdict line is never a ready-made receipt"
assert "Do not hand a `gh pr merge` line to the owner" in text, text
assert f"{TMP}/card pr view 7 --repo acme/app --json number,headRefOid,url" in LOG.read_text(), LOG.read_text()
# An old name: the PR's url gives the current one, which names the program.
text = reason(run("gh pr merge 7 --repo acme/old-name"))
assert "`orch land app-standalone --repo acme/app --pr 7" in text, text

# Every way of naming the repo and the PR.
for cmd in ("gh pr merge https://github.com/acme/app/pull/7 --merge", "gh pr merge 7 -Racme/app",
            "gh pr merge 7 --repo=acme/app", "GH_REPO=acme/app gh pr merge 7", "gh -R acme/app pr merge 7",
            "gh pr merge --squash", "gh pr merge feature-branch", "/opt/homebrew/bin/gh pr merge 7 --auto",
            "git push && gh pr merge 7 --repo acme/app", "gh pr merge 7 --subject 'x --disable-auto' --repo acme/app",
            "gh pr merge 7 --repo github.com/acme/app", "GH_REPO=github.com/acme/app gh pr merge 7",
            "gh pr merge 7 --repo https://github.com/acme/app.git", "gh pr merge 7 --repo GitHub.com/Acme/App"):
    assert decision(run(cmd)) == "deny", cmd
# No repo named: the PR's own url says which, from the session's directory.
run("gh pr merge")
assert f"{TMP}/card pr view --json number,headRefOid,url" in LOG.read_text(), LOG.read_text()

# Not merges, or not this gate's call.
for cmd in ("gh pr merge 7 --disable-auto --repo acme/app", "gh pr merge --help", "gh pr view 7 --repo acme/app",
            "echo gh pr merge 7", "git merge main"):
    assert run(cmd) == {}, cmd

# Roles: an orchestrator, a lead, a worker and its subagent, or a PROGRAM brief; a general session passes.
assert decision(run("gh pr merge 7 --repo acme/app", prompt=FREE, agent_type="agent-skills:orchestrator")) == "deny"
assert decision(run("gh pr merge 7 --repo acme/app", prompt=WORKER, agent_id="sub1")) == "deny", "a worker's subagent"
assert run("gh pr merge 7 --repo acme/app", prompt=FREE) == {}, "a session nobody dispatched merges as it likes"

# The program that holds the PR is the route; then the brief's PROGRAM; never a fresh standalone over either.
program("demo", prs=())
text = reason(run("gh pr merge 7 --repo acme/app", prompt=PROGRAM_BRIEF))
assert "`orch land demo --pr 7 --wait-minutes 50`" in text, text
program("held", prs=(7,))
text = reason(run("gh pr merge 7 --repo acme/app", prompt=PROGRAM_BRIEF))
assert "`orch land held --pr 7" in text and "standalone" not in text.split("A fork")[0], text
program("gated", policy="human-gate", prs=(7,))
text = reason(run("gh pr merge 7 --repo acme/app"))
assert "more than one program (gated, held)" in text, text
shutil.rmtree(PROGRAMS / "held")
text = reason(run("gh pr merge 7 --repo acme/app"))
assert "`orch land gated --pr 7" in text and "human-gate" in text and "READY-WAIT: owner-merge" in text, text
program("other-repo", repo="acme/web", prs=(7,))
text = reason(run("gh pr merge 7 --repo acme/app", api='{"full_name": "acme/web"}'))
assert "`orch land gated --pr 7" in text, "a program on another repo is not it: " + text
# ... and when GitHub cannot say where that other name leads, nothing is guessed.
assert "could not tell whether acme/web" in reason(run("gh pr merge 7 --repo acme/app"))
for slug in ("gated", "other-repo", "demo"):
    shutil.rmtree(PROGRAMS / slug)
# A program made before a rename keeps the old name: it still holds the PR, gate and all; no fresh standalone.
program("gated-old", repo="acme/old-name", policy="human-gate", prs=(7,))
text = reason(run("gh pr merge 7 --repo acme/app", api='{"full_name": "acme/app"}'))
assert "`orch land gated-old --pr 7" in text and "READY-WAIT: owner-merge" in text, text
assert "app-standalone" not in text, text
shutil.rmtree(PROGRAMS / "gated-old")
program("demo", repo="acme/old-name")
text = reason(run("gh pr merge 7 --repo acme/app", prompt=PROGRAM_BRIEF, api='{"full_name": "acme/app"}'))
assert "`orch land demo --pr 7" in text, "the brief's program under the old name: " + text
shutil.rmtree(PROGRAMS / "demo")

# Same owner, outside the scope: denied with the spec that adds the one repo (or the owner) and the emit line.
config(repos=["acme/app"])
NEW = json.dumps({"number": 3, "headRefOid": HEAD, "url": "https://github.com/acme/new/pull/3"})
out = run("gh pr merge 3 --repo acme/new", gh=NEW)
text = reason(out)
assert decision(out) == "deny", out
assert '{"agent_skills": {"set": {"orchestrate.land_executor.repos": ["acme/app", "acme/new"]}}}' in text, text
assert '"acme/app", "acme/*"' in text and "config_emit.py emit --spec" in text, text
assert "`orch land new-standalone --repo acme/new --pr 3 --wait-minutes 50`" in text, text
assert "orchestrate.land_executor.exclude" in text and "Never hand a `gh pr merge` line to the owner" in text, text
# ... and a program's PR there lands through its program, which merges directly while the repo is out.
program("newprog", repo="acme/new", prs=(3,))
text = reason(run("gh pr merge 3 --repo acme/new", gh=NEW))
assert "belongs to program newprog" in text and "`orch land newprog --pr 3" in text and "skills-config" not in text, text
shutil.rmtree(PROGRAMS / "newprog")
# ... but a program whose recorded name is listed while GitHub's current name is not: orch land refuses it, so the
# denial gives the spec that lists the new name, never "merges directly".
config(repos=["acme/old-name"])
program("moved", repo="acme/old-name", prs=(7,))
text = reason(run("gh pr merge 7 --repo acme/app", api='{"full_name": "acme/app"}'))
assert "refuses it until the new name is in scope" in text and "merges it directly" not in text, text
assert '{"agent_skills": {"set": {"orchestrate.land_executor.repos": ["acme/old-name", "acme/app"]}}}' in text, text
assert "`orch land moved --pr 7" in text, text
shutil.rmtree(PROGRAMS / "moved")

# Excluded on purpose, another owner, the executor off or absent: pass.
config(repos=["acme/*"], exclude=["acme/legacy"])
LEGACY = json.dumps({"number": 1, "headRefOid": HEAD, "url": "https://github.com/acme/legacy/pull/1"})
assert run("gh pr merge 1 --repo acme/legacy", gh=LEGACY) == {}
assert run("gh pr merge 1 --repo acme/legacy-old-name", gh=LEGACY) == {}, "renamed into an exclusion"
assert run("gh pr merge 1 --repo acme/legacy") == {}, "excluded under the name given, whatever it is now"
ELSE = json.dumps({"number": 1, "headRefOid": HEAD, "url": "https://github.com/someone/else/pull/1"})
assert run("gh pr merge 1 --repo someone/else", gh=ELSE) == {}
assert run("gh pr merge 1 --repo ghe.example.test/acme/app") == {}, "another GitHub host is outside this scope"
config(enabled=False)
assert run("gh pr merge 7 --repo acme/app") == {}
CONFIG.unlink()
assert run("gh pr merge 7 --repo acme/app") == {}

# A repo this hook cannot trust: a directory change first, or two repos named. Denied until --repo says which.
config()
text = reason(run("cd ../other && gh pr merge 7"))
assert "name it with `--repo OWNER/NAME`" in text, text
assert decision(run("cd ../other && gh pr merge 7 --repo acme/app")) == "deny"
assert run("cd ../other && gh pr merge 1 --repo someone/else", gh=ELSE) == {}
assert "more than one repo" in reason(run("gh pr merge https://github.com/acme/app/pull/7 --repo acme/web"))

# Settings that cannot be read deny, as orch land stops on them; a PR GitHub does not answer passes with one line.
CONFIG.write_text('{"orchestrate": {"land_executor": {"enabled": tr')
assert "cannot be parsed" in reason(run("gh pr merge 7 --repo acme/app"))
config(repos=["*/*"])
assert "is not OWNER/NAME or OWNER/*" in reason(run("gh pr merge 7 --repo acme/app"))
config()
out = run("gh pr merge 7", fail=True)
assert decision(out) is None and "passed this call unjudged" in out["systemMessage"], out
out = run("gh pr merge 7 --repo acme/app", fail=True)
assert decision(out) == "deny" and "<head sha>" in reason(out), "a named repo is judged without GitHub"

# Levels: warn speaks without blocking, off reads nothing.
config(level="warn")
out = run("gh pr merge 7 --repo acme/app")
assert decision(out) is None and "orch land app-standalone" in out["systemMessage"], out
config(level="off")
assert run("gh pr merge 7 --repo acme/app") == {} and LOG.read_text() == ""

shutil.rmtree(TMP)
print("ok")
