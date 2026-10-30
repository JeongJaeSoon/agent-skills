"""Levels, the round's reap-or-report path and its notify dedupe, with every outside call faked.
Run: python3 test_pressure.py"""
import os, pathlib, sys, tempfile

os.environ["AGENT_SKILLS_STATE"] = tempfile.mkdtemp()
os.environ["ORCH_FLEET_STATE"] = tempfile.mkdtemp()
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
assert p.judge({**OK, "containers": 99}, cfg)[0] == "warn"
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

# Containers: a pod counts once with its pause container, kube-system not at all, always-on services apart.
PS = ("stack\t\t\tstack-db-1\nstack\t\t\tstack-web-1\n\t\t\tlonely\nlf\t\t\tlf-web-1\nlf\t\t\tlf-db-1\n\t\t\twebui\n"
      "\tpr-1\tu1\tk8s_POD_api-x_pr-1_u1_0\n\tpr-1\tu1\tk8s_api_api-x_pr-1_u1_0\n\tpr-1\tu1\tk8s_init_api-x_pr-1_u1_0\n"
      "\tpr-1\tu2\tk8s_POD_db-y_pr-1_u2_0\n\tpr-1\tu2\tk8s_db_db-y_pr-1_u2_0\n"
      "\tkube-system\tu3\tk8s_POD_coredns-z_kube-system_u3_0\n\tkube-system\tu3\tk8s_coredns_coredns-z_kube-system_u3_0\n"
      "\tsvc\tu4\tk8s_POD_s_svc_u4_0\n\tsvc\tu4\tk8s_s_s_svc_u4_0\n\n")
assert p.count_containers(PS) == (9, 0)
assert p.count_containers(PS, ["lf", "webui", "svc"]) == (5, 4)
def fake_ps(cmd, timeout=None):
    assert cmd[:3] == ["docker", "ps", "--format"] and "io.kubernetes.pod.uid" in cmd[3], cmd
    return PS
m = p.measure(run=fake_ps, always_on=["lf"]) if p.shutil.which("docker") else None
assert m is None or (m["containers"], m["always_on"]) == (7, 2), m
assert p.summary({**OK, "always_on": 2}).endswith("컨테이너 10개(상시 2개 제외)")
assert p.summary({**OK, "always_on": 0}).endswith("컨테이너 10개")
assert p.config([("always_on", "lf,webui")])["always_on"] == ["lf", "webui"]

assert p.swap_gb("total = 3072.00M  used = 1785.31M  free = 1286.69M  (encrypted)") == 1.74
assert p.swap_gb("total = 0.00M  used = 2.50G  free = 0") == 2.5 and p.swap_gb("") is None
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

# launchd spec: runs `round` from this file with a PATH that finds the tools reap calls.
s = p.spec(180)
assert s["ProgramArguments"][-2:] == [str(pathlib.Path(p.__file__).resolve()), "round"] and s["StartInterval"] == 180
assert "/usr/bin" in s["EnvironmentVariables"]["PATH"]

print("ok")
