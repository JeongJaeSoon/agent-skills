#!/usr/bin/env python3
"""Watch this machine's load and reap what is safe before it slows down (reap-resources SKILL.md "Pressure").

Usage:
  pressure.py check [--json] [--set KEY=WARN,DANGER]...
                     measure only; exit 0 ok, 1 warn, 2 danger
  pressure.py round [--dry-run] [--set KEY=WARN,DANGER]...
                     measure; at warn or danger reap the safe kinds (reap.py scan + reap --plan); at danger with
                     nothing to reap, name the top consumers and stop. Each outcome is one line to the notify address.
                     --dry-run prints the plan and the message; it writes only its own plan file, reaps and sends nothing
  pressure.py install [--here] | uninstall | status
                     macOS LaunchAgent running `round` every `interval` seconds

No model is called. Thresholds live under "reap" → "pressure" in ~/.claude/agent-skills.json (DEFAULTS); each is
[warn, danger]. Nothing outside reap's own target list is ever touched: no kill by pattern, no compose down -v,
no prune, no volume or image removal.
"""
import contextlib, fcntl, json, os, pathlib, plistlib, re, shlex, shutil, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
REAP = HERE / "reap.py"
sys.path.insert(0, str(HERE))
import reap  # noqa: E402

LABEL = "io.github.jeongjaesoon.pressure-watch"
PLIST = pathlib.Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
STATE_FILE = reap.STATE / "pressure.json"
PLAN = reap.STATE / "pressure-plan.json"
LOG = reap.STATE / "pressure.log"

# Defaults measured on an 18-core, 64 GB Apple Silicon machine (docs/pressure.md has the numbers behind them).
DEFAULTS = {
    "interval": 180,
    "load_per_cpu": [1.0, 2.0],  # 5-minute load / logical CPUs, at or above
    "mem_free_pct": [20, 10],  # kern.memorystatus_level, at or below
    "vm_pressure": [2, 4],  # kern.memorystatus_vm_pressure_level: 1 normal, 2 warn, 4 critical
    "swap_gb": [8, 16],
    "disk_free_gb": [50, 20],  # on the volume holding $HOME, at or below
    "containers": [40, 80],  # running docker containers
    # What reap may close with no one asking: finished cards' registered stacks, leftover process trees of dead
    # sessions, registered GUI apps of merged PRs, reclaimable worker terminals. Kinds reap does not know are dropped.
    "safe_kinds": ["orphan", "compose-stack", "gui-app", "orca-worker"],
    "notify_to": None,  # an Orca address; unset falls back to reap.inbox_to
    "renotify_minutes": 30,  # a danger report with the same consumers is not repeated sooner
    "rescan_minutes": 10,  # a machine that stays at warn is scanned again only after this; danger always scans
    # The hook lets a heavy start through at warn when safe targets were swept this recently. Longer than
    # rescan_minutes plus interval, so a machine that stays at warn under the LaunchAgent is not held.
    "gate_fresh_minutes": 15,
    "top": 5,
}
# (key, metric, higher is worse)
CHECKS = (("load_per_cpu", "load_per_cpu", True), ("mem_free_pct", "mem_free_pct", False),
          ("vm_pressure", "vm_pressure", True), ("swap_gb", "swap_gb", True),
          ("disk_free_gb", "disk_free_gb", False), ("containers", "containers", True))
LEVELS = ("ok", "warn", "danger")
LEVEL_KO = {"ok": "정상", "warn": "경고", "danger": "위험"}


def config(overrides=()):
    cfg = {**DEFAULTS, **(reap.config().get("pressure") or {})}
    for key, value in overrides:
        if key not in DEFAULTS:
            sys.exit(f"--set: unknown key {key}; known: {', '.join(DEFAULTS)}")
        default = DEFAULTS[key]
        if key == "safe_kinds":
            cfg[key] = value.split(",")
        elif isinstance(default, list):
            cfg[key] = [float(x) for x in value.split(",")]
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


def measure(docker=True, run=sh):
    """Every metric this OS offers; a metric it cannot read is None and never raises a level."""
    cpus = os.cpu_count() or 1
    m = {"cpus": cpus, "load5": round(os.getloadavg()[1], 1)}
    m["load_per_cpu"] = round(m["load5"] / cpus, 2)
    level, pressure = sysctl("kern.memorystatus_level"), sysctl("kern.memorystatus_vm_pressure_level")
    m["mem_free_pct"] = int(level) if level and level.isdigit() else None
    m["vm_pressure"] = int(pressure) if pressure and pressure.isdigit() else None
    m["swap_gb"] = swap_gb(sysctl("vm.swapusage"))
    m["disk_free_gb"] = round(shutil.disk_usage(pathlib.Path.home()).free / (1 << 30), 1)
    out = run(["docker", "ps", "-q"], timeout=5) if docker and shutil.which("docker") else None
    m["containers"] = len(out.split()) if out is not None else None
    return m


def judge(m, cfg):
    """(level, reasons): the worst level any metric reaches, and one phrase per metric at warn or above."""
    worst, why = 0, []
    for key, metric, higher in CHECKS:
        v = m.get(metric)
        if v is None:
            continue
        hit = [i + 1 for i, t in enumerate(cfg[key][:2]) if (v >= t if higher else v <= t)]
        if hit:
            worst = max(worst, hit[-1])
            why.append(f"{metric} {v:g} {'≥' if higher else '≤'} {cfg[key][hit[-1] - 1]:g}")
    return LEVELS[worst], why


def summary(m):
    parts = [f"load5 {m['load5']:g}/{m['cpus']}코어"]
    for k, fmt in (("mem_free_pct", "메모리 여유 {}%"), ("vm_pressure", "압박 레벨 {}"), ("swap_gb", "swap {}GB"),
                   ("disk_free_gb", "디스크 여유 {}GB"), ("containers", "컨테이너 {}개")):
        if m.get(k) is not None:
            parts.append(fmt.format(m[k]))
    return ", ".join(parts)


# ---------------------------------------------------------------- state

def load_state():
    with contextlib.suppress(OSError, ValueError):
        st = json.loads(STATE_FILE.read_text())
        if isinstance(st, dict):
            return st
    return {}


def save_state(st):
    reap.STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False))
    tmp.replace(STATE_FILE)


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


# ---------------------------------------------------------------- round

def reaped_lines(out):
    """reap's lines for items it acted on: skipped and failed ones and notes are not 'cleaned'."""
    return [line for line in out if line.startswith("- ") and not line.startswith(("- 건너뜀", "- 실패", "- 메모"))]


def round_once(cfg, dry_run, out=print, now=None, measure_fn=measure, plan_fn=plan_targets, reap_fn=reap_plan,
               consumers_fn=consumers, send_fn=send):
    now = now or time.time()
    st = {} if dry_run else load_state()
    m = measure_fn()
    level, why = judge(m, cfg)
    out(f"# PC 사용량 {LEVEL_KO[level]} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(now))})\n")
    out(f"- 측정: {summary(m)}")
    if why:
        out(f"- 임계값 초과: {', '.join(why)}")
    record = {"t": now, "level": level, "metrics": m, "why": why}
    if level == "ok":
        if not dry_run:
            if st.get("level") not in (None, "ok"):
                log(f"ok {summary(m)}")
            save_state({**st, **record})
        return 0
    if level == "warn" and st.get("level") == "warn" and now - st.get("scan_t", 0) < float(cfg["rescan_minutes"]) * 60:
        out(f"- 경고가 이어지는 중: 안전 대상 정리는 {(now - st['scan_t']) / 60:.0f}분 전에 했고 {cfg['rescan_minutes']:g}분마다 한다")
        save_state({**st, **record})
        return 1
    record["scan_t"] = now
    kinds = safe_kinds(cfg)
    plan_path = PLAN.with_name("pressure-plan.dry-run.json") if dry_run else PLAN
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
            message = ("escalation", f"PC 사용량 위험, 정리할 안전 대상 없음: {', '.join(why)} — 최대 소비자 {top[:80]}",
                       f"{summary(m)}\n" + "\n".join(lines) + "\n남의 프로세스·활동 중 카드는 종료하지 않았다. "
                       "새 무거운 작업과 dispatch 를 멈추고, 끝난 카드의 소유자가 정리하게 한다")
            record["danger_t"] = now
    if message:
        kind, subject, body = message
        out(f"\n## 알림 {'(보내지 않음, dry-run)' if dry_run else ''}\n")
        out(f"- 받는 곳: {to or '없음 (reap.pressure.notify_to 또는 reap.inbox_to 미설정)'}")
        out(f"- 제목: {subject}")
        if to and not dry_run and not send_fn(to, subject, body, kind):
            out(f"- {to} 로 보내지 못함")
            if "danger_t" in record:
                record["danger_t"] = 0  # tried again next round
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
            print(f"wrote {PLIST} and loaded {agent_target()} (every {config()['interval']}s)")
            return 0
        time.sleep(1)
    print(f"launchctl bootstrap failed: {p.stderr.strip()}", file=sys.stderr)
    return 1


def cmd_uninstall():
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    PLIST.unlink(missing_ok=True)
    print(f"removed {agent_target()}")
    return 0


def cmd_status():
    st = load_state()
    print(f"LaunchAgent: {'loaded' if loaded() else 'not loaded'} ({PLIST}{'' if PLIST.exists() else ', absent'})")
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
    cfg = config(overrides(argv)) if cmd in ("check", "round") else None
    if cmd == "check":
        m = measure()
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
            return round_once(cfg, dry)
        except Exception as e:  # launchd reruns it; leave one line instead of a traceback per round
            if dry:
                raise
            log(f"round failed: {type(e).__name__}: {e}")
            return 3
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
