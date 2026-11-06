#!/usr/bin/env python3
"""sweep.py's shadow stage on fixed patrol verdict files: what refuses a round or a candidate, and the diff against
the old cleaners' logs. Nothing here closes, kills or deletes; the shadow has no way to."""
import contextlib, io, json, os, pathlib, plistlib, signal, subprocess, sys, tempfile, time

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp / "state")
os.environ["AGENT_SKILLS_CONFIG"] = str(tmp / "config.json")
os.environ["AGENT_SKILLS_SWEEP"] = "off"
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guard, reap, sweep  # noqa: E402

STATE = pathlib.Path(os.environ["AGENT_SKILLS_STATE"])
STATE.mkdir(parents=True)
PROTECT = tmp / "protect.txt"
PROTECT.write_text("project kept\n")
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT)}}))
NOW = time.time()
OK = {"reap_scan": "ok", "reap_scan:tmpdir": "ok", "reap_scan:card": "ok", "terminals": "ok", "waits": "ok",
      "workers": "ok", "protect": "ok", "reap_hard_cards": "ok", "reap_hard_rest": "ok", "pressure_live": "ok"}


def cand(rule, key, kind, needs, item, **extra):
    return {"rule": rule, "key": key, "kind": kind, "needs": needs, "why": "w", "item": item, **extra}


CANDS = [
    cand("trash:tmpdir", "tmpdir:/t/a", "tmpdir", ["reap_scan", "reap_scan:tmpdir"],
         {"kind": "tmpdir", "key": "tmpdir:/t/a", "name": str(tmp / "a")}),
    cand("card:landed", "card:/ws/x", "card", ["reap_scan", "reap_scan:card", "terminals", "waits", "workers"],
         {"kind": "card", "key": "card:/ws/x", "name": "/ws/x", "verdict": "삭제", "head": "hx", "close": ["t1"]}),
    cand("card:strict", "card:/ws/y", "card", ["reap_hard_cards", "protect", "terminals", "waits", "workers"],
         {"kind": "card", "id": "/ws/y", "head": "h", "handles": ["t2"], "target": True}),
    cand("stack:gone", "stack:kept", "stack", ["reap_hard_rest", "protect"], {"kind": "stack", "id": "kept"}),
    cand("trash:tmpdir", "tmpdir:/t/p", "tmpdir", ["reap_scan", "pressure_live"],
         {"kind": "tmpdir", "key": "tmpdir:/t/p", "name": str(tmp / "p")}, when="pressure"),
]


def verdicts(**over):
    v = {"t": NOW, "round": "r1", "complete": True, "sources": dict(OK), "candidates": [dict(c) for c in CANDS],
         "cards": {}}
    v.update(over)
    (STATE / "patrol-verdicts.json").write_text(json.dumps(v))
    return v


seen = []


def fake_keep(item, protect, cleaner):
    seen.append((item.get("key") or item.get("id"), cleaner, item.get("handles")))
    return guard.item(item, protect, cleaner) if item["kind"] == "stack" else None


free = lambda targets: True  # noqa: E731

# ---------------------------------------------------------------- one candidate at a time

protect = guard.read_protect(str(PROTECT))
rows = sweep.decide(verdicts(), protect, NOW, keep=fake_keep, lock=free)
by = {r["key"]: r for r in rows}
assert by["tmpdir:/t/a"]["decision"] == "act" and by["card:/ws/x"]["decision"] == "act", rows
assert by["card:/ws/y"]["decision"] == "act" and by["stack:kept"]["why"] == "보호 목록·상시 서비스의 프로젝트"
assert by["tmpdir:/t/p"]["why"] == "pressure 회차에서만"
assert all(r["owner"] == "old" for r in rows)  # no ownership file: every rule is still its old cleaner's
# A janitor card is checked against the terminals its plan saw; reap-hard's against its own; each by its cleaner.
assert ("card:/ws/x", "reap", ["t1"]) in seen and ("/ws/y", "hard", ["t2"]) in seen, seen
rows = sweep.decide(verdicts(), protect, NOW, pressure=True, keep=fake_keep, lock=free)
assert {r["key"]: r for r in rows}["tmpdir:/t/p"]["decision"] == "act"

# needs: a source this round did not read leaves every candidate that relied on it
src = {**OK, "waits": "failed"}
rows = sweep.decide(verdicts(sources=src), protect, NOW, keep=fake_keep, lock=free)
by = {r["key"]: r for r in rows}
assert "waits" in by["card:/ws/x"]["why"] and "waits" in by["card:/ws/y"]["why"]
assert by["tmpdir:/t/a"]["decision"] == "act"
rows = sweep.decide(verdicts(sources={k: v for k, v in OK.items() if k != "protect"}), protect, NOW,
                    keep=fake_keep, lock=free)
assert all(r["decision"] == "skip" for r in rows if "protect" in CANDS[[c["key"] for c in CANDS].index(r["key"])]["needs"])

# owner: recorded, never a reason for the shadow to leave a candidate
(STATE / "sweep-owner.json").write_text(json.dumps({"generation": 4, "rules": {"trash:tmpdir": "sweep"}}))
(STATE / "sweep-last.json").write_text(json.dumps({"t": NOW}))
rows = sweep.decide(verdicts(), protect, NOW, own=guard.ownership(), keep=fake_keep, lock=free)
by = {r["key"]: r for r in rows}
assert by["tmpdir:/t/a"]["owner"] == "sweep" and by["card:/ws/x"]["owner"] == "old"
assert by["tmpdir:/t/a"]["decision"] == "act"
(STATE / "sweep-owner.json").unlink()
(STATE / "sweep-last.json").unlink()

# lock: a target another cleaner holds is left, and the probe lets go of what it took
rows = sweep.decide(verdicts(), protect, NOW, keep=fake_keep, lock=lambda t: ("card", "/ws/x") not in t)
assert {r["key"]: r for r in rows}["card:/ws/x"]["why"] == guard.LOCK_BUSY
# The shadow takes no lock at all: a target another cleaner holds is still decided, and no lock file is made, so
# the shadow never makes an old cleaner leave a target.
guard.lock_file("other", "x").parent.mkdir(parents=True, exist_ok=True)
child = subprocess.Popen([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'w'); "
                          "fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); sys.stdin.read()",
                          str(guard.lock_file("other", "tmpdir:/t/a"))], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True)
assert child.stdout.readline().strip() == "held"
try:
    before = set((STATE / "locks").iterdir())
    rows = sweep.decide(verdicts(), protect, NOW, keep=fake_keep)
    assert {r["key"]: r for r in rows}["tmpdir:/t/a"]["decision"] == "act"
    assert set((STATE / "locks").iterdir()) == before and not guard.held()
finally:
    child.stdin.close()
    child.wait(10)

# guard: the shared checks run without the owner check, and a guard that raises keeps that one target
rows = sweep.decide(verdicts(), protect, NOW, keep=lambda i, p, c: "남김" if i.get("name") == str(tmp / "a") else None,
                    lock=free)
assert {r["key"]: r for r in rows}["tmpdir:/t/a"]["why"] == "남김"
saved_item = guard.item
guard.item = lambda *a: (_ for _ in ()).throw(RuntimeError("ps failed"))
assert sweep.shadow_keep({"kind": "tmpdir"}, protect, "reap").startswith("검사를 마치지 못함(RuntimeError")
guard.item = saved_item
(STATE / "sweep-owner.json").write_text(json.dumps({"rules": {"trash:tmpdir": "sweep"}}))
(STATE / "sweep-last.json").write_text(json.dumps({"t": NOW}))
assert sweep.shadow_keep({"kind": "tmpdir", "name": str(tmp / "a")}, protect, "reap") is None
(STATE / "sweep-owner.json").unlink()
(STATE / "sweep-last.json").unlink()

# cap and repeated failures
rows = sweep.decide(verdicts(), protect, NOW, cap=1, keep=fake_keep, lock=free)
assert [r["decision"] for r in rows].count("act") == 1 and rows[1]["why"] == "회차 상한 1건", rows
fails = {}
assert not sweep.record_result(fails, "tmpdir:/t/a", False, NOW)
assert sweep.record_result(fails, "tmpdir:/t/a", False, NOW)  # the second failure in a row: a day's rest, one alert
assert not sweep.record_result(fails, "tmpdir:/t/a", False, NOW + 1)  # no second alert while resting
rows = sweep.decide(verdicts(), protect, NOW + 60, fails=fails, keep=fake_keep, lock=free)
assert "하루 쉼" in {r["key"]: r for r in rows}["tmpdir:/t/a"]["why"]
rows = sweep.decide(verdicts(), protect, NOW + sweep.REST_S + 120, fails=fails, keep=fake_keep, lock=free)
assert {r["key"]: r for r in rows}["tmpdir:/t/a"]["decision"] == "act"
sweep.record_result(fails, "tmpdir:/t/a", True, NOW)
assert "tmpdir:/t/a" not in fails

# ---------------------------------------------------------------- the round as a whole

assert sweep.refuse_round(None, None, set(), NOW) == "순찰 판정 파일을 읽지 못함"
assert "r2 가 아님" in sweep.refuse_round(verdicts(round="r1"), "r2", set(), NOW)
assert "complete 아님" in sweep.refuse_round(verdicts(complete=False), None, set(), NOW)
assert "회차 id" in sweep.refuse_round({"t": NOW, "complete": True}, None, set(), NOW)
assert "분 전" in sweep.refuse_round(verdicts(t=NOW - 2 * sweep.FRESH_S), None, set(), NOW)
assert "이미 판정함" in sweep.refuse_round(verdicts(), None, {"r1"}, NOW)
assert sweep.refuse_round(verdicts(), "r1", set(), NOW) is None

saved_keep = sweep.shadow_keep
sweep.shadow_keep = fake_keep
verdicts()
res = sweep.run_shadow(now=NOW)
assert res["round"] == "r1" and res["acts"] == 3 and res["skips"] == 2 and not res["refused"], res
assert sweep.run_shadow(now=NOW + 1)["refused"].endswith("이미 판정함")
lines = sweep.read_jsonl(sweep.shadow_log())
assert len(lines) == 1 and lines[0]["mode"] == "shadow"  # an already-decided round writes nothing
assert lines[0]["round_t"] == NOW and "seen_t" not in lines[0]  # a file without candidates_t: the round's start
verdicts(round="r1s", candidates_t=NOW + 40,
         candidates=[{**c, "seen": NOW + 45} if c["key"] == "tmpdir:/t/a" else c for c in CANDS])
assert sweep.run_shadow(now=NOW + 50)["round"] == "r1s"
logged = sweep.read_jsonl(sweep.shadow_log())[-1]
assert logged["seen_t"] == NOW + 40 and next(r for r in logged["rows"] if r["key"] == "tmpdir:/t/a")["seen_t"] == NOW + 45
verdicts(round="r2", complete=False)
assert "complete 아님" in sweep.run_shadow(now=NOW)["refused"] and len(sweep.read_jsonl(sweep.shadow_log())) == 3
assert "complete 아님" in sweep.run_shadow(now=NOW + 1)["refused"] and len(sweep.read_jsonl(sweep.shadow_log())) == 3
PROTECT.unlink()  # unlike the janitor, the sweep stops without a protect file
verdicts(round="r3")
assert "보호 목록" in sweep.run_shadow(now=NOW)["refused"]
PROTECT.write_text("project kept\n")
# A round refused for a file since mended is decided on the next run, not counted as done.
res = sweep.run_shadow(now=NOW + 2)
assert res["round"] == "r3" and not res["refused"] and res["acts"] == 3, res
(STATE / "sweep-owner.json").write_text("{")
verdicts(round="r4")
assert "sweep-owner.json" in sweep.run_shadow(now=NOW)["refused"]
(STATE / "sweep-owner.json").unlink()
# One run at a time: a second while the first holds sweep.lock records nothing.
n = len(sweep.read_jsonl(sweep.shadow_log()))
with sweep.run_lock() as got:
    assert got
    child = subprocess.run([sys.executable, str(HERE / "sweep.py"), "run", "--shadow"], capture_output=True,
                           text=True, env={**os.environ}, timeout=60)
assert "다른 sweep 회차" in child.stdout and len(sweep.read_jsonl(sweep.shadow_log())) == n, child
sweep.shadow_keep = saved_keep

# The real guards on the shadow's path only ever read: with every listing answering as a quiet card would, the
# whole path runs, and no command that changes anything is even attempted (recorded apart, so no guard can swallow
# it).
cmds, forbidden = [], []
LISTINGS = {("terminal", "list"): {"terminals": [{"handle": "t1"}]}, ("worktree", "ps"): {"worktrees": []},
            ("worktree", "list"): {"worktrees": [{"path": reap.real("/ws/x")}, {"path": reap.real("/ws/y")}]},
            ("orchestration", "worker-list"): {"workers": [], "page": {}}}
CHANGES = ("close", "rm", "kill", "down", "off", "trash", "stop", "remove", "delete", "send", "create")


def fake_orca(*a, **k):
    cmds.append(["orca", *a])
    if any(x in CHANGES for x in a):
        forbidden.append(a)
    if a[:2] == ("terminal", "list"):  # each card shows the terminals its judgment saw
        # /ws/y's is an agent terminal: the guard reads its wait, screen and transcript too
        return {"terminals": [{"handle": "t1"} if a[-1].endswith("/ws/x") else {"handle": "t2", "agentIdentity": "claude"}]}
    return LISTINGS.get(a[:2])


def fake_run(cmd, **k):
    cmds.append(list(cmd))
    if any(x in CHANGES for x in cmd[:4]) or cmd[0] in ("kill", "trash", "docker"):
        forbidden.append(cmd)
    if cmd[:2] == ["git", "-C"] and cmd[3:] == ["rev-parse", "HEAD"]:  # each card still at the HEAD its scan saw
        return "hx" if cmd[2].endswith("/ws/x") else "h"
    return ""


import cardguard, types  # noqa: E402
agent_reads = []
fake_prog = types.SimpleNamespace(agent_waits=lambda strict=False: agent_reads.append("waits") or {},
                                  read_screen=lambda h: agent_reads.append(("screen", h)) or ["❯ "])
saved_orch = cardguard._orch
cardguard._orch = lambda name: fake_prog if name == "prog" else saved_orch(name)
saved = reap.run, reap.orca_json, os.kill, reap.trash, reap.processes, reap.cwds
reap.run, reap.orca_json = fake_run, fake_orca
reap.processes, reap.cwds = lambda: {}, lambda: {}  # a machine with nothing running in the cards
os.kill = lambda *a: forbidden.append(("kill", *a))
reap.trash = lambda *a: forbidden.append(("trash", *a))
try:
    verdicts(round="r5")
    res = sweep.run_shadow(now=NOW)
finally:
    reap.run, reap.orca_json, os.kill, reap.trash, reap.processes, reap.cwds = saved
    cardguard._orch = saved_orch
assert agent_reads == ["waits", ("screen", "t2")], agent_reads
assert res["round"] == "r5" and not res["refused"], res
by = {r["key"]: r for r in res["rows"]}
assert by["card:/ws/x"]["decision"] == "act" and by["card:/ws/y"]["decision"] == "act", by  # the full guard path ran
assert by["tmpdir:/t/a"]["decision"] == "act" and by["stack:kept"]["decision"] == "skip"
assert sum(c[:2] == ["git", "-C"] and c[3:] == ["rev-parse", "HEAD"] for c in cmds) == 2  # cardguard.guard, per card
assert forbidden == [], forbidden

# The shadow touches nothing: no act, no run, no state file an old cleaner reads.
assert not (STATE / "sweep-last.json").exists() and not (STATE / "sweep-fails.json").exists()
assert not (STATE / "reap-actions.jsonl").exists()

assert reap.spawn_sweep_shadow("test") is False  # AGENT_SKILLS_SWEEP=off starts nothing

# The run's budget is not a read failure: a guard's own "could not see" handler lets it through.
saved_run_b = reap.run
reap.run = lambda cmd, **k: (_ for _ in ()).throw(sweep.Budget())
try:
    sweep.shadow_keep({"kind": "card", "name": "/ws/x", "head": "hx", "handles": ["t1"]}, protect, "reap")
    raise AssertionError("the budget was swallowed")
except sweep.Budget:
    pass
reap.run = saved_run_b
saved_rs = sweep.run_shadow
sweep.run_shadow = lambda *a, **k: (_ for _ in ()).throw(sweep.Budget())
err = io.StringIO()
with contextlib.redirect_stderr(err):
    assert sweep.main(["sweep.py", "run", "--shadow"]) == 4 and "그만둠" in err.getvalue()
sweep.run_shadow = saved_rs
assert signal.alarm(0) == 0  # no alarm left armed


# The log keeps its last KEEP_LINES rounds.
sweep.shadow_log().unlink()
for n in range(5):
    sweep.append_keep(sweep.shadow_log(), {"t": NOW, "round": f"k{n}"}, keep=3)
assert [r["round"] for r in sweep.read_jsonl(sweep.shadow_log())] == ["k2", "k3", "k4"]
sweep.shadow_log().unlink()

# ---------------------------------------------------------------- diff


def shadow_round(t, acts):
    sweep.append_keep(sweep.shadow_log(), {
        "t": t + 30, "round_t": t, "round": f"d{t}", "mode": "shadow",
        "rows": [{"rule": a[0], "key": a[1], "identity": a[2] if len(a) > 2 else {}, "decision": "act"} for a in acts]})


def hard_row(t, kind, id_, result, dry=False, **extra):
    # ts is the pass's start; t is when it acted
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t - 200)), "t": t, "dry_run": dry,
            "kind": kind, "id": id_, "result": result, **extra}


T0 = NOW - 10 * 3600
# The cards below are made-up paths that exist nowhere; "gone" is tested further down against real folders.
real_gone_card, sweep.gone_card = sweep.gone_card, lambda key: False
real_gone_volume, sweep.gone_volume = sweep.gone_volume, lambda key: False
shadow_round(T0, [("card:strict", "card:/ws/y", {"head": "h1"}), ("trash:tmpdir", "tmpdir:/t/a"),
                  ("stack:gone", "stack:s9"), ("card:landed", "card:/ws/z", {"head": "z1"}),
                  ("card:strict", "card:/ws/re", {"head": "new"}), ("card:close-idle", "card:/ws/c", {"close": ["A"]}),
                  ("card:close-idle", "card:/ws/b", {"close": ["B"]}), ("card:close-idle", "card:/ws/a", {"close": ["A"]}),
                  ("card:landed", "card:/ws/kept", {"head": "k"})])
shadow_round(T0 + 900, [("card:strict", "card:/ws/y", {"head": "h1"}), ("trash:tmpdir", "tmpdir:/t/a")])
for k in range(1, 40):  # wanted every round for ten hours and never acted on: excess, however recent its last round
    shadow_round(T0 + k * 900, [("proc:listener", "listener:77", {"pid": 77, "start": "s"})])
shadow_round(NOW - 60, [("card:strict", "card:/ws/new")])
with open(STATE / "reap-hard.jsonl", "w") as f:
    for row in (hard_row(T0 + 1000, "card", "/ws/y", "터미널 닫음, " + reap.REMOVED, identity={"head": "h1"}),
                hard_row(T0 + 1000, "stack", "s9", "건너뜀: 다시 보니"),
                hard_row(T0 + 1000, "stack", "s9", "dry-run", dry=True),
                hard_row(T0 + 1000, "card", "/ws/z", "터미널 닫음, " + reap.REMOVED, identity={"head": "z1"}),
                hard_row(T0 + 1000, "card", "/ws/re", "터미널 닫음, " + reap.REMOVED, identity={"head": "old"})):
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)
reap.log_action({"key": "tmpdir:/t/a", "kind": "tmpdir"}, "휴지통", True, "trash:tmpdir")
reap.log_action({"key": "card:/ws/c", "kind": "card"}, "터미널 0개 닫음, worktree 는 남김", True, "card:close-idle")
reap.log_action({"key": "proc:9", "kind": "orphan", "pid": 9}, "사라졌거나 다른 프로세스", True, "proc:orphan")
# Terminal A closed on /ws/b: not the B the shadow planned. On /ws/a: the very terminal planned.
reap.log_action({"key": "card:/ws/b", "kind": "card"}, "터미널 1개 닫음, worktree 는 남김", True, "card:close-idle",
                ["A"])
reap.log_action({"key": "card:/ws/a", "kind": "card"}, "터미널 1개 닫음, worktree 는 남김", True, "card:close-idle",
                ["A"])
# A removal its second look skipped closes terminals only: no answer to a removal plan.
reap.log_action({"key": "card:/ws/kept", "kind": "card", "head": "k"},
                "터미널 1개 닫음, 삭제는 건너뜀: 판정 뒤 HEAD 가 바뀜", True, "card:landed", ["t"])
act = sweep.read_jsonl(STATE / "reap-actions.jsonl")
assert [a["ok"] for a in act] == [True, False, False, True, True, True], act  # a result that did nothing is not an act
assert [a["effect"] for a in act] == ["trash:tmpdir", None, None, "card:close", "card:close", "card:close"], act
for a in act:
    a["t"] = T0 + 3600  # the janitor's next round, inside its lag
(STATE / "reap-actions.jsonl").write_text("".join(json.dumps(a) + "\n" for a in act))
d = sweep.diff(48, now=NOW + 1)
states = {r["key"]: r["state"] for r in d["rows"]}
# /ws/y: reap-hard's act 200 s into a pass that started before the round still matches (t, not ts). /ws/z:
# card:landed matched by reap-hard's removal, the same effect. /ws/re: a card made again on the path (other HEAD) is
# not matched by the old card's removal. /ws/c: a close that closed nothing is not a match.
assert states == {"card:/ws/y": "matched", "tmpdir:/t/a": "matched", "stack:s9": "excess", "card:/ws/z": "matched",
                  "card:/ws/re": "excess", "card:/ws/c": "excess", "listener:77": "excess", "card:/ws/b": "excess",
                  "card:/ws/a": "matched", "card:/ws/kept": "excess", "card:/ws/new": "pending"}, states
assert d["by_rule"]["card:strict"] == {"matched": 1, "pending": 1, "excess": 1}, d["by_rule"]
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sweep.print_diff(d)
assert "넘침 stack:s9" in out.getvalue() and "넘침 6건" in out.getvalue(), out.getvalue()
# A removal does not answer a close-only plan, and an act long before the round is not its match.
(STATE / "reap-actions.jsonl").write_text(
    json.dumps({"t": T0 - 3600, "key": "tmpdir:/t/a", "ok": True, "rule": "trash:tmpdir"}) + "\n" +
    json.dumps({"t": T0 + 60, "key": "card:/ws/c", "ok": True, "rule": "card:landed"}) + "\n")
states = {r["key"]: r["state"] for r in sweep.diff(48, now=NOW + 1)["rows"]}
assert states["tmpdir:/t/a"] == "excess" and states["card:/ws/c"] == "excess", states
# The window opens when the round's scans started: a removal between the round's start and its scans was on the card
# before it (one made again on the same path at the same HEAD), not on the one the scans saw.
sweep.shadow_log().unlink()
sweep.append_keep(sweep.shadow_log(), {"t": T0 + 90, "round_t": T0, "seen_t": T0 + 60, "round": "s1", "mode": "shadow",
                                       "rows": [{"rule": "card:strict", "key": "card:/ws/again", "identity": {"head": "h"},
                                                 "decision": "act"}]})
(STATE / "reap-actions.jsonl").unlink()
with open(STATE / "reap-hard.jsonl", "w") as f:
    f.write(json.dumps(hard_row(T0 + 20, "card", "/ws/again", "터미널 닫음, " + reap.REMOVED, identity={"head": "h"})) + "\n")
assert {r["key"]: r["state"] for r in sweep.diff(48, now=NOW + 1)["rows"]} == {"card:/ws/again": "excess"}
with open(STATE / "reap-hard.jsonl", "a") as f:
    f.write(json.dumps(hard_row(T0 + 61, "card", "/ws/again", "터미널 닫음, " + reap.REMOVED, identity={"head": "h"})) + "\n")
assert {r["key"]: r["state"] for r in sweep.diff(48, now=NOW + 1)["rows"]} == {"card:/ws/again": "matched"}
# Per candidate: the scan that saw it started later than the round's scans did, and a removal in between is excluded.
sweep.shadow_log().unlink()
sweep.append_keep(sweep.shadow_log(), {"t": T0 + 90, "round_t": T0, "seen_t": T0 + 10, "round": "s2", "mode": "shadow",
                                       "rows": [{"rule": "card:strict", "key": "card:/ws/again", "identity": {"head": "h"},
                                                 "decision": "act", "seen_t": T0 + 70}]})
assert {r["key"]: r["state"] for r in sweep.diff(48, now=NOW + 1)["rows"]} == {"card:/ws/again": "excess"}
# A card made again on the same path at the same HEAD has another folder inode: the old card's removal is not proof.
sweep.shadow_log().unlink()
sweep.append_keep(sweep.shadow_log(), {"t": T0 + 90, "round_t": T0, "round": "s3", "mode": "shadow",
                                       "rows": [{"rule": "card:strict", "key": "card:/ws/again",
                                                 "identity": {"head": "h", "ino": 2}, "decision": "act"}]})
with open(STATE / "reap-hard.jsonl", "w") as f:
    f.write(json.dumps(hard_row(T0 + 100, "card", "/ws/again", "터미널 닫음, " + reap.REMOVED,
                                identity={"head": "h", "ino": 1})) + "\n")
assert {r["key"]: r["state"] for r in sweep.diff(48, now=NOW + 1)["rows"]} == {"card:/ws/again": "excess"}
d = tmp / "inode-card"
d.mkdir()
assert reap.dir_ino(str(d)) == os.stat(d).st_ino and reap.dir_ino(str(tmp / "gone-card")) is None
assert "ino" in reap.ACT_IDENTITY and "ino" in __import__("candidates").IDENTITY
# Two program folders of one name (another PROGRAMS_HOME) are two targets: the folder is in the identity.
assert reap.act_identity({"kind": "program", "name": "/a/demo", "dir": "/a/demo"}) == {"dir": "/a/demo"}
assert not sweep.same_target({"dir": "/a/demo"}, {"dir": "/b/demo"}) and sweep.same_target({"dir": "/a/demo"}, {"dir": "/a/demo"})
# The janitor's compose-stack act records the stack it really brings down, read again just before.
saved_cs, saved_run_c, saved_la = reap.compose_stacks, reap.run, reap.ledger_add
reap.compose_stacks = lambda: {"s1": {"last": 77.0}}
reap.run = lambda cmd, **k: ""
reap.ledger_add = lambda *a, **k: None
done = {}
reap.act({"kind": "compose-stack", "key": "compose-stack:s1", "name": "s1", "start": "77.0"}, [], done)
assert done == {"identity": {"start": "77.0"}}, done
for stacks_now, why in (({}, "이미 없음"), (None, "다시 읽지 못함"), ({"s1": {"last": 78.0}}, "바뀜")):
    reap.compose_stacks = lambda s=stacks_now: s
    done = {}
    got = reap.act({"kind": "compose-stack", "key": "compose-stack:s1", "name": "s1", "start": "77.0"}, [], done)
    assert got.startswith("건너뜀") and why in got and done == {}, (got, done)
reap.compose_stacks, reap.run, reap.ledger_add = saved_cs, saved_run_c, saved_la

# Same key, another target: a serve entry re-pointed to another local port, a state-file act on other files, a
# close of only some of the planned terminals.
sweep.shadow_log().unlink()
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)
shadow_round(T0, [("serve:dark", "serve:--https=8443", {"flag": "--https", "port": 8443, "local": 4500}),
                  ("state:rotate", "state-file:reorient", {"paths": ["/s/A.json"]}),
                  ("state:rotate", "state-file:other", {"paths": ["/s/C.json"]}),
                  ("card:close-idle", "card:/ws/ab", {"close": ["A", "B"]})])
with open(STATE / "reap-hard.jsonl", "w") as f:
    f.write(json.dumps(hard_row(T0 + 600, "serve", "--https=8443", "tailscale serve off",
                                identity={"flag": "--https", "port": 8443, "local": 4501})) + "\n")
reap.log_action({"key": "state-file:reorient", "kind": "state-file", "paths": ["/s/B.json"]}, "휴지통", True,
                "state:rotate")
reap.log_action({"key": "state-file:other", "kind": "state-file", "paths": ["/s/C.json", "/s/D.json"]}, "휴지통",
                True, "state:rotate")
reap.log_action({"key": "card:/ws/ab", "kind": "card"}, "터미널 1개 닫음, worktree 는 남김", True, "card:close-idle",
                ["B"])  # A was closed by hand before the act; only B was really closed
act = sweep.read_jsonl(STATE / "reap-actions.jsonl")
for a in act:
    a["t"] = T0 + 600
(STATE / "reap-actions.jsonl").write_text("".join(json.dumps(a) + "\n" for a in act))
states = {r["key"]: r["state"] for r in sweep.diff(48, now=NOW + 1)["rows"]}
assert states == {"serve:--https=8443": "excess", "state-file:reorient": "excess", "state-file:other": "matched",
                  "card:/ws/ab": "excess"}, states
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)

# A round recorded just inside the window, judged by patrol a little before it, still sees an act from before the
# window's start that is inside its own slack.
sweep.shadow_log().unlink()
(STATE / "reap-hard.jsonl").unlink()
since = NOW - 48 * 3600
sweep.append_keep(sweep.shadow_log(), {"t": since + 100, "round_t": since - 900, "round": "edge", "rows": [
    {"rule": "proc:orphan", "key": "proc:5", "identity": {"pid": 5, "start": "s"}, "decision": "act"}]})
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": since - 400, "key": "proc:5", "ok": True,
                                                      "effect": "proc:orphan", "identity": {"pid": 5, "start": "s"}})
                                          + "\n")
assert sweep.diff(48, now=NOW)["rows"][0]["state"] == "matched"
# A card removed just before the round and made again at the same path and HEAD: the old removal is not the answer.
sweep.shadow_log().unlink()
shadow_round(T0, [("card:landed", "card:/ws/reused", {"head": "h"})])
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 - 300, "key": "card:/ws/reused", "ok": True,
                                                      "effect": "card:remove", "identity": {"head": "h"}}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "excess"
# A log line that cannot be encoded, or a state folder that cannot be written, never stops the cleaner.
reap.log_action({"key": "user-folder:/D/file\udcff", "kind": "user-folder"}, "휴지통", True, "user-folder:trash")
saved_state = reap.STATE
reap.STATE = tmp / "no-such" / "\0bad"
reap.log_action({"key": "tmpdir:/x", "kind": "tmpdir"}, "휴지통", True, "trash:tmpdir")
reap.STATE = saved_state

# A plan with no identity (a scratch folder) is not answered by an act from before its round: that may have removed
# an earlier folder at the same path.
sweep.shadow_log().unlink()
shadow_round(T0, [("trash:tmpdir", "tmpdir:/t/reused")])
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 - 300, "key": "tmpdir:/t/reused", "ok": True,
                                                      "effect": "trash:tmpdir"}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "excess"
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 + 300, "key": "tmpdir:/t/reused", "ok": True,
                                                      "effect": "trash:tmpdir"}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)

# The same card (same folder inode) removed at a HEAD other than the plan's is the plan answered: its HEAD moved
# between the scan and the removal. Another inode, or no inode to compare, still needs the HEAD to agree.
sweep.shadow_log().unlink()
shadow_round(T0, [("card:landed", "card:/ws/moved", {"head": "h1", "ino": 7})])
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 + 300, "key": "card:/ws/moved", "ok": True,
                                                      "effect": "card:remove", "identity": {"head": "h2", "ino": 7}}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 + 300, "key": "card:/ws/moved", "ok": True,
                                                      "effect": "card:remove", "identity": {"head": "h2", "ino": 8}}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "excess"
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 + 300, "key": "card:/ws/moved", "ok": True,
                                                      "effect": "card:remove", "identity": {"head": "h2"}}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "excess"
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)

# A card already gone when the diff runs, with no old cleaner act on it, was cleaned outside the cleaners (a worker's
# end-session, a lead's `orca worktree rm`): counted apart, not as excess. One still on disk that nobody cleaned is
# excess as before, and so is a gone card the old cleaner acted on at another HEAD or inode (that is a mismatch).
sweep.gone_card = real_gone_card
alive = tmp / "alive-card"
alive.mkdir()
sweep.shadow_log().unlink()
shadow_round(T0, [("card:landed", f"card:{tmp}/gone-card", {"head": "g", "ino": 1}),
                  ("card:landed", f"card:{alive}", {"head": "a", "ino": 2}),
                  ("trash:tmpdir", f"tmpdir:{tmp}/gone-dir")])
d = sweep.diff(48, now=NOW + 1)
states = {r["key"]: r["state"] for r in d["rows"]}
assert states == {f"card:{tmp}/gone-card": "external", f"card:{alive}": "excess", f"tmpdir:{tmp}/gone-dir": "excess"}, states
assert [r["key"] for r in d["excess"]] == [f"card:{alive}", f"tmpdir:{tmp}/gone-dir"], d["excess"]
assert [r["key"] for r in d["external"]] == [f"card:{tmp}/gone-card"], d["external"]
assert d["by_rule"]["card:landed"] == {"matched": 0, "pending": 0, "excess": 1}, d["by_rule"]
assert sweep.verdict(d) == "excess"
out = io.StringIO()
with contextlib.redirect_stdout(out):
    sweep.print_diff(d)
assert f"외부 정리 card:{tmp}/gone-card" in out.getvalue() and "넘침 2건" in out.getvalue(), out.getvalue()
# Only a confirmed absence counts: a stat that fails for another reason (no permission) leaves the excess.
assert sweep.gone_card(f"card:{tmp}/gone-card") and not sweep.gone_card(f"card:{alive}")
real_lstat = os.lstat
def denied(p, *a, **k):
    raise PermissionError(13, "denied")
os.lstat = denied
try:
    assert not sweep.gone_card(f"card:{tmp}/gone-card")
finally:
    os.lstat = real_lstat
# Only gone cards: no excess, so the verdict is not "excess", and the external one is still reported.
sweep.shadow_log().unlink()
shadow_round(T0, [("card:landed", f"card:{tmp}/gone-card", {"head": "g"})])
d = sweep.diff(48, now=NOW + 1)
assert not d["excess"] and len(d["external"]) == 1 and sweep.verdict(d) != "excess", d
# Inside the lag it is still pending, not external: the old cleaner may yet remove it.
d = sweep.diff(48, now=T0 + 31)
assert [r["state"] for r in d["rows"]] == ["pending"] and not d["external"], d
# A gone card the old cleaner did remove is matched, not external.
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 + 300, "key": f"card:{tmp}/gone-card", "ok": True,
                                                      "effect": "card:remove", "identity": {"head": "g"}}) + "\n")
d = sweep.diff(48, now=NOW + 1)
assert [r["state"] for r in d["rows"]] == ["matched"] and not d["external"], d
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)

# A stack volume that is gone when the diff runs, with no old cleaner act on it, was removed by its stack's owner
# (a teardown or purge that ran between the round and the janitor): external, like a gone card. One docker still lists
# is excess as before, and so is one docker cannot be asked about.
sweep.gone_volume = real_gone_volume
real_inspect = sweep.volume_inspect
sweep.volume_inspect = lambda name: {"vol-gone": (1, "Error response from daemon: get vol-gone: no such volume"),
                                     "vol-here": (0, ""), "vol-down": (1, "Cannot connect to the Docker daemon")
                                     }.get(name, (124, ""))
assert sweep.gone_volume("stack-volume:vol-gone")
assert not sweep.gone_volume("stack-volume:vol-here")  # still there: a real leftover
assert not sweep.gone_volume("stack-volume:vol-down")  # another failure proves nothing
assert not sweep.gone_volume("stack-volume:vol-timeout")
assert not sweep.gone_volume("card:/ws/x") and not sweep.gone_volume("volume:vol-gone")  # only stack volumes
sweep.shadow_log().unlink()
shadow_round(T0, [("volume:prefixed", "stack-volume:vol-gone", {"created": 1}),
                  ("volume:prefixed", "stack-volume:vol-here", {"created": 2}),
                  ("volume:prefixed", "stack-volume:vol-down", {"created": 3})])
d = sweep.diff(48, now=NOW + 1)
states = {r["key"]: r["state"] for r in d["rows"]}
assert states == {"stack-volume:vol-gone": "external", "stack-volume:vol-here": "excess",
                  "stack-volume:vol-down": "excess"}, states
assert [r["key"] for r in d["external"]] == ["stack-volume:vol-gone"], d["external"]
assert d["by_rule"]["volume:prefixed"] == {"matched": 0, "pending": 0, "excess": 2}, d["by_rule"]
# Inside the lag it is pending, not external; one the old cleaner removed is matched.
d = sweep.diff(48, now=T0 + 31)
assert {r["state"] for r in d["rows"]} == {"pending"} and not d["external"], d
(STATE / "reap-actions.jsonl").write_text(json.dumps({"t": T0 + 300, "key": "stack-volume:vol-gone", "ok": True,
                                                      "effect": "volume:prefixed", "identity": {"created": 1}}) + "\n")
d = sweep.diff(48, now=NOW + 1)
assert {r["key"]: r["state"] for r in d["rows"]}["stack-volume:vol-gone"] == "matched" and not d["external"], d
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)
# Docker is asked once per target however many rounds named it, and never for one that is matched or pending.
asked = []
sweep.volume_inspect = lambda name: asked.append(name) or (0, "")
sweep.shadow_log().unlink()
for k in range(5):
    shadow_round(T0 + k * 60, [("volume:prefixed", "stack-volume:vol-here", {"created": 2})])
sweep.diff(48, now=NOW + 1)
assert asked == ["vol-here"], asked
asked.clear()
sweep.diff(48, now=T0 + 31)
assert asked == [], asked
# The real call: a timeout or a missing docker is "could not ask", not "gone".
sweep.volume_inspect = real_inspect
real_run = sweep.subprocess.run
def slow(cmd, **k):
    assert k["timeout"] == sweep.CMD_TIMEOUT_S, k
    raise sweep.subprocess.TimeoutExpired(cmd, k["timeout"])
sweep.subprocess.run = slow
try:
    assert sweep.volume_inspect("v") == (124, "") and not sweep.gone_volume("stack-volume:v")
    sweep.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker"))
    assert sweep.volume_inspect("v") == (124, "") and not sweep.gone_volume("stack-volume:v")
finally:
    sweep.subprocess.run = real_run
sweep.gone_volume = lambda key: False

# A record that does not carry the identity the plan names proves nothing (a log line from before identities).
assert not sweep.same_target({"head": "new"}, {}) and sweep.same_target({}, {"head": "x"})
assert sweep.answers({"rule": "card:strict", "identity": {"head": "h"}},
                     [{"effect": "card:remove", "identity": {"head": "h"}, "handles": None}])
assert not sweep.answers({"rule": "card:strict", "identity": {"head": "new"}},
                         [{"effect": "card:remove", "identity": {}, "handles": None}])

# Terminals really closed by an act that then failed still answer a close plan.
sweep.shadow_log().unlink()
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)
shadow_round(T0, [("card:close-idle", "card:/ws/p", {"close": ["A"]})])
reap.log_action({"key": "card:/ws/p", "kind": "card"}, RuntimeError("orca terminal close: B failed"), False,
                "card:close-idle", ["A"])
row = sweep.read_jsonl(STATE / "reap-actions.jsonl")[0]
assert not row["ok"] and row["effect"] is None and row["handles"] == ["A"], row
row["t"] = T0 + 600
(STATE / "reap-actions.jsonl").write_text(json.dumps(row) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"

# reap-hard's lag follows its configured interval.
sweep.shadow_log().unlink()
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)
shadow_round(T0, [("stack:gone", "stack:slow")])
with open(STATE / "reap-hard.jsonl", "w") as f:
    f.write(json.dumps(hard_row(T0 + 2700 * 2, "stack", "slow", "docker compose down(볼륨·이미지 유지)")) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "excess"  # past two 15-minute passes
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT), "interval": 2700}}))
assert sweep.lags()["hard"] == 2 * 2700 + 600 and sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT)}}))
(STATE / "reap-hard.jsonl").unlink()

# One compose project, two rules: reap-hard brought it down first, which answers the janitor's plan for it too.
sweep.shadow_log().unlink()
shadow_round(T0, [("stack:registered", "compose-stack:demo", {"start": "123.0"})])
with open(STATE / "reap-hard.jsonl", "w") as f:
    f.write(json.dumps(hard_row(T0 + 600, "stack", "demo", "docker compose down(볼륨·이미지 유지)",
                                identity={"start": "123.0"})) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"

# A close split over two runs (A, then B after a failure) answers a plan for both.
sweep.shadow_log().unlink()
shadow_round(T0, [("card:close-idle", "card:/ws/ab2", {"close": ["A", "B"], "head": "h"})])
(STATE / "reap-actions.jsonl").write_text(
    json.dumps({"t": T0 + 300, "key": "card:/ws/ab2", "ok": False, "effect": None, "identity": {"head": "h"},
                "handles": ["A"]}) + "\n" +
    json.dumps({"t": T0 + 3000, "key": "card:/ws/ab2", "ok": True, "effect": "card:close", "identity": {"head": "h"},
                "handles": ["B"]}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"
(STATE / "reap-actions.jsonl").write_text(
    json.dumps({"t": T0 + 300, "key": "card:/ws/ab2", "ok": False, "effect": None, "identity": {"head": "other"},
                "handles": ["A"]}) + "\n" +
    json.dumps({"t": T0 + 3000, "key": "card:/ws/ab2", "ok": True, "effect": "card:close", "identity": {"head": "h"},
                "handles": ["B"]}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "excess"  # A was closed on another card at that path
(STATE / "reap-actions.jsonl").unlink(missing_ok=True)

# Terminals reap-hard really closed answer a close plan, even when the removal after them failed.
sweep.shadow_log().unlink()
shadow_round(T0, [("card:close-idle", "card:/ws/h", {"close": ["A"], "head": "h"})])
with open(STATE / "reap-hard.jsonl", "w") as f:
    f.write(json.dumps({**hard_row(T0 + 600, "card", "/ws/h", None, identity={"head": "h"}), "error": "rm failed",
                        "handles": ["A"]}) + "\n")
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"
(STATE / "reap-hard.jsonl").unlink()

# A window with no decided round, or rounds covering only part of it, is "not yet", never a pass.
sweep.shadow_log().unlink()
d = sweep.diff(48, now=NOW)
assert sweep.verdict(d) == "not yet" and d["decided"] == 0
sweep.append_keep(sweep.shadow_log(), {"t": NOW - 3600, "round_t": NOW - 3600, "round": "x", "refused": "보호 목록"})
assert sweep.verdict(sweep.diff(48, now=NOW)) == "not yet"
for h in (47, 1):  # 46 hours with no round between: the shadow was down, not passing
    sweep.append_keep(sweep.shadow_log(), {"t": NOW - h * 3600, "round_t": NOW - h * 3600, "round": f"h{h}", "rows": []})
d = sweep.diff(48, now=NOW)
assert d["decided"] == 2 and sweep.verdict(d) == "not yet", d
sweep.shadow_log().unlink()
for k in range(48 * 2):  # every 30 minutes
    t = NOW - 48 * 3600 + 900 + k * 1800
    sweep.append_keep(sweep.shadow_log(), {"t": t, "round_t": t, "round": f"e{k}", "rows": []}, keep=200)
d = sweep.diff(48, now=NOW)
assert d["decided"] == 96 and d["max_gap_s"] <= 1800 and sweep.verdict(d) == "passed", d
assert sweep.verdict({**d, "max_gap_s": sweep.MAX_GAP_S + 100}) == "not yet"  # judged on seconds, not a rounded figure
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert sweep.main(["sweep.py", "diff", "--hours", "48"]) == 0
assert "통과(넘침 0, 지연 안이라 아직 대기 0건)" in out.getvalue(), out.getvalue()
# ================================================================ the real mode (PR D)
# Nothing below runs a real act, launchctl or tailscale: acts are a fake, launchctl is a script that records its arguments,
# HOME is a folder of the test's own.

for f in ("sweep-owner.json", "sweep-last.json", "sweep-fails.json", "sweep.jsonl", "reap-actions.jsonl",
          "reap-hard.jsonl"):
    (STATE / f).unlink(missing_ok=True)
sweep.shadow_log().unlink(missing_ok=True)
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import reap_hard  # noqa: E402

SERVE = cand("serve:dark", "serve:--https=8443", "serve", ["reap_hard_rest", "protect"],
             {"kind": "serve", "id": "--https=8443", "flag": "--https", "port": 8443, "local": 4501, "target": True})
STACK = cand("stack:gone", "stack:free", "stack", ["reap_hard_rest", "protect"],
             {"kind": "stack", "id": "free", "target": True})
CARD = cand("card:strict", "card:/ws/y", "card", ["reap_hard_cards", "protect", "terminals", "waits", "workers"],
            {"kind": "card", "id": "/ws/y", "head": "h", "handles": ["t2"], "target": True})
JANITOR = cand("trash:tmpdir", "tmpdir:/t/a", "tmpdir", ["reap_scan", "reap_scan:tmpdir"],
               {"kind": "tmpdir", "key": "tmpdir:/t/a", "name": str(tmp / "a")})
calls = []


def fake_act(c, protect_, res):
    calls.append(c["key"])
    res["result"] = "tailscale serve off" if c["rule"] == "serve:dark" else "docker compose down(볼륨·이미지 유지)"


def round_(rid, t=None, cands=(SERVE, STACK, CARD, JANITOR)):
    return verdicts(round=rid, t=t or NOW, candidates=[dict(c) for c in cands])


def no_state_files():
    return not any((STATE / f).exists() for f in ("sweep-last.json", "sweep-fails.json", "sweep.jsonl",
                                                   "sweep-owner.json"))


# dry-run: every rule decided as if owned, printed, and nothing written, locked or acted on
saved_keep = sweep.shadow_keep
sweep.shadow_keep = lambda item, protect_, cleaner: None
round_("d1")
out = sweep.run_round(dry=True, now=NOW, acter=fake_act)
assert out["mode"] == "dry-run" and out["acts"] == 4 and all(r["owner"] == "old" for r in out["rows"]), out
assert calls == [] and no_state_files()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    assert sweep.main(["sweep.py", "run", "--dry-run"]) == 0
text = buf.getvalue()
assert "- serve:dark serve:--https=8443: 처리" in text and "규칙 serve:dark: 처리 1 · 남김 0" in text, text
assert "trash:tmpdir" not in text and "소유 old" in text  # only reap-hard's rules are shown; the owner is named
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sweep.main(["sweep.py", "run", "--dry-run", "--rule", "stack:gone"])
assert "stack:gone" in buf.getvalue() and "serve:dark" not in buf.getvalue()
assert no_state_files()  # a dry-run twice on the same round: still nothing decided, nothing recorded
# It takes no run lock: no lock file is made, and a round holding the lock does not stop it.
(STATE / "sweep.lock").unlink(missing_ok=True)
assert sweep.run_round(dry=True, now=NOW)["acts"] == 4 and not (STATE / "sweep.lock").exists()
with sweep.run_lock() as held_by_other:
    assert held_by_other and sweep.run_round(dry=True, now=NOW)["acts"] == 4
sweep.shadow_keep = saved_keep

# real, nothing owned: no act, and sweep-last.json is still written (the "last good round" an owner waits on)
out = sweep.run_round(now=NOW, acter=fake_act)
last = json.loads((STATE / "sweep-last.json").read_text())
assert calls == [] and out["acted"] == 0 and last["round"] == "d1" and last["owned"] == [] and last["t"] == NOW, (out, last)
# the same round again refreshes the heartbeat while it is fresh, and never acts twice on it
out = sweep.run_round(now=NOW + 60, acter=fake_act)
assert out["refused"].endswith("이미 판정함") and calls == []
assert json.loads((STATE / "sweep-last.json").read_text())["t"] == NOW + 60
out = sweep.run_round(now=NOW + 7200, acter=fake_act)  # an hour-old round proves nothing: no heartbeat
assert "전 것" in out["refused"] and json.loads((STATE / "sweep-last.json").read_text())["t"] == NOW + 60

# own / release
assert sweep.set_owner("trash:tmpdir", True)[0] == 2  # not a rule of this stage
code, msg = sweep.set_owner("serve:dark", True)
own = json.loads((STATE / "sweep-owner.json").read_text())
assert code == 0 and own == {"generation": 1, "rules": {"serve:dark": "sweep"}, "interval": 1800.0}, (msg, own)
assert "첫 정상 회차" not in msg  # sweep-last.json exists already
assert sweep.set_owner("serve:dark", True) == (0, "serve:dark: 이미 sweep 소유(세대 1), 바꾼 것 없음")
assert json.loads((STATE / "sweep-owner.json").read_text())["generation"] == 1
(STATE / "sweep-owner.json").write_text(json.dumps({**own, "rules": {**own["rules"], "trash:tmpdir": "sweep"}}))
assert sweep.set_owner("stack:gone", True)[0] == 0
own = json.loads((STATE / "sweep-owner.json").read_text())
assert own["generation"] == 2 and own["rules"] == {"serve:dark": "sweep", "trash:tmpdir": "sweep", "stack:gone": "sweep"}
assert sweep.set_owner("stack:gone", False)[0] == 0
own = json.loads((STATE / "sweep-owner.json").read_text())
assert own["generation"] == 3 and own["rules"] == {"serve:dark": "sweep", "trash:tmpdir": "sweep"}  # others kept
assert "이미 옛 주체" in sweep.set_owner("card:strict", False)[1]
(STATE / "sweep-owner.json").write_text("{")  # a file that cannot be read is not overwritten
code, msg = sweep.set_owner("serve:dark", True)
assert code == 1 and (STATE / "sweep-owner.json").read_text() == "{" and "읽지 못해" in msg
(STATE / "sweep-owner.json").write_text(json.dumps(own))
(STATE / "sweep-last.json").rename(STATE / "x")  # no good round of its own yet: the message says who still acts
assert "첫 정상 회차" in sweep.set_owner("card:strict", True)[1]
(STATE / "x").rename(STATE / "sweep-last.json")
assert sweep.set_owner("card:strict", False)[0] == 0

# real, serve:dark owned: only its candidate is acted on; the janitor's, and the rules still reap-hard's, are not
round_("r3", t=NOW + 100)
out = sweep.run_round(now=NOW + 120, acter=fake_act)
assert calls == ["serve:--https=8443"], calls
logged = sweep.read_jsonl(sweep.real_log())
assert len(logged) == 1 and logged[0]["ok"] is True and logged[0]["effect"] == "serve:dark", logged
assert logged[0]["rule"] == "serve:dark" and logged[0]["identity"] == {"flag": "--https", "port": 8443, "local": 4501}
last = json.loads((STATE / "sweep-last.json").read_text())
assert last["round"] == "r3" and last["acted"] == 1 and last["owned"] == ["serve:dark"], last
# a second hand-over cannot be acted on until the sweep has a good round of its own: stale last → the old cleaner has it
calls.clear()
(STATE / "sweep-last.json").write_text(json.dumps({"t": NOW - 10 * 3600}))
round_("r4", t=NOW + 200)
out = sweep.run_round(now=NOW + 220, acter=fake_act)
assert out["acted"] == 0 and calls == [] and out["deferred"] is True
# ...but that pass wrote the sweep's first good round, so reap-hard leaves the rule now, and the same round is not
# recorded as done: the next pass acts on it instead of leaving it to nobody until the next patrol round.
assert guard.old_acts("serve:dark", guard.ownership(), NOW + 230)[0] is False
assert json.loads((STATE / "sweep-last.json").read_text())["round"] != "r4"
out = sweep.run_round(now=NOW + 240, acter=fake_act)
assert calls == ["serve:--https=8443"] and out["acted"] == 1 and out["deferred"] is False, out
assert json.loads((STATE / "sweep-last.json").read_text())["round"] == "r4"
calls.clear()

# the diff counts the sweep's own acts, so an owned rule's shadow rows do not read as excess
sweep.shadow_log().unlink(missing_ok=True)
shadow_round(T0, [("serve:dark", "serve:--https=9", {"flag": "--https", "port": 9, "local": 4502})])
sweep.append_keep(sweep.real_log(), {"t": T0 + 300, "key": "serve:--https=9", "kind": "serve", "rule": "serve:dark",
                                     "ok": True, "effect": "serve:dark",
                                     "identity": {"flag": "--https", "port": 9, "local": 4502}})
assert sweep.diff(48, now=NOW + 1)["rows"][0]["state"] == "matched"
sweep.shadow_log().unlink()

# failures: a target that errors twice in a row rests, and the run goes on
for f in ("sweep-last.json", "sweep.jsonl"):
    (STATE / f).unlink(missing_ok=True)
(STATE / "sweep-last.json").write_text(json.dumps({"t": NOW}))
(STATE / "sweep-fails.json").unlink(missing_ok=True)
calls.clear()


def failing_act(c, protect_, res):
    calls.append(c["key"])
    res["error"] = "tailscale: boom"


for n in (5, 6):
    round_(f"r{n}", t=NOW + n * 10)
    out = sweep.run_round(now=NOW + n * 10 + 5, acter=failing_act)
    assert out["failed"] == 1 and out["acted"] == 0, out
assert out["rested"] == ["serve:--https=8443"] and len(calls) == 2
round_("r7", t=NOW + 70)
out = sweep.run_round(now=NOW + 75, acter=failing_act)
assert len(calls) == 2 and out["rows"][0]["why"].startswith("연속 2회 실패"), out
(STATE / "sweep-fails.json").unlink()

# the cap, counted over the owned candidates only
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT)}, "sweep": {"max_per_round": 1}}))
sweep.set_owner("stack:gone", True)
calls.clear()
round_("r8", t=NOW + 80)
out = sweep.run_round(now=NOW + 85, acter=fake_act)
assert calls == ["serve:--https=8443"] and out["rows"][1]["why"] == "회차 상한 1건", (calls, out["rows"])
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT)}}))
sweep.set_owner("stack:gone", False)

# the run's budget ends a round mid-act: the act's line is written, sweep-last.json is not
before = (STATE / "sweep-last.json").read_text()


def budget_act(c, protect_, res):
    res["error"] = "Budget: "
    raise sweep.Budget()


round_("r9", t=NOW + 90)
try:
    sweep.run_round(now=NOW + 95, acter=budget_act)
    raise AssertionError("the budget was swallowed")
except sweep.Budget:
    pass
assert sweep.read_jsonl(sweep.real_log())[-1]["ok"] is False and (STATE / "sweep-last.json").read_text() == before
with sweep.run_lock() as got:
    assert got  # and the run lock was let go

# act_item: the lock, the owner read again inside it, the shared guards, then reap-hard's act
fake = []
saved_act = reap_hard.act
reap_hard.act = lambda item, cfg, protect_, done: (fake.append(item["id"]), done.update(identity={"port": 8443}),
                                                   "tailscale serve off")[2]
open_protect = {"port": set(), "pid": set(), "path": [], "project": set()}
(STATE / "sweep-owner.json").write_text(json.dumps({"generation": 5, "rules": {"serve:dark": "sweep"}}))
(STATE / "sweep-last.json").write_text(json.dumps({"t": time.time()}))
res = {}
sweep.act_item(SERVE, open_protect, res)
assert res == {"result": "tailscale serve off", "identity": {"port": 8443}} and fake == ["--https=8443"], res
res = {}
sweep.act_item(SERVE, {**open_protect, "port": {8443}}, res)
assert res["result"] == "건너뜀: 보호 포트" and len(fake) == 1, res
(STATE / "sweep-owner.json").write_text(json.dumps({"generation": 6, "rules": {}}))  # released after the round was read
res = {}
sweep.act_item(SERVE, open_protect, res)
assert res["result"].startswith("건너뜀") and "옛 주체" in res["result"] and len(fake) == 1, res
(STATE / "sweep-owner.json").write_text("{")
res = {}
sweep.act_item(SERVE, open_protect, res)
assert res["result"].startswith("건너뜀: 소유권 파일을 읽지 못함") and len(fake) == 1, res
(STATE / "sweep-owner.json").write_text(json.dumps({"generation": 7, "rules": {"serve:dark": "sweep"}}))
saved_hold = guard.hold
guard.hold = contextlib.contextmanager(lambda targets: (yield False))
res = {}
sweep.act_item(SERVE, open_protect, res)
guard.hold = saved_hold
assert res["result"] == "건너뜀: " + guard.LOCK_BUSY and len(fake) == 1, res
reap_hard.act = lambda item, cfg, protect_, done: (_ for _ in ()).throw(RuntimeError("tailscale: boom"))
res = {}
sweep.act_item(SERVE, open_protect, res)
assert res == {"error": "tailscale: boom"}, res


def closes_then_budget(item, cfg, protect_, done):
    done["closed"] = ["t2"]
    raise sweep.Budget()


reap_hard.act = closes_then_budget
(STATE / "sweep-owner.json").write_text(json.dumps({"generation": 8, "rules": {"card:strict": "sweep"}}))
res = {}
try:
    sweep.act_item(CARD, open_protect, res)
    raise AssertionError("the budget was swallowed")
except sweep.Budget:
    pass
assert res["handles"] == ["t2"] and res["error"].startswith("Budget"), res
reap_hard.act = saved_act
(STATE / "sweep-owner.json").unlink()

# settings: the `sweep` key lies over `reap-hard`, in the cleaners and in the printed carry-over
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"idle_minutes": 45, "serve_ranges": [[1, 2]],
                                                           "protect_file": str(PROTECT)}}))
block = sweep.carried_config()["sweep"]
assert block["idle_minutes"] == 45 and block["serve_ranges"] == [[1, 2]] and block["interval"] == 1800, block
assert block["workspaces"] == "~/orca/workspaces" and block["max_per_round"] == 50 and block["protect_file"] == str(PROTECT)
other = tmp / "other-protect.txt"
other.write_text("port 1\n")
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"idle_minutes": 45, "protect_file": str(PROTECT)},
                                             "sweep": {"idle_minutes": 10, "interval": 600, "protect_file": str(other)}}))
assert reap_hard.config()["idle_minutes"] == 10 and sweep.interval() == 600 and guard.protect_file() == str(other)
assert sweep.lags()["hard"] == 1800
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT)}, "sweep": []}))
assert reap_hard.config()["idle_minutes"] == 30  # a `sweep` of the wrong shape is none for reap-hard's own reads
try:
    guard.protect_file()
    raise AssertionError("a `sweep` of the wrong shape was read as a protect file")
except guard.NoProtect:
    pass
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT)}}))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    assert sweep.main(["sweep.py", "config"]) == 0
assert '"sweep": {' in buf.getvalue() and "고치지 않는다" in buf.getvalue()

# install: a fake HOME and a launchctl that only records what it was asked
home = tmp / "home"
home.mkdir()
saved_home = os.environ.get("HOME")
os.environ["HOME"] = str(home)
stub_log = tmp / "launchctl.log"
stub = tmp / "launchctl-stub"
stub.write_text(f'#!/bin/sh\necho "$@" >> {stub_log}\n[ "$1" = bootstrap ] && [ -n "$STUB_FAIL" ] && exit 5\nexit 0\n')
stub.chmod(0o755)
saved_plat, saved_lc, saved_sleep = sweep.PLATFORM, sweep.LAUNCHCTL, time.sleep
sweep.LAUNCHCTL = str(stub)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    sweep.PLATFORM = "linux"
    assert sweep.cmd_install(["--here"]) == 1 and "launchd" in err.getvalue()
    sweep.PLATFORM = "darwin"
    real_here = sweep.HERE
    sweep.HERE = tmp / "no-checkout" / "skills" / "reap-resources" / "scripts"  # no .git beside it
    assert sweep.cmd_install([]) == 1 and "메인 git 체크아웃이 아님" in err.getvalue()
    sweep.HERE = real_here
    saved_pf = PROTECT.read_text()
    PROTECT.rename(tmp / "protect.gone")  # without the protect file every pass stops: nothing is installed over reap-hard
    assert sweep.cmd_install(["--here"]) == 1 and "install 중단" in err.getvalue()
    (tmp / "protect.gone").rename(PROTECT)
assert not sweep.plist_path().exists() and not stub_log.exists()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    assert sweep.cmd_install(["--here", "--dry-run"]) == 0
assert "[dry-run]" in buf.getvalue() and not sweep.plist_path().exists() and not stub_log.exists()
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT), "interval": 600}}))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    assert sweep.cmd_install(["--here"]) == 0
plist = plistlib.loads(sweep.plist_path().read_bytes())
assert plist["Label"] == sweep.LABEL and plist["StartInterval"] == 600 and plist["ProgramArguments"][-1] == "tick"
assert plist["ProgramArguments"][-2].endswith("sweep.py") and plist["EnvironmentVariables"]["HOME"] == str(home)
assert plist["StandardOutPath"] == str(STATE / "sweep.log") and plist["RunAtLoad"] is True
asked = stub_log.read_text().splitlines()
assert asked[0].startswith("bootout gui/") and asked[1].startswith("bootstrap gui/") and asked[1].endswith("plist"), asked
assert '"sweep": {' in buf.getvalue() and "reap-hard.json" not in buf.getvalue()
assert str(home) in str(sweep.plist_path())  # nothing went to the real LaunchAgents folder
os.environ["STUB_FAIL"] = "1"
time.sleep = lambda s: None
err = io.StringIO()
with contextlib.redirect_stderr(err):
    assert sweep.cmd_install(["--here"]) == 1 and "bootstrap 실패" in err.getvalue()
del os.environ["STUB_FAIL"]
time.sleep = saved_sleep
assert sweep.cmd_uninstall() == 0 and not sweep.plist_path().exists()
assert stub_log.read_text().splitlines()[-1].startswith("bootout gui/")
sweep.PLATFORM, sweep.LAUNCHCTL = saved_plat, saved_lc
if saved_home is None:
    del os.environ["HOME"]
else:
    os.environ["HOME"] = saved_home
(tmp / "config.json").write_text(json.dumps({"reap-hard": {"protect_file": str(PROTECT)}}))

# tick: patrol, shadow, real round, then reap-hard without its own patrol; a step that fails leaves the next
steps, saved = [], (subprocess.run, sweep.run_shadow, sweep.run_round)


def fake_run(cmd, **kw):
    steps.append(cmd[1:] if cmd[0] == sys.executable else cmd)
    if "patrol" in cmd:
        raise subprocess.TimeoutExpired(cmd, 1)  # a patrol that hangs is given up on, and the rest still runs
    return subprocess.CompletedProcess(cmd, 0)


def run_tick(shadow=lambda *a, **k: steps.append("shadow") or {"round": "r"},
             real=lambda *a, **k: steps.append("real") or {"round": "r"}, run=fake_run):
    steps.clear()
    subprocess.run, sweep.run_shadow, sweep.run_round = run, shadow, real
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return sweep.tick()
    finally:
        subprocess.run, sweep.run_shadow, sweep.run_round = saved


# reap-hard goes before the real round, so a rule just handed over is still reap-hard's in that pass
code = run_tick()
assert code == 1, code  # the patrol that timed out shows in the exit code
assert len(steps) == 4 and steps[0][-2:] == ["patrol", "--if-due"] and steps[1] == "shadow" and steps[3] == "real", steps
assert steps[2][-2:] == ["run", "--no-patrol"] and steps[2][0].endswith("reap_hard.py"), steps
# a step that raises, or fails, leaves the others
code = run_tick(shadow=lambda *a, **k: (_ for _ in ()).throw(PermissionError("log")),
                run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
assert code == 1 and "real" in steps, (code, steps)
code = run_tick(real=lambda *a, **k: steps.append("real") or {"round": "r", "failed": 1},
                run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
assert code == 1 and steps == ["shadow", "real"], steps
code = run_tick(run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
assert code == 0 and steps == ["shadow", "real"], (code, steps)
code = run_tick(run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2 if "reap_hard.py" in cmd[1] else 0))
assert code == 2 and "real" in steps, (code, steps)
signal.alarm(0)

# the CLI: own and release go through main, an unknown rule is refused with the list
for f in ("sweep-owner.json",):
    (STATE / f).unlink(missing_ok=True)
buf, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
    assert sweep.main(["sweep.py", "own", "serve:dark"]) == 0
    assert sweep.main(["sweep.py", "release", "serve:dark"]) == 0
    assert sweep.main(["sweep.py", "own", "nope"]) == 2
assert "가능: card:strict" in err.getvalue() and "세대 2" in buf.getvalue(), (buf.getvalue(), err.getvalue())
print("ok")
