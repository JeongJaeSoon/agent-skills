"""Offline tests for token_usage.py: fixture transcripts in a temp directory, no real transcript read, no model called.

Run: python3 test_token_usage.py
"""
import atexit, datetime as dt, json, os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(tempfile.mkdtemp(prefix="test-token-usage-"))
atexit.register(shutil.rmtree, root, True)
os.environ.update({"ORCH_FLEET_STATE": str(root / "dash"), "AGENT_SKILLS_STATE": str(root),
                   "AGENT_SKILLS_CONFIG": str(root / "agent-skills.json"), "CLAUDE_PROJECTS_DIR": str(root / "projects")})
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import board  # noqa: E402
import fleet  # noqa: E402
import token_usage as tu  # noqa: E402

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc)
SECRET = "PROMPT-TEXT-MUST-NOT-BE-KEPT"
AUTO_PROMPT = f"watch-prs 회차다. 리뷰 대기 PR 을 본다. {SECRET}"
proj = root / "projects" / "-tmp-acme-app"
proj.mkdir(parents=True)


def ts(days_ago, minute=0):
    return (NOW - dt.timedelta(days=days_ago) + dt.timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")


def line(d):
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False) + "\n"  # Claude Code writes compact JSON


def user(sid, text, days_ago, origin="human", **kw):
    return line({"type": "user", "sessionId": sid, "timestamp": ts(days_ago), "turnOrigin": origin,
                 "message": {"role": "user", "content": text}, **kw})


def result(sid, tool_id, text, days_ago, as_blocks=True):
    content = [{"type": "text", "text": text}] if as_blocks else text
    return line({"type": "user", "sessionId": sid, "timestamp": ts(days_ago, 2),
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]}})


def usage(sid, mid, days_ago, inp=100, c5=0, c1=0, read=0, out=10, tools=(), **kw):
    content = [{"type": "text", "text": SECRET}] + [{"type": "tool_use", "name": t[0], "input": t[1], "id": (t[2:] or [None])[0]} for t in tools]
    return line({"type": "assistant", "sessionId": sid, "timestamp": ts(days_ago, 1), **kw,
                 "message": {"id": mid, "content": content, "usage": {
                     "input_tokens": inp, "cache_creation_input_tokens": c5 + c1, "cache_read_input_tokens": read,
                     "output_tokens": out, "cache_creation": {"ephemeral_5m_input_tokens": c5, "ephemeral_1h_input_tokens": c1}}}})


# An automation round: its first prompt is the automation's prompt; one message written as two lines (two blocks).
auto = usage("s-auto", "m-a1", 1, inp=1000, read=10000, out=100, tools=[("Skill", {"skill": "agent-skills:watch-prs"})])
(proj / "s-auto.jsonl").write_text(user("s-auto", AUTO_PROMPT, 1) + auto + auto)
# A slash-command session whose role file says orchestrator.
(root / "roles").mkdir()
(root / "roles" / "s-orch.json").write_text(json.dumps({"role": "orchestrator"}))
(proj / "s-orch.jsonl").write_text(
    user("s-orch", "<command-name>/goal</command-name>\n<command-args>run things</command-args>", 1)
    + user("s-orch", "<local-command-stdout>ok</local-command-stdout>", 1)
    + user("s-orch", f"expanded {SECRET}", 1, isMeta=True)
    + usage("s-orch", "m-o1", 1, inp=500, c1=2000, out=50))
# An interactive session: a prompt, a skill credited by attributionSkill, a monitor wake-up, a hook injection.
hook = {"type": "attachment", "timestamp": ts(1), "sessionId": "s-int",
        "attachment": {"type": "hook_success", "command": "python3 /x/hooks/reorient.py", "content": "",
                       "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": "ROLE: you lead acme " * 10}})}}
(proj / "s-int.jsonl").write_text(
    user("s-int", f"please look {SECRET}", 1) + line(hook)
    + usage("s-int", "m-i1", 1, inp=200, out=20, attributionSkill="agent-skills:brief",
            tools=[("Bash", {"command": "gh pr merge 1 --squash"})])
    + user("s-int", "<task-notification>done</task-notification>", 1, origin="task_notification")
    + usage("s-int", "m-i2", 1, inp=300, read=1000, out=30))
# A subagent of that session, and one call by it a week before (for the trend).
sub = proj / "s-int" / "subagents"
sub.mkdir(parents=True)
(sub / "agent-a.meta.json").write_text(json.dumps({"agentType": "general-purpose"}))
(sub / "agent-a.jsonl").write_text(
    line({"type": "user", "sessionId": "s-int", "timestamp": ts(9), "message": {"role": "user", "content": "task"}})
    + usage("s-int", "m-s0", 9, inp=10, out=1) + usage("s-int", "m-s1", 2, inp=4000, out=400))

AUTOS = {tu.prompt_key(AUTO_PROMPT): ["watch-prs", "watch-prs"]}
cache = tu.collect(now=NOW, autos=AUTOS, hooks={"reorient.py"})
days = cache["days"]
d1 = days[ts(1)[:10]]
assert set(d1["who"]) == {"automation:watch-prs", "orchestrator", "interactive"}, d1["who"]
assert d1["who"]["automation:watch-prs"][0] == 11100 and d1["who"]["automation:watch-prs"][2] == 1, "one message, counted once"
assert d1["who"]["orchestrator"][1] == 500 + 2 * 2000 + 5 * 50, "a 1 h cache write weighs 2, output 5"
assert d1["skill"]["skill:agent-skills:watch-prs"][2] == 1 and d1["skill"]["skill:agent-skills:brief"][2] == 1, d1["skill"]
assert d1["origin"]["task_notification"][2] == 1 and d1["origin"]["human"][2] == 3, d1["origin"]
assert days[ts(2)[:10]]["who"] == {"subagent:general-purpose": [4400, 6000.0, 1]}, days[ts(2)[:10]]
assert days[ts(2)[:10]]["origin"] == {"subagent": [4400, 6000.0, 1]}, "a subagent's turns are not unknown"
assert d1["hooks"]["reorient.py"][0] == 1 and d1["hooks"]["reorient.py"][1] > 0, d1["hooks"]
assert list(cache["sessions"].values()) == [{"who": "automation:watch-prs", "start": ts(1, 1), "outward": False,
                                              "weighted": 1000 + 1000 + 500}], cache["sessions"]
stored = tu.cache_path().read_text()
assert SECRET not in stored and "acme" not in stored and str(root) not in stored, "no text, no path is kept"
assert cache["last"]["parsed"] == 4

# Incremental: nothing new reads nothing; a half-written line waits for its newline.
assert tu.collect(now=NOW, autos=AUTOS, hooks=set())["last"]["parsed"] == 0
late = usage("s-int", "m-i3", 0, inp=7, out=0)
with open(proj / "s-int.jsonl", "a") as f:
    f.write(late[:40])
assert ts(0)[:10] not in tu.collect(now=NOW, autos=AUTOS, hooks=set())["days"]
with open(proj / "s-int.jsonl", "a") as f:
    f.write(late[40:])
cache = tu.collect(now=NOW, autos=AUTOS, hooks=set())
assert cache["days"][ts(0)[:10]]["who"] == {"interactive": [7, 7.0, 1]}, cache["days"][ts(0)[:10]]
# An outward step marks the automation round as not empty; a replaced file is read again from the start.
(proj / "s-auto2.jsonl").write_text(user("s-auto2", AUTO_PROMPT, 0) + usage(
    "s-auto2", "m-b1", 0, tools=[("Bash", {"command": "orca orchestration send --to x --subject y"})]))
cache = tu.collect(now=NOW, autos=AUTOS, hooks=set())
assert cache["sessions"]["s-auto2"]["outward"] is True

# A janitor round counts when `reap --plan` acted on a target, judged from its output; skips, failures, notes, a scan
# (which prints the same `- target: why` shape) and an outcome that is itself a skip leave the round empty.
jproj = root / "janitor" / "-tmp-acme-app"
jproj.mkdir(parents=True)
J = {"root": root / "janitor", "path": root / "janitor-cache.json", "now": NOW, "autos": AUTOS, "hooks": set()}
REAP = "python3 /tmp/acme/reap.py reap --plan /tmp/acme/janitor-plan.json"
DONE = "- orca-worktree [app] /tmp/acme/wt (3일): ignored 항목 0개는 휴지통으로, worktree 는 orca worktree rm 으로 제거"
rounds = {
    "s-r-done": [("Bash", REAP, "- 건너뜀 tmpdir /tmp/acme/x: 더는 정리 대상이 아님\n" + DONE)],
    "s-r-str": [("Bash", 'python3 "$R" reap --plan p.json', DONE, False)],
    "s-r-skip": [("Bash", REAP, "- 건너뜀 tmpdir /tmp/acme/x: 더는 정리 대상이 아님\n- 메모: docker: 데몬 응답 없음, 건너뜀")],
    "s-r-fail": [("Bash", REAP, "- 실패 orca-worktree [app] /tmp/acme/wt: 보내지 않음")],
    "s-r-human": [("Bash", REAP, "- gui-app pid 9 (프로세스 1, 1M, CPU 0%, 3일) App: 건너뜀: 사람이 띄운 셸의 자식\n"
                                 "- proc pid 8 (프로세스 1, 1M, CPU 0%, 3일) x: 사라졌거나 다른 프로세스")],
    "s-r-scan": [("Bash", "python3 /tmp/acme/reap.py scan --kinds janitor", DONE),
                 ("Bash", 'grep -n "reap --plan" /tmp/acme/SKILL.md', DONE)],
    "s-r-tab": [("mcp__claude-in-chrome__tabs_close_mcp", None, "closed")],
    "s-r-rm": [("Bash", "orca worktree rm --worktree path:/tmp/acme/wt --run-hooks", "ok")],
}
for sid, calls in rounds.items():
    text = user(sid, AUTO_PROMPT, 0)
    for i, (tool, cmd, out, *blocks) in enumerate(calls):
        text += usage(sid, f"m-{sid}-{i}", 0, tools=[(tool, {"command": cmd} if cmd else {}, f"t-{sid}-{i}")])
        text += result(sid, f"t-{sid}-{i}", out, 0, *blocks)
    (jproj / f"{sid}.jsonl").write_text(text)
cache = tu.collect(**J)
got = {sid: cache["sessions"][sid]["outward"] for sid in rounds}
assert got == {"s-r-done": True, "s-r-str": True, "s-r-skip": False, "s-r-fail": False, "s-r-human": False,
               "s-r-scan": False, "s-r-tab": True, "s-r-rm": True}, got
assert "휴지통" not in J["path"].read_text(), "only the verdict is kept, not the output"
# The output can land in a later collect than the call.
(jproj / "s-r-late.jsonl").write_text(user("s-r-late", AUTO_PROMPT, 0)
                                     + usage("s-r-late", "m-late", 0, tools=[("Bash", {"command": REAP}, "t-late")]))
assert tu.collect(**J)["sessions"]["s-r-late"]["outward"] is False
with open(jproj / "s-r-late.jsonl", "a") as f:
    f.write(result("s-r-late", "t-late", DONE, 0))
assert tu.collect(**J)["sessions"]["s-r-late"]["outward"] is True
# The outcomes read as "did nothing" are reap.py's own words: a rewording there must fail here.
reap_src = (pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts" / "reap.py").read_text()
for word in [f"- {w}" for w in tu.REAP_NOT_DONE_LINES] + list(tu.REAP_NOT_DONE_OUTCOMES):
    assert word.strip() in reap_src, word

# The same transcript reached through two symlinked projects folders (one per Claude profile) counts once.
alias = root / "profile-b-projects"
alias.symlink_to(root / "projects")
before = json.dumps(tu.collect(now=NOW, autos=AUTOS, hooks=set())["days"], sort_keys=True)
cache = tu.collect(root=alias, now=NOW, autos=AUTOS, hooks=set())
assert cache["last"]["parsed"] == 0 and json.dumps(cache["days"], sort_keys=True) == before, cache["last"]
before = cache["days"][ts(2)[:10]]["who"]["subagent:general-purpose"][2]
(sub / "agent-a.jsonl").write_text(usage("s-int", "m-s9", 2, inp=1, out=0))
cache = tu.collect(now=NOW, autos=AUTOS, hooks=set())
assert cache["days"][ts(2)[:10]]["who"]["subagent:general-purpose"][2] == before + 1

# Report: tables, trend against the week before, warnings linked to static sizes.
static = {"skills": [{"name": "watch-prs", "tokens": 100, "width": 300}, {"name": "orchestrate", "tokens": 120, "width": 400}],
          "invoke": [{"skill": "watch-prs", "tokens": 8000}, {"skill": "orchestrate", "tokens": 15000}]}
cfg = {**tu.DEFAULTS, "daily_min": 0, "daily_share": 0.4, "surge_min": 0, "empty_runs": 1, "empty_window": 2}
rep = tu.report(cache, days=7, now=NOW, static=static, cfg=cfg)
assert rep["window"] == ["2026-09-21", "2026-09-28"], rep["window"]
who = {r["key"]: r for r in rep["who"]}
assert who["subagent:general-purpose"]["trend"] > 1 and who["orchestrator"]["trend"] is None, who
assert rep["unclassified"]["who"] == 0, rep["unclassified"]
kinds = {(w["kind"], w["key"]) for w in rep["warnings"]}
assert ("surge", "subagent:general-purpose") in kinds, kinds
assert not any(k in tu.NOT_FEATURES for _, k in kinds), "interactive use never warns"
empty = [w for w in rep["warnings"] if w["kind"] == "empty_runs"]
assert [w["key"] for w in empty] == ["automation:watch-prs"] and "1/2" in empty[0]["text"], empty
assert empty[0]["static"] == {"skill": "watch-prs", "listing_tokens": 100, "listing_width": 300, "skill_md_tokens": 8000}
daily = [w for w in rep["warnings"] if w["kind"] == "daily"]
assert daily and all(w["key"] not in tu.NOT_FEATURES for w in daily), daily
orch = [w for w in daily if w["key"] == "orchestrator"]
assert orch and orch[0]["static"]["skill"] == "orchestrate", orch
assert {a["key"]: a["rounds"] for a in rep["automations"]} == {"automation:watch-prs": 2}
assert rep["hooks"] == [{"name": "reorient.py", "count": 1, "tokens": d1["hooks"]["reorient.py"][1]}]
quiet = tu.report(cache, days=7, now=NOW, static=static, cfg=tu.DEFAULTS)
assert quiet["warnings"] == [], "default thresholds are far above a fixture's few thousand tokens"
text = tu.render(rep)
assert "WARNINGS (" in text and "static: watch-prs listing 100 tokens/turn" in text and SECRET not in text, text
assert SECRET not in json.dumps(rep)

# Thresholds come from the token_usage key of the config; unknown keys and non-numbers are ignored.
(root / "agent-skills.json").write_text(json.dumps({"token_usage": {"surge": 3, "empty_runs": "x", "other": 1}}))
assert tu.settings()["surge"] == 3 and tu.settings()["empty_runs"] == 7 and "other" not in tu.settings()

# The automation map: prompt keys from `orca automations list`, the first skill named as a whole word.


def fake_orca(*a, **kw):
    out = {"result": {"automations": [{"name": "watch", "prompt": "show how watch-prs works " + SECRET},
                                      {"name": "broken"}]}}
    return subprocess.CompletedProcess(a, 0, json.dumps(out), "")


found = tu.automations(["how", "watch-prs", "prs"], run=fake_orca)
assert found[tu.prompt_key("show how  watch-prs works " + SECRET)] == ["watch", "how"], found
assert found[tu.prompt_key("")] == ["broken", None], found
assert tu.automations(["x"], run=lambda *a, **kw: (_ for _ in ()).throw(OSError("no orca"))) == {}

# The brief: one line while the report is fresh and has warnings, nothing otherwise.
fleet.write_atomic(tu.out_path(), json.dumps(rep))
got = board.token_line(NOW + dt.timedelta(hours=1))
assert got and got.startswith(f"🔥 토큰 경고 {len(rep['warnings'])}건: ") and "외" in got and "\n" not in got, got
assert board.token_line(NOW + dt.timedelta(hours=27)) is None, "a stale report shows nothing"
fleet.write_atomic(tu.out_path(), json.dumps(quiet))
assert board.token_line(NOW) is None

# The CLI reads the cache only with --no-collect, and says so when there is none.
os.environ["ORCH_FLEET_STATE"] = str(root / "empty")
assert tu.main(["--no-collect"]) == 1
print("ok")
