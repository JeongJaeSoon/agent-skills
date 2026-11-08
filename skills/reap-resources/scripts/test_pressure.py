"""Levels, the round's reap-or-report path and its notify dedupe, with every outside call faked.
Run: python3 test_pressure.py"""
import os, pathlib, sys, tempfile

os.environ["AGENT_SKILLS_STATE"] = tempfile.mkdtemp()
os.environ["ORCH_FLEET_STATE"] = tempfile.mkdtemp()
os.environ["AGENT_SKILLS_PATROL"] = "off"
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pressure as p

cfg = dict(p.DEFAULTS)
OK = {"cpus": 18, "load5": 9.0, "load_per_cpu": 0.5, "mem_free_pct": 60, "vm_pressure": 1, "swap_gb": 1.0,
      "disk_free_gb": 200.0, "containers": 10}  # measured now; the round's samples come in through snapshot()

# Levels: the worst metric wins; lower-is-worse metrics compare the other way; an unread metric never counts.
assert p.judge(OK, cfg) == ("ok", [])
assert p.judge({**OK, "load_per_cpu": 1.4}, cfg) == ("warn", ["load_per_cpu 1.4 ≥ 1.3"])
assert p.judge({**OK, "load_per_cpu": 1.4, "mem_free_pct": 9}, cfg)[0] == "danger"
assert p.judge({**OK, "disk_free_gb": 50}, cfg) == ("warn", ["disk_free_gb 50 ≤ 50"])
assert p.judge({**OK, "containers": None, "swap_gb": None}, cfg) == ("ok", [])

# False alarms seen on the reference machine: a leak's cumulative swap with nothing swapping out, the kernel sitting at
# pressure level 2 for days, 2x load while a third of the CPU idles, many idle containers. Warn at most.
LEAK = {**OK, "swap_gb": 56.0, "swapout_pps": 0.0, "vm_pressure": 2, "containers": 45, "docker_mem_pct": 33}
assert p.judge(LEAK, cfg)[0] == "warn", p.judge(LEAK, cfg)
assert p.judge({**OK, "load_per_cpu": 2.4, "cpu_idle_pct": 30}, cfg) == \
    ("warn", ["load_per_cpu 2.4 ≥ 2 (CPU idle 30% > 10%)"])
# A container count is shown, never judged: mostly idle containers are no load. The Docker VM's CPU warns instead.
assert p.judge({**OK, "containers": 99}, cfg) == ("ok", [])
assert p.judge({**OK, "docker_cpu_pct": 55.0}, cfg) == ("warn", ["docker_cpu_pct 55 ≥ 50"])
assert p.judge({**OK, "docker_cpu_pct": 100.0}, cfg)[0] == "warn"
# Misses the old rule had or would hide: real swapping, a saturated CPU, the kernel's critical level, a full Docker VM.
assert p.judge({**LEAK, "swapout_pps": 2000}, cfg)[0] == "danger"
assert p.judge({**OK, "load_per_cpu": 2.4, "cpu_idle_pct": 0}, cfg)[0] == "danger"
assert p.judge({**OK, "load_per_cpu": 2.4}, cfg)[0] == "danger"  # idle unknown: load alone decides
assert p.judge({**OK, "vm_pressure": 4}, cfg)[0] == "danger"
assert p.judge({**OK, "docker_mem_pct": 92}, cfg)[0] == "danger"
assert p.judge({**OK, "swap_growth_gb_h": 4.5}, cfg) == ("warn", ["swap_growth_gb_h 4.5 ≥ 2"])
assert p.blocked_by({**OK, "disk_free_gb": 5, "swap_gb": 60}, cfg) == ["disk_free_gb"]
# A danger of none turns it off, whichever way the metric points; a null where a number belongs falls back.
assert p.config([("swap_gb", "8,none")])["swap_gb"] == [8.0, None]
assert p.judge({**OK, "disk_free_gb": 500}, p.config([("disk_free_gb", "50,none")]))[0] == "ok"
assert p.judge({**OK, "disk_free_gb": 5}, p.config([("disk_free_gb", "50,inf")]))[0] == "warn"
assert p.judge({**OK, "load_per_cpu": 2.4, "cpu_idle_pct": 30}, {**cfg, "load_idle_pct": None})[0] == "warn"

# Samples between rounds: rates over the gap since the last counters; a reboot or a long gap gives no rate.
prev = {"t": 1000.0, "boot": 5, "swapouts": 100, "cpu": [10, 10, 80, 0]}
cur = {"t": 1180.0, "boot": 5, "swapouts": 100 + 180 * 400, "cpu": [40, 40, 100, 0]}
assert p.rates(prev, cur, cfg) == {"swapout_pps": 400.0, "cpu_idle_pct": 25.0}
assert p.rates({**prev, "boot": 4}, cur, cfg) == {"swapout_pps": None, "cpu_idle_pct": None}
assert p.rates({**prev, "t": 1180.0 - 4 * 180}, cur, cfg) == {"swapout_pps": None, "cpu_idle_pct": None}
assert p.rates(None, cur, cfg) == {"swapout_pps": None, "cpu_idle_pct": None}
# A round run by hand seconds after the LaunchAgent's keeps the 3-minute sample instead of a 10-second one.
kept = {"counters": prev, "sample": {"t": 1000.0, "cpu_idle_pct": 40.0}}
assert p.take_sample(kept, 1010.0, cfg) == {}
hist = [[0.0, 50.0], [1800.0, 51.0]]
assert p.swap_growth(hist, 3600.0, 54.5) == 4.5 and p.swap_growth(hist[-1:], 3000.0, 54.5) is None
assert [t for t, _ in p.swap_history(hist, 3700.0 + 3600, 60.0)] == [3700.0 + 3600]  # an hour old drops out

# One snapshot for hook, check and round: cheap metrics now, the round's sampled ones only while fresh.
sample = {"t": 5000.0, "swapout_pps": 2000.0, "cpu_idle_pct": 5.0, "docker_mem_pct": 40.0, "swap_growth_gb_h": 0.1}
snap = p.snapshot(cfg, {"sample": sample}, now=5000.0 + 6 * 60, measure_fn=lambda: dict(OK))
assert snap["swapout_pps"] == 2000.0 and p.judge(snap, cfg)[0] == "danger"
snap = p.snapshot(cfg, {"sample": sample}, now=5000.0 + 8 * 60, measure_fn=lambda: dict(OK))
assert snap["swapout_pps"] is None and snap["sample_age_min"] == 8 and p.judge(snap, cfg)[0] == "ok"
assert "샘플 8분 전" in p.summary(snap)
# CPU idle read where the caller judges replaces the sample's, stale or fresh: a round run late under the very load it
# measures leaves load judged alone otherwise.
snap = p.snapshot(cfg, {"sample": sample}, now=5000.0 + 8 * 60, measure_fn=lambda: {**OK, "load_per_cpu": 2.4},
                  idle_fn=lambda: lambda: 30.0)
assert snap["cpu_idle_pct"] == 30.0 and snap["idle_live"] and p.judge(snap, cfg)[0] == "warn"
assert "CPU idle 30%(지금 잰 값)" in p.summary(snap)
snap = p.snapshot(cfg, {"sample": sample}, now=5000.0 + 60, measure_fn=lambda: {**OK, "load_per_cpu": 2.4},
                  idle_fn=lambda: lambda: 2.0)
assert snap["cpu_idle_pct"] == 2.0 and p.judge(snap, cfg)[0] == "danger"
snap = p.snapshot(cfg, {"sample": sample}, now=5000.0 + 60, measure_fn=lambda: dict(OK), idle_fn=lambda: lambda: None)
assert snap["cpu_idle_pct"] == 5.0 and "idle_live" not in snap  # no ticks: the fresh sample stands
ticks = iter([[100, 100, 800, 0], [130, 130, 840, 0]])
slept = []
assert p.live_idle(1.0, ticks=lambda: next(ticks), sleep=slept.append, clock=lambda: 0.0) == 40.0 and slept == [1.0]
# What runs between start and finish counts toward the window: 1.5 s of reading leaves 0.5 s of a 2 s window.
ticks, clock = iter([[0, 0, 0, 0], [10, 0, 10, 0]]), iter([10.0, 11.5])
finish = p.idle_start(2.0, ticks=lambda: next(ticks), sleep=slept.append, clock=lambda: next(clock))
assert finish() == 50.0 and slept[-1] == 0.5
assert p.live_idle(1.0, ticks=lambda: None, sleep=lambda s: None) is None
ticks = iter([[100, 100, 800, 0], [90, 100, 800, 0]])
assert p.live_idle(1.0, ticks=lambda: next(ticks), sleep=lambda s: None) is None  # a counter gone backwards
if p.cpu_ticks():  # this machine's own counters: a window under a second is raised to one, so it always sees a change
    assert 0 <= p.live_idle(0.0) <= 100

# Docker usage: one `docker stats` gives the VM's memory and CPU share; anything unreadable gives None for both.
def fake_docker(cmd, timeout=None):
    if cmd[:2] == ["docker", "info"]:
        return f"{16 << 30} 18\n"
    assert "{{.CPUPerc}}" in cmd[-1], cmd
    return "180.5%\t1GiB / 15.6GiB\n9.5%\t512MiB / 15.6GiB\n--\t0B / 0B\n"
assert p.docker_usage(fake_docker) == {"docker_mem_pct": 9.4, "docker_cpu_pct": 10.6}
assert p.docker_usage(lambda c, timeout=None: None) == {"docker_mem_pct": None, "docker_cpu_pct": None}
assert p.docker_usage(lambda c, timeout=None: "0 18" if c[1] == "info" else "") == \
    {"docker_mem_pct": None, "docker_cpu_pct": None}

# Containers: a pod counts once with its pause container, kube-system not at all, always-on services apart.
PS = ("stack\t\t\tstack-db-1\nstack\t\t\tstack-web-1\n\t\t\tlonely\nlf\t\t\tlf-web-1\nlf\t\t\tlf-db-1\n\t\t\twebui\n"
      "\tpr-1\tu1\tk8s_POD_api-x_pr-1_u1_0\n\tpr-1\tu1\tk8s_api_api-x_pr-1_u1_0\n\tpr-1\tu1\tk8s_init_api-x_pr-1_u1_0\n"
      "\tpr-1\tu2\tk8s_POD_db-y_pr-1_u2_0\n\tpr-1\tu2\tk8s_db_db-y_pr-1_u2_0\n"
      "\tkube-system\tu3\tk8s_POD_coredns-z_kube-system_u3_0\n\tkube-system\tu3\tk8s_coredns_coredns-z_kube-system_u3_0\n"
      "\tsvc\tu4\tk8s_POD_s_svc_u4_0\n\tsvc\tu4\tk8s_s_s_svc_u4_0\n\n")
assert p.count_containers(PS) == (9, 0)
assert p.count_containers(PS, ["lf", "webui", "svc"]) == (5, 4)
VM_STAT = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:      100.\nPages active:    1000000.\n"
           "Pages wired down:      500000.\nAnonymous pages:      800000.\n")
def fake_ps(cmd, timeout=None):
    if cmd == ["vm_stat"]:
        return VM_STAT
    assert cmd[:3] == ["docker", "ps", "--format"] and "io.kubernetes.pod.uid" in cmd[3], cmd
    return PS
m = p.measure(run=fake_ps, always_on=["lf"]) if p.shutil.which("docker") else None
assert m is None or (m["containers"], m["always_on"]) == (7, 2), m
assert m is None or (m["mem_active_wired_gb"], m["mem_anon_wired_gb"]) == (22.9, 19.8), m
m = p.measure(docker=False, run=fake_ps)  # no docker needed for the memory fields
assert (m["mem_active_wired_gb"], m["mem_anon_wired_gb"]) == (22.9, 19.8) and m["containers"] is None, m
m = p.measure(docker=False, run=lambda cmd, timeout=None: None)  # vm_stat failing leaves them None, never raises
assert (m["mem_active_wired_gb"], m["mem_anon_wired_gb"]) == (None, None), m
assert p.vm_used_gb(VM_STAT.replace("16384", "4096")) == (5.7, 5.0)
assert p.summary({**OK, "always_on": 2}).endswith("컨테이너 10개(상시 2개 제외)")
assert p.summary({**OK, "always_on": 0}).endswith("컨테이너 10개")
assert p.config([("always_on", "lf,webui")])["always_on"] == ["lf", "webui"]

assert p.swap_gb("total = 3072.00M  used = 1785.31M  free = 1286.69M  (encrypted)") == 1.74
assert p.swap_gb("total = 0.00M  used = 2.50G  free = 0") == 2.5 and p.swap_gb("") is None
assert p.vm_used_gb(VM_STAT) == (22.9, 19.8)  # (1.0M+0.5M) and (0.8M+0.5M) pages of 16 KiB
assert p.vm_used_gb("Pages active: 1.\n") == (None, None) and p.vm_used_gb(None) == (None, None)
assert p.mem_mb("406MiB ") == 406 and p.mem_mb("1.5GiB") == 1536 and p.mem_mb("--") == 0

# --set overrides take the [warn, danger] pair; safe_kinds a list; an unknown key stops.
c = p.config([("load_per_cpu", "0.1,0.2"), ("safe_kinds", "orphan,nope"), ("renotify_minutes", "5")])
assert c["load_per_cpu"] == [0.1, 0.2] and c["renotify_minutes"] == 5.0
assert p.safe_kinds(c) == ["orphan"], p.safe_kinds(c)  # a kind reap does not know is dropped
try:
    p.config([("bogus", "1")])
    raise AssertionError("unknown key accepted")
except SystemExit:
    pass

assert p.card_of("/w/app/card-a/sub", ["/w/app", "/w/app/card-a"]) == "/w/app/card-a"
assert p.card_of("/elsewhere", ["/w/app"]) is None and p.card_of(None, ["/w"]) is None

# The plan: a target in a card with a live terminal is spared (and the plan file says so); unreadable Orca or cwds reap nothing.
import json, subprocess
plan = pathlib.Path(os.environ["AGENT_SKILLS_STATE"]) / "plan.json"
def scan_out(*items):
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps({"items": list(items), "notes": []}), "")
orphan = {"kind": "orphan", "key": "proc:5", "target": True, "pid": 5, "cmd": "/usr/bin/node /x/srv.js", "name": "node",
          "why": "cwd 사라짐", "procs": 1, "rss": 1, "cpu": 0, "age": 1}
stack = {"kind": "compose-stack", "key": "stack:s", "target": True, "name": "s", "why": "PR 머지됨",
         "links": {"worktree": "/w/done"}}
cards = [{"path": "/w/live", "live": True}, {"path": "/w/done", "live": False}]
got, notes = p.plan_targets(["orphan", "compose-stack"], plan, run=scan_out(orphan, stack), cards=cards,
                            cwd_of={5: "/w/live/apps/docs"})
assert [i["key"] for i in got] == ["stack:s"] and any("남김" in n for n in notes), (got, notes)
saved = {i["key"]: i for i in json.loads(plan.read_text())["items"]}
assert not saved["proc:5"]["target"] and "활동 중인 카드 live" in saved["proc:5"]["why"] and saved["stack:s"]["target"]
got, notes = p.plan_targets(["orphan"], plan, run=scan_out(dict(orphan)), cards=[], cwd_of={})
assert len(got) == 1  # no Orca cards: nothing to spare
saved_cwds = p.reap.cwds
p.reap.cwds = lambda: None
assert p.plan_targets(["orphan"], plan, run=scan_out(dict(orphan)), cards=[])[0] is None
p.reap.cwds = saved_cwds
saved_cards = p.orca_cards
p.orca_cards = lambda: None
assert p.plan_targets(["orphan"], plan, run=scan_out(dict(orphan)), cwd_of={})[0] is None
p.orca_cards = saved_cards
assert p.plan_targets([], plan) == ([], ["safe_kinds 중 reap 이 아는 종류가 없음"])

# Docker grouping: containers of one compose project add up; a non-compose container stands alone.
def fake_docker(cmd, timeout=None):
    if cmd[1] == "ps":
        return ("aaaaaaaaaaaa1\tstack\t/w/card\tstack-db-1\nbbbbbbbbbbbb2\tstack\t/w/card\tstack-web-1\n"
                "cccccccccccc3\t\t\tlonely\n")
    return "aaaaaaaaaaaa\t50.0%\t100MiB / 15GiB\nbbbbbbbbbbbb\t30.0%\t1GiB / 15GiB\ncccccccccccc\t90%\t10MiB / 1GiB\n"
groups = p.compose_projects(fake_docker)
assert [(g["project"], g["containers"], round(g["cpu"]), g["mem_mb"]) for g in groups] == \
    [("lonely", 1, 90, 10), ("stack", 2, 80, 1124)], groups

# Round: ok does nothing; warn reaps the plan's targets and says what went; nothing reaped sends nothing.
sent, lines = [], []
def run_round(metrics, targets, reaped=(), consumers=([], []), dry=False, now=1000.0, sample=None, send_fn_ok=True,
              **over):
    lines.clear()
    return p.round_once({**cfg, "notify_to": "run:orch", **over}, dry, out=lines.append, now=now,
                        measure_fn=lambda: metrics,
                        sample_fn=lambda st, now: {"sample": {"t": now, **sample}} if sample else {},
                        plan_fn=lambda kinds, path: (targets, []),
                        reap_fn=lambda path: list(reaped), consumers_fn=lambda c: consumers,
                        send_fn=lambda *a: sent.append(a) or send_fn_ok)

target = {"kind": "orphan", "key": "proc:42", "target": True, "name": "vite", "why": "cwd 사라짐", "pid": 42,
          "procs": 1, "rss": 1, "cpu": 0, "age": 1}
assert run_round(OK, [target]) == 0 and not sent and p.load_state()["level"] == "ok"
WARN = {**OK, "load_per_cpu": 1.4}
assert run_round(WARN, [], now=2000.0) == 1 and not sent  # warn with nothing to reap: no message
assert run_round(WARN, [target], now=2100.0) == 1 and not sent  # still warn within rescan_minutes: no scan
assert any("이어지는 중" in line for line in lines), lines
assert run_round(WARN, [target], reaped=["- orphan pid 42 vite: 종료함", "- 건너뜀 x"], now=2000.0 + 11 * 60) == 1
assert len(sent) == 1 and sent[0][0] == "run:orch" and sent[0][3] == "status", sent
assert "정리함 1개" in sent[0][1] and "orphan pid 42" in sent[0][1] and "load_per_cpu" in sent[0][1], sent[0][1]
assert p.load_state()["scan_t"] == 2000.0 + 11 * 60

# Danger with nothing to reap: the top consumers go out once, and again only after renotify_minutes.
sent.clear()
DANGER = {**OK, "load_per_cpu": 2.5}
procs = [{"pid": 7, "cls": "ours-working", "cpu": 400, "rss": 1 << 30, "cmd": "OrbStack Helper", "card": None}]
projects = [{"project": "stack", "containers": 12, "cpu": 80, "mem_mb": 2000, "dir": "/w/card", "card": "/w/card"}]
assert run_round(DANGER, [], consumers=(procs, projects), now=5000.0) == 2
assert len(sent) == 1 and sent[0][3] == "status" and "위험" in sent[0][1] and "pid 7" in sent[0][1] and "카드 card" in sent[0][2], sent
assert "종료하지 않았다" in sent[0][2]
assert run_round(DANGER, [], consumers=(procs, projects), now=5600.0) == 2 and len(sent) == 1
assert run_round(DANGER, [], consumers=(procs, projects), now=5000.0 + 31 * 60) == 2 and len(sent) == 2

# #368: reap's exit code and its "did nothing" lines. A non-zero exit counts nothing as cleaned (its other lines may
# be real acts, but the round cannot tell which); "- 아무것도 하지 않았다" and a close-only card with no terminal are
# not cleanups either. Before this, all of them were counted and a message said "정리함".
DID = "- card app/x: PR 7 merged: worktree 는 orca worktree rm 으로 제거"
NOTHING = ["- 아무것도 하지 않았다: 보호 목록을 읽지 못함", "- card app/y: 터미널 0개 닫음, worktree 는 남김"]
assert p.reaped_lines([DID] + NOTHING) == [DID], p.reaped_lines([DID] + NOTHING)
assert p.reaped_lines([f"{p.REAP_FAILED} 1", DID]) == []
# Codex round 2: reap prints "- <label>: <result>", so a skip, a gone process or a refusal sits in the result part.
IN_RESULT = ["- card app/x: 건너뜀: 사람에게 넘김: worktree rm 이 거부된 카드", "- orphan pid 42 vite: 사라졌거나 다른 프로세스",
             "- orphan pid 43 vite: 거부됨: [Errno 1] Operation not permitted"]
assert p.reaped_lines([DID] + IN_RESULT) == [DID], p.reaped_lines([DID] + IN_RESULT)
# Round 3: no terminal closed and the removal skipped is nothing done; one that closed terminals did something.
SKIPPED = "- card app/w: 터미널 0개 닫음, 삭제는 건너뜀: worktree rm 거부 기록을 읽지 못함"
CLOSED = "- card app/v: 터미널 2개 닫음, 삭제는 건너뜀: 판정 뒤 HEAD 가 바뀜"
assert p.reaped_lines([DID, SKIPPED, CLOSED]) == [DID, CLOSED], p.reaped_lines([DID, SKIPPED, CLOSED])
RACED = "- card app/u: 터미널 0개 닫음, 건너뜀: 사람에게 넘김: worktree rm 이 거부된 카드"  # reap-hard's wording
assert p.reaped_lines([DID, RACED]) == [DID] and not p.reap.acted(RACED.split(": ", 1)[1])
class Done:
    def __init__(self, code, out):
        self.returncode, self.stdout, self.stderr = code, out, ""
assert p.reap_plan("plan.json", run=lambda *a, **k: Done(0, DID + "\n")) == [DID]
bad = p.reap_plan("plan.json", run=lambda *a, **k: Done(1, "- 아무것도 하지 않았다: x\n"))
assert bad[0] == f"{p.REAP_FAILED} 1" and p.reaped_lines(bad) == [], bad
sent.clear()
assert run_round(WARN, [target], reaped=[f"{p.REAP_FAILED} 1", DID], now=9000.0) == 1 and not sent, sent
assert any(line.startswith(p.REAP_FAILED) for line in lines), lines
assert run_round(WARN, [target], reaped=NOTHING, now=9000.0 + 11 * 60) == 1 and not sent, sent

# Danger where reap skipped every target still reports the consumers.
sent.clear()
run_round(OK, [])
assert run_round(DANGER, [target], reaped=["- 건너뜀 x"], consumers=(procs, projects), now=7000.0) == 2
assert len(sent) == 1 and "위험" in sent[0][1], sent

# A failed send (a LaunchAgent has no sender terminal) is not retried every round: the danger lands on the dashboard's
# Needs-you list once, and leaves it when the machine is back to ok.
sent.clear()
inbox = pathlib.Path(os.environ["ORCH_FLEET_STATE"]) / "inbox-external.jsonl"
run_round(OK, [])
p.round_once({**cfg, "notify_to": "run:orch"}, False, out=lines.append, now=9000.0, measure_fn=lambda: DANGER,
             sample_fn=lambda st, now: {}, plan_fn=lambda k, path: ([], []), reap_fn=None,
             consumers_fn=lambda c: (procs, projects), send_fn=lambda *a: False)
rows = [json.loads(x) for x in inbox.read_text().splitlines()]
assert [(r["op"], r["key"], r["type"]) for r in rows] == [("add", "pressure:danger", "pc_pressure")], rows
assert "위험" in rows[0]["title"] and p.load_state()["danger_t"] == 9000.0
run_round(DANGER, [], consumers=(procs, projects), now=9060.0)
assert not sent, sent
run_round(WARN, [], now=9120.0)  # out of danger, if not yet ok: the danger row goes
assert json.loads(inbox.read_text().splitlines()[-1])["op"] == "resolve"
run_round(OK, [], now=9180.0)
assert len(inbox.read_text().splitlines()) == 2  # resolved once

# A scan that failed still counts as the round the warn deny asked for: warn alone holds nothing.
run_round(OK, [])
p.round_once({**cfg, "notify_to": None}, False, out=lines.append, now=9300.0, measure_fn=lambda: WARN,
             sample_fn=lambda st, now: {}, plan_fn=lambda k, path: (None, ["reap scan 실패"]), reap_fn=None,
             consumers_fn=None, send_fn=None)
assert p.load_state()["scan_t"] == 9300.0

# The round saves its sample before the sweep, so the hook reads it even while reap runs for minutes.
seen = {}
def slow_plan(kinds, path):
    seen.update(p.load_state())
    return [], []
p.round_once(cfg, False, out=lines.append, now=9400.0, measure_fn=lambda: DANGER,
             sample_fn=lambda st, now: {"sample": {"t": now, "swapout_pps": 1.0}}, plan_fn=slow_plan,
             reap_fn=None, consumers_fn=lambda c: ([], []), send_fn=lambda *a: True)
assert seen["sample"]["t"] == 9400.0 and seen["t"] == 9400.0, seen

# Swap growing for half an hour sends one leak note per renotify_minutes, whatever the level.
sent.clear()
run_round({**OK, "swap_gb": 30.0}, [], now=20000.0, sample={"swap_growth_gb_h": 4.5})
run_round({**OK, "swap_gb": 31.0}, [], now=20180.0, sample={"swap_growth_gb_h": 4.5})
assert len(sent) == 1 and "누수" in sent[0][1] and "4.5GB" in sent[0][1], sent
run_round({**OK, "swap_gb": 30.0}, [], now=22000.0, sample={"swap_growth_gb_h": 4.5}, send_fn_ok=False)
assert json.loads(inbox.read_text().splitlines()[-1])["key"] == "pressure:swap-growth"
run_round({**OK, "swap_gb": 30.0}, [], now=22180.0, sample={"swap_growth_gb_h": 0.1})
assert json.loads(inbox.read_text().splitlines()[-1]) | {"at": 0} == \
    {"v": 1, "op": "resolve", "key": "pressure:swap-growth", "at": 0}
run_round({**OK, "swap_gb": 30.0}, [], now=22360.0, sample={"swap_growth_gb_h": 4.5},
          swap_growth_gb_h=[None, None])  # a growth note turned off

# Dry run: prints the plan and the message, reaps, sends and records nothing.
sent.clear()
before = p.load_state()
assert run_round(WARN, [target], reaped=["- should not run"], dry=True, now=99999.0) == 1
assert not sent and p.load_state() == before
assert any("정리 예정 1개" in line for line in lines) and any("dry-run" in line for line in lines), lines
run_round(WARN, [], now=100000.0)
before = p.load_state()
assert run_round(WARN, [target], dry=True, now=100060.0) == 1 and p.load_state() == before  # nothing saved

# A scan that failed reaps nothing and, at danger, still reports consumers.
lines.clear()
p.round_once(cfg, True, out=lines.append, measure_fn=lambda: DANGER, sample_fn=lambda st, now: {}, plan_fn=lambda k, path: (None, ["reap scan 실패"]),
             reap_fn=None, consumers_fn=lambda c: (procs, []), send_fn=None)
assert any("읽지 못해" in line for line in lines) and any("pid 7" in line for line in lines), lines

# History: every saved round adds one point (dry runs none); a file whose oldest point is an hour past the 24 h it
# keeps is cut back to 24 h; the report reads only the asked hours and never measures.
hist = p.HISTORY
hist.unlink(missing_ok=True)
run_round(OK, [], now=200000.0)
run_round(WARN, [], now=200180.0)
run_round(WARN, [], dry=True, now=200360.0)
rows = p.history_read()
assert [(r["t"], r["level"], r["load_per_cpu"], r["queued"]) for r in rows] == \
    [(200000.0, "ok", 0.5, 0), (200180.0, "warn", 1.4, 0)], rows
assert set(rows[0]) == {"t", "level", "queued", *p.HISTORY_FIELDS}
run_round(OK, [], now=200000.0 + p.HISTORY_KEEP_S + 3500)  # oldest 23h58m+1h past: not yet cut
assert len(p.history_read()) == 3
run_round(OK, [], now=200000.0 + p.HISTORY_KEEP_S + 3700)
assert [r["t"] for r in p.history_read()] == [200000.0 + p.HISTORY_KEEP_S + 3500, 200000.0 + p.HISTORY_KEEP_S + 3700]
with hist.open("a") as f:
    f.write("not json\n")
assert len(p.history_read()) == 2  # a torn line costs that line only
rep = p.history_report({**cfg, "swapout_pps": [300, float("inf")]}, hours=1, now=200000.0 + p.HISTORY_KEEP_S + 3700 + 3500)
assert [r["t"] for r in rep["series"]] == [200000.0 + p.HISTORY_KEEP_S + 3700], rep["series"]
assert rep["thresholds"]["load_per_cpu"] == [1.3, 2.0] and rep["thresholds"]["swapout_pps"] == [300, None]
assert rep["higher_worse"]["mem_free_pct"] is False and rep["last"]["level"] == "ok" and rep["queue"] == []
json.dumps(rep, allow_nan=False)
assert p.history_report(cfg, hours=999)["hours"] == p.HISTORY_KEEP_S // 3600 == 192

# Retention is 8 days (a 7-day window stays whole) and the file is bounded in bytes: past HISTORY_MAX_BYTES the
# oldest lines go until 3/4 of it is left, and the newest line always stays.
assert p.HISTORY_KEEP_S >= 7 * 86400
hist.unlink(missing_ok=True)
base = 300000.0
run_round(OK, [], now=base)
run_round(OK, [], now=base + 7 * 86400)  # 7 days old: kept
assert [r["t"] for r in p.history_read()] == [base, base + 7 * 86400]
orig_max = p.HISTORY_MAX_BYTES
try:
    p.HISTORY_MAX_BYTES = 600
    for i in range(1, 11):
        run_round(OK, [], now=base + 7 * 86400 + i * 180)
    size = hist.stat().st_size
    ts = [r["t"] for r in p.history_read()]
    assert size <= 600 + 200 and ts[-1] == base + 7 * 86400 + 10 * 180 and ts[0] > base, (size, ts)
finally:
    p.HISTORY_MAX_BYTES = orig_max
# A non-numeric metric is stored as null, so one corrupt value cannot outgrow the cap.
hist.unlink(missing_ok=True)
p.history_add({"t": base, "level": "ok", "metrics": {"load_per_cpu": "x" * 5_000_000, "swap_gb": 1.5}}, 0)
row = p.history_read()[0]
assert row["load_per_cpu"] is None and row["swap_gb"] == 1.5 and hist.stat().st_size < 1000
assert rep["load_idle_pct"] == cfg["load_idle_pct"] and rep["thresholds"]["disk_free_gb"] == [50, 20]

# Turns in progress, from the shape `orca worktree ps --json` gives (names and paths replaced). Orca keeps an agent at
# state "working" after its main turn ends while a background wait or subagent lives on: workingMode "monitoring"
# and mainAgent.state "done" say so, and that agent runs no turn. An older Orca without either field counts as before.
def agent(state, mode=None, main=None, subs=0):
    a = {"paneKey": "tab-1:1", "agentType": "claude", "state": state, "prompt": "do ABC-1", "toolName": "Bash",
         "subagents": [{"state": "working"}] * subs}
    if mode:
        a["workingMode"] = mode
    if main:
        a["mainAgent"] = {"state": main, "stateStartedAt": 1700000000000}
    return a


def ps(*agents):
    return json.dumps({"id": "x", "ok": True, "result": {"worktrees": [
        {"path": "/w/acme-app/card", "displayName": "card", "agents": list(agents)}], "totalCount": 1,
        "truncated": False}})


turning = [agent("working", main="working"), agent("working", main="working", subs=2)]
waiting = [agent("working", "monitoring", "done"), agent("working", main="done", subs=1),
           agent("working", "monitoring"), agent("working", main="waiting")]  # each field holds on its own
older = [agent("working"), agent("working")]  # no workingMode, no mainAgent
assert p.active_turns(run=lambda *a, **k: ps(*turning)) == 2
assert p.active_turns(run=lambda *a, **k: ps(*waiting)) == 0
assert p.active_turns(run=lambda *a, **k: ps(*older)) == 2
assert p.active_turns(run=lambda *a, **k: ps(*turning, *waiting, *older, agent("done", main="done"))) == 4

# launchd spec: runs `round` from this file with a PATH that finds the tools reap calls.
s = p.spec(180)
assert s["ProgramArguments"][-2:] == [str(pathlib.Path(p.__file__).resolve()), "round"] and s["StartInterval"] == 180
assert "/usr/bin" in s["EnvironmentVariables"]["PATH"]

# `turns --wait` without a usable minute count is an error: it used to skip the wait and end 0 as if the cap had cleared.
import contextlib, io
for bad in (["turns", "--wait"], ["turns", "--wait", "soon"], ["turns", "--wait", "-3"], ["turns", "--wait", "nan"], ["turns", "--wait", "inf"], ["turns", "--wait", "1e999"],
            ["turns", "--set", "active_turns_per_cpu=0.5", "--wait"]):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = p.main(bad)
    assert code == 2 and "--wait needs" in err.getvalue(), (bad, code, err.getvalue())

# The patrol trigger: a rise only, held by hysteresis (a load-driven danger leaves at 1.7, not 2.0) and 15 minutes apart.
# `level`, which the gate and the hook read, still follows the raw metrics.
assert p.trigger_level("danger", True, "warn", {"load_per_cpu": 1.8}, False) == ("danger", True)
assert p.trigger_level("danger", True, "warn", {"load_per_cpu": 1.69}, False) == ("warn", False)
assert p.trigger_level("danger", True, "warn", {"load_per_cpu": None}, False) == ("warn", False)
assert p.trigger_level("warn", False, "warn", {"load_per_cpu": 1.8}, False) == ("warn", False)
assert p.trigger_level("ok", False, "danger", {"load_per_cpu": 2.1}, True) == ("danger", True)
# A danger another metric drove (memory) is not held by the load: it leaves with that metric.
assert p.trigger_level("ok", False, "danger", {"load_per_cpu": 1.8}, False) == ("danger", False)
assert p.trigger_level("danger", False, "warn", {"load_per_cpu": 1.8}, False) == ("warn", False)
# A held load danger survives a memory danger on top of it, and leaves only when the load does.
assert p.trigger_level("danger", True, "danger", {"load_per_cpu": 1.8}, False) == ("danger", True)
assert p.trigger_level("danger", True, "warn", {"load_per_cpu": 1.8}, False) == ("danger", True)
# judge caps the load at warn while the CPU idles: the load did not drive that danger.
idle_cap = {**OK, "load_per_cpu": 2.1, "cpu_idle_pct": 30, "mem_free_pct": 9}
assert p.judge(idle_cap, cfg)[0] == "danger" and not any(mt == "load_per_cpu" and lv == 2 for mt, lv, _ in p.hits(idle_cap, cfg))
obs = p.trigger_obs_add(None, "warn", 10.0)
assert obs == [[10.0, "warn"]] and p.trigger_obs_add(obs, "warn", 20.0) == obs, "only changes are kept"
assert p.trigger_obs_add(obs, "danger", 30.0) == [[10.0, "warn"], [30.0, "danger"]]
assert len(p.trigger_obs_add([[i, "ok" if i % 2 else "warn"] for i in range(100)], "danger", 200.0)) == p.TRIGGER_OBS_KEEP
# Past the cap the lowest level that falls off stays, so a start patrol never took still sees the fall to ok.
long = [[1000.0, "ok"]] + [[1001.0 + i, "danger" if i % 2 else "warn"] for i in range(40)]
kept = p.trigger_obs_add(long, "danger", 2000.0, since=1000.0)
assert len(kept) == p.TRIGGER_OBS_KEEP and kept[0] == [1000.0, "ok"] and p.floor_since(kept, {"t": 1000.0, "level": "warn"}) == "ok"
# A clock set back leaves future entries behind: they are dropped. Entries patrol's last round has seen are dropped too.
assert p.trigger_obs_add([[10000.0, "warn"]], "danger", 1200.0) == [[1200.0, "danger"]]
assert p.trigger_obs_add([[10.0, "warn"], [900.0, "danger"]], "danger", 1000.0, since=800.0) == [[900.0, "danger"]]
# What patrol saw before its round began is not a fall since: no tolerance window brings it back.
assert p.floor_since([[900.0, "warn"], [950.0, "danger"]], {"t": 1000.0, "level": "danger"}) == "danger"
assert p.floor_since([[1000.0, "warn"], [1020.0, "danger"]], {"t": 1000.0, "level": "danger"}) == "warn"
# A stored history that is not [time, level] pairs never stops a round.
for junk in (42, "x", {"a": 1}, [["1000", "warn"]], [[1000, "legacy"]], [[True, "warn"]], [[1]], [None], [[1000, ["warn"]]]):
    assert p.valid_obs(junk) == [], junk
    assert p.trigger_obs_add(junk, "warn", 50.0) == [[50.0, "warn"]]
    assert p.floor_since(junk, {"t": 5, "level": "danger"}) == "danger"
assert p.floor_since([[10.0, "warn"]], {"t": 5, "level": "legacy"}) == "ok", "an unknown memo level reads as ok"
p.save_state({"level": "ok", "trigger_level": "ok", "trigger_obs": 42, "t": 1.0})
run_round(OK, [], now=300.0)  # the real round, over a stored history that is junk
assert p.load_state()["trigger_obs"][-1][1] == "ok"
assert p.floor_since([[10.0, "warn"], [30.0, "danger"]], {"t": 5, "level": "danger"}) == "warn", "a fall since the round began"
assert p.floor_since([[10.0, "warn"], [500.0, "danger"]], {"t": 400, "level": "danger"}) == "danger", "an older fall is not below it"
assert p.floor_since([], {"t": 5, "level": "warn"}) == "warn" and p.floor_since([], {}) == "ok"
assert p.rise_due("ok", 100.0, "warn", 1000.0) and not p.rise_due("warn", 100.0, "warn", 1000.0)
assert not p.rise_due("ok", 500.0, "warn", 1000.0), "inside the 15-minute gap"
# Rounds against a patrol memo. The memo is what patrol wrote on its last round; a start patrol refused or never ran
# leaves it, so the next pressure round asks again.
spawned = []
spawn0, p.reap.spawn_patrol = p.reap.spawn_patrol, lambda reason, **k: spawned.append(reason) or True
T = 100000.0
p.save_state({"level": "ok", "trigger_level": "ok", "t": T - 1})
p.PATROL_MEMO.write_text(json.dumps({"t": T - 3600, "level": "ok"}))
LOAD = lambda v: {**OK, "load_per_cpu": v}
for step, (load, at, want, patrol_runs) in enumerate([
        (1.4, T, 1, True),            # ok -> warn: a rise
        (2.1, T + 300, 0, False),     # warn -> danger inside the gap: pending
        (1.9, T + 400, 0, False),     # raw warn, but the pending danger is held at 1.9: still pending
        (1.9, T + 960, 1, True),      # the gap is over: the held rise fires, patrol runs at danger
        (1.5, T + 1000, 0, False),    # released below 1.7: a fall starts nothing
        (2.1, T + 1100, 0, False),    # danger again, 140 s after the last round: pending
        (2.1, T + 1900, 1, True),     # a climb back to danger after a fall is a rise
        (0.5, T + 2000, 0, False),    # ok
        (1.4, T + 2900, 1, False),    # a rise whose patrol never ran (refused, lock held)...
        (1.4, T + 2950, 0, False),    # ...is not asked again within the retry interval...
        (1.4, T + 3100, 0, False),    # asked again too soon (retry interval): not every round
        (1.4, T + 3250, 1, True)]):   # ...and this time runs
    spawned.clear()
    run_round(LOAD(load), [], consumers=([], []), now=at, rescan_minutes=0)
    assert len(spawned) == want, (step, load, spawned)
    assert p.load_state()["level"] == p.judge(LOAD(load), cfg)[0], "the raw level is not held"
    if at == T + 1000:  # the fall below patrol's danger is in the history patrol's commit cannot touch
        last = p.load_state()["trigger_obs"][-1]
        assert last[1] == "warn" and at <= last[0] < at + 5, last  # stamped when saved, a moment after the round began
    if spawned and patrol_runs:
        p.PATROL_MEMO.write_text(json.dumps({"t": at, "level": p.load_state()["trigger_level"]}))
# The entry is stamped when written, not when the round began: notices and queue work run in between.
import itertools
mono0, p.time.monotonic = p.time.monotonic, itertools.count(0, 10).__next__  # each reading is 10 s after the last
p.save_state({"level": "ok", "trigger_level": "ok", "t": 1.0})
run_round(LOAD(1.4), [], consumers=([], []), now=500.0, rescan_minutes=0)
p.time.monotonic = mono0
stamp = p.load_state()["trigger_obs"][-1]
assert stamp[1] == "warn" and stamp[0] >= 500.0 + 20, stamp
# The retry interval counts from when the start was written, not from when the round began: a slow measurement (each
# clock reading here is 60 s after the last) must not shorten it.
p.save_state({"level": "ok", "trigger_level": "ok", "t": 1.0})
p.PATROL_MEMO.write_text(json.dumps({"t": 1.0, "level": "ok"}))
p.time.monotonic = itertools.count(0, 60).__next__
for now, want in ((1000.0, 1), (1300.0, 0), (1400.0, 1)):
    spawned.clear()
    run_round(LOAD(1.4), [], consumers=([], []), now=now, rescan_minutes=0)
    assert len(spawned) == want, (now, spawned, p.load_state().get("spawn_t"))
p.time.monotonic = mono0
p.reap.spawn_patrol = spawn0

print("ok")
