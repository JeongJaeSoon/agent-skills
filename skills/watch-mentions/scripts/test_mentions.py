"""Rules of mentions.py: precheck, inbox check, plan window, ingest filters, bundle, commit. Run: python3 test_mentions.py"""
import json, os, pathlib, shlex, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import mentions as m

ME, OTHER = "U0FAKEOWNER", "U0FAKEOTHER"
NOW = 1_800_000_000.0
cfg = {**m.DEFAULTS, "user_id": ME, "names": ["Owner", "オーナー"], "exclude_channels": ["C0NOISE"], "inbox_to": "run:run_0FAKE"}
state = pathlib.Path(tempfile.mkdtemp()) / "mentions"
live = lambda args: {"ok": True}
dead = lambda args: {"ok": False, "error": {"code": "terminal_handle_stale"}}

# inbox_to: a run that exists passes quietly; a live terminal handle passes with a warning; anything dead or unset is exit 2.
calls = []
assert m.inbox_check(cfg, lambda a: calls.append(a) or {"ok": True}) == (0, "inbox_to run:run_0FAKE 확인")
assert calls == [["orchestration", "run-show", "--id", "run_0FAKE"]], calls
code, why = m.inbox_check(cfg, dead)
assert code == 2 and "Run 이 없다" in why and "inbox_to" in why, why
term = {**cfg, "inbox_to": "term_0FAKE"}
code, why = m.inbox_check(term, live)
assert code == 0 and "run:<id> 를 권장" in why, why
code, why = m.inbox_check(term, dead)
assert code == 2 and "터미널이 죽었다" in why and "run:<id>" in why, why
code, why = m.inbox_check({**cfg, "inbox_to": None}, live)
assert code == 2 and "설정되지 않음" in why, why
assert m.inbox_check(term, lambda a: {})[0] == 2, "orca unreachable means the address cannot be trusted"

# precheck: config first, then the lock, then inbox_to.
assert m.precheck({**m.DEFAULTS}, state, NOW, live)[0] == 1
assert m.precheck(cfg, state, NOW, live)[0] == 0
assert m.precheck(cfg, state, NOW, dead)[0] == 2

# First plan looks back lookback_hours and takes the lock.
p = m.plan(cfg, state, NOW)
assert p["oldest"] == NOW - 24 * 3600 and not p["read_private"]
kinds = [s["kind"] for s in p["searches"]]
assert kinds == ["mention", "name", "name", "thread"], kinds
assert p["searches"][0]["keywords"] == [f"<@{ME}>"]
assert all(f"-from:<@{ME}>" in s["filters"] for s in p["searches"] if s["kind"] == "name")
assert p["searches"][-1]["filters"].startswith(f"from:<@{ME}> is:thread after:")
assert m.precheck(cfg, state, NOW + 60, live)[0] == 1, "a held lock skips the run"
assert m.precheck(cfg, state, NOW + 61 * 60, live)[0] == 0, "a stale lock does not block forever"

def c(ch, ts, kind="mention", author=OTHER, **kw):
    return {"channel_id": ch, "ts": ts, "kind": kind, "author_id": author,
            "permalink": f"https://example.slack.com/archives/{ch}/p{ts.replace('.', '')}", **kw}

old = f"{NOW - 25 * 3600:.6f}"
fresh = f"{NOW - 3600:.6f}"
cands = [c("C1", fresh), c("C1", fresh, kind="name"), c("C1", f"{NOW - 7200:.6f}", author=ME),
         c("G1", fresh), c("D1", fresh), c("C2", fresh, is_private=True), c("C0NOISE", fresh), c("C1", old),
         c("C3", fresh, kind="thread", thread_ts="1.0"), c("C4", fresh, kind="thread", is_bot=True)]
new, dropped = m.ingest(cands, cfg, state)
assert [(i["channel_id"], i["kinds"]) for i in new] == [("C1", ["mention", "name"]), ("C3", ["thread"])], new
reasons = sorted(d["why"] for d in dropped)
assert reasons == sorted(["봇", "본인 발언", "공개 채널 아님", "공개 채널 아님", "공개 채널 아님", "대상 채널 아님", "창 이전"]), reasons
assert all("text" not in i for i in new), "message text never travels through the script"

# A channel allowlist keeps only those channels.
only, _ = m.ingest(cands, {**cfg, "channels": ["C3"]}, state)
assert [i["channel_id"] for i in only] == ["C3"]

# bundle: one message, categories in a fixed order, drafts and PRs inline, summaries capped.
items = [{**new[1], "category": "question", "summary": "언제 끝나요?"},
         {**new[0], "category": "work", "summary": "x" * 500, "draft": {"title": "T", "requester": "R", "duplicates": []}}]
body = m.bundle(items, cfg)
lines = body.splitlines()
assert lines[0] == "Slack 새 항목 2건 (작업 의뢰 1 / 질문 1)", lines[0]
assert lines[1].startswith("- [작업 의뢰] " + "x" * 120 + " · 기표 후보: T (의뢰자 R, 중복 후보 없음)")
assert "x" * 121 not in body and lines[2].startswith("- [질문] 언제 끝나요?")
try:
    m.bundle([{**new[0], "category": "urgent", "summary": "s"}], cfg)
    raise AssertionError("unknown category must fail")
except SystemExit:
    pass

# pr_ref: owner/repo#N or a PR URL, nothing else.
assert m.pr_ref("acme/widgets#12") == m.pr_ref("https://github.com/acme/widgets/pull/12/files") == "acme/widgets#12"
assert m.pr_ref("see #12") is None and m.pr_ref(None) is None and m.pr_ref("https://example.test/doc") is None

# commit: seen ledger without text, the PR ref normalized, cursor moves to run start, lock released.
items[0]["pr"] = "https://github.com/acme/widgets/pull/7"
assert m.commit(items, cfg, state, NOW) == 2
seen = json.loads((state / "seen.json").read_text())
assert set(seen) == {f"C1/{fresh}", f"C3/{fresh}"} and all("summary" not in v for v in seen.values())
assert seen[f"C3/{fresh}"]["pr"] == "acme/widgets#7" and "pr" not in seen[f"C1/{fresh}"]

# annotate: backfills pr on rows already seen, ignores unseen rows and bad refs, leaves cursor and lock alone.
assert m.annotate([{**items[0], "pr": "acme/widgets#8"}, {"channel_id": "C9", "ts": "1.0", "pr": "acme/x#1"},
                   {**items[1], "pr": "no"}], cfg, state, NOW) == 1
seen = json.loads((state / "seen.json").read_text())
assert seen[f"C3/{fresh}"]["pr"] == "acme/widgets#8" and "C9/1.0" not in seen and "pr" not in seen[f"C1/{fresh}"]
assert not (state / "lock.json").exists()
assert json.loads((state / "cursor.json").read_text()) == {"until": NOW}
assert not (state / "lock.json").exists()
m.write_json(state / "lock.json", {"run": NOW, "oldest": NOW})
try:
    m.annotate([{**items[0], "pr": "acme/widgets#9"}], cfg, state, NOW + 60)
    raise AssertionError("annotate must not race a running round's commit")
except SystemExit:
    pass
(state / "lock.json").unlink()

# Next run starts at the cursor minus the overlap, and the overlap does not re-report.
p2 = m.plan(cfg, state, NOW + 1200)
assert p2["oldest"] == NOW - 600
again, dropped = m.ingest([c("C1", fresh)], cfg, state)
assert again == [] and dropped[0]["why"] in ("창 이전", "이미 보고함")
again, dropped = m.ingest([c("C1", f"{NOW - 300:.6f}"), c("C1", f"{NOW - 300:.6f}")], cfg, state)
assert len(again) == 1
m.commit([], cfg, state, NOW + 1200)
assert json.loads((state / "cursor.json").read_text()) == {"until": NOW + 1200}

# Old seen rows are pruned.
m.plan(cfg, state, NOW + 40 * 86400)
m.commit([], cfg, state, NOW + 40 * 86400)
assert json.loads((state / "seen.json").read_text()) == {}

# schedule: precheck with absolute path and the state dir, timezone when set, nothing created without --write.
line = m.schedule_cmd(pathlib.Path("/abs/skills/watch-mentions/scripts/mentions.py"), {**cfg, "timezone": "Asia/Seoul"}, "path:/abs",
                      state=m.STATE_HOME)
assert line[:3] == ["orca", "automations", "create"] and "--write" not in line
pre = line[line.index("--precheck") + 1]
assert pre == "python3 /abs/skills/watch-mentions/scripts/mentions.py precheck", pre
assert "python3 /abs/skills/watch-mentions/scripts/mentions.py plan " in line[line.index("--prompt") + 1]
assert line[line.index("--timezone") + 1] == "Asia/Seoul" and line[line.index("--trigger") + 1] == "*/20 9-20 * * 1-5"
off = m.schedule_cmd(pathlib.Path("/abs/mentions.py"), cfg, "path:/abs", state="/scratch dir")
assert off[off.index("--precheck") + 1] == "python3 /abs/mentions.py precheck --state '/scratch dir'"
prompt = off[off.index("--prompt") + 1]
assert "python3 /abs/mentions.py plan --state '/scratch dir'" in prompt and "--state '/scratch dir'" in prompt.split("plan")[-1]
assert "env " not in shlex.join(off) and "AGENT_SKILLS_STATE" not in shlex.join(off)
hint = m.runner_hint(pathlib.Path("/abs dir"))
assert "--repo 'path:/abs dir'" in hint and "--no-parent" in hint and "settings.local.json" in hint, hint

# CLI: precheck exit codes and plan refusing a second run. A stub orca on PATH answers for inbox_to.
fakebin = state.parent / "bin"
fakebin.mkdir()
(fakebin / "orca").write_text('#!/bin/sh\necho \'{"ok": true}\'\n')
(fakebin / "orca").chmod(0o755)
env = {**os.environ, "AGENT_SKILLS_STATE": str(state.parent), "PATH": f"{fakebin}:{os.environ['PATH']}"}
conf = state.parent / "conf.json"
conf.write_text(json.dumps({"mentions": {"user_id": ME, "inbox_to": "run:run_0FAKE"}}))
S = str(pathlib.Path(__file__).parent / "mentions.py")
run = lambda *a: subprocess.run([sys.executable, S, *a, "--config", str(conf)], env=env, capture_output=True, text=True)
assert run("precheck").returncode == 0
assert run("plan").returncode == 0 and run("plan").returncode != 0
assert run("precheck").returncode == 1
assert run("commit").returncode == 0 and run("precheck").returncode == 0
assert run("inbox").returncode == 0
conf.write_text(json.dumps({"mentions": {"user_id": ME}}))
r = run("inbox")
assert r.returncode == 2 and "inbox_to" in r.stderr, r
assert run("precheck").returncode == 2
# --state wins over $AGENT_SKILLS_STATE: the lock lands in the flag's folder.
conf.write_text(json.dumps({"mentions": {"user_id": ME, "inbox_to": "run:run_0FAKE"}}))
flagged = state.parent / "flagged"
assert run("plan", "--state", str(flagged)).returncode == 0
assert (flagged / "mentions" / "lock.json").exists() and not (state / "lock.json").exists()
print("ok")
