"""Offline tests for skill_usage.py: fixture transcripts and an invented skill inventory, no real transcript read.

Run: python3 test_skill_usage.py
"""
import datetime as dt, json, os, pathlib, sys, tempfile, time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import skill_usage as su

root = pathlib.Path(tempfile.mkdtemp(prefix="test-skill-usage-"))
NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc)


def ts(days_ago, minute=0):
    return (NOW - dt.timedelta(days=days_ago) + dt.timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")


def skill_md(d, name, desc):
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {json.dumps(desc)}\n---\n\n# {name}\n")


repo = root / "repo" / "skills"
skill_md(repo / "ship", "ship", 'Ship a change. Use for "ship it", "배포해줘", or "go".')
skill_md(repo / "tidy", "tidy", 'Tidy things. Use for "정리해줘" or "clean up the mess".')
skill_md(repo / "quiet", "quiet", 'Never asked for. Not bare "그냥 둬" or "leave it alone": those mean something else.')
skill_md(repo / "alias", "alias", "Short slash command for ship.")
(repo / "alias" / "SKILL.md").write_text((repo / "alias" / "SKILL.md").read_text().replace("---\n\n", "disable-model-invocation: true\n---\n\n"))
cached = root / "cache" / "acme-tools" / "1.0.0"
skill_md(cached / "skills" / "lint", "lint", 'Lint. Use for "lint this".')
skill_md(root / "cache" / "stale-copy" / "skills" / "ship", "ship", "An older copy of this plugin.")
(root / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
    "acme-tools@example-market": [{"scope": "user", "installPath": str(cached)}],
    "agent-skills@example-market": [{"scope": "user", "installPath": str(root / "cache" / "stale-copy")}]}}))
skill_md(root / "user-skills" / "notes", "notes", 'Notes. Use for "노트에 적어" or "정본".')

inv = su.inventory(plugins_json=root / "installed_plugins.json", user_dir=root / "user-skills", repo_dir=repo)
assert set(inv) == {"agent-skills:ship", "agent-skills:tidy", "agent-skills:quiet", "agent-skills:alias", "acme-tools:lint", "notes"}, inv
assert inv["agent-skills:alias"]["slash_by_design"] and not inv["agent-skills:ship"]["slash_by_design"], inv
# Claude Code 2.1.295 reads the first five as true and the last two as false.
dmi = root / "dmi-skills"
for name, val in [("comment", "true  # slash only"), ("yes", "yes"), ("quoted", '"true"'), ("upper", "TRUE"), ("one", "1"),
                  ("glued", "true#x"), ("off", "false  # on purpose")]:
    skill_md(dmi / name, name, "Probe.")
    (dmi / name / "SKILL.md").write_text((dmi / name / "SKILL.md").read_text().replace("---\n\n", f"disable-model-invocation: {val}\n---\n\n"))
dmi_inv = su.inventory(plugins_json=root / "none.json", user_dir=root / "none", repo_dir=dmi)
assert {k.split(":")[1] for k, v in dmi_inv.items() if v["slash_by_design"]} == {"comment", "yes", "quoted", "upper", "one"}, dmi_inv
assert inv["agent-skills:ship"]["source"] == "agent-skills" and inv["acme-tools:lint"]["source"] == "acme-tools", inv
assert inv["notes"]["source"] == "user", inv
assert "배포해줘" in inv["agent-skills:ship"]["phrases"], inv["agent-skills:ship"]
assert set(inv["agent-skills:ship"]["phrases"]) == {"ship it", "배포해줘"}, inv["agent-skills:ship"]["phrases"]
assert inv["notes"]["phrases"] == ["노트에 적어"], inv["notes"]
assert inv["agent-skills:quiet"]["phrases"] == [], inv["agent-skills:quiet"]


def human(t, text, session="s1", cwd="/work/alpha"):
    return {"type": "user", "timestamp": t, "sessionId": session, "cwd": cwd, "message": {"role": "user", "content": text}}


def meta(t, text, session="s1"):
    return {"type": "user", "isMeta": True, "timestamp": t, "sessionId": session, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def tool_result(t, session="s1"):
    return {"type": "user", "timestamp": t, "sessionId": session, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


def skill_call(t, name, attribution=None, session="s1", cwd="/work/alpha"):
    row = {"type": "assistant", "timestamp": t, "sessionId": session, "cwd": cwd,
           "message": {"role": "assistant", "content": [{"type": "text", "text": "..."},
                                                         {"type": "tool_use", "id": "t", "name": "Skill", "input": {"skill": name}}]}}
    if attribution:
        row["attributionSkill"] = attribution
    return row


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows) + "not json\n")  # compact, as Claude Code writes


projects = root / "projects"
write(projects / "-work-alpha" / "s1.jsonl", [
    human(ts(1), "please ship it now"),
    skill_call(ts(1, 1), "agent-skills:ship"),
    tool_result(ts(1, 2)),
    meta(ts(1, 2), "Base directory for this skill: /x"),
    skill_call(ts(1, 3), "agent-skills:tidy", attribution="agent-skills:ship"),
    human(ts(1, 10), "배포해줘"),
    tool_result(ts(1, 11)),
    human(ts(1, 20), "<command-message>tidy</command-message>\n<command-name>/agent-skills:tidy</command-name>\n<command-args>정리해줘</command-args>"),
    human(ts(1, 21), "<command-name>/mcp</command-name>"),
    human(ts(1, 30), "lint this please"),
    skill_call(ts(1, 31), "lint", attribution="agent-skills:tidy"),
    skill_call(ts(1, 32), "simplify"),
    human(ts(1, 33), "<task-notification>ship it</task-notification>"),
])
# A compaction summary quotes old prompts and an interruption marker is not a prompt: neither opens a turn or
# counts a miss, so tidy's chained call after them still belongs to the turn that loaded ship.
summary = human(ts(3, 1), "Summary: the user asked to ship it and said 정리해줘", session="s4")
summary["isCompactSummary"] = True
write(projects / "-work-alpha" / "s4.jsonl", [
    human(ts(3), "please ship it", session="s4"),
    skill_call(ts(3, 1), "agent-skills:ship", session="s4"),
    summary,
    human(ts(3, 2), "[Request interrupted by user]", session="s4"),
    skill_call(ts(3, 3), "agent-skills:tidy", attribution="agent-skills:ship", session="s4"),
    # A phrase inside a longer word ("relationship it") is not the phrase; a pasted prompt is a human prompt.
    human(ts(3, 10), "the relationship it has", session="s4"),
    human(ts(3, 20), "<pasted_content id=x>quoted: ship it, 정리해줘</pasted_content> 배포해줘", session="s4"),
    human(ts(3, 25), "lint this를 해줘", session="s4"),
    human(ts(3, 30), "done", session="s4"),
])
write(projects / "-work-beta" / "s2.jsonl", [
    human(ts(20), "<command-name>/agent-skills:ship</command-name>", session="s2", cwd="/work/beta"),
    human(ts(20, 5), "정리해줘", session="s2", cwd="/work/beta"),
    human(ts(20, 6), "정리해줘 again", session="s2", cwd="/work/beta"),
    human(ts(20, 7), "done", session="s2", cwd="/work/beta"),
])
write(projects / "-work-alpha" / "s1" / "subagents" / "agent-1.jsonl", [
    human(ts(2), "ship it", session="s1"),
    skill_call(ts(2, 1), "agent-skills:tidy", session="s1"),
])
write(projects / "-work-alpha" / "old.jsonl", [human(ts(40), "x", session="s0"), skill_call(ts(40, 1), "notes", session="s0")])

cache = root / "state" / "skill-usage-cache.json"
rep = su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW, miss_threshold=1)
rows = {r["name"]: r for r in rep["skills"]}
ship, tidy, lint = rows["agent-skills:ship"], rows["agent-skills:tidy"], rows["acme-tools:lint"]

assert (ship["uses_7d"], ship["uses_30d"], ship["uses_total"]) == (2, 3, 3), ship
assert (ship["auto_30d"], ship["slash_30d"], ship["chained_30d"]) == (2, 1, 0), ship
assert ship["misses_30d"] == 2 and ship["sessions_30d"] == 3 and ship["repos_30d"] == 2, ship
assert (tidy["auto_30d"], tidy["slash_30d"], tidy["chained_30d"]) == (1, 1, 2), tidy
assert tidy["misses_30d"] == 2, tidy
assert (lint["auto_30d"], lint["uses_30d"]) == (1, 1) and lint["misses_30d"] == 1, lint
assert rows["simplify"]["source"] == "other" and rows["simplify"]["uses_30d"] == 1, rows["simplify"]
assert "mcp" not in rows and "agent-skills:mcp" not in rows, rows.keys()
assert rows["notes"]["uses_30d"] == 0 and rows["notes"]["uses_total"] == 1 and rows["notes"]["last_used"] == ts(40, 1), rows["notes"]
assert ship["last_used"] == ts(1, 1), ship

assert rows["agent-skills:quiet"]["flags"] == ["unused_30d"], rows["agent-skills:quiet"]
assert rows["notes"]["flags"] == ["unused_30d"], rows["notes"]
assert "misses" in tidy["flags"] and "misses" in ship["flags"], (tidy, ship)
assert su.flags({**ship, "misses_30d": 1}, 1) == [], "a miss count equal to the threshold is not flagged"
assert "slash_only" not in ship["flags"], ship
assert rows["simplify"]["flags"] == [], rows["simplify"]
assert [s["source"] for s in rep["sources"]] == ["agent-skills", "acme-tools", "user", "other"], rep["sources"]

blob = json.dumps(rep, ensure_ascii=False) + cache.read_text()
for secret in ("ship it now", "배포해줘\"", "lint this please", "/work/alpha", "/work/beta", "again"):
    assert secret not in blob, secret

rep2 = su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW, miss_threshold=1)
assert rep2["files_parsed"] == 0 and rep2["files"] == rep["files"] == 5, (rep["files"], rep2["files_parsed"])
assert {r["name"]: r for r in rep2["skills"]} == rows
with (projects / "-work-alpha" / "old.jsonl").open("a") as f:
    f.write(json.dumps(skill_call(ts(3), "agent-skills:quiet", session="s0"), separators=(",", ":")) + "\n")
rep3 = su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW, miss_threshold=1)
assert rep3["files_parsed"] == 1, rep3["files_parsed"]
q = {r["name"]: r for r in rep3["skills"]}["agent-skills:quiet"]
assert q["uses_30d"] == 1 and q["flags"] == [], q
write(projects / "-work-alpha" / "s3.jsonl", [human(ts(0), "<command-name>/notes</command-name>", session="s3"),
                                             human(ts(0, 1), "<command-name>/agent-skills:alias</command-name>", session="s3")])
rep4 = su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW, miss_threshold=1)
n = {r["name"]: r for r in rep4["skills"]}["notes"]
assert n["slash_30d"] == 1 and n["flags"] == ["slash_only"], n
# A skill the model cannot invoke is slash-only by design: no flag, so no weekly rewrite-description signal.
a = {r["name"]: r for r in rep4["skills"]}["agent-skills:alias"]
assert a["slash_30d"] == 1 and a["flags"] == [], a

ledger = root / "store" / "_skill-usage" / "ledger.jsonl"
added = su.write_signals(ledger, rep4, NOW)
got = {(r["skill"], r["flag"]) for r in added}
assert got == {("notes", "slash_only"), ("agent-skills:tidy", "misses"), ("agent-skills:ship", "misses")}, got
r0 = next(r for r in added if r["skill"] == "notes")
assert r0["ev"] == "signal" and r0["kind"] == "skill_usage" and r0["evidence"] == "skill-usage:notes:slash_only@2026-W39", r0
assert r0["source"] == "user" and r0["suggest"] == ["rewrite-description"], r0
assert r0["note"].startswith("slash_only; suggest: rewrite-description; "), r0
assert su.write_signals(ledger, rep4, NOW) == [], "same week: nothing new"
assert len(ledger.read_text().splitlines()) == 3
later = su.write_signals(ledger, rep4, NOW + dt.timedelta(days=7))
assert len(later) == 3 and all(r["evidence"].endswith("@2026-W40") for r in later), later
assert su.signal_rows({"skills": [{**rows["agent-skills:quiet"]}]}, NOW) == [], "unused_30d alone is no signal"

# A retirement candidate needs every criterion; zero uses alone, or any criterion missing, is not one.
base = {"name": "agent-skills:old", "source": "agent-skills", "uses_7d": 0, "uses_30d": 0, "uses_total": 0, "auto_30d": 0,
        "slash_30d": 0, "chained_30d": 0, "last_used": None, "misses_30d": 0, "first_seen": ts(120),
        "last_used_ever": ts(100), "overlaps": ["agent-skills:new"], "rare": False}
assert su.flags(base, now=NOW) == ["unused_30d", "retire_candidate"], su.flags(base, now=NOW)
for why, change in [("one window only", {"last_used_ever": ts(40)}), ("recently installed", {"first_seen": ts(20), "last_used_ever": None}),
                    ("no miss and no overlap", {"overlaps": []}), ("rare by design", {"rare": True})]:
    assert su.flags({**base, **change}, now=NOW) == ["unused_30d"], why
assert su.flags({**base, "overlaps": [], "misses_30d": 1}, now=NOW) == ["unused_30d", "retire_candidate"]
cand = su.signal_rows({"skills": [{**base, "flags": su.flags(base, now=NOW)}]}, NOW)
assert [(r["flag"], r["suggest"]) for r in cand] == [("retire_candidate", ["merge", "retire"])], cand
assert "overlaps agent-skills:new" in cand[0]["note"] and f"unused since {ts(100)}" in cand[0]["note"], cand[0]["note"]

# A scan logic change (SCAN_VERSION) re-reads every cached transcript.
su.SCAN_VERSION += 1
rep5 = su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW, miss_threshold=1)
assert rep5["files_parsed"] == rep5["files"], (rep5["files_parsed"], rep5["files"])
# An unreadable transcript is skipped, not fatal.
real_scan = su.scan_file


def failing_scan(path, *a):
    if path.name == "s3.jsonl":
        raise OSError("gone")
    return real_scan(path, *a)


su.scan_file = failing_scan
su.SCAN_VERSION += 1
rep6 = su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW, miss_threshold=1)
su.scan_file = real_scan
assert rep6["files"] == rep5["files"] - 1, (rep6["files"], rep5["files"])
# Rows whose message is not an object are skipped, not fatal.
odd = root / "odd.jsonl"
write(odd, [{"type": "user", "message": "x"}, {"type": "assistant", "message": "Skill"}, {"type": "assistant", "message": {"content": "Skill"}}])
assert su.scan_file(odd, inv, su.resolver(inv)) == {"events": [], "misses": []}
# A ledger line that is JSON but not an object does not stop the idempotency check.
with ledger.open("a") as f:
    f.write("[]\n")
assert su.write_signals(ledger, rep4, NOW) == []
# refresh() keeps the signals path and the store location out of skills.json.
os.environ["CLAUDE_PROJECTS_DIR"], os.environ["ORCH_FLEET_STATE"] = str(projects), str(root / "state2")
real_inventory, su.inventory = su.inventory, lambda: inv
rep7 = su.refresh(out=root / "state2" / "skills.json", signals=ledger)
su.inventory = real_inventory
assert str(root) not in (root / "state2" / "skills.json").read_text() and set(rep7["signals"]) == {"added"}, rep7["signals"]

# The collector remembers first sight and the last use across collects, so a transcript Claude Code deleted
# does not make a skill look unused for longer than it is.
h = json.loads(cache.read_text())["seen"]
assert h["notes"]["last_used"] == ts(0) and h["agent-skills:quiet"]["first_seen"] == su.iso(NOW), h
(projects / "-work-alpha" / "s3.jsonl").unlink()
later_rows = {r["name"]: r for r in su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW + dt.timedelta(days=95))["skills"]}
assert later_rows["notes"]["last_used_ever"] == ts(0) and later_rows["notes"]["last_used"] == ts(40, 1), later_rows["notes"]
# quiet: unused for 98 days, but it has no quoted trigger and no overlap: zero uses alone keeps it off the list.
assert later_rows["agent-skills:quiet"]["flags"] == ["unused_30d"], later_rows["agent-skills:quiet"]
assert later_rows["notes"]["flags"] == ["unused_30d"], later_rows["notes"]
# A user skill that shadows a plugin skill of the same name does not count as its overlap.
twin_inv = {**inv, "ship": {"name": "ship", "source": "user", "phrases": ["ship it"]},
            "agent-skills:ship2": {"name": "agent-skills:ship2", "source": "agent-skills", "phrases": ["ship it"]}}
twins = {r["name"]: r for r in su.collect(projects_dir=projects, inv=twin_inv, cache_path=root / "twin.json", now=NOW)["skills"]}
assert twins["agent-skills:ship"]["overlaps"] == ["agent-skills:ship2"], twins["agent-skills:ship"]["overlaps"]
cfg = root / "rare.json"
cfg.write_text(json.dumps({"rare": ["notes"]}))
assert su.rare_config(cfg) == {"notes"} and su.rare_config(root / "missing.json") == set()

# precheck: wake on a row after the cursor, skip once the cursor has moved past it or while a round holds the lock.
home = root / "programs"
write(home / "acme" / "ledger.jsonl", [{"ts": "2026-09-20T00:00:00+00:00", "ev": "signal", "kind": "stall"},
                                       {"ts": "2026-09-21T00:00:00+00:00", "ev": "verdict", "result": "pass"}])
assert su.new_findings(home) == {}, "a ledger the cursor does not list starts at its newest row"
with (home / "acme" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-22T00:00:00+00:00", "ev": "verdict", "result": "fail"}) + "\n")
assert [r["ev"] for r in su.new_findings(home)["acme"]] == ["verdict"]
assert su.precheck(home, collect_first=False) == 0
assert su.precheck(home, collect_first=False) == 0, "the precheck keeps no state: only the round moves the cursor"
(home / "_standing" / "reflect").mkdir(parents=True)
(home / "_standing" / "reflect" / "cursor.json").write_text(json.dumps({"acme": "2026-09-22T00:00:00+00:00"}))
assert su.precheck(home, collect_first=False) == 1
with (home / "acme" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-23T00:00:00Z", "ev": "land_failed", "pr": 7}) + "\n")
    f.write(json.dumps({"ts": "2026-09-23T01:00:00Z", "ev": "landed", "pr": 8}) + "\n")
assert [r["ev"] for r in su.new_findings(home)["acme"]] == ["land_failed"]
# A cursor written without an offset reads as UTC; a ledger check that still fails wakes the agent rather than
# skipping every run for good.
(home / "_standing" / "reflect" / "cursor.json").write_text(json.dumps({"acme": "2026-09-23T00:30:00"}))
assert su.new_findings(home) == {}, su.new_findings(home)
real_new, su.new_findings = su.new_findings, lambda home: 1 / 0
assert su.precheck(home, collect_first=False) == 0
su.new_findings = real_new
(home / "_standing" / "reflect" / "lock").mkdir()
assert su.precheck(home, collect_first=False) == 1, "a round in progress"
assert not su.lock_held(home, now=time.time() + 5 * 3600), "a lock older than 4 hours is stale"

# Waiting lessons sit in the notes store, which the precheck cannot read: a round that leaves them behind an open
# standing PR writes waiting.json, and the precheck wakes once that PR is no longer open (or its state is unreadable).
(home / "_standing" / "reflect" / "lock").rmdir()
assert su.precheck(home, collect_first=False) == 1
waiting = home / "_standing" / "reflect" / "waiting.json"
waiting.write_text(json.dumps({"pr": "https://github.com/acme/skills/pull/21"}))
for state, code in (("OPEN", 1), ("MERGED", 0), ("CLOSED", 0), (None, 0)):
    assert su.precheck(home, collect_first=False, pr_state=lambda url: state) == code, state
path = os.environ["PATH"]
os.environ["PATH"] = ""  # gh missing from the automation's PATH reads as unreadable, not a crash
try:
    assert su.gh_pr_state("https://github.com/acme/skills/pull/21") is None
    assert su.precheck(home, collect_first=False) == 0
finally:
    os.environ["PATH"] = path
waiting.unlink()

# schedule only prints; the command runs the precheck by absolute path in an existing workspace.
cmd = su.schedule_cmd(pathlib.Path("/repo/skills/orchestrate/scripts/skill_usage.py"), "path:/repo")
assert cmd[:3] == ["orca", "automations", "create"], cmd
assert cmd[cmd.index("--workspace-mode") + 1] == "existing" and cmd[cmd.index("--workspace") + 1] == "path:/repo", cmd
assert cmd[cmd.index("--precheck") + 1] == "python3 /repo/skills/orchestrate/scripts/skill_usage.py precheck", cmd
prompt = cmd[cmd.index("--prompt") + 1]
assert "standing" in prompt and "draft PR" in prompt and "머지하지 않" in prompt, prompt

print("test_skill_usage: ok")
