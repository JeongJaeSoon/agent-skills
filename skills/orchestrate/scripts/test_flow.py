#!/usr/bin/env python3
"""flow.py: the data flow map filled by stat, and skill changes from git log, prs.json and the signals ledger."""
import json, os, pathlib, subprocess, sys, tempfile, time

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"ORCH_FLEET_STATE": str(tmp / "state" / "dashboard"), "PROGRAMS_HOME": str(tmp / "programs"),
                   "HOME": str(tmp / "home")})
os.environ.pop("AGENT_SKILLS_STATE", None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import flow  # noqa: E402

d = tmp / "state" / "dashboard"
d.mkdir(parents=True)
(d / "state.json").write_text("{}")
os.utime(d / "state.json", (time.time() - 900, time.time() - 900))  # past its 300 s: stale
(d / "events.jsonl").write_text('{"ev":"phase","kind":"x"}\n{"ev":"pr_merged"}\n')
(d / "leftover.json").write_text("{}")
(tmp / "state" / "janitor.json").write_text("{}")
(tmp / "programs" / "_skill-usage").mkdir(parents=True)
(tmp / "programs" / "a").mkdir()
(tmp / "programs" / "a" / "ledger.jsonl").write_text("{}\n")
report = tmp / "cards" / "c.md"
report.parent.mkdir()
report.write_text("# c\n")

(d / "tidy.jsonl").mkdir()  # a directory where the closure log belongs: an error, not a missing file
(tmp / "state" / "roles").mkdir()
(tmp / "state" / "roles" / "s1.json").write_text("{}")
(tmp / "programs" / "_locks").mkdir()
(tmp / "programs" / "_locks" / "stray.txt").write_text("")
(tmp / "programs" / "a" / "notes-by-hand.md").write_text("")  # a lead's own file: registered as a hand file
(tmp / "state" / ".stray.json").write_text("{}")  # a hidden file no writer makes is still unregistered
(d / ".board.json.abc123").write_text("")  # a write_atomic temporary beside board.json is not
(d / "board.json").write_text("{}")
(d / ".prs.json.abc123").write_text("")  # a temporary of a registered name whose file is not there yet
(d / ".leftover.json.backup").write_text("")  # named after an unregistered file: as unregistered as the file
(tmp / "state" / "card-watch").mkdir()
(tmp / "state" / "card-watch" / "x.json").mkdir()  # a directory where a card-watch file belongs

rep = flow.flow([{"report": {"path": str(report)}}, {"report": None}, {}])
by = {s["id"]: s for s in rep["stores"]}
assert by["state"]["stale"] and by["state"]["state"] == "present", by["state"]
assert by["events"]["lines"] == 2 and by["events"]["last"] == "pr_merged" and not by["events"]["stale"], by["events"]
assert by["prs"]["state"] == "not yet" and by["prs"]["name"] == "prs.json"
assert by["tidy"]["state"] == "error" and "directory" in by["tidy"]["error"], by["tidy"]
assert by["prsstate"]["state"] == "off", by["prsstate"]  # watch-prs has no "prs" key in the config
assert by["orca"]["state"] == "outside" and by["github"]["writers"] == ["outside"]
assert by["progledger"]["entries"] == 1, by["progledger"]  # the */ledger.jsonl glob
assert by["roles"]["entries"] == 1 and by["roles"]["writers"] == ["hooks"], by["roles"]
assert by["reports"]["entries"] == 1 and by["reports"]["files"][0]["age"] is not None
assert sorted(pathlib.Path(p).name for p in rep["unread"]) == [".leftover.json.backup", ".stray.json", "janitor.json",
                                                                "leftover.json", "stray.txt"], rep["unread"]
assert by["cardwatch"]["state"] == "error" and "directory" in by["cardwatch"]["error"], by["cardwatch"]
assert by["notes"]["state"] == "off", by["notes"]  # reflect.ledger is not set: an outside source switched off
cons = {c["id"]: c for c in rep["consumers"]}
assert cons["mentions"]["state"] == "off" and cons["mentions"]["starved"], cons["mentions"]
assert not cons["page"]["starved"] and not cons["brief"]["starved"], cons["brief"]
assert {c["fact"] for c in rep["conflicts"]} == set(flow.registry.DUAL_CANONICAL), rep["conflicts"]
rule = cons["brief"]["rule"]
assert "agent-skills.db" in rule and "closures" in rule and "REPORT" in rule, rule
assert "subagents.jsonl" in rule and "board.subagent_stale_h" in rule, rule
# Every file the rule names is a store the brief reads.
brief_in = {s.path.rsplit("/", 1)[-1] for s in flow.registry.STORES if "brief" in s.readers}
assert {"state.json", "prs.json", "agent-skills.db", "subagents.jsonl"} <= brief_in, brief_in

# An unreadable directory under a glob is an error, not a store never written.
(tmp / "state" / "roles").chmod(0)
try:
    roles = {s["id"]: s for s in flow.flow()["stores"]}["roles"]
finally:
    (tmp / "state" / "roles").chmod(0o755)
assert roles["state"] == "error" and "roles" in roles["error"], roles
(tmp / "programs" / "a").chmod(0)  # a glob ending in a fixed name (`*/ledger.jsonl`)
try:
    led = {s["id"]: s for s in flow.flow()["stores"]}["progledger"]
finally:
    (tmp / "programs" / "a").chmod(0o755)
assert led["state"] == "error", led
# A file its reader cannot open is an error too, one file or under a glob, and starves a consumer with only it.
(d / "state.json").chmod(0)
(tmp / "state" / "roles" / "s1.json").chmod(0)
try:
    got = {s["id"]: s for s in flow.flow()["stores"]}
    alone = {c["id"]: c for c in flow.flow(stores=[flow.registry.Store(
        "p", "D/state.json", {"f.p": "canonical"}, (), ("janitor",))])["consumers"]}
finally:
    (d / "state.json").chmod(0o644)
    (tmp / "state" / "roles" / "s1.json").chmod(0o644)
assert got["state"]["state"] == "error" and got["state"]["error"] == "not readable", got["state"]
assert got["roles"]["state"] == "error" and "not readable" in got["roles"]["error"], got["roles"]
assert alone["janitor"]["starved"] and alone["janitor"]["state"] == "error", alone

# The programs home sharing the shared-state directory: the shared stores' directories are still walked.
os.environ["PROGRAMS_HOME"] = str(tmp / "state")
(tmp / "state" / "roles" / "stray.txt").write_text("")
shared_unread, _ = flow.unregistered(flow.registry.STORES)
assert any(p.endswith("roles/stray.txt") for p in shared_unread), shared_unread
(tmp / "state" / "roles" / "stray.txt").unlink()
os.environ["PROGRAMS_HOME"] = str(tmp / "programs")

# The config key turns a feature on: its store is then not yet written, not off.
(tmp / "home" / ".claude").mkdir(parents=True)
(tmp / "home" / ".claude" / "agent-skills.json").write_text(json.dumps({"prs": {}}))
assert {s["id"]: s for s in flow.flow()["stores"]}["prsstate"]["state"] == "not yet"
(tmp / "home" / ".claude" / "agent-skills.json").unlink()

# A consumer is starved for the worst state among its inputs: error, then not yet, then off.
S = flow.registry.Store
mini = [S("e", "D/tidy.jsonl", {"f.e": "canonical"}, (), ("tidy",)), S("n", "D/nope.json", {"f.n": "canonical"}, (), ("tidy", "sync")),
        S("o", "S/off/*.json", {"f.o": "canonical"}, (), ("sync", "janitor"), feature="nothing.here"),
        S("p", "D/state.json", {"f.p": "canonical"}, (), ("janitor",))]
cons = {c["id"]: c["state"] for c in flow.flow(stores=mini)["consumers"]}
assert cons == {"tidy": "error", "sync": "not yet", "janitor": "present"}, cons

# A grown log is counted from where the last count ended; a shrunk or replaced one from the start.
with open(d / "events.jsonl", "a") as f:
    f.write('{"ev":"third"}\n')
st = (d / "events.jsonl").stat()
assert flow.line_count(d / "events.jsonl", st) == 3
(d / "events.jsonl").write_text('{"ev":"new"}\n')
assert flow.line_count(d / "events.jsonl", (d / "events.jsonl").stat()) == 1

# Skill changes: a throwaway repo with one merged, one direct and one lesson commit.
repo = tmp / "repo"
(repo / "skills" / "brief").mkdir(parents=True)
(repo / "skills" / "orchestrate").mkdir(parents=True)
g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
g("init", "-q")
g("config", "user.email", "a@example.test"); g("config", "user.name", "a"); g("config", "commit.gpgsign", "false")
g("remote", "add", "origin", "https://github.com/acme/skills.git")
for i, (files, subject) in enumerate([(["skills/brief/a"], "feat(brief): first (#3)"),
                                      (["skills/orchestrate/b", "skills/brief/c"], "fix(orchestrate): second"),
                                      (["skills/brief/d"], "reflect: L-12 applied to brief")]):
    for f in files:
        (repo / f).write_text(str(i))
    g("add", "-A"); g("commit", "-q", "-m", subject)
flow.REPO = repo
(d / "prs.json").write_text(json.dumps({"prs": {
    "acme/skills#4": {"detail": {"repo": "acme/skills", "state": "OPEN", "title": "feat(reflect): open one", "number": 4,
                                 "url": "https://github.com/acme/skills/pull/4", "updated": "2099-01-01T00:00:00Z"}},
    "acme/other#5": {"detail": {"repo": "acme/other", "state": "OPEN", "title": "feat(brief): elsewhere", "number": 5}}}}))
(tmp / "programs" / "_skill-usage" / "ledger.jsonl").write_text(
    json.dumps({"ts": "2099-01-02T00:00:00Z", "ev": "signal", "skill": "agent-skills:swarm", "flag": "misses",
                "suggest": "add the phrase", "evidence": "skill-usage:swarm:misses:W1"}) + "\n"
    + json.dumps({"ts": "2000-01-01T00:00:00Z", "ev": "signal", "skill": "agent-skills:old", "flag": "misses"}) + "\n")
ch = flow.changes()
assert ch["repo"] == "acme/skills", ch["repo"]
st = {(r["skills"][0], r["status"]) for r in ch["rows"]}
assert st == {("brief", "merged"), ("orchestrate", "applied"), ("brief", "applied"), ("reflect", "in PR"), ("swarm", "proposed")}, st
second = next(r for r in ch["rows"] if r["skills"] == ["orchestrate"])
assert second["also"] == ["brief"], second
assert next(r for r in ch["rows"] if r["status"] == "merged")["url"] == "https://github.com/acme/skills/pull/3"
assert any(r["lessons"] == ["L-12"] for r in ch["rows"])
assert ch["rows"][0]["status"] == "proposed"  # newest first
per = {p["skill"]: p for p in ch["skills"]}
assert per["brief"]["merged"] == 1 and per["brief"]["applied"] == 1, per["brief"]
assert ch["lessons"] == {"configured": False, "note": "reflect.ledger is not set in ~/.claude/agent-skills.json"}

home = tmp / "home" / ".claude"
home.mkdir(parents=True, exist_ok=True)
(tmp / "lessons.md").write_text("L-1\nL-2\n")
(home / "agent-skills.json").write_text(json.dumps({"reflect": {"ledger": str(tmp / "lessons.md")}}))
assert flow.lessons_ledger()["lines"] == 2
(home / "agent-skills.json").write_text(json.dumps({"reflect": {"ledger": "vault:Notes/lessons.md"}}))
assert flow.lessons_ledger()["readable"] is False

print("test_flow: ok")
