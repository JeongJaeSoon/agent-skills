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
start_t = rh.reap.lstart_epoch(START)
lead_rows = {
    20: proc(20, "-/bin/zsh -l", ppid=0), 21: {**proc(21, "claude --agent agent-skills:lead --name lead:x", ppid=20), "start": START},
    30: proc(30, "-/bin/zsh -l", ppid=0), 31: {**proc(31, "claude --permission-mode auto", ppid=30), "start": START},
}
projects, roles = tmp / "projects", tmp / "roles"
tdir = projects / "-ws-app-lead"
tdir.mkdir(parents=True)
roles.mkdir()
cwd = {20: "/ws/app/lead", 21: "/ws/app/lead", 30: "/ws/app/done", 31: "/ws/app/done"}
assert rh.reap.coordinator_in("/ws/app/lead", lead_rows, cwd, projects, roles).endswith("--agent agent-skills:lead")
assert rh.reap.coordinator_in("/ws/app/done", lead_rows, cwd, projects, roles) is None
orch = {**lead_rows, 21: {**lead_rows[21], "cmd": "claude --permission-mode auto"}}
assert rh.reap.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) is None
(tdir / "sess-a.jsonl").write_text("{}")
os.utime(tdir / "sess-a.jsonl", (start_t + 600, start_t + 600))
for role, want in (("none", None), ("orchestrator", "세션 sess-a ROLE 선언"), ("lead", "세션 sess-a ROLE 선언")):
    (roles / "sess-a.json").write_text(json.dumps({"role": role}))
    assert rh.reap.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) == want, role
os.utime(tdir / "sess-a.jsonl", (start_t - 3600, start_t - 3600))  # an older session of the card, not this process
assert rh.reap.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) is None
# A role record or a process row it cannot read keeps the card; only a missing record means "no role".
os.utime(tdir / "sess-a.jsonl", (start_t + 600, start_t + 600))
for bad in ("{broken", "[]"):
    (roles / "sess-a.json").write_text(bad)
    assert "ROLE 기록을 읽지 못함" in rh.reap.coordinator_in("/ws/app/lead", orch, cwd, projects, roles), bad
(roles / "sess-a.json").write_text(json.dumps({"role": "lead"}))
(roles / "sess-a.json").chmod(0)
try:
    unreadable = rh.reap.coordinator_in("/ws/app/lead", orch, cwd, projects, roles)
finally:
    (roles / "sess-a.json").chmod(0o644)
assert os.geteuid() == 0 or "ROLE 기록을 읽지 못함(PermissionError)" in unreadable, unreadable
(roles / "sess-a.json").unlink()
assert rh.reap.coordinator_in("/ws/app/lead", orch, cwd, projects, roles) is None
tdir.chmod(0)  # a transcript folder it cannot list may hold the ROLE session
try:
    unreadable = rh.reap.coordinator_in("/ws/app/lead", orch, cwd, projects, roles)
finally:
    tdir.chmod(0o755)
assert os.geteuid() == 0 or unreadable == "세션 기록 폴더를 읽지 못함(PermissionError)", unreadable
assert rh.reap.coordinator_in("/ws/app/lead", {}, cwd, projects, roles) == "pid 20 의 프로세스 행을 읽지 못함"
no_claude = {20: lead_rows[20], 21: {**lead_rows[21], "cmd": "node vite"}}
assert rh.reap.coordinator_in("/ws/app/lead", no_claude, cwd, projects, roles) is None  # no live Claude: nothing to protect
# A session waiting on its own background work keeps its card: a shell child of a Claude in the card (a Bash call,
# a background shell, a Monitor). An MCP server or caffeinate child lives as long as the session and doesn't count.
busy_rows = {**lead_rows, 40: proc(40, "/bin/zsh -c source snapshot.sh && sleep 600", ppid=31),
             41: proc(41, "caffeinate -i -t 300", ppid=31), 42: proc(42, "bun run --cwd plugin start", ppid=31),
             43: proc(43, "/bin/zsh -c make", ppid=30)}
assert rh.cardguard.busy_shells("/ws/app/done", busy_rows, cwd) == [40]
assert rh.cardguard.busy_shells("/ws/app/done", {**busy_rows, 40: {**busy_rows[40], "ppid": 99}}, cwd) == []
assert rh.cardguard.busy_shells("/ws/app/lead", busy_rows, cwd) == []  # pid 31 is another card's session
assert rh.cardguard.busy_shells("/ws/app/done", {}, cwd) == []

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
assert rh.reap.last_entry_time(t) == last
assert rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/idle") == NOW - last
(qdir / "s2.jsonl").write_text('{"type":"atis-latch"}\n')
os.utime(qdir / "s2.jsonl", (NOW - 60, NOW - 60))
assert rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/idle") == 60  # untimed only: the mtime keeps it
assert rh.transcript_quiet(tmp / "qprojects", NOW)("/ws/app/none") is None
assert rh.reap.last_entry_time(qdir / "gone.jsonl") is None
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

# act_card: under the card's lock, judged again, guard()ed, and only the terminals seen at the scan close (never --all).
cg = rh.cardguard
ran, removed = [], []
saved_ac = rh.orca, rh.card_verdict, rh.read_machine, rh.reap.run, rh.reap.act, cg.observe
rh.orca = lambda *a: {"worktrees": [{"path": "/ws/app/done"}]}
rh.read_machine = lambda now: {}
rh.card_verdict = lambda w, m, cfg, protect, now: (True, "w", {"head": "abc", "regenerated": []})
rh.reap.run = lambda cmd, **k: ran.append(cmd) or "{}"
rh.reap.act = lambda item, notes: removed.append(item["name"]) or "worktree 제거"
ok_facts = {"patrol": False, "head": "abc", "terminals": [{"handle": "t1"}], "waits": {}, "screens": {}}
cg.observe = lambda path: ok_facts
plan = {"kind": "card", "id": "/ws/app/done", "head": "abc", "handles": ["t1"]}
done = {}
assert rh.act(plan, rh.DEFAULTS, P, done) == "터미널 1개 닫음, worktree 제거", ran
assert done == {"closed": ["t1"], "identity": {"head": "abc"}}  # what run_pass logs: handles closed, card acted on
assert ran == [["orca", "terminal", "close", "--terminal", "t1", "--json"]] and removed == ["/ws/app/done"]
assert not any("--all" in c for c in ran)
ran.clear(), removed.clear()
# A card folder made again since the scan (another inode) is left; the one acted on is the one logged.
saved_ino = rh.reap.dir_ino
rh.reap.dir_ino = lambda p: 2
assert rh.act({**plan, "ino": 1}, rh.DEFAULTS, P) == "건너뜀: 판정 뒤 카드 폴더가 다시 만들어짐" and not ran and not removed
cg.observe = lambda path: {**ok_facts, "ino": 2}
done = {}
rh.act({**plan, "ino": 2}, rh.DEFAULTS, P, done)
assert done["identity"] == {"head": "abc", "ino": 2} and removed == ["/ws/app/done"], done
ran.clear(), removed.clear()
# Made again between closing its terminals and removing it: the removal is left.
looks = iter([{**ok_facts, "ino": 2}, {**ok_facts, "ino": 3}])
cg.observe = lambda path: next(looks)
got = rh.act({**plan, "ino": 2}, rh.DEFAULTS, P)
assert "삭제는 건너뜀" in got and "다시 만들어짐" in got and not removed, got
cg.observe = lambda path: ok_facts
rh.reap.dir_ino = saved_ino
ran.clear(), removed.clear()
for facts_now, why in (({**ok_facts, "patrol": True}, "순찰"), ({**ok_facts, "terminals": [{"handle": "t1"}, {"handle": "t9"}]}, "새 터미널"),
                       ({**ok_facts, "terminals": None}, "터미널 목록"), ({**ok_facts, "head": "zzz"}, "HEAD")):
    cg.observe = lambda path, f=facts_now: f
    got = rh.act(plan, rh.DEFAULTS, P)
    assert got.startswith("건너뜀") and why in got and not ran and not removed, (why, got)
cg.observe = lambda path: ok_facts
assert "계획에 없음" in rh.act({k: v for k, v in plan.items() if k != "handles"}, rh.DEFAULTS, P) and not ran
# Another cleaner's process holds the card's lock (inside this process the same lock is re-entrant).
import subprocess  # noqa: E402
lock = rh.guard.lock_file("card", rh.reap.real("/ws/app/done"))
lock.parent.mkdir(parents=True, exist_ok=True)
holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f, fcntl.LOCK_EX); "
                           "print('held', flush=True); sys.stdin.read()", str(lock)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert holder.stdout.readline().strip() == "held"
assert rh.act(plan, rh.DEFAULTS, P) == "건너뜀: 다른 정리가 이 카드를 처리 중" and not ran
holder.stdin.close()
holder.wait(10)
# A terminal opened between the close and the removal stops the removal.
looks = iter([ok_facts, {**ok_facts, "terminals": [{"handle": "t1"}, {"handle": "t9"}]}])
cg.observe = lambda path: next(looks)
got = rh.act(plan, rh.DEFAULTS, P)
assert "삭제는 건너뜀" in got and "새 터미널 t9" in got and ran and not removed, got
rh.orca, rh.card_verdict, rh.read_machine, rh.reap.run, rh.reap.act, cg.observe = saved_ac

# scan_cards: a card whose `orca worktree rm` was refused is listed as a hand-off with its one command, not as a target;
# a card patrol keeps as parked (v2 `activity`) is kept too. Both read the same way the guard before a close reads them.
import rm_refused  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    root = os.path.realpath(td)
    cards_ = {n: os.path.join(root, "app", n) for n in ("refused", "parked", "plain")}
    for c in cards_.values():
        os.makedirs(c)
    saved_sc = rh.orca, rh.card_verdict, rh.read_machine, rh.reap.STATE, rh.reap.patrol_states
    state = pathlib.Path(root) / "state"
    rh.reap.STATE = state
    rm_refused.add(cards_["refused"], state)
    rh.reap.patrol_states = lambda now=None, activity=False: {cards_["parked"]: "parked"} if activity else {}
    rh.read_machine = lambda now: {"cwd_pairs": {}, "cwd_of": {}, "listening": {}}
    rh.orca = lambda *a: {"worktrees": [{"path": p, "hostId": "local"} for p in cards_.values()]}
    rh.card_verdict = lambda w, m, cfg, protect, now: (True, "깨끗함", {"head": "h", "regenerated": [], "handles": []})
    got = {i["id"]: i for i in rh.scan_cards({"workspaces": root}, None, 0)[0]}
    assert [got[cards_[n]]["target"] for n in ("refused", "parked", "plain")] == [False, False, True], got
    assert "사람에게 넘김" in got[cards_["refused"]]["why"] and rm_refused.command(cards_["refused"]) in got[cards_["refused"]]["why"]
    assert "parked" in got[cards_["parked"]]["why"] or "작업 중" in got[cards_["parked"]]["why"], got[cards_["parked"]]
    (state / rm_refused.NAME).write_text("{not json\n")
    got = {i["id"]: i for i in rh.scan_cards({"workspaces": root}, None, 0)[0]}
    assert not got[cards_["plain"]]["target"] and "거부 기록을 읽지 못함" in got[cards_["plain"]]["why"], got
    rh.orca, rh.card_verdict, rh.read_machine, rh.reap.STATE, rh.reap.patrol_states = saved_sc

# Stopped containers and labeled volumes: off without their settings, one exact target each, the shared rules of reap.py.
saved_lo = rh.reap.leftovers, rh.reap.remove_leftover, rh.reap.compose_stacks, rh.pw.tailscale_cli, rh.guard.settings, rh.always_on
rh.reap.compose_stacks, rh.pw.tailscale_cli = lambda: {}, lambda c: None
rh.always_on = lambda: {"always-on"}
machine = {"rows": {}, "cwd_pairs": {}, "listening": {}}
seen = []


def fake_leftovers(cfg, hours, now, notes, keep, ports):
    seen.append((cfg, hours, keep, ports))
    return [{"kind": "stopped-container", "key": "stopped-container:cid1", "cid": "cid1", "name": "example-verify-1", "target": True,
             "why": "w", "start": "s", "created": "c"},
            {"kind": "labeled-volume", "key": "labeled-volume:v1", "name": "v1", "target": True, "why": "w", "created": 1.0}]


rh.reap.leftovers = fake_leftovers
rh.guard.settings = lambda: {"reap": {"hours": 2, "labeled_volumes": {"namespace": "example.agent"}}}
got = rh.scan_rest(rh.DEFAULTS, P, set(), [], machine, NOW)[0]
assert seen[-1][1] == 2.0 and seen[-1][2] == {"keepme", "always-on"} and seen[-1][3] == {4999}, seen
assert rh.guard.targets(got[0]) == [("container", "cid1")] and rh.guard.targets(got[1]) == [("volume", "v1")]
rh.guard.settings = lambda: None  # a settings file that cannot be read turns both off for the pass
seen.clear()
got, notes = rh.scan_rest(rh.DEFAULTS, P, set(), [], machine, NOW)[:2]
assert not seen and not got and "멈춘 컨테이너" in notes[0], notes
rh.guard.settings = lambda: {"reap": {}}
rh.always_on = lambda: (_ for _ in ()).throw(ValueError("설정 읽지 못함"))  # the always-on list unreadable: same
got = rh.scan_rest(rh.DEFAULTS, P, set(), [], machine, NOW)[0]
assert not seen and not got
removed = []
rh.reap.remove_leftover = lambda item, cfg, protect_path: removed.append((item["id"], cfg, protect_path)) or "docker rm"
assert rh.act({"kind": "container", "id": "cid1", "cid": "cid1", "name": "x"}, rh.DEFAULTS, P) == "docker rm"
assert rh.act({"kind": "volume", "id": "v1", "name": "v1"}, rh.DEFAULTS, P) == "docker rm", removed
assert removed[-1] == ("v1", {}, rh.DEFAULTS["protect_file"]), "the file reap-hard requires, read again before the rm"
rh.reap.leftovers, rh.reap.remove_leftover, rh.reap.compose_stacks, rh.pw.tailscale_cli, rh.guard.settings, rh.always_on = saved_lo

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
    m["stacks"] = {"v1": {"dirs": set()}}
    return [dict(i) for i in items[1:]], [], {"--https=4600>4600"}


rh.scan_rest = fake_rest
acted = []


def fake_act(item, cfg, protect, done=None):
    acted.append(item["id"])
    if item["kind"] == "stack":
        raise RuntimeError("docker down failed")
    return outcome[0]


outcome = ["건너뜀: 다시 보니 열린 PR acme/app#1"]


rh.act = fake_act
guard_item = rh.guard.item
rh.guard.item = lambda *a, **k: None  # the shared guards have their own tests below
noted = []
rh.reap.note_stack_dirs = noted.append
res, code = rh.run_pass(True, cfg, now=NOW)
assert code == 0 and acted == [] and res["acted"] == 2, res
assert not noted, "a dry-run writes nothing"
assert seen_closed[-1] == ["/ws/app/done"]  # a dry-run plans the rest as if the card were closed
log = [json.loads(line) for line in rh.LOG.read_text().splitlines()]
assert [r.get("result") for r in log[-2:]] == ["dry-run", "dry-run"] and all(r["dry_run"] for r in log[-2:])
assert "멈춤" in rh.last_line(now=NOW + 60, interval=900)  # a dry-run does not count as a pass
res, code = rh.run_pass(False, cfg, now=NOW)
assert code == 1 and acted == ["/ws/app/done", "v1"] and res["acted"] == 0 and res["skipped"] == 1, res
# A close skipped on the second look frees neither its stack nor its servers.
assert seen_closed[-1] == [], seen_closed
assert noted == [{"v1": {"dirs": set()}}], noted


def bad_record(stacks):
    raise TypeError("worktree path is null")


rh.reap.note_stack_dirs = bad_record  # a record that fails ends neither the pass nor the rows after it
acted.clear()
res = rh.run_pass(False, cfg, now=NOW)[0]
assert acted == ["/ws/app/done", "v1"] and any("ledger" in n for n in res["notes"]), res
acted[:] = ["/ws/app/done", "v1"]
rh.reap.note_stack_dirs = noted.append
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
