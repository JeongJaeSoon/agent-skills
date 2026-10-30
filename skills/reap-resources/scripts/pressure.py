#!/usr/bin/env python3
"""Watch this machine's load and reap what is safe before it slows down (reap-resources SKILL.md "Pressure").

Usage:
  pressure.py check [--json] [--set KEY=WARN,DANGER]...
                     measure only; exit 0 ok, 1 warn, 2 danger
  pressure.py round [--dry-run] [--set KEY=WARN,DANGER]...
                     measure; at warn or danger reap the safe kinds (reap.py scan + reap --plan); at danger with
                     nothing to reap, name the top consumers and stop. Each outcome is one line to the notify address.
                     --dry-run prints the plan and the message; it writes only its own plan file, reaps and sends nothing
  pressure.py queue | queue wait <id>
                     the heavy starts the hook held at danger; wait exits 0 once the hook would let that one through
                     (its session runs the same command again), 3 when it should not run (deadline passed, or gone)
  pressure.py install [--here] | uninstall | status
                     macOS LaunchAgent running `round` every `interval` seconds

No model is called. Thresholds live under "reap" → "pressure" in ~/.claude/agent-skills.json (DEFAULTS); each is
[warn, danger], a danger of null (or inf) meaning warn only. check and the hook judge what `round` last sampled
(swapout rate, CPU idle, Docker memory) only while it is younger than `sample_fresh_minutes`. Nothing outside reap's own target list is ever touched: no kill by pattern, no compose down -v,
no prune, no volume or image removal.
"""
import contextlib, ctypes, ctypes.util, fcntl, hashlib, json, math, os, pathlib, plistlib, re, shlex, shutil, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
REAP = HERE / "reap.py"
sys.path.insert(0, str(HERE))
import reap  # noqa: E402

LABEL = "io.github.jeongjaesoon.pressure-watch"
PLIST = pathlib.Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
STATE_FILE = reap.STATE / "pressure.json"
PLAN = reap.STATE / "pressure-plan.json"
LOG = reap.STATE / "pressure.log"
# Left by uninstall so `skills-sync setup apply` keeps a job the user turned off off; install removes it.
DISABLED = reap.STATE / "pressure-disabled"

# Defaults measured on an 18-core, 64 GB Apple Silicon machine (docs/pressure.md has the numbers behind them).
# A danger of None is warn only: the metric is worth a sweep or a note, never a reason to hold a start.
DEFAULTS = {
    "interval": 180,
    "load_per_cpu": [1.3, 2.0],  # 5-minute load / logical CPUs, at or above
    "load_idle_pct": 10,  # load reaches danger only while CPU idle over the last round is at or below this
    "mem_free_pct": [20, 10],  # kern.memorystatus_level, at or below
    "vm_pressure": [2, 4],  # kern.memorystatus_vm_pressure_level: 1 normal, 2 warn, 4 critical
    "swap_gb": [16, None],  # in use: what past pressure or a leak left, not what is swapping now
    "swap_growth_gb_h": [2, None],  # net growth over the last 30-60 minutes: a leak to look into
    "swapout_pps": [300, 1500],  # pages swapped out per second between rounds
    "disk_free_gb": [50, 20],  # on the volume holding $HOME, at or below
    "containers": [40, None],  # running docker containers, a Kubernetes pod as one and kube-system not at all
    "docker_mem_pct": [70, 90],  # every container's memory (docker stats) / the Docker VM's MemTotal
    "sample_fresh_minutes": 7,  # a round's sample older than this is not judged on (two intervals and a slow round)
    # Compose projects, container names or Kubernetes namespaces that run all day (the user's own services): left out
    # of `containers` and shown apart. A restart policy cannot tell them, since card stacks use unless-stopped too.
    "always_on": [],
    # What reap may close with no one asking: finished cards' registered stacks, leftover process trees of dead
    # sessions, registered GUI apps of merged PRs, reclaimable worker terminals. Kinds reap does not know are dropped.
    "safe_kinds": ["orphan", "compose-stack", "gui-app", "orca-worker"],
    "notify_to": None,  # an Orca address; unset falls back to reap.inbox_to
    "renotify_minutes": 30,  # a danger report with the same consumers is not repeated sooner
    "rescan_minutes": 10,  # a machine that stays at warn is scanned again only after this; danger always scans
    # The hook lets a heavy start through at warn when safe targets were swept this recently. Longer than
    # rescan_minutes plus interval, so a machine that stays at warn under the LaunchAgent is not held.
    "gate_fresh_minutes": 15,
    # A heavy start the hook held at danger waits in the queue this long for its session to run it again.
    "queue_minutes": 15,
    "top": 5,
}
# (metric, higher is worse); each is also its threshold's key
CHECKS = (("load_per_cpu", True), ("mem_free_pct", False), ("vm_pressure", True), ("swap_gb", True),
          ("swap_growth_gb_h", True), ("swapout_pps", True), ("disk_free_gb", False), ("containers", True),
          ("docker_mem_pct", True))
# What a round samples and check and the hook read back from its state.
SAMPLED = ("swapout_pps", "cpu_idle_pct", "docker_mem_pct", "swap_growth_gb_h")
LEVELS = ("ok", "warn", "danger")
LEVEL_KO = {"ok": "정상", "warn": "경고", "danger": "위험"}


def config(overrides=()):
    cfg = {**DEFAULTS, **(reap.config().get("pressure") or {})}
    for key, value in overrides:
        if key not in DEFAULTS:
            sys.exit(f"--set: unknown key {key}; known: {', '.join(DEFAULTS)}")
        default = DEFAULTS[key]
        if key in ("safe_kinds", "always_on"):
            cfg[key] = value.split(",")
        elif isinstance(default, list):
            cfg[key] = [None if x.strip().lower() in ("inf", "-inf", "none", "null") else float(x)
                        for x in value.split(",")]
        else:
            cfg[key] = float(value) if isinstance(default, (int, float)) else value
    return cfg


def sh(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=reap.ENV)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def sysctl(name):
    out = sh(["sysctl", "-n", name], timeout=3)
    return out.strip() if out else None


def swap_gb(text):
    """vm.swapusage: 'total = 3072.00M  used = 1785.31M  free = ...'."""
    m = re.search(r"used = ([\d.]+)([KMG])", text or "")
    return round(float(m[1]) / {"K": 1 << 20, "M": 1 << 10, "G": 1}[m[2]], 2) if m else None


CONTAINER_FORMAT = "\t".join(f'{{{{.Label "{k}"}}}}' for k in (
    "com.docker.compose.project", "io.kubernetes.pod.namespace", "io.kubernetes.pod.uid")) + "\t{{.Names}}"


def count_containers(ps, always_on=()):
    """(counted, always on) from `docker ps --format CONTAINER_FORMAT`. A Kubernetes pod's containers, its pause
    container among them, count once; kube-system is the cluster itself and counts as neither."""
    counted, pods, kept = 0, set(), set()
    for line in ps.splitlines():
        if not line.strip():
            continue
        project, ns, uid, name = (line.split("\t") + ["", "", ""])[:4]
        if any(x and x in always_on for x in (project, ns, name)):
            kept.add(uid or name)
        elif ns == "kube-system":
            continue
        elif ns or uid:
            pods.add(uid or name)
        else:
            counted += 1
    return counted + len(pods), len(kept)


def measure(docker=True, run=sh, always_on=()):
    """Every metric this OS offers; a metric it cannot read is None and never raises a level."""
    cpus = os.cpu_count() or 1
    m = {"cpus": cpus, "load5": round(os.getloadavg()[1], 1)}
    m["load_per_cpu"] = round(m["load5"] / cpus, 2)
    level, pressure = sysctl("kern.memorystatus_level"), sysctl("kern.memorystatus_vm_pressure_level")
    m["mem_free_pct"] = int(level) if level and level.isdigit() else None
    m["vm_pressure"] = int(pressure) if pressure and pressure.isdigit() else None
    m["swap_gb"] = swap_gb(sysctl("vm.swapusage"))
    m["disk_free_gb"] = round(shutil.disk_usage(pathlib.Path.home()).free / (1 << 30), 1)
    out = run(["docker", "ps", "--format", CONTAINER_FORMAT], timeout=5) if docker and shutil.which("docker") else None
    m["containers"], m["always_on"] = count_containers(out, always_on) if out is not None else (None, None)
    return m


def hits(m, cfg):
    """[(metric, level 1 or 2, phrase)] for each metric at warn or above. The one rule hook, check and round share."""
    out = []
    for metric, higher in CHECKS:
        v = m.get(metric)
        if v is None:
            continue
        pair = [(math.inf if higher else -math.inf) if t is None else t for t in cfg[metric][:2]]
        if not higher:
            pair = [-t for t in pair]
        hit = [i + 1 for i, t in enumerate(pair) if (v if higher else -v) >= t]
        if not hit:
            continue
        level, phrase = hit[-1], f"{metric} {v:g} {'≥' if higher else '≤'} {cfg[metric][hit[-1] - 1]:g}"
        idle = m.get("cpu_idle_pct")
        # Load counts waiting threads (the Docker VM's vCPUs among them): with CPU left idle it is no danger.
        cap = DEFAULTS["load_idle_pct"] if cfg.get("load_idle_pct") is None else float(cfg["load_idle_pct"])
        if metric == "load_per_cpu" and level == 2 and idle is not None and idle > cap:
            level, phrase = 1, f"{phrase} (CPU idle {idle:g}% > {cap:g}%)"
        out.append((metric, level, phrase))
    return out


def judge(m, cfg):
    """(level, reasons): the worst level any metric reaches, and one phrase per metric at warn or above."""
    found = hits(m, cfg)
    return LEVELS[max((lvl for _, lvl, _ in found), default=0)], [why for _, _, why in found]


def blocked_by(m, cfg):
    return [metric for metric, lvl, _ in hits(m, cfg) if lvl == 2]


def summary(m):
    parts = [f"load5 {m['load5']:g}/{m['cpus']}코어"]
    for k, fmt in (("cpu_idle_pct", "CPU idle {:g}%"), ("mem_free_pct", "메모리 여유 {}%"), ("vm_pressure", "압박 레벨 {}"),
                   ("swap_gb", "swap {}GB"), ("swap_growth_gb_h", "swap 증가 {:g}GB/h"),
                   ("swapout_pps", "swapout {:g}/s"), ("disk_free_gb", "디스크 여유 {}GB"),
                   ("docker_mem_pct", "Docker 메모리 {:g}%"), ("containers", "컨테이너 {}개")):
        if m.get(k) is not None:
            parts.append(fmt.format(m[k]))
            if k == "containers" and m.get("always_on"):
                parts[-1] += f"(상시 {m['always_on']}개 제외)"
    if m.get("sample_age_min") is not None:
        parts.append(f"샘플 {m['sample_age_min']:g}분 전(오래돼 판정에서 뺌)")
    return ", ".join(parts)


# ---------------------------------------------------------------- sampled between rounds

def cpu_ticks():
    """host_statistics(HOST_CPU_LOAD_INFO): cumulative [user, system, idle, nice] ticks of all CPUs, None elsewhere."""
    with contextlib.suppress(OSError, AttributeError, TypeError):
        lib = ctypes.CDLL(ctypes.util.find_library("System") or "")
        lib.mach_host_self.restype = ctypes.c_uint
        buf, n = (ctypes.c_uint * 4)(), ctypes.c_uint(4)
        if lib.host_statistics(lib.mach_host_self(), 3, buf, ctypes.byref(n)) == 0:
            return list(buf)
    return None


def counters(now, run=sh):
    """What a rate is computed from. boot tells a reboot, after which every counter starts over."""
    boot = re.search(r"sec = (\d+)", sysctl("kern.boottime") or "")
    swapouts = sysctl("vm.compressor.swapper.swapouts_total")
    return {"t": now, "boot": int(boot[1]) if boot else None,
            "swapouts": int(swapouts) if swapouts and swapouts.isdigit() else None, "cpu": cpu_ticks()}


def rates(prev, cur, cfg):
    """Swapout pages/s and CPU idle % since the previous round's counters. A reboot, a counter gone backwards or a
    gap over three intervals gives None: no rate, never a zero one."""
    out = {"swapout_pps": None, "cpu_idle_pct": None}
    dt = cur["t"] - (prev or {}).get("t", 0)
    if not prev or prev.get("boot") != cur.get("boot") or not 0 < dt <= 3 * float(cfg["interval"]):
        return out
    if None not in (prev.get("swapouts"), cur.get("swapouts")) and cur["swapouts"] >= prev["swapouts"]:
        out["swapout_pps"] = round((cur["swapouts"] - prev["swapouts"]) / dt, 1)
    if prev.get("cpu") and cur.get("cpu"):
        d = [b - a for a, b in zip(prev["cpu"], cur["cpu"])]
        if min(d) >= 0 and sum(d):
            out["cpu_idle_pct"] = round(100 * d[2] / sum(d), 1)
    return out


def swap_history(hist, now, gb):
    """The last hour of (t, swap GB), one per round, with this one added."""
    return [h for h in hist or () if now - h[0] < 3600] + ([[now, gb]] if gb is not None else [])


def swap_growth(hist, now, gb):
    """Net swap growth in GB/h since the oldest point of the hour, once that is 30 minutes old."""
    if gb is None or not hist or now - hist[0][0] < 1800:
        return None
    return round((gb - hist[0][1]) / ((now - hist[0][0]) / 3600), 1)


def docker_mem_pct(run=sh):
    """Every running container's memory as a share of the Docker VM's. The VM's own kernel and daemons are not in it,
    so a low value does not prove the VM has room."""
    total = run(["docker", "info", "--format", "{{.MemTotal}}"], timeout=10)
    stats = run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}"], timeout=20)
    if not total or not total.strip().isdigit() or stats is None or not int(total):
        return None
    used = sum(mem_mb(line.split("/")[0]) for line in stats.splitlines())
    return round(100 * used * (1 << 20) / int(total), 1)


def take_sample(st, now, cfg, run=sh):
    """The state fields one round adds: its counters, the swap history, and the sample check and the hook read.
    Nothing when the last counters are under half an interval old: a round run by hand right after the LaunchAgent's
    would put a 10-second rate, spikes and all, in place of the 3-minute one."""
    if now - (st.get("counters") or {}).get("t", -math.inf) < float(cfg["interval"]) / 2:
        return {}
    cur = counters(now, run)
    gb = swap_gb(sysctl("vm.swapusage"))
    hist = swap_history(st.get("swap_hist"), now, gb)
    sample = {"t": now, **rates(st.get("counters"), cur, cfg), "swap_growth_gb_h": swap_growth(hist, now, gb),
              "docker_mem_pct": docker_mem_pct(run) if shutil.which("docker") else None}
    return {"counters": cur, "swap_hist": hist, "sample": sample}


def snapshot(cfg, st, now=None, measure_fn=None):
    """What hook, check and round judge: the metrics read now, plus the last round's sample while it is fresh."""
    now = now or time.time()
    m = (measure_fn or (lambda: measure(always_on=cfg["always_on"])))()
    sample = st.get("sample") or {}
    age = now - sample.get("t", 0)
    fresh = 0 <= age <= float(cfg["sample_fresh_minutes"]) * 60
    for k in SAMPLED:
        m[k] = sample.get(k) if fresh else None
    if sample and not fresh:
        m["sample_age_min"] = round(age / 60)
    return m


# ---------------------------------------------------------------- state

def load_state(path=None):
    with contextlib.suppress(OSError, ValueError):
        st = json.loads((path or STATE_FILE).read_text())
        if isinstance(st, dict):
            return st
    return {}


def save_state(st, path=None):
    path = path or STATE_FILE
    reap.STATE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False))
    tmp.replace(path)


def log(msg):
    reap.STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")


# ---------------------------------------------------------------- reap and consumers

def safe_kinds(cfg):
    known = set(reap.KINDS + reap.SCHEDULED)
    return [k for k in cfg["safe_kinds"] if k in known]


def unknown_kinds(cfg):
    known = set(reap.KINDS + reap.SCHEDULED)
    return [k for k in cfg["safe_kinds"] if k not in known]


def plan_targets(kinds, plan_path, run=subprocess.run, cards=None, cwd_of=None):
    """reap.py scan over the safe kinds, saved as a plan, minus what lies in a card with a live terminal.
    (targets, notes); targets None when the scan or the card list failed."""
    if not kinds:
        return [], ["safe_kinds 중 reap 이 아는 종류가 없음"]
    try:
        p = run([sys.executable, str(REAP), "scan", "--kinds", ",".join(kinds), "--plan", str(plan_path), "--json"],
                capture_output=True, text=True, timeout=240)
        data = json.loads(p.stdout)
        targets = [i for i in data["items"] if i["target"]]
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as e:
        return None, [f"reap scan 실패: {type(e).__name__}"]
    notes = data.get("notes") or []
    if not targets:
        return targets, notes
    cards = orca_cards() if cards is None else cards
    if cards is None:
        return None, notes + ["orca worktree ps 를 읽지 못해 활동 중인 카드를 가릴 수 없음: 정리하지 않음"]
    live = [c["path"] for c in cards if c["live"]]
    cwd_of = reap.cwds() if cwd_of is None else cwd_of
    if cwd_of is None and any("pid" in i for i in targets):
        return None, notes + ["프로세스 cwd 를 모두 읽지 못해 활동 중인 카드를 가릴 수 없음: 정리하지 않음"]
    cwd_of = cwd_of or {}
    for i in targets:
        card = next(filter(None, (card_of(x, live) for x in item_paths(i, cwd_of))), None)
        if card:
            i.update(target=False, why=f"활동 중인 카드 {os.path.basename(card)} 안: {i['why']}")
            notes.append(f"남김 {reap.label(i)}")
    plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return [i for i in targets if i["target"]], notes


def item_paths(i, cwd_of):
    """Where a reap item lives: its path or worktree, a process's cwd and the program it runs."""
    links = i.get("links") or {}
    out = [i.get("path"), i.get("worktree"), links.get("worktree"), cwd_of.get(i.get("pid"))]
    out += [w for w in (i.get("cmd") or "").split()[:2] if w.startswith("/")]
    return [x for x in out if x]


def reap_plan(plan_path, run=subprocess.run):
    """reap.py reap --plan: it re-checks each target and acts on those still the same. The lines it printed."""
    try:
        p = run([sys.executable, str(REAP), "reap", "--plan", str(plan_path)], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"- 실패: reap --plan ({type(e).__name__})"]
    return [line for line in (p.stdout + p.stderr).splitlines() if line.strip()]


def compose_projects(run=sh):
    """Running containers grouped by compose project: [{project, dir, containers, cpu, mem_mb}], heaviest first."""
    ps = run(["docker", "ps", "--format", '{{.ID}}\t{{.Label "com.docker.compose.project"}}\t'
              '{{.Label "com.docker.compose.project.working_dir"}}\t{{.Names}}'], timeout=10)
    if ps is None:
        return None
    stats = {}
    for line in (run(["docker", "stats", "--no-stream", "--format", "{{.ID}}\t{{.CPUPerc}}\t{{.MemUsage}}"], timeout=20)
                 or "").splitlines():
        cid, cpu, mem = (line.split("\t") + ["", ""])[:3]
        stats[cid[:12]] = (float(cpu.rstrip("%") or 0), mem_mb(mem.split("/")[0]))
    groups = {}
    for line in ps.splitlines():
        cid, project, wd, name = (line.split("\t") + ["", "", ""])[:4]
        g = groups.setdefault(project or f"(compose 아님) {name}", {"project": project or name, "dir": wd,
                                                                     "containers": 0, "cpu": 0.0, "mem_mb": 0})
        cpu, mem = stats.get(cid[:12], (0.0, 0))
        g["containers"] += 1
        g["cpu"] += cpu
        g["mem_mb"] += mem
        g["dir"] = g["dir"] or wd
    return sorted(groups.values(), key=lambda g: (-g["cpu"], -g["mem_mb"]))


def mem_mb(text):
    m = re.match(r"\s*([\d.]+)\s*([KMG]i?B|B)", text or "")
    if not m:
        return 0
    return round(float(m[1]) * {"B": 1 / (1 << 20), "KiB": 1 / 1024, "KB": 1 / 1000, "MiB": 1, "MB": 1,
                                  "GiB": 1024, "GB": 1000}[m[2]])


def card_of(path, cards):
    """The card (worktree) a path lies in: the longest worktree path it is under."""
    hits = [c for c in cards if path and reap.under(path, c)]
    return max(hits, key=len) if hits else None


def orca_cards(run=sh):
    """[{path, live}] for every Orca card, live when a terminal is open in it; [] without Orca, None when unreadable."""
    if not shutil.which("orca"):
        return []
    out = run(["orca", "worktree", "ps", "--limit", "10000", "--json"], timeout=15)
    with contextlib.suppress(ValueError, TypeError, KeyError, AttributeError):
        res = json.loads(out)["result"]
        if not res.get("truncated"):  # a card left out could be a live one
            return [{"path": w["path"], "live": bool(w.get("liveTerminalCount"))}
                    for w in res["worktrees"] if w.get("path")]
    return None


def consumers(cfg, run=sh, load=reap.load_report):
    """The top process consumers (reap's load classes) and compose projects, each with the card it belongs to.
    Reported only: nothing here is stopped, whoever owns it."""
    cards = [c["path"] for c in orca_cards(run) or ()]
    procs = []
    with contextlib.suppress(RuntimeError, OSError):
        for i in load(reap.config(), int(cfg["top"]))["items"]:
            procs.append({**i, "card": card_of(i.get("cwd"), cards)})
    projects = compose_projects(run) or []
    for g in projects:
        g["card"] = card_of(g["dir"], cards)
    return procs, projects[:int(cfg["top"])]


def consumer_lines(procs, projects):
    lines = [f"- 프로세스 pid {i['pid']} ({i['cls']}, CPU {i['cpu']:.0f}%, {reap.human(i['rss'])}) `{i['cmd'][:60]}`"
             + (f" · 카드 {os.path.basename(i['card'])}" if i.get("card") else "") for i in procs]
    lines += [f"- compose {g['project']} (컨테이너 {g['containers']}, CPU {g['cpu']:.0f}%, {g['mem_mb']}MB)"
              + (f" · 카드 {os.path.basename(g['card'])}" if g.get("card") else f" · {g['dir'] or '경로 없음'}")
              for g in projects]
    return lines


# ---------------------------------------------------------------- notify

def notify_to(cfg):
    return cfg.get("notify_to") or reap.config().get("inbox_to")


def send(to, subject, body, kind, run=reap.run):
    return run(["orca", "orchestration", "send", "--to", to, "--type", kind, "--subject", subject, "--body", body,
                "--json"], check=False) is not None


# orchestrate's dashboard lists rows appended here under "Needs you" (fleet.py inbox_add, the same line format).
DASH_KEY = "pressure:danger"


def dash_inbox():
    return pathlib.Path(os.environ.get("ORCH_FLEET_STATE", "~/.local/state/agent-skills/dashboard")).expanduser() \
        / "inbox-external.jsonl"


def dash_note(op, title=None, key=DASH_KEY, command=None):
    """Add or resolve a row on the dashboard. A machine without the dashboard has no such directory: False."""
    path = dash_inbox()
    if not path.parent.is_dir():
        return False
    row = {"v": 1, "op": op, "key": key, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if op == "add":
        row.update(type="pc_pressure", title=title[:200], session=None, url=None, command=command, source="pressure-watch")
    with contextlib.suppress(OSError), path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    return False


# ---------------------------------------------------------------- queue

# A heavy start the hook held at danger, kept until the session that asked runs the same command again. The queue never
# runs anything itself: the retry is that session's own tool call, so it passes the same hooks and permission checks
# as the first try did, and no other session's run of the same text counts.
QUEUE = reap.STATE / "pressure-queue.json"
QUEUE_KEEP_S = 7 * 86400  # a finished entry stays this long for `queue list`, as long as its dashboard row


def queue_id(session, command):
    return "q-" + hashlib.sha1(f"{session}\0{command}".encode()).hexdigest()[:8]


QUEUE_FIELDS = {"id", "session", "command", "what", "why", "added", "deadline", "attempts", "state"}
LOCK_WAIT_S = 3  # well inside the hook's timeout: a hook killed by its timeout lets the command through


def queue_read():
    """The queue, less any entry that is not a whole one: a hand edit or a torn write costs that entry, not every round."""
    return {k: e for k, e in load_state(QUEUE).items() if isinstance(e, dict) and QUEUE_FIELDS <= e.keys()}


def queue_change(fn):
    """Run fn(queue) under the queue's lock. fn returns None when it changed nothing, so the file is left alone, or the
    entries whose dashboard row changes. Raises when the lock is not had within LOCK_WAIT_S."""
    reap.STATE.mkdir(parents=True, exist_ok=True)
    with open(reap.STATE / "pressure-queue.lock", "w") as lock:
        until = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > until:
                    raise TimeoutError("pressure-queue.lock held")
                time.sleep(0.05)
        q = queue_read()
        out = fn(q)
        changed = [dict(e) for e in out or ()]
        if out is not None:
            cutoff = time.time() - QUEUE_KEEP_S
            save_state({k: e for k, e in q.items()
                        if e["state"] in ("waiting", "retrying") or e.get("ended", e["added"]) >= cutoff}, QUEUE)
    for e in changed:
        queue_note(e)
    return changed


def hhmm(t):
    return time.strftime("%H:%M", time.localtime(t))


def queue_note(e):
    """The entry's dashboard row: open while it waits or once it ended badly, resolved once the retry went through."""
    key = f"pressure:queue:{e['id']}"
    state = e["state"]
    if state == "done":
        return dash_note("resolve", key=key)
    if state == "waiting":
        title = f"{e['what']} 대기 중(PC 사용량 위험: {e['why']}) · 기한 {hhmm(e['deadline'])}, 부하가 내려가면 요청한 세션이 같은 명령을 다시 실행"
    elif state == "retrying":
        title = f"{e['what']} 재시도 중(대기 {e['attempts']}회)"
    elif state == "failed":
        title = f"{e['what']} 재시도 실패: {e.get('error') or '?'}"
    else:
        title = f"{e['what']} 포기: 기한 {hhmm(e['deadline'])}까지 PC 사용량 위험이 이어짐({e['why']})"
    # The copy button gets the list, not the held command: run from anywhere else, it would skip its session's checks.
    return dash_note("add", f"[{e['id']}] {title}", key=key, command=shlex.join(["python3", str(HERE / "pressure.py"), "queue"]))


def enqueue(session, cwd, command, what, why, cfg, now=None):
    """The entry for this session's command, added or, while it still waits, counted again with its first deadline.
    A retry denied again waits anew, with a fresh deadline."""
    now = now or time.time()
    qid, seen = queue_id(session, command), []

    def add(q):
        e = q.get(qid)
        if e and e["state"] in ("waiting", "retrying"):
            e.update(attempts=e["attempts"] + 1, why=", ".join(why))
            seen.append(dict(e))
            if e["state"] == "waiting":  # its dashboard row already says so
                return []
            e.update(state="waiting", deadline=now + e.get("window", float(cfg["queue_minutes"]) * 60))
        else:
            e = q[qid] = {"id": qid, "session": session, "cwd": cwd, "command": command, "what": what,
                          "why": ", ".join(why), "added": now, "window": float(cfg["queue_minutes"]) * 60,
                          "attempts": 1, "state": "waiting"}
            e["deadline"] = now + e["window"]
            log(f"queue {qid} {what} waiting · {e['why']}")
        return [e]
    return (queue_change(add) or seen)[0]


# Which state each event may leave from: an outcome only of a retry. The session's own run of the same command after
# its entry gave up or failed is a retry too, so a later success clears that row.
MOVES = {"retrying": ("waiting", "gave_up", "failed"), "done": ("retrying",), "failed": ("retrying",),
         "gave_up": ("waiting",)}
NO_OUTCOME = "결과가 오지 않음: 다른 hook·권한 확인이 막았거나 세션이 끝남"


def queue_mark(session, command, state, error=None, now=None):
    """Move this session's entry for command to state; None when it has none in the state that moves there."""
    qid = queue_id(session, command)
    if (queue_read().get(qid) or {}).get("state") not in MOVES[state]:  # the common case: nothing queued, no lock
        return None

    def mark(q):
        e = q.get(qid)
        if not e or e["state"] not in MOVES[state]:
            return None
        t = now or time.time()
        if state == "retrying":
            # The hook marks a retry before other hooks and the permission check have had their say; an outcome that
            # never comes ends it after one more window of the entry's own length.
            e["deadline"] = t + e.get("window", 15 * 60)
            e.pop("error", None)
        else:
            e["ended"] = t
        e["state"] = state
        if error:
            e["error"] = str(error)[:200]
        log(f"queue {qid} {e['what']} {state}" + (f" · {error}" if error else ""))
        return [e]
    return next(iter(queue_change(mark)), None)


def queue_expire(now=None):
    """Open entries past their deadline end: a waiting one gives up (its session stopped waiting or never came back),
    a retry with no outcome fails (something else held the call, or the session ended)."""
    now = now or time.time()

    def overdue(q):
        return [e for e in q.values() if e["state"] in ("waiting", "retrying") and e["deadline"] <= now]
    if not overdue(queue_read()):
        return []

    def expire(q):
        gone = overdue(q)
        for e in gone:
            if e["state"] == "waiting":
                e.update(state="gave_up", ended=now)
            else:
                e.update(state="failed", ended=now, error=NO_OUTCOME)
            log(f"queue {e['id']} {e['what']} {e['state']}")
        return gone or None
    return queue_change(expire)


def fresh(st, cfg, now):
    """A round scanned in the last gate_fresh_minutes."""
    return bool(st.get("scan_t")) and now - st["scan_t"] < float(cfg["gate_fresh_minutes"]) * 60


def gate_open(level, st, cfg, now):
    """What the hook lets through: ok, or warn once a round scanned in the last gate_fresh_minutes."""
    return level == "ok" or level == "warn" and fresh(st, cfg, now)


def queue_wait(qid, cfg, poll=30.0, now_fn=time.time, sleep=time.sleep, measure_fn=None, out=print):
    """0 once the hook would let the entry's command through, so its session runs it again; 3 when it should not:
    the deadline passed (recorded as given up), or the entry is gone or no longer waiting."""
    while True:
        e = queue_read().get(qid)
        if not e or e["state"] != "waiting":
            out(f"{qid}: {'없음' if not e else e['state']} — 다시 실행하지 않는다")
            return 3
        now = now_fn()
        if now >= e["deadline"]:
            queue_expire(now)
            out(f"{qid}: 기한 {hhmm(e['deadline'])} 지남, 포기로 기록 — 다시 실행하지 않는다. {e['what']}: {e['why']}")
            return 3
        st = load_state()
        m = snapshot(cfg, st, now, measure_fn)
        level, why = judge(m, cfg)
        if gate_open(level, st, cfg, now):
            out(f"{qid}: PC 사용량 {LEVEL_KO[level]}, 같은 세션에서 같은 명령을 다시 실행한다: {e['command']}")
            return 0
        sleep(poll)


def cmd_queue(argv, cfg):
    if argv[:1] == ["wait"] and len(argv) > 1:
        return queue_wait(argv[1], cfg)
    for e in sorted(queue_read().values(), key=lambda e: e["added"]):
        print(f"{e['id']}  {e['state']}  {time.strftime('%m-%d %H:%M', time.localtime(e['added']))}  "
              f"기한 {hhmm(e['deadline'])}  대기 {e['attempts']}회  {e['what']}  ({e['why']})"
              + (f"  {e['error']}" if e.get("error") else ""))
    return 0


# ---------------------------------------------------------------- round

def reaped_lines(out):
    """reap's lines for items it acted on: skipped and failed ones and notes are not 'cleaned'."""
    return [line for line in out if line.startswith("- ") and not line.startswith(("- 건너뜀", "- 실패", "- 메모"))]


def leak_notice(cfg, m, st, record, now, dry_run, out, send_fn):
    """Swap growing for half an hour holds nothing, but someone should find what leaks: one note per renotify_minutes."""
    growth, warn = m.get("swap_growth_gb_h"), cfg["swap_growth_gb_h"][0]
    if growth is None or warn is None or growth < warn:
        if growth is not None and st.get("leak_dash_t") and not dry_run:
            dash_note("resolve", key="pressure:swap-growth")
            st.pop("leak_dash_t")
        return
    if now - st.get("leak_t", 0) < float(cfg["renotify_minutes"]) * 60:
        return
    subject = f"PC swap 이 시간당 {growth:g}GB 늘고 있음(지금 {m.get('swap_gb')}GB): 메모리 누수 의심, 막지는 않음"
    body = f"{summary(m)}\n`top -o mem` 으로 메모리가 계속 느는 프로세스를 찾는다. 재시작·종료는 소유자가 정한다"
    out(f"- 누수 알림: {subject}")
    if dry_run:
        return
    record["leak_t"] = now
    to = notify_to(cfg)
    if not (to and send_fn(to, subject, body, "status")) and dash_note("add", subject, key="pressure:swap-growth"):
        record["leak_dash_t"] = now
    log(f"leak {subject}")


def round_once(cfg, dry_run, out=print, now=None, measure_fn=measure, sample_fn=None, plan_fn=plan_targets,
               reap_fn=reap_plan, consumers_fn=consumers, send_fn=send):
    now = now or time.time()
    st = load_state()  # a dry run reads the last counters too, and saves nothing
    st.update((sample_fn or (lambda s, t: take_sample(s, t, cfg)))(st, now))
    m = snapshot(cfg, st, now, measure_fn)
    level, why = judge(m, cfg)
    out(f"# PC 사용량 {LEVEL_KO[level]} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(now))})\n")
    out(f"- 측정: {summary(m)}")
    if why:
        out(f"- 임계값 초과: {', '.join(why)}")
    record = {"t": now, "level": level, "metrics": m, "why": why}
    if level != "danger" and st.get("dash_t") and not dry_run:
        dash_note("resolve")
        st.pop("dash_t")
    leak_notice(cfg, m, st, record, now, dry_run, out, send_fn)
    if not dry_run:
        for e in queue_expire(now):
            out(f"- 대기열 {e['id']} 포기(기한 지남): {e['what']}")
    if not dry_run:  # before the sweep, which can take minutes: the hook and check judge this sample meanwhile
        save_state({**st, **record})
    if level == "ok":
        if not dry_run:
            if st.get("level") not in (None, "ok"):
                log(f"ok {summary(m)}")
            save_state({**st, **record})
        return 0
    if level == "warn" and st.get("level") == "warn" and now - st.get("scan_t", 0) < float(cfg["rescan_minutes"]) * 60:
        out(f"- 경고가 이어지는 중: 안전 대상 정리는 {(now - st['scan_t']) / 60:.0f}분 전에 했고 {cfg['rescan_minutes']:g}분마다 한다")
        if not dry_run:
            save_state({**st, **record})
        return 1
    kinds = safe_kinds(cfg)
    plan_path = PLAN.with_name("pressure-plan.dry-run.json") if dry_run else PLAN
    # Set even when the scan failed: the warn deny asked for one round, and a failure every round must not hold
    # every heavy start at warn.
    record["scan_t"] = now
    targets, notes = plan_fn(kinds, plan_path)
    out(f"- 안전 대상 종류: {', '.join(kinds) or '없음'}")
    if unknown_kinds(cfg):
        out(f"- 메모: 이 reap.py 에 없는 종류라 뺌: {', '.join(unknown_kinds(cfg))}")
    for n in notes:
        out(f"- 메모: {n}")
    to, message, done = notify_to(cfg), None, []
    if targets:
        out(f"\n## 정리 {'계획' if dry_run else ''} {len(targets)}개\n")
        for i in targets:
            out(f"- {reap.label(i)}: {i['why']}")
        done = [] if dry_run else reaped_lines(reap_fn(plan_path))
        if not dry_run:
            out("\n## reap 결과\n")
            out("\n".join(done) or "- 정리된 것 없음 (모두 건너뜀 또는 실패)")
        names = [reap.label(i) for i in targets] if dry_run else [line[2:] for line in done]
        if names:
            # reap's label is "<kind> pid N (…) <cmd>" or "<kind> <name> (…)": the part before the details names it.
            short = [n.split(" (")[0].split(": ")[0][:60] for n in names[:3]]
            message = ("status", f"PC 사용량 {LEVEL_KO[level]}: {'정리 예정' if dry_run else '정리함'} {len(names)}개 "
                       f"({'; '.join(short)}{' 외' if len(names) > 3 else ''}) "
                       f"— 이유: {', '.join(why)}", "\n".join(names))
    elif targets is None:
        out("\n- 정리 대상을 읽지 못해 이번 회차는 정리하지 않음")
    # Danger with nothing gone, whether nothing was listed or reap skipped or failed every target: report and stop.
    if level == "danger" and not (targets and (dry_run or done)):
        procs, projects = consumers_fn(cfg)
        lines = consumer_lines(procs, projects)
        out("\n## 정리할 것 없음: 상위 소비자 (보고만, 종료하지 않음)\n")
        out("\n".join(lines) or "- 읽지 못함")
        # Consumers shift every round, so only entering danger or renotify_minutes passing sends again.
        if st.get("level") != "danger" or now - st.get("danger_t", 0) >= float(cfg["renotify_minutes"]) * 60:
            top = (lines[:1] or ["읽지 못함"])[0].lstrip("- ")
            # `escalation` needs an active dispatch of the sender, which a LaunchAgent never has.
            message = ("status", f"PC 사용량 위험, 정리할 안전 대상 없음: {', '.join(why)} — 최대 소비자 {top[:80]}",
                       f"{summary(m)}\n" + "\n".join(lines) + "\n남의 프로세스·활동 중 카드는 종료하지 않았다. "
                       "새 무거운 작업과 dispatch 를 멈추고, 끝난 카드의 소유자가 정리하게 한다")
            record["danger_t"] = now
    if message:
        kind, subject, body = message
        out(f"\n## 알림 {'(보내지 않음, dry-run)' if dry_run else ''}\n")
        out(f"- 받는 곳: {to or '없음 (reap.pressure.notify_to 또는 reap.inbox_to 미설정)'}")
        out(f"- 제목: {subject}")
        if not dry_run and not (to and send_fn(to, subject, body, kind)):
            # A LaunchAgent has no Orca sender terminal, so `orchestration send` fails there every time: retrying
            # each round only repeats it. A danger goes to the dashboard instead and waits for renotify_minutes.
            out(f"- {to} 로 보내지 못함" if to else "- 받는 곳 없음")
            if "danger_t" in record and dash_note("add", subject):
                record["dash_t"] = now
                out("- 대시보드 Needs you 에 올림")
    if not dry_run:
        if message or st.get("level") != level:
            log(f"{level} {', '.join(why)}" + (f" · {message[1]}" if message else ""))
        save_state({**st, **record})
    return 1 if level == "warn" else 2


# ---------------------------------------------------------------- launchd

def agent_target():
    return f"gui/{os.getuid()}/{LABEL}"


def launch_path():
    """launchd starts jobs with a bare PATH; reap needs git, gh, orca, docker and lsof."""
    dirs = [os.path.dirname(p) for p in map(shutil.which, ("orca", "docker", "gh", "git")) if p]
    return ":".join(dict.fromkeys(dirs + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/bin"]))


def spec(interval):
    return {"Label": LABEL,
            "ProgramArguments": [shutil.which("python3") or sys.executable, "-I", str(pathlib.Path(__file__).resolve()),
                                 "round"],
            "StartInterval": int(interval), "RunAtLoad": True,
            "ProcessType": "Background", "LowPriorityIO": True, "Nice": 10,
            "EnvironmentVariables": {"PATH": launch_path(), "HOME": str(pathlib.Path.home())},
            "StandardOutPath": "/dev/null", "StandardErrorPath": str(LOG)}


def plist_spec():
    with contextlib.suppress(OSError, ValueError, plistlib.InvalidFileException):
        return plistlib.loads(PLIST.read_bytes())
    return None


def loaded():
    return subprocess.run(["launchctl", "print", agent_target()], capture_output=True).returncode == 0


def cmd_install(here_ok):
    if sys.platform != "darwin":
        print("no launchd on this OS; run `pressure.py round` from cron instead", file=sys.stderr)
        return 1
    repo = HERE.parents[2]
    if not (repo / ".git").is_dir() and not here_ok:  # a worktree goes away under launchd
        print(f"{repo} is not a main git checkout; install from the checkout sessions load (or pass --here)",
              file=sys.stderr)
        return 1
    reap.STATE.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    PLIST.write_bytes(plistlib.dumps(spec(config()["interval"])))
    for _ in range(5):  # bootstrap right after bootout can fail with error 5 until the old job is gone
        p = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], capture_output=True, text=True)
        if p.returncode == 0:
            DISABLED.unlink(missing_ok=True)
            print(f"wrote {PLIST} and loaded {agent_target()} (every {config()['interval']}s)")
            return 0
        time.sleep(1)
    print(f"launchctl bootstrap failed: {p.stderr.strip()}", file=sys.stderr)
    return 1


def cmd_uninstall():
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    PLIST.unlink(missing_ok=True)
    reap.STATE.mkdir(parents=True, exist_ok=True)
    DISABLED.touch()
    print(f"removed {agent_target()}")
    return 0


def disabled():
    return DISABLED.exists()


def cmd_status():
    st = load_state()
    print(f"LaunchAgent: {'loaded' if loaded() else 'not loaded'} ({PLIST}{'' if PLIST.exists() else ', absent'})"
          + (f"; turned off by uninstall ({DISABLED}), so setup apply leaves it off" if disabled() else ""))
    if st:
        print(f"last round: {time.strftime('%Y-%m-%d %H:%M', time.localtime(st['t']))} "
              f"{LEVEL_KO.get(st.get('level'), '?')} · {summary(st['metrics'])}")
    return 0


def locked():
    reap.STATE.mkdir(parents=True, exist_ok=True)
    lock = open(reap.STATE / "pressure.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def overrides(argv):
    return [tuple(argv[i + 1].split("=", 1)) for i, a in enumerate(argv[:-1]) if a == "--set" and "=" in argv[i + 1]]


def main(argv):
    cmd = argv[0] if argv else ""
    cfg = config(overrides(argv)) if cmd in ("check", "round", "queue") else None
    if cmd == "check":
        m = snapshot(cfg, load_state())
        level, why = judge(m, cfg)
        if "--json" in argv:
            print(json.dumps({"level": level, "why": why, "metrics": m}, ensure_ascii=False))
        else:
            print(f"{LEVEL_KO[level]}: {summary(m)}" + (f" — {', '.join(why)}" if why else ""))
        return LEVELS.index(level)
    if cmd == "round":
        dry = "--dry-run" in argv
        lock = None if dry else locked()
        if not dry and not lock:
            return 0  # another round is running
        try:
            return round_once(cfg, dry, measure_fn=lambda: measure(always_on=cfg["always_on"]))
        except Exception as e:  # launchd reruns it; leave one line instead of a traceback per round
            if dry:
                raise
            log(f"round failed: {type(e).__name__}: {e}")
            return 3
    if cmd == "queue":
        return cmd_queue(argv[1:], cfg)
    if cmd == "install":
        return cmd_install("--here" in argv)
    if cmd == "uninstall":
        return cmd_uninstall()
    if cmd == "status":
        return cmd_status()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
