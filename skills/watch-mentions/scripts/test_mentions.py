"""Rules of mentions.py: precheck, plan window, ingest filters, bundle, commit. Run: python3 test_mentions.py"""
import json, os, pathlib, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import mentions as m

ME, OTHER = "U0FAKEOWNER", "U0FAKEOTHER"
NOW = 1_800_000_000.0
cfg = {**m.DEFAULTS, "user_id": ME, "names": ["Owner", "オーナー"], "exclude_channels": ["C0NOISE"]}
state = pathlib.Path(tempfile.mkdtemp()) / "mentions"

# precheck: config first, then the lock.
assert m.precheck({**m.DEFAULTS}, state, NOW)[0] == 1
assert m.precheck(cfg, state, NOW)[0] == 0

# First plan looks back lookback_hours and takes the lock.
p = m.plan(cfg, state, NOW)
assert p["oldest"] == NOW - 24 * 3600 and not p["read_private"]
kinds = [s["kind"] for s in p["searches"]]
assert kinds == ["mention", "name", "name", "thread"], kinds
assert p["searches"][0]["keywords"] == [f"<@{ME}>"]
assert all(f"-from:<@{ME}>" in s["filters"] for s in p["searches"] if s["kind"] == "name")
assert p["searches"][-1]["filters"].startswith(f"from:<@{ME}> is:thread after:")
assert m.precheck(cfg, state, NOW + 60)[0] == 1, "a held lock skips the run"
assert m.precheck(cfg, state, NOW + 61 * 60)[0] == 0, "a stale lock does not block forever"

def c(ch, ts, kind="mention", author=OTHER, **kw):
    return {"channel_id": ch, "ts": ts, "kind": kind, "author_id": author,
            "permalink": f"https://example.slack.com/archives/{ch}/p{ts.replace('.', '')}", **kw}

old = f"{NOW - 25 * 3600:.6f}"
fresh = f"{NOW - 3600:.6f}"
cands = [c("C1", fresh), c("C1", fresh, kind="name"), c("C1", f"{NOW - 7200:.6f}", author=ME),
         c("G1", fresh), c("D1", fresh), c("C2", fresh, is_private=True), c("C0NOISE", fresh), c("C1", old),
         c("C3", fresh, kind="thread", thread_ts="1.0")]
new, dropped = m.ingest(cands, cfg, state)
assert [(i["channel_id"], i["kinds"]) for i in new] == [("C1", ["mention", "name"]), ("C3", ["thread"])], new
reasons = sorted(d["why"] for d in dropped)
assert reasons == sorted(["본인 발언", "공개 채널 아님", "공개 채널 아님", "공개 채널 아님", "대상 채널 아님", "창 이전"]), reasons
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

# commit: seen ledger without text, cursor moves to run start, lock released.
assert m.commit(items, cfg, state, NOW) == 2
seen = json.loads((state / "seen.json").read_text())
assert set(seen) == {f"C1/{fresh}", f"C3/{fresh}"} and all("summary" not in v for v in seen.values())
assert json.loads((state / "cursor.json").read_text()) == {"until": NOW}
assert not (state / "lock.json").exists()

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
line = m.schedule_cmd(pathlib.Path("/abs/skills/watch-mentions/scripts/mentions.py"), {**cfg, "timezone": "Asia/Seoul"}, "path:/abs")
assert line[:3] == ["orca", "automations", "create"] and "--write" not in line
pre = line[line.index("--precheck") + 1]
assert pre.startswith("env AGENT_SKILLS_STATE=") and pre.endswith("/abs/skills/watch-mentions/scripts/mentions.py precheck")
assert line[line.index("--timezone") + 1] == "Asia/Seoul" and line[line.index("--trigger") + 1] == "*/20 9-20 * * 1-5"

# CLI: precheck exit codes and plan refusing a second run.
env = {**os.environ, "AGENT_SKILLS_STATE": str(state.parent)}
conf = state.parent / "conf.json"
conf.write_text(json.dumps({"mentions": {"user_id": ME}}))
S = str(pathlib.Path(__file__).parent / "mentions.py")
run = lambda *a: subprocess.run([sys.executable, S, *a, "--config", str(conf)], env=env, capture_output=True, text=True)
assert run("precheck").returncode == 0
assert run("plan").returncode == 0 and run("plan").returncode != 0
assert run("precheck").returncode == 1
assert run("commit").returncode == 0 and run("precheck").returncode == 0
print("ok")
