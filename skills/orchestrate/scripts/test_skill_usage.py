"""Offline tests for skill_usage.py: fixture transcripts and an invented skill inventory, no real transcript read.

Run: python3 test_skill_usage.py
"""
import contextlib, datetime as dt, io, json, os, pathlib, sys, tempfile, time

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


def tool_call(t, name, inp, session="s1"):
    return {"type": "assistant", "timestamp": t, "sessionId": session, "cwd": "/work/alpha",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": name, "input": inp}]}}


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
    # The moments how and why could have run: Explore called directly and history dug by hand.
    tool_call(ts(1, 34), "Agent", {"subagent_type": "Explore", "prompt": "map the runtime"}),
    tool_call(ts(1, 35), "Agent", {"subagent_type": "general-purpose", "prompt": "x"}),
    tool_call(ts(1, 36), "Bash", {"command": 'git log -p -S"pickaxe-term" -- a.py'}),
    tool_call(ts(1, 37), "Bash", {"command": "git -C ../repo blame a.py | head"}),
    tool_call(ts(1, 38), "Bash", {"command": "git log -L10,20:a.py"}),
    tool_call(ts(1, 39), "Bash", {"command": "git log --since=1.week --oneline && git status -s"}),
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
    skill_call(ts(3, 31), "agent-skills:architect", session="s4"),
    skill_call(ts(3, 32), "agent-skills:arena", session="s4"),
])
write(projects / "-work-beta" / "s2.jsonl", [
    human(ts(20), "<command-name>/agent-skills:ship</command-name>", session="s2", cwd="/work/beta"),
    human(ts(20, 5), "정리해줘", session="s2", cwd="/work/beta"),
    human(ts(20, 6), "정리해줘 again", session="s2", cwd="/work/beta"),
    human(ts(20, 7), "done", session="s2", cwd="/work/beta"),
    skill_call(ts(20, 8), "agent-skills:architect", session="s2", cwd="/work/beta"),
    tool_call(ts(20, 9), "Task", {"subagent_type": "Explore", "prompt": "x"}, session="s2"),
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
# Only git run as a command counts: global options and quoting are read, a mention inside a string is not a run,
# a path after "--" is not an option, and a skill of the same name from another plugin is not this one.
for cmd, n in [("git blame a.py", 1), ("git --no-pager blame a.py", 1), ("git --no-pager log -Sneedle -- a.py", 1),
               ('git -C "../sample repo" blame a.py', 1), ("printf '%s\\n' 'git blame a.py'", 0), ("git log -- -Sample.py", 0),
               ("git log -Sneedle -- a.py; git blame a.py", 2), ("cd x && GIT_PAGER=cat git log -G'x|y' | head", 1),
               ("git log --since=1.week --stat", 0), ("echo git log -Sx", 0), ("git log -S'unbalanced", 0),
               ('printf "%s" ";" "git" "blame"', 0), ('git -C ";" blame a.py', 1), ("# inspect history\ngit blame a.py", 1),
               ("git \\\nblame a.py", 1), ("git log --grep -Something", 0), ("git log --grep x -S y", 1),
               ("git blame a.py # and git log -Sx", 1), ("git log -S y\ngit blame b", 2),
               ("git log --pretty -Sneedle -- a.py", 1), ("git log --format=%h -G x", 1)]:
    assert su.git_history_cmds(cmd) == n, (cmd, su.git_history_cmds(cmd))
foreign = [[ts(1), "other-plugin:architect", "auto", "s9", "h"], [ts(1), "agent-skills:arena", "auto", "s9", "h"]]
assert su.denominators(foreign, [], NOW)["7d"]["architect_sessions"] == 0, su.denominators(foreign, [], NOW)
renamed = [[ts(1), "agent-skills:architect", "auto", "s9", "h"], [ts(1), "agent-skills:fan-out", "auto", "s9", "h"]]
assert su.denominators(renamed, [], NOW)["7d"]["architect_sessions_with_arena"] == 1, su.denominators(renamed, [], NOW)
# A subagent prompt carrying a trigger counts apart from human misses and raises no flag of its own.
assert ship["subagent_misses_30d"] == 1 and tidy["subagent_misses_30d"] == 0, (ship, tidy)
assert su.flags({**ship, "misses_30d": 0, "subagent_misses_30d": 9}, 1) == [], "subagent misses alone are not flagged"
assert rep["denominators"] == {
    "7d": {"explore_calls": 1, "git_history_cmds": 3, "architect_sessions": 1, "architect_sessions_with_arena": 1},
    "30d": {"explore_calls": 2, "git_history_cmds": 3, "architect_sessions": 2, "architect_sessions_with_arena": 1}}, rep["denominators"]

assert rows["agent-skills:quiet"]["flags"] == ["unused_30d"], rows["agent-skills:quiet"]
assert rows["notes"]["flags"] == ["unused_30d"], rows["notes"]
assert "misses" in tidy["flags"] and "misses" in ship["flags"], (tidy, ship)
assert su.flags({**ship, "misses_30d": 1}, 1) == [], "a miss count equal to the threshold is not flagged"
assert "slash_only" not in ship["flags"], ship
assert rows["simplify"]["flags"] == [], rows["simplify"]
assert [s["source"] for s in rep["sources"]] == ["agent-skills", "acme-tools", "user", "other"], rep["sources"]

# A miss keeps the description's phrase it matched (public text) and the session to resume, never the prompt.
assert tidy["miss_phrases"] == [["정리해줘", 2]] and tidy["miss_sessions_30d"] == 1 and tidy["last_miss"] == ts(20, 6), tidy
blob = json.dumps(rep, ensure_ascii=False) + cache.read_text()
for secret in ("ship it now", "quoted:", "pickaxe-term", "map the runtime", "lint this please", "/work/alpha", "/work/beta", "again"):
    assert secret not in blob, secret
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert su.list_misses("agent-skills:tidy", cache, NOW) == 0
assert out.getvalue().splitlines() == [f'{ts(20, 6)}  s2  "정리해줘"', f'{ts(20, 5)}  s2  "정리해줘"',
                                       "2 misses of agent-skills:tidy in 30 days (claude --resume <session> opens one)"], out.getvalue()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    su.list_misses("agent-skills:ship", cache, NOW)
assert f'{ts(2)}  s1  "ship it"  (subagent)' in out.getvalue().splitlines(), out.getvalue()
# A skill that fires on most prompts carrying its phrases is not flagged for the few it missed.
assert su.flags({**ship, "misses_30d": 4, "auto_30d": 40}, 1) == [] and su.flags({**ship, "misses_30d": 4, "auto_30d": 10}, 1) == ["misses"]

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
assert su.scan_file(odd, inv, su.resolver(inv)) == {"events": [], "misses": [], "sub_misses": [], "probes": []}
# A ledger line that is JSON but not an object does not stop the idempotency check.
with ledger.open("a") as f:
    f.write("[]\n")
assert su.write_signals(ledger, rep4, NOW) == []
# refresh() keeps the signals path and the store location out of skills.json.
os.environ["CLAUDE_PROJECTS_DIR"], os.environ["ORCH_FLEET_STATE"] = str(projects), str(root / "state2")
real_inventory, su.inventory = su.inventory, lambda: inv
real_collect = su.collect
# The clock is pinned: refresh() collects as of NOW (rep4's flagged skills), or as of an ISO year boundary.
su.collect = lambda miss_threshold=su.MISS_THRESHOLD: json.loads(json.dumps(rep4))
fresh = root / "refresh-ledger.jsonl"
rep7 = su.refresh(out=root / "state2" / "skills.json", signals=fresh)
rows7 = fresh.read_text().splitlines()
assert rep7["signals"]["added"] == len(rows7) == 3, (rep7["signals"], rows7)  # the 3 flagged skills of rep4
rep7b = su.refresh(out=root / "state2" / "skills.json", signals=fresh)
assert rep7b["signals"]["added"] == 0 and fresh.read_text().splitlines() == rows7, "same week: nothing new"
saved7 = json.loads((root / "state2" / "skills.json").read_text())
assert str(root) not in (root / "state2" / "skills.json").read_text()
# skills.json carries no count of its own: `signals` is read back from the ledger (the source), so a second record
# of "how many" cannot drift from the rows. Only the returned report says what this collect added.
assert saved7["signals"] == {"week": 3, "rows": 3} and "added" not in saved7["signals"], saved7["signals"]
assert rep7["signals"] == {"week": 3, "rows": 3, "added": 3}, rep7["signals"]
# 2027-01-01 belongs to ISO week 2026-W53: the week count uses the ISO year, not the calendar year.
newyear = root / "newyear-ledger.jsonl"
su.collect = lambda miss_threshold=su.MISS_THRESHOLD: {**json.loads(json.dumps(rep4)), "generated_at": "2027-01-01T12:00:00Z"}
rep8 = su.refresh(out=root / "state2" / "skills.json", signals=newyear)
assert rep8["signals"] == {"week": 3, "rows": 3, "added": 3}, rep8["signals"]
assert all(json.loads(l)["evidence"].endswith("@2026-W53") for l in newyear.read_text().splitlines())
su.collect, su.inventory = real_collect, real_inventory
# derive_signals() is the whole derivation: rows of other kinds and unreadable lines do not count.
junk = root / "junk.jsonl"
junk.write_text('{"ev":"signal","kind":"skill_usage","evidence":"skill-usage:a:misses@2026-W39"}\n'
                '{"ev":"signal","kind":"skill_usage","evidence":"skill-usage:a:misses@2026-W38"}\n'
                '{"ev":"signal","kind":"lesson","evidence":"x@2026-W39"}\nnot json\n[]\n')
assert su.derive_signals(junk, "2026-W39") == {"week": 1, "rows": 2}
assert su.derive_signals(root / "absent.jsonl", "2026-W39") == {"week": 0, "rows": 0}

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
# The orchestrator-feedback check reads its own config and registry: point both at nothing, so it is off here.
os.environ["AGENT_SKILLS_CONFIG"], os.environ["AGENT_SKILLS_STATE"] = str(root / "no-config.json"), str(root / "as")
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

# Orchestrator feedback (reflect.orchestrator_feedback): off, the precheck is as above; on, a registered orchestrator
# transcript wakes it with no ledger finding, from file metadata only.
assert su.precheck(home, collect_first=False) == 1
transcript = root / "orch.jsonl"
transcript.write_text('{"type": "user"}\n')
(root / "as" / "transcripts").mkdir(parents=True)
(root / "as" / "transcripts" / "o1.json").write_text(json.dumps({"role": "orchestrator", "transcript": str(transcript),
                                                                  "since": "2026-09-01T00:00:00Z", "until": None}))
assert su.precheck(home, collect_first=False) == 1, "registered, but the feature is off"
(root / "no-config.json").write_text(json.dumps({"reflect": {"orchestrator_feedback": True}}))
assert su.precheck(home, collect_first=False) == 1, "on without reflect.ledger: nothing is read"
(root / "no-config.json").write_text(json.dumps({"reflect": {"orchestrator_feedback": True, "ledger": "L/learnings.md"}}))
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert su.precheck(home, collect_first=False) == 0
assert "wake: orchestrator feedback, o1: new session" in out.getvalue(), out.getvalue()
ocur = home / "_standing" / "reflect" / "orchestrator-cursor.json"
st = transcript.stat()
recent = su.iso(dt.datetime.now(dt.timezone.utc))
old = su.iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25))


def mark(**kw):
    m = {"scan_end": st.st_size, "inode": st.st_ino, "pending": False, "scanned_at": recent, **kw}
    ocur.write_text(json.dumps({"round": "r", "sessions": {"o1": m}}))


for kw, code, why in (({}, 1, "caught up"), ({"pending": True}, 0, "pending"), ({"scan_end": 5}, 1, "grew, read today"),
                      ({"scan_end": 5, "scanned_at": old}, 0, "grew, last read 25 hours ago"),
                      ({"scan_end": st.st_size + 1}, 0, "shrank"), ({"inode": st.st_ino + 1}, 0, "replaced")):
    mark(**kw)
    assert su.precheck(home, collect_first=False) == code, why
# A span closed by `ROLE: none` and read past its end: later growth is not orchestrator conversation.
reg = root / "as" / "transcripts" / "o1.json"
closed = {**json.loads(reg.read_text()), "until": su.iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=20))}
reg.write_text(json.dumps(closed))
mark(scan_end=5, scanned_at=old)
assert su.precheck(home, collect_first=False) == 0, "not yet read past the span's end"
mark(scan_end=5, scanned_at=old, passed_until=closed["until"])
assert su.precheck(home, collect_first=False) == 1, "read past the span's end: growth is not orchestrator conversation"
mark(scan_end=5, scanned_at=old, passed_until="2026-01-01T00:00:00Z")
assert su.precheck(home, collect_first=False) == 0, "a span closed again since: read it"
mark(scan_end=5, scanned_at=recent, passed_until=closed["until"], pending=True)
assert su.precheck(home, collect_first=False) == 0, "still pending"
mark(scanned_at=recent, inode=st.st_ino + 1)
assert su.precheck(home, collect_first=False) == 0, "replaced: recover"
reg.write_text(json.dumps({**closed, "until": None}))
mark()
(home / "_standing" / "reflect" / "lock").mkdir()
mark(pending=True)
assert su.precheck(home, collect_first=False) == 1, "the lock still wins"
(home / "_standing" / "reflect" / "lock").rmdir()
assert su.precheck(home, collect_first=False, orchestrator=lambda h: 1 / 0) == 0, "a failing check wakes, as above"
assert (home / "_standing" / "reflect" / "cursor.json").exists() and not waiting.exists(), "the other files are untouched"
(root / "no-config.json").unlink()

# schedule only prints; the command runs the precheck by absolute path in an existing workspace.
cmd = su.schedule_cmd(pathlib.Path("/repo/skills/orchestrate/scripts/skill_usage.py"), "path:/repo")
assert cmd[:3] == ["orca", "automations", "create"], cmd
assert cmd[cmd.index("--workspace-mode") + 1] == "existing" and cmd[cmd.index("--workspace") + 1] == "path:/repo", cmd
assert cmd[cmd.index("--precheck") + 1] == "python3 /repo/skills/orchestrate/scripts/skill_usage.py precheck", cmd
prompt = cmd[cmd.index("--prompt") + 1]
assert "standing" in prompt and "draft PR" in prompt and "머지하지 않" in prompt, prompt
assert "/repo/skills/reflect/scripts/reflect_round.py begin" in prompt and "reflect_round.py end --round " in prompt, prompt
assert not any(a.startswith("--extra-agent-args") for a in cmd), cmd
saved_model, su.round_model = su.round_model, lambda: "sonnet"
modelled = su.schedule_cmd(pathlib.Path("/repo/skills/orchestrate/scripts/skill_usage.py"), "path:/repo")
other = su.schedule_cmd(pathlib.Path("/repo/skills/orchestrate/scripts/skill_usage.py"), "path:/repo", "codex")
su.round_model = saved_model
assert "--extra-agent-args=--model sonnet" in modelled, modelled
assert not any(a.startswith("--extra-agent-args") for a in other), other
hint = su.runner_hint(pathlib.Path("/repo"))
assert "path:/repo --name automations-runner --no-parent" in hint and "settings.local.json" in hint, hint

print("test_skill_usage: ok")
