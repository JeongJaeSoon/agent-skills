"""Levels, the round's reap-or-report path and its notify dedupe, with every outside call faked.
Run: python3 test_pressure.py"""
import os, pathlib, sys, tempfile

os.environ["AGENT_SKILLS_STATE"] = tempfile.mkdtemp()
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pressure as p

cfg = dict(p.DEFAULTS)
OK = {"cpus": 18, "load5": 9.0, "load_per_cpu": 0.5, "mem_free_pct": 60, "vm_pressure": 1, "swap_gb": 1.0,
      "disk_free_gb": 200.0, "containers": 10}

# Levels: the worst metric wins; lower-is-worse metrics compare the other way; an unread metric never counts.
assert p.judge(OK, cfg) == ("ok", [])
assert p.judge({**OK, "load_per_cpu": 1.2}, cfg) == ("warn", ["load_per_cpu 1.2 ≥ 1"])
assert p.judge({**OK, "load_per_cpu": 1.2, "mem_free_pct": 9}, cfg)[0] == "danger"
assert p.judge({**OK, "disk_free_gb": 50}, cfg) == ("warn", ["disk_free_gb 50 ≤ 50"])
assert p.judge({**OK, "vm_pressure": 4}, cfg)[0] == "danger"
assert p.judge({**OK, "containers": None, "swap_gb": None}, cfg) == ("ok", [])

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
def run_round(metrics, targets, reaped=(), consumers=([], []), dry=False, now=1000.0, **over):
    lines.clear()
    return p.round_once({**cfg, "notify_to": "run:orch", **over}, dry, out=lines.append, now=now,
                        measure_fn=lambda: metrics, plan_fn=lambda kinds, path: (targets, []),
                        reap_fn=lambda path: list(reaped), consumers_fn=lambda c: consumers,
                        send_fn=lambda *a: sent.append(a) or True)

target = {"kind": "orphan", "key": "proc:42", "target": True, "name": "vite", "why": "cwd 사라짐", "pid": 42,
          "procs": 1, "rss": 1, "cpu": 0, "age": 1}
assert run_round(OK, [target]) == 0 and not sent and p.load_state()["level"] == "ok"
WARN = {**OK, "load_per_cpu": 1.2}
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
assert len(sent) == 1 and sent[0][3] == "escalation" and "pid 7" in sent[0][1] and "카드 card" in sent[0][2], sent
assert "종료하지 않았다" in sent[0][2]
assert run_round(DANGER, [], consumers=(procs, projects), now=5600.0) == 2 and len(sent) == 1
assert run_round(DANGER, [], consumers=(procs, projects), now=5000.0 + 31 * 60) == 2 and len(sent) == 2

# A failed send is tried again on the next round.
sent.clear()
run_round(OK, [])
p.round_once({**cfg, "notify_to": "run:orch"}, False, out=lines.append, now=9000.0, measure_fn=lambda: DANGER,
             plan_fn=lambda k, path: ([], []), reap_fn=None, consumers_fn=lambda c: (procs, projects),
             send_fn=lambda *a: False)
run_round(DANGER, [], consumers=(procs, projects), now=9060.0)
assert len(sent) == 1, sent

# Dry run: prints the plan and the message, reaps, sends and records nothing.
sent.clear()
before = p.load_state()
assert run_round(WARN, [target], reaped=["- should not run"], dry=True, now=99999.0) == 1
assert not sent and p.load_state() == before
assert any("정리 예정 1개" in line for line in lines) and any("dry-run" in line for line in lines), lines

# A scan that failed reaps nothing and, at danger, still reports consumers.
lines.clear()
p.round_once(cfg, True, out=lines.append, measure_fn=lambda: DANGER, plan_fn=lambda k, path: (None, ["reap scan 실패"]),
             reap_fn=None, consumers_fn=lambda c: (procs, []), send_fn=None)
assert any("읽지 못해" in line for line in lines) and any("pid 7" in line for line in lines), lines

# launchd spec: runs `round` from this file with a PATH that finds the tools reap calls.
s = p.spec(180)
assert s["ProgramArguments"][-2:] == [str(pathlib.Path(p.__file__).resolve()), "round"] and s["StartInterval"] == 180
assert "/usr/bin" in s["EnvironmentVariables"]["PATH"]

print("ok")
