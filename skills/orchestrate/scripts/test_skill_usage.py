"""Offline tests for skill_usage.py: fixture transcripts and an invented skill inventory, no real transcript read.

Run: python3 test_skill_usage.py
"""
import datetime as dt, json, pathlib, sys, tempfile

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
cached = root / "cache" / "acme-tools" / "1.0.0"
skill_md(cached / "skills" / "lint", "lint", 'Lint. Use for "lint this".')
skill_md(root / "cache" / "stale-copy" / "skills" / "ship", "ship", "An older copy of this plugin.")
(root / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
    "acme-tools@example-market": [{"scope": "user", "installPath": str(cached)}],
    "agent-skills@example-market": [{"scope": "user", "installPath": str(root / "cache" / "stale-copy")}]}}))
skill_md(root / "user-skills" / "notes", "notes", 'Notes. Use for "노트에 적어" or "정본".')

inv = su.inventory(plugins_json=root / "installed_plugins.json", user_dir=root / "user-skills", repo_dir=repo)
assert set(inv) == {"agent-skills:ship", "agent-skills:tidy", "agent-skills:quiet", "acme-tools:lint", "notes"}, inv
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
    human(ts(3, 20), "<pasted_content id=x>notes</pasted_content> 배포해줘", session="s4"),
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
assert (lint["auto_30d"], lint["uses_30d"]) == (1, 1) and lint["misses_30d"] == 0, lint
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
write(projects / "-work-alpha" / "s3.jsonl", [human(ts(0), "<command-name>/notes</command-name>", session="s3")])
rep4 = su.collect(projects_dir=projects, inv=inv, cache_path=cache, now=NOW, miss_threshold=1)
n = {r["name"]: r for r in rep4["skills"]}["notes"]
assert n["slash_30d"] == 1 and n["flags"] == ["slash_only"], n

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
unused = su.signal_rows({"skills": [{**rows["agent-skills:quiet"]}]}, NOW)
assert unused[0]["suggest"] == ["rewrite-description", "merge", "retire"], unused

print("test_skill_usage: ok")
