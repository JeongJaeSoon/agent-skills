"""Rules of reap_hard.py, and a pass with the machine replaced. Run: python3 scripts/test_reap_hard.py"""
import contextlib, io, json, os, pathlib, sys, tempfile

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
# An idle Claude screen writes to its terminal every ~30 minutes with nothing new: with its transcript quiet, a write
# 12 minutes ago is not work. A Codex or plain shell terminal still needs the full 30 minutes.
idle_claude = [{"handle": "term_1", "agentIdentity": "claude", "lastOutputAt": (NOW - 12 * M) * 1000}]
assert card_says(terms=idle_claude)[0] is True, card_says(terms=idle_claude)
assert card_says(terms=[{**idle_claude[0], "agentIdentity": "codex"}])[0] is False
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
    "출력이 5분 안에": card_says(terms=[{"handle": "t", "agentIdentity": "claude", "lastOutputAt": (NOW - 2 * M) * 1000}]),
    "t 출력이 30분 안에": card_says(terms=[{"handle": "t", "agentIdentity": "claude", "lastOutputAt": (NOW - 12 * M) * 1000}],
                                 quiet=None),
    "세션 기록이 10분 전": card_says(quiet=10 * M),
    "셸을 돌리는 중(pid 40)": rh.judge_card(card, facts, merged, quiet_terms, 40 * M, NOW, 30 * M, P, [], [], [], None, [40]),
    "하위 카드 /ws/app/w1 가 남아 있음(리드)": rh.judge_card(card, facts, merged, quiet_terms, 40 * M, NOW, 30 * M, P,
                                                     [], [], ["/ws/app/w1"]),
}
for want, (ok, why) in cases.items():
    assert ok is False and want in why, (want, why)

# Stacks: down only when every working directory is gone; protected projects and ports stay.
stack = {"ports": {5173}, "dirs": {"/ws/app/done"}}
assert rh.judge_stack("v1", stack, lambda d: True, P, set()) == (True, "소유 worktree 가 없어짐: /ws/app/done")
assert rh.judge_stack("v1", {**stack, "dirs": {"/ws/app/done", "/ws/app/live"}}, lambda d: d.endswith("done"), P,
                      set())[0] is False
assert rh.judge_stack("v1", {**stack, "dirs": set()}, lambda d: True, P, set())[0] is False
# A container with no working-directory label, a relative label, or a protected path keeps the whole project.
assert rh.judge_stack("v1", {**stack, "nodir": 1}, lambda d: True, P, set()) == (False, "작업 폴더 라벨 없는 컨테이너가 있음")
assert rh.judge_stack("v1", {**stack, "dirs": {"app"}}, lambda d: True, P, set()) == (False, "상대 경로 작업 폴더 app")
assert rh.judge_stack("v1", {**stack, "dirs": {"/x/lead/web"}}, lambda d: True, P, set()) == (False, "보호 목록의 경로")
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
assert listener_says(rows[9], cwd=("/x/lead/web", 1)) == (False, "보호 목록의 경로")
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
closed = lambda port: False
K = rh.serve_key
assert rh.judge_serve(("--https", 4600, 4600), closed, {K(("--https", 4600, 4600))}, R, P)[0] is True
# The same public port pointed at another local port since the last pass starts over.
assert rh.judge_serve(("--https", 4600, 4601), closed, {K(("--https", 4600, 4600))}, R, P)[0] is False
assert rh.judge_serve(("--https", 4600, 4600), closed, set(), R, P)[0] is False  # first dark pass: wait
assert rh.judge_serve(("--https", 4600, 4600), lambda port: True, {K(("--https", 4600, 4600))}, R, P)[0] is False
assert rh.judge_serve(("--https", 443, 8080), closed, {K(("--https", 443, 8080))}, R, P) == (False, "미리보기 포트 범위 밖")
assert rh.judge_serve(("--https", 13100, 4999), closed, {K(("--https", 13100, 4999))}, R, P) == (False, "보호 포트")
assert rh.judge_serve(("--tcp", 45000, 45001), closed, {K(("--tcp", 45000, 45001))}, R, P)[0] is True

# A lead or orchestrator session alive in the card keeps it, however long it has been quiet: by its launch flags, or
# by a ROLE record of a session that wrote to this card's transcripts since the process started.
START = "Mon Oct 26 00:00:00 2026"
start_t = rh.lstart_epoch(START)
lead_rows = {
    20: proc(20, "-/bin/zsh -l", ppid=0), 21: {**proc(21, "claude --agent agent-skills:lead --name lead:x", ppid=20), "start": START},
    30: proc(30, "-/bin/zsh -l", ppid=0), 31: {**proc(31, "claude --permission-mode auto", ppid=30), "start": START},
}
projects, roles = tmp / "projects", tmp / "roles"
tdir = projects / "-ws-app-lead"
tdir.mkdir(parents=True)
roles.mkdir()
cwd = {20: "/ws/app/lead", 21: "/ws/app/lead", 30: "/ws/app/done", 31: "/ws/app/done"}
assert rh.coordinator_in("/ws/app/lead", lead_rows, cwd, projects, roles).endswith("--agent agent-skills:lead")
assert rh.coordinator_in("/ws/app/done", lead_rows, cwd, projects, roles) is None
orch = {**lead_rows, 21: {**lead_rows[21], "cmd": "claude --permission-mode auto"}}
assert rh.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) is None
(tdir / "sess-a.jsonl").write_text("{}")
os.utime(tdir / "sess-a.jsonl", (start_t + 600, start_t + 600))
for role, want in (("none", None), ("orchestrator", "세션 sess-a ROLE 선언"), ("lead", "세션 sess-a ROLE 선언")):
    (roles / "sess-a.json").write_text(json.dumps({"role": role}))
    assert rh.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) == want, role
os.utime(tdir / "sess-a.jsonl", (start_t - 3600, start_t - 3600))  # an older session of the card, not this process
assert rh.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) is None
# A role record or a process row it cannot read keeps the card; only a missing record means "no role".
os.utime(tdir / "sess-a.jsonl", (start_t + 600, start_t + 600))
for bad in ("{broken", "[]"):
    (roles / "sess-a.json").write_text(bad)
    assert "ROLE 기록을 읽지 못함" in rh.coordinator_in("/ws/app/lead", orch, cwd, projects, roles), bad
(roles / "sess-a.json").write_text(json.dumps({"role": "lead"}))
(roles / "sess-a.json").chmod(0)
try:
    unreadable = rh.coordinator_in("/ws/app/lead", orch, cwd, projects, roles)
finally:
    (roles / "sess-a.json").chmod(0o644)
assert os.geteuid() == 0 or "ROLE 기록을 읽지 못함(PermissionError)" in unreadable, unreadable
(roles / "sess-a.json").unlink()
assert rh.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) is None
assert rh.coordinator_in("/ws/app/lead", {}, cwd, projects, roles) == "pid 20 의 프로세스 행을 읽지 못함"
no_claude = {20: lead_rows[20], 21: {**lead_rows[21], "cmd": "node vite"}}
assert rh.coordinator_in("/ws/app/lead", no_claude, cwd, projects, roles) is None  # no live Claude: nothing to protect
# A session waiting on its own background work keeps its card: a shell child of a Claude in the card (a Bash call,
# a background shell, a Monitor). An MCP server or caffeinate child lives as long as the session and doesn't count.
busy_rows = {**lead_rows, 40: proc(40, "/bin/zsh -c source snapshot.sh && sleep 600", ppid=31),
             41: proc(41, "caffeinate -i -t 300", ppid=31), 42: proc(42, "bun run --cwd plugin start", ppid=31),
             43: proc(43, "/bin/zsh -c make", ppid=30)}
assert rh.busy_in("/ws/app/done", busy_rows, cwd) == [40]
assert rh.busy_in("/ws/app/done", {**busy_rows, 40: {**busy_rows[40], "ppid": 99}}, cwd) == []
assert rh.busy_in("/ws/app/lead", busy_rows, cwd) == []  # pid 31 is another card's session
assert rh.busy_in("/ws/app/done", {}, cwd) == []

ok, why = rh.judge_card(card, facts, merged, quiet_terms, 40 * M, NOW, 30 * M, P, [], [], [], "pid 21 --agent agent-skills:lead")
assert ok is False and why.startswith("리드·오케스트레이터 보호: pid 21"), why

# A bare call never cleans: usage and a non-zero exit, and the LaunchAgent names `run`.
ran, real_pass = [], rh.run_pass
rh.run_pass = lambda *a, **k: ran.append(a)
with contextlib.redirect_stderr(io.StringIO()):
    assert rh.main([]) == 2 and ran == []
rh.run_pass = real_pass

# Transcript quiet comes from the last timestamped entry: the untimed lines an idle session appends keep the mtime
# fresh without any work. With no timestamp in the tail, the mtime stands (reads as recent, keeps the card).
qdir = tmp / "qprojects" / "-ws-app-idle"
qdir.mkdir(parents=True)
t = qdir / "s1.jsonl"
t.write_text('{"type":"user","timestamp":"2026-10-25T07:20:36.064Z"}\n' + '{"type":"atis-latch","atis":""}\n' * 3)
os.utime(t, (NOW - 60, NOW - 60))
last = rh.reap.parse_ts("2026-10-25T07:20:36.064Z")
assert rh.last_entry_time(t) == last
assert rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/idle") == NOW - last
(qdir / "s2.jsonl").write_text('{"type":"atis-latch"}\n')
os.utime(qdir / "s2.jsonl", (NOW - 60, NOW - 60))
assert rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/idle") == 60  # untimed only: the mtime keeps it
assert rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/none") is None
assert rh.last_entry_time(qdir / "gone.jsonl") is None
# Anything it cannot read keeps the card: a newest timestamp that doesn't parse (never fall back to an older,
# quieter entry), and a transcript it may not open.
(qdir / "s2.jsonl").write_text('{"type":"user","timestamp":"2026-10-25T07:20:36Z"}\n{"type":"user","timestamp":"soon"}\n')
why = rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/idle")
assert isinstance(why, str) and "시각 'soon' 을 읽지 못함" in why, why
(qdir / "s2.jsonl").write_text('{"type":"user","timestamp":"2026-10-25T07:20:36Z"}\n')
(qdir / "s2.jsonl").chmod(0)
try:
    why = rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/idle")
finally:
    (qdir / "s2.jsonl").chmod(0o644)
assert os.geteuid() == 0 or why == "세션 기록 s2.jsonl 을 읽지 못함(PermissionError)", why
assert card_says(quiet="세션 기록 s2.jsonl 을 읽지 못함(PermissionError)") == (False, "세션 기록 s2.jsonl 을 읽지 못함(PermissionError)")

# card_dir: only <root>/<repo>/<card>.
assert rh.card_dir("/ws/app/done/web/src", "/ws") == "/ws/app/done"
assert rh.card_dir("/ws/app", "/ws") is None and rh.card_dir("/elsewhere/x/y", "/ws") is None

# A pass: no protect file means nothing is scanned or done, and the brief line says it stopped.
calls = []
rh.scan_cards = lambda *a, **k: calls.append("scan") or ([], [], {})
cfg = {**rh.DEFAULTS, "protect_file": str(tmp / "missing.txt")}
res, code = rh.run_pass(False, cfg, now=NOW)
assert code == 2 and calls == [] and "읽지 못함" in res["error"], res
assert "멈춤" in rh.last_line(now=NOW + 60, interval=900)

# With the file: targets act (or only log under --dry-run), failures are logged, the summary feeds the brief.
cfg["protect_file"] = str(f)
items = [{"kind": "card", "id": "/ws/app/done", "target": True, "why": "w"},
         {"kind": "stack", "id": "v1", "target": True, "why": "w"},
         {"kind": "serve", "id": "--https=4600", "target": False, "why": "k"}]
seen_closed = []
rh.scan_cards = lambda *a, **k: ([dict(i) for i in items[:1]], ["n"], {})


def fake_rest(cfg, protect, dark_before, closed, m, now):
    seen_closed.append(list(closed))
    return [dict(i) for i in items[1:]], [], {"--https=4600>4600"}


rh.scan_rest = fake_rest
acted = []


def fake_act(item, cfg, protect):
    acted.append(item["id"])
    if item["kind"] == "stack":
        raise RuntimeError("docker down failed")
    return outcome[0]


outcome = ["건너뜀: 다시 보니 열린 PR acme/app#1"]


rh.act = fake_act
res, code = rh.run_pass(True, cfg, now=NOW)
assert code == 0 and acted == [] and res["acted"] == 2, res
assert seen_closed[-1] == ["/ws/app/done"]  # a dry-run plans the rest as if the card were closed
log = [json.loads(line) for line in rh.LOG.read_text().splitlines()]
assert [r.get("result") for r in log[-2:]] == ["dry-run", "dry-run"] and all(r["dry_run"] for r in log[-2:])
assert "멈춤" in rh.last_line(now=NOW + 60, interval=900)  # a dry-run does not count as a pass
res, code = rh.run_pass(False, cfg, now=NOW)
assert code == 1 and acted == ["/ws/app/done", "v1"] and res["acted"] == 0 and res["skipped"] == 1, res
# A close skipped on the second look frees neither its stack nor its servers.
assert seen_closed[-1] == [], seen_closed
outcome[0] = "터미널 닫음, worktree 제거"
res, code = rh.run_pass(False, cfg, now=NOW)
assert seen_closed[-1] == ["/ws/app/done"] and res["acted"] == 1 and res["failed"] == 1, (seen_closed, res)
log = [json.loads(line) for line in rh.LOG.read_text().splitlines()]
assert log[-1]["error"] == "docker down failed" and log[-2]["result"] == outcome[0], log[-2:]
assert rh.read_last()["dark"] == ["--https=4600>4600"]
assert rh.last_line(now=NOW + 120, interval=900) == "🧹 reap-hard " + \
    __import__("time").strftime("%H:%M", __import__("time").localtime(NOW)) + "(2분 전) 닫음 1 · 실패 1 · 남김 1"
assert "주기를 넘김" in rh.last_line(now=NOW + 3 * 900 + 60, interval=900)

# The LaunchAgent runs `run` from this file every interval, with the tools on PATH.
s = rh.spec(900)
assert s["ProgramArguments"][-2:] == [str(pathlib.Path(rh.__file__).resolve()), "run"] and s["StartInterval"] == 900
assert "/usr/bin" in s["EnvironmentVariables"]["PATH"]
print("ok")
