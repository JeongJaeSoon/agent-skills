"""Rules of reap_hard.py, and a pass with the machine replaced. Run: python3 scripts/test_reap_hard.py"""
import json, os, pathlib, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp / "state")
os.environ["AGENT_SKILLS_CONFIG"] = str(tmp / "config.json")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import reap_hard as rh  # noqa: E402

M = 60
NOW = 2_000_000_000.0

# The protect file: missing or unreadable stops everything.
for text, bad in ((None, "읽지 못함"), ("port x\n", ":1 을 읽지 못함"), ("rm -rf /\n", ":1 을 읽지 못함")):
    f = tmp / "protect.txt"
    f.unlink(missing_ok=True)
    if text is not None:
        f.write_text(text)
    try:
        rh.read_protect(f)
        raise AssertionError("read_protect accepted " + repr(text))
    except rh.NoProtect as e:
        assert bad in str(e), e
f.write_text("# keep\nport 4999  # stack\npid 42\npath /x/lead\nproject keepme\n\n")
P = rh.read_protect(f)
assert P == {"port": {4999}, "pid": {42}, "path": ["/x/lead"], "project": {"keepme"}}, P

# Cards: only a clean card whose merged or closed PR carried its HEAD, silent and quiet, with nobody's shell in it.
card = {"path": "/ws/app/done"}
facts = {"main": False, "pr": "acme/app#1", "head": "abc", "dirty": False, "unreadable": False, "regenerated": []}
merged = {"state": "MERGED", "headRefOid": "abc"}
quiet_terms = [{"handle": "term_1", "lastOutputAt": (NOW - 40 * M) * 1000}, {"handle": "term_2", "lastOutputAt": None}]


def card_says(c=card, f=facts, pr=merged, terms=quiet_terms, quiet=40 * M, people=(), holders=()):
    return rh.judge_card(c, f, pr, terms, quiet, NOW, 30 * M, P, list(people), list(holders))


assert card_says()[0] is True, card_says()
assert card_says(pr={"state": "CLOSED", "headRefOid": "abc"})[0] is True
assert card_says(quiet=None)[0] is True  # a card no Claude session ran in: the terminals decide
cases = {
    "메인 체크아웃 또는 고정된 카드": card_says(c={**card, "isPinned": True}),
    "보호 목록의 경로": card_says(c={"path": "/x/lead"}),
    "PR 없음": card_says(f={**facts, "pr": None}),
    "PR 을 읽지 못함(gh)": card_says(f={**facts, "pr": None, "pr_unread": True}),
    "상태를 읽지 못함": card_says(pr=None),
    "열린 PR": card_says(pr={"state": "OPEN", "headRefOid": "abc"}),
    "그 뒤 HEAD 가 바뀜": card_says(pr={"state": "MERGED", "headRefOid": "old"}),
    "커밋 안 된 변경": card_says(f={**facts, "dirty": True}),
    "보호 대상 프로세스": card_says(holders=[7]),
    "사람이 띄운 셸의 자식": card_says(people=[8]),
    "터미널 목록을 읽지 못함": card_says(terms=None),
    "출력이 30분 안에": card_says(terms=[{"handle": "t", "lastOutputAt": (NOW - 5 * M) * 1000}]),
    "세션 기록이 10분 전": card_says(quiet=10 * M),
}
for want, (ok, why) in cases.items():
    assert ok is False and want in why, (want, why)

# Stacks: down only when every working directory is gone; protected projects and ports stay.
stack = {"ports": {5173}, "dirs": {"/ws/app/done"}}
assert rh.judge_stack("v1", stack, lambda d: True, P, set()) == (True, "소유 worktree 가 없어짐: /ws/app/done")
assert rh.judge_stack("v1", {**stack, "dirs": {"/ws/app/done", "/ws/app/live"}}, lambda d: d.endswith("done"), P,
                      set())[0] is False
assert rh.judge_stack("v1", {**stack, "dirs": set()}, lambda d: True, P, set()) == (False, "작업 폴더 라벨 없음")
assert rh.judge_stack("keepme", stack, lambda d: True, P, set())[0] is False
assert rh.judge_stack("webui", stack, lambda d: True, P, {"webui"})[0] is False
assert rh.judge_stack("v1", {**stack, "ports": {4999}}, lambda d: True, P, set()) == (False, "보호 포트 4999")


def proc(pid, cmd, ppid=1, age=2 * 3600):
    return {"pid": pid, "ppid": ppid, "age": age, "start": "Mon Oct 26 00:00:00 2026", "cmd": cmd}


# Listeners: an orphaned dev server whose card is gone; never a pid or port on the list, never a person's.
rows = {9: proc(9, "node vite --port 4600"), 10: proc(10, "-zsh", ppid=0), 11: proc(11, "node server.js", ppid=10)}
gone_cwd = (lambda c: c[0] == "/gone"), (lambda p: "/ws/app/done" if p.startswith("/ws/app/done") else None)


def listener_says(r, port=4600, cwd=("/gone", 1), alive=frozenset()):
    return rh.judge_listener(r, port, cwd, *gone_cwd, alive, 30 * M, P, rows)


assert listener_says(rows[9]) == (True, "cwd /gone 가 사라짐")
assert listener_says(rows[9], cwd=("/ws/app/done/web", 1)) == (True, "cwd 의 카드 /ws/app/done 가 닫힘")
assert listener_says(rows[9], cwd=("/ws/app/done/web", 1), alive={"/ws/app/done"})[0] is False
assert listener_says(rows[9], port=4999)[0] is False
assert listener_says({**rows[9], "pid": 42})[0] is False
assert listener_says(rows[11])[0] is False  # a person's shell is its parent
assert listener_says(proc(12, "postgres -D /data"))[0] is False
assert listener_says(proc(13, "node vite", age=5 * M))[0] is False
assert listener_says(rows[9], cwd=None)[0] is False

# tailscale serve: preview ranges only, a listener gone two passes in a row, never a protected port.
status = {"TCP": {"443": {"HTTPS": True}, "4600": {"HTTPS": True}, "45000": {"TCPForward": "127.0.0.1:45001"}},
          "Web": {"h.ts.net:4600": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:4600"}}},
                  "h.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}},
                  "h.ts.net:13100": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:4999"}}}}}
entries = rh.serve_entries(status)
assert sorted(entries) == [("--https", 443, 8080), ("--https", 4600, 4600), ("--https", 13100, 4999),
                           ("--tcp", 45000, 45001)], entries
R = [(4500, 4699), (32768, 60999)]
assert rh.judge_serve(("--https", 4600, 4600), set(), {4600}, R, P)[0] is True
assert rh.judge_serve(("--https", 4600, 4600), set(), set(), R, P)[0] is False  # first dark pass: wait
assert rh.judge_serve(("--https", 4600, 4600), {4600}, {4600}, R, P)[0] is False
assert rh.judge_serve(("--https", 443, 8080), set(), {443}, R, P) == (False, "미리보기 포트 범위 밖")
assert rh.judge_serve(("--https", 13100, 4999), set(), {13100}, R, P) == (False, "보호 포트")
assert rh.judge_serve(("--tcp", 45000, 45001), set(), {45000}, R, P)[0] is True

# card_dir: only <root>/<repo>/<card>.
assert rh.card_dir("/ws/app/done/web/src", "/ws") == "/ws/app/done"
assert rh.card_dir("/ws/app", "/ws") is None and rh.card_dir("/elsewhere/x/y", "/ws") is None

# A pass: no protect file means nothing is scanned or done, and the brief line says it stopped.
calls = []
rh.scan = lambda *a, **k: calls.append("scan") or ([], [], set())
cfg = {**rh.DEFAULTS, "protect_file": str(tmp / "missing.txt")}
res, code = rh.run_pass(False, cfg, now=NOW)
assert code == 2 and calls == [] and "읽지 못함" in res["error"], res
assert "멈춤" in rh.last_line(now=NOW + 60, interval=900)

# With the file: targets act (or only log under --dry-run), failures are logged, the summary feeds the brief.
cfg["protect_file"] = str(f)
items = [{"kind": "card", "id": "/ws/app/done", "target": True, "why": "w"},
         {"kind": "stack", "id": "v1", "target": True, "why": "w"},
         {"kind": "serve", "id": "--https=4600", "target": False, "why": "k"}]
rh.scan = lambda *a, **k: ([dict(i) for i in items], ["n"], {4600})
acted = []


def fake_act(item, cfg):
    acted.append(item["id"])
    if item["kind"] == "stack":
        raise RuntimeError("docker down failed")
    return "done"


rh.act = fake_act
res, code = rh.run_pass(True, cfg, now=NOW)
assert code == 0 and acted == [] and res["acted"] == 2, res
log = [json.loads(line) for line in rh.LOG.read_text().splitlines()]
assert [r.get("result") for r in log[-2:]] == ["dry-run", "dry-run"] and all(r["dry_run"] for r in log[-2:])
assert "멈춤" in rh.last_line(now=NOW + 60, interval=900)  # a dry-run does not count as a pass
res, code = rh.run_pass(False, cfg, now=NOW)
assert code == 1 and acted == ["/ws/app/done", "v1"] and res["acted"] == 1 and res["failed"] == 1, res
log = [json.loads(line) for line in rh.LOG.read_text().splitlines()]
assert log[-1]["error"] == "docker down failed" and log[-2]["result"] == "done", log[-2:]
assert rh.read_last()["dark"] == [4600]
assert rh.last_line(now=NOW + 120, interval=900) == "🧹 reap-hard " + \
    __import__("time").strftime("%H:%M", __import__("time").localtime(NOW)) + "(2분 전) 닫음 1 · 실패 1 · 남김 1"
assert "주기를 넘김" in rh.last_line(now=NOW + 3 * 900 + 60, interval=900)

# The LaunchAgent runs `run` from this file every interval, with the tools on PATH.
s = rh.spec(900)
assert s["ProgramArguments"][-2:] == [str(pathlib.Path(rh.__file__).resolve()), "run"] and s["StartInterval"] == 900
assert "/usr/bin" in s["EnvironmentVariables"]["PATH"]
print("ok")
