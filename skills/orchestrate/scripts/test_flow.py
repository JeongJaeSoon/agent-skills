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

rep = flow.flow([{"report": {"path": str(report)}}, {"report": None}, {}])
by = {s["id"]: s for s in rep["stores"]}
assert by["state"]["stale"] and by["state"]["exists"], by["state"]
assert by["events"]["lines"] == 2 and by["events"]["last"] == "pr_merged" and not by["events"]["stale"], by["events"]
assert not by["prs"]["exists"] and by["prs"]["name"] == "prs.json"
assert by["ledgers"]["entries"] == 1, by["ledgers"]  # the */ledger.jsonl glob
assert by["reports"]["entries"] == 1 and by["reports"]["files"][0]["age"] is not None
assert sorted(pathlib.Path(p).name for p in rep["unread"]) == ["janitor.json", "leftover.json"], rep["unread"]
starved = {c["id"] for c in rep["consumers"] if c["starved"]}
assert "reorient" in starved and "page" not in starved and "brief" not in starved, starved
assert "report file" in next(c for c in rep["consumers"] if c["id"] == "brief")["rule"]

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
home.mkdir(parents=True)
(tmp / "lessons.md").write_text("L-1\nL-2\n")
(home / "agent-skills.json").write_text(json.dumps({"reflect": {"ledger": str(tmp / "lessons.md")}}))
assert flow.lessons_ledger()["lines"] == 2
(home / "agent-skills.json").write_text(json.dumps({"reflect": {"ledger": "vault:Notes/lessons.md"}}))
assert flow.lessons_ledger()["readable"] is False

print("test_flow: ok")
