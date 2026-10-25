#!/usr/bin/env python3
"""Close what finished cards left behind, by rules fixed in code, with no model in the loop.

Usage:
  reap-hard run [--dry-run] [--json]     one pass; --dry-run decides and logs, and touches nothing
  reap-hard last                         the last pass in one line (`orch brief` shows it)
  reap-hard install [--here] | uninstall | status   macOS LaunchAgent, a pass every `interval` seconds

A pass, in order (each later step sees what the earlier ones closed):
  card      an Orca worktree under `workspaces` whose branch PR (its own, never a fork's) is merged or closed and
            carried the worktree's HEAD, with no open PR on the branch; clean, untracked files included; nothing off
            the remote unless the PR carried HEAD; every Orca terminal silent and its sessions' transcripts quiet
            for `idle_minutes`; no process in it that a person started from their own shell.
            -> `orca terminal close --worktree path:P --all`, ignored files to the trash, `orca worktree rm --run-hooks`
  stack     a compose project (containers compose created) whose working directories are all gone, or are cards
            closed in this pass -> `docker compose -p P down`. Never -v: volumes and images stay.
  listener  a node/bun/deno/vite process reparented to pid 1, listening on TCP, at least `idle_minutes` old, whose
            cwd was removed or lies in a card that is gone -> SIGTERM to that pid alone, after pid and start time match.
  serve     a `tailscale serve` entry in `serve_ranges` whose local port has had no listener for two passes in a row
            -> `tailscale serve --https=<port> off` (or --tcp)
Before any rule, the protect file is read; without it nothing runs (exit 2). It holds `port N`, `pid N`, `path P`
(a card or any prefix) and `project NAME` lines; `#` starts a comment. Pinned cards, the main checkout, the
compose projects in reap.pressure.always_on and anything a person's shell started are always kept.
Each decision that acts, or would act under --dry-run, and each failure is one JSON line in
<state>/reap-hard.jsonl; the pass's summary is <state>/reap-hard-last.json.
Settings: "reap-hard" in ~/.claude/agent-skills.json (DEFAULTS).
"""
import contextlib, fcntl, json, os, pathlib, plistlib, re, shlex, shutil, signal, subprocess, sys, time, uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "skills" / "reap-resources" / "scripts"))
import preview_watch as pw  # noqa: E402
import reap  # noqa: E402

LABEL = "io.github.jeongjaesoon.reap-hard"
PLIST = pathlib.Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
STATE_DIR = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE", "~/.local/state/agent-skills")).expanduser()
LOG = STATE_DIR / "reap-hard.jsonl"
LAST = STATE_DIR / "reap-hard-last.json"
OUT = STATE_DIR / "reap-hard.log"
DEFAULTS = {
    "interval": 900,
    "idle_minutes": 30,
    "workspaces": "~/orca/workspaces",
    "protect_file": "~/.config/agent-skills/reap-protect.txt",
    # Hand-started previews and Docker's ephemeral range, as preview-watch publishes them; a service's own
    # fixed port outside these is never turned off, even while it is down.
    "serve_ranges": [[4500, 4599], [32768, 60999]],
}
DEV_SERVERS = ("node", "bun", "deno", "vite")


def config():
    try:
        mine = json.loads(CONFIG.read_text()).get("reap-hard") or {}
    except (OSError, ValueError):
        mine = {}
    return {**DEFAULTS, **mine}


def always_on():
    try:
        return set(((json.loads(CONFIG.read_text()).get("reap") or {}).get("pressure") or {}).get("always_on") or ())
    except (OSError, ValueError):
        return set()


class NoProtect(Exception):
    pass


def read_protect(path):
    """{"port": {int}, "pid": {int}, "path": [str], "project": {str}}. A missing file or a line it cannot read
    stops the pass: a guess at what to keep is how a lead's card goes."""
    try:
        text = pathlib.Path(path).expanduser().read_text()
    except OSError as e:
        raise NoProtect(f"보호 목록 {path} 을 읽지 못함({e.strerror}): 아무것도 하지 않는다")
    out = {"port": set(), "pid": set(), "path": [], "project": set()}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        kind, _, value = line.partition(" ")
        value = value.strip()
        if kind in ("port", "pid") and value.isdigit():
            out[kind].add(int(value))
        elif kind == "path" and value:
            out["path"].append(reap.real(os.path.expanduser(value)))
        elif kind == "project" and value:
            out["project"].add(value)
        else:
            raise NoProtect(f"보호 목록 {path}:{n} 을 읽지 못함: {line!r}")
    return out


def protected_path(p, protect):
    return any(reap.under(p, q) or reap.under(q, p) for q in protect["path"])


# ---------------------------------------------------------------- judgements (pure)

def judge_card(card, facts, pr, terminals, quiet_s, now, idle_s, protect, people, holders):
    """(target, why). card: a row of `orca worktree list`; facts: reap.worktree_facts; pr: reap's PR state of
    facts["pr"]; terminals: the card's rows of `orca terminal list`, None when unreadable; quiet_s: seconds since
    its transcripts last changed, None when it has none; people: pids in it a person started; holders: pids in it
    that are protected or listen on a protected port."""
    p = reap.real(card["path"])
    if card.get("isMainWorktree") or card.get("isPinned"):
        return False, "메인 체크아웃 또는 고정된 카드"
    if protected_path(p, protect):
        return False, "보호 목록의 경로"
    if not facts or facts.get("main"):
        return False, "git 상태를 읽지 못함" if not facts else "메인 체크아웃"
    if facts.get("pr_unread"):
        return False, "브랜치의 PR 을 읽지 못함(gh)"
    if not facts.get("pr"):
        return False, "PR 없음"
    if pr is None:
        return False, f"PR {facts['pr']} 상태를 읽지 못함"
    if pr["state"] == "OPEN":
        return False, f"열린 PR {facts['pr']}"
    if pr.get("headRefOid") != facts.get("head"):
        # A lead may reuse a merged branch's name; only the PR that carried this HEAD says the work is done.
        return False, f"PR {facts['pr']} {pr['state'].lower()}, 그 뒤 HEAD 가 바뀜"
    if facts.get("unreadable"):
        return False, "git 상태를 읽지 못함"
    if facts.get("dirty"):
        return False, "커밋 안 된 변경(추적 안 되는 파일 포함)"
    if holders:
        return False, f"보호 대상 프로세스가 이 카드에 있음(pid {holders[0]})"
    if people:
        return False, f"사람이 띄운 셸의 자식이 이 카드에 있음(pid {people[0]})"
    if terminals is None:
        return False, "터미널 목록을 읽지 못함"
    loud = [t for t in terminals if now - (t.get("lastOutputAt") or 0) / 1000 < idle_s]
    if loud:
        return False, f"터미널 {loud[0].get('handle')} 출력이 {int(idle_s // 60)}분 안에 있음"
    if quiet_s is not None and quiet_s < idle_s:
        return False, f"세션 기록이 {int(quiet_s // 60)}분 전에 바뀜"
    return True, f"PR {facts['pr']} {pr['state'].lower()}, HEAD 가 PR 에 있음, 깨끗함, {int(idle_s // 60)}분 넘게 조용"


def judge_stack(name, stack, gone, protect, keep):
    """(target, why). gone(dir) says whether a working directory is gone or a card closed in this pass."""
    if name in protect["project"] or name in keep:
        return False, "보호 목록·상시 서비스의 프로젝트"
    if hit := sorted(stack["ports"] & protect["port"]):
        return False, f"보호 포트 {hit[0]}"
    if not stack["dirs"]:
        return False, "작업 폴더 라벨 없음"
    alive = sorted(d for d in stack["dirs"] if not gone(d))
    if alive:
        return False, f"작업 폴더 {alive[0]} 있음"
    return True, "소유 worktree 가 없어짐: " + ", ".join(sorted(stack["dirs"]))


def dev_server(cmd):
    argv = cmd.split()[:3]
    return bool(argv) and (os.path.basename(argv[0]) in DEV_SERVERS or any(os.path.basename(a) == "vite" for a in argv))


def judge_listener(r, port, cwd, now_gone, card_of, cards_alive, idle_s, protect, rows):
    """(target, why) for a process holding a TCP listener. now_gone(cwd) is reap.cwd_gone; card_of(path) is the
    card directory a path lies in, or None."""
    if r["ppid"] != 1:
        return False, "부모 프로세스가 살아 있음"
    if not dev_server(r["cmd"]):
        return False, "개발 서버(node·bun·deno·vite)가 아님"
    if port in protect["port"] or r["pid"] in protect["pid"]:
        return False, "보호 목록의 포트·pid"
    if reap.human_owned(r["pid"], rows):
        return False, "사람이 띄운 셸의 자식"
    if r["age"] < idle_s:
        return False, f"{int(idle_s // 60)}분 안에 시작됨"
    if cwd is None:
        return False, "cwd 를 읽지 못함"
    if now_gone(cwd):
        return True, f"cwd {cwd[0]} 가 사라짐"
    card = card_of(cwd[0])
    if card and card not in cards_alive:
        return True, f"cwd 의 카드 {card} 가 닫힘"
    return False, "cwd 가 살아 있는 곳"


def serve_entries(status):
    """[(flag, public port, local port)] for every serve entry that forwards to this machine."""
    out = []
    for hostport, web in (status.get("Web") or {}).items():
        proxy = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy") or ""
        m = re.match(r"https?://(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)", proxy)
        if m:
            out.append(("--https", int(hostport.rpartition(":")[2]), int(m.group(1))))
    for port, tcp in (status.get("TCP") or {}).items():
        m = re.match(r"(?:127\.0\.0\.1|localhost):(\d+)$", tcp.get("TCPForward") or "")
        if m:
            out.append(("--tcp", int(port), int(m.group(1))))
    return out


def judge_serve(entry, listening, dark_before, ranges, protect):
    """(target, why). dark_before: the public ports the last pass saw with no listener."""
    flag, public, local = entry
    if public in protect["port"] or local in protect["port"]:
        return False, "보호 포트"
    if not any(lo <= local <= hi for lo, hi in ranges):
        return False, "미리보기 포트 범위 밖"
    if local in listening:
        return False, "리스너 있음"
    if public not in dark_before:
        return False, "리스너 없음(첫 회차, 다음 회차에 다시 본다)"
    return True, f"127.0.0.1:{local} 리스너가 두 회차 연속 없음"


# ---------------------------------------------------------------- reading the machine

def listeners_all():
    """{port: pid} for every TCP listener of this user, any address."""
    out = subprocess.run(["lsof", "-nP", "-a", "-u", str(os.getuid()), "-iTCP", "-sTCP:LISTEN", "-F", "pn"],
                         capture_output=True, text=True, timeout=20).stdout
    found, pid = {}, 0
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and line.rpartition(":")[2].isdigit():
            found.setdefault(int(line.rpartition(":")[2]), pid)
    return found


def people_in(path, rows, cwd_of):
    """Pids in or under path that a person started: a process whose cwd lies there, or anything under a shell whose
    cwd lies there, owned by a human shell with no Claude session between. The idle shells themselves don't count."""
    inside = {pid for pid, c in cwd_of.items() if reap.under(reap.real(c), path)}
    shells = [pid for pid in inside if reap.human_shell(pid, rows)]
    pids = inside | {q for s in shells for q in reap.tree(s, rows)}
    return sorted(p for p in pids if p in rows and reap.human_owned(p, rows) and not reap.human_shell(p, rows))


def card_dir(path, root):
    """<root>/<repo>/<card> that path lies in, or None."""
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return os.path.join(root, parts[0], parts[1]) if len(parts) >= 2 and parts[0] != ".." else None


def orca(*args):
    return reap.orca_json(*args)


def scan(cfg, protect, dark_before, now=None):
    """Every decision of one pass, in order, with no action taken."""
    now = now or time.time()
    idle_s = float(cfg["idle_minutes"]) * 60
    root = reap.real(os.path.expanduser(cfg["workspaces"]))
    items, notes = [], []
    rows = reap.processes()
    cwd_pairs = reap.cwds(inodes=True)
    if cwd_pairs is None:
        notes.append("프로세스 cwd 를 읽지 못함: 카드·리스너는 건너뜀")
    cwd_of = {p: c for p, (c, _) in (cwd_pairs or {}).items()}
    listening = listeners_all()
    held_ports = {pid for port, pid in listening.items() if port in protect["port"]}

    closing = set()
    wt = orca("worktree", "list", "--limit", "10000")
    if not isinstance(wt, dict) or wt.get("truncated"):
        notes.append("orca worktree list 를 읽지 못함 또는 잘림: 카드는 건너뜀")
    elif cwd_pairs is not None:
        cards = [w for w in wt.get("worktrees") or [] if w.get("hostId", "local") == "local"
                 and card_dir(reap.real(w.get("path") or "/"), root) == reap.real(w.get("path") or "/")]
        quiet = reap.transcript_quiet(pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
                                      / "projects", now)
        pr_state = reap.pr_reader()
        for w in cards:
            p = reap.real(w["path"])
            if not os.path.isdir(p):
                continue
            facts = reap.worktree_facts(p, None, None, cwd_of)
            pr = pr_state(facts["pr"]) if facts and facts.get("pr") else None
            term = orca("terminal", "list", "--worktree", f"path:{p}")
            terminals = None if not isinstance(term, dict) else term.get("terminals") or []
            inside = [pid for pid, c in cwd_of.items() if reap.under(reap.real(c), p)]
            holders = sorted(pid for pid in inside if pid in protect["pid"] or pid in held_ports)
            ok, why = judge_card(w, facts, pr, terminals, quiet(p), now, idle_s, protect,
                                 people_in(p, rows, cwd_of), holders)
            item = {"kind": "card", "id": p, "target": ok, "why": why}
            if ok:
                closing.add(p)
                item.update(head=facts["head"], regenerated=facts.get("regenerated") or [])
            items.append(item)

    stacks = reap.compose_stacks()
    if stacks is None:
        notes.append("docker 를 읽지 못함: 스택은 건너뜀")
    keep = always_on()
    downing = set()
    for name, s in sorted((stacks or {}).items()):
        ok, why = judge_stack(name, s, lambda d: not os.path.isdir(d) or reap.real(d) in closing
                              or any(reap.under(reap.real(d), c) for c in closing), protect, keep)
        items.append({"kind": "stack", "id": name, "target": ok, "why": why})
        if ok:
            downing |= s["ports"]

    alive = {reap.real(os.path.join(root, r, c)) for r in (os.listdir(root) if os.path.isdir(root) else ())
             if os.path.isdir(os.path.join(root, r)) for c in os.listdir(os.path.join(root, r))
             if os.path.isdir(os.path.join(root, r, c))} - closing
    stopping = set()
    for port, pid in sorted(listening.items()):
        r = rows.get(pid)
        if not r or cwd_pairs is None or r["ppid"] != 1 or not dev_server(r["cmd"]):
            continue
        ok, why = judge_listener(r, port, cwd_pairs.get(pid), lambda c: reap.cwd_gone(*c),
                                 lambda c: card_dir(reap.real(c), root), alive, idle_s, protect, rows)
        items.append({"kind": "listener", "id": f"{pid}:{port}", "pid": pid, "start": r["start"], "cmd": r["cmd"][:120],
                      "target": ok, "why": why})
        if ok:
            stopping.add(port)

    dark = set()
    cli = pw.tailscale_cli(pw.config())
    if cli:
        try:
            status = pw.serve_status(cli)
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
            notes.append(f"tailscale serve status 를 읽지 못함: {e}")
            status = {}
        left = {p for p in listening if p not in stopping and p not in downing}
        ranges = [tuple(r) for r in cfg["serve_ranges"]]
        for e in serve_entries(status):
            if e[2] not in left:
                dark.add(e[1])
            ok, why = judge_serve(e, left, dark_before, ranges, protect)
            items.append({"kind": "serve", "id": f"{e[0]}={e[1]}", "flag": e[0], "port": e[1], "local": e[2],
                          "target": ok, "why": why})
    return items, notes, dark


# ---------------------------------------------------------------- acting

def act(item, cfg):
    k = item["kind"]
    if k == "card":
        p = item["id"]
        cwd_of = reap.cwds()
        facts = cwd_of is not None and reap.worktree_facts(p, None, None, cwd_of)
        if not facts or facts.get("head") != item["head"] or facts.get("dirty") or facts.get("unreadable"):
            return "건너뜀: 판정 뒤 카드가 바뀜"
        if people := people_in(p, reap.processes(), cwd_of):
            return f"건너뜀: 판정 뒤 사람이 띄운 프로세스가 생김(pid {people[0]})"
        reap.run(["orca", "terminal", "close", "--worktree", f"path:{p}", "--all", "--json"])
        return "터미널 닫음, " + reap.act({"kind": "orca-worktree", "key": f"orca-worktree:{p}", "name": p,
                                           "regenerated": item.get("regenerated")}, [])
    if k == "stack":
        reap.run(["docker", "compose", "-p", item["id"], "down"])  # no -v: volumes are data
        return "docker compose down(볼륨·이미지 유지)"
    if k == "listener":
        rows = reap.processes()
        r = rows.get(item["pid"])
        if not r or r["start"] != item["start"]:
            return "건너뜀: 사라졌거나 다른 프로세스"
        if reap.human_owned(item["pid"], rows):
            return "건너뜀: 사람이 띄운 셸의 자식"
        os.kill(item["pid"], signal.SIGTERM)
        return "SIGTERM"
    if k == "serve":
        cli = pw.tailscale_cli(pw.config())
        p = pw.ts(cli, "serve", f"{item['flag']}={item['port']}", "off")
        if p.returncode:
            raise RuntimeError(p.stderr.strip()[:200])
        return "tailscale serve off"
    raise RuntimeError(f"모르는 종류 {k}")


def log(rows):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_last(summary):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LAST.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False))
    tmp.replace(LAST)


def read_last():
    try:
        return json.loads(LAST.read_text())
    except (OSError, ValueError):
        return None


def run_pass(dry, cfg=None, now=None):
    cfg = cfg or config()
    now = now or time.time()
    run_id = uuid.uuid4().hex[:8]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
    try:
        protect = read_protect(cfg["protect_file"])
    except NoProtect as e:
        summary = {"t": now, "at": stamp, "run": run_id, "dry_run": dry, "error": str(e)}
        if not dry:
            write_last(summary)
        log([{**summary, "kind": "pass"}])
        return summary, 2
    last = read_last() or {}
    items, notes, dark = scan(cfg, protect, set(last.get("dark") or ()), now)
    rows = []
    for i in items:
        if not i["target"]:
            continue
        row = {"ts": stamp, "run": run_id, "dry_run": dry, "kind": i["kind"], "id": i["id"], "why": i["why"]}
        if dry:
            row["result"] = "dry-run"
        else:
            try:
                row["result"] = act(i, cfg)
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                row["error"] = str(e)[:300]
        i["result"] = row.get("result") or "실패: " + row.get("error", "")
        rows.append(row)
    log(rows)
    summary = {"t": now, "at": stamp, "run": run_id, "dry_run": dry,
               "acted": sum(1 for r in rows if "result" in r), "failed": sum(1 for r in rows if "error" in r),
               "kept": sum(1 for i in items if not i["target"]), "notes": notes, "dark": sorted(dark)}
    if not dry:
        write_last(summary)
    return {**summary, "items": items}, 1 if summary["failed"] else 0


def last_line(now=None, interval=None):
    """One line on the last real pass, for `orch brief`; None when it never ran."""
    s = read_last()
    if not s:
        return None
    now, interval = now or time.time(), interval or config()["interval"]
    mins = int((now - s["t"]) // 60)
    when = time.strftime("%H:%M", time.localtime(s["t"])) + f"({mins}분 전)"
    if s.get("error"):
        return f"🧹 reap-hard {when} 멈춤: {s['error']}"
    stale = " ⚠️ 주기를 넘김(LaunchAgent 확인)" if now - s["t"] > 3 * interval else ""
    return f"🧹 reap-hard {when} 닫음 {s['acted']} · 실패 {s['failed']} · 남김 {s['kept']}{stale}"


def print_pass(res):
    head = "dry-run" if res.get("dry_run") else "실행"
    if res.get("error"):
        print(f"reap-hard {head}: {res['error']}")
        return
    print(f"reap-hard {head} {res['at']} run={res['run']}")
    for i in res["items"]:
        if i["target"]:
            print(f"- {i['kind']} {i['id']}: {i['why']} → {i.get('result')}")
    kept = [i for i in res["items"] if not i["target"]]
    print(f"남김 {len(kept)}개")
    for i in kept:
        print(f"  · {i['kind']} {i['id']}: {i['why']}")
    for n in res["notes"]:
        print(f"- 메모: {n}")


@contextlib.contextmanager
def locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "reap-hard.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("reap-hard: 다른 회차가 도는 중")
        yield


# ---------------------------------------------------------------- LaunchAgent

def agent_target():
    return f"gui/{os.getuid()}/{LABEL}"


def spec(interval):
    tools = [os.path.dirname(t) for t in map(shutil.which, ("orca", "gh", "docker", "lsof")) if t]
    path = ":".join(dict.fromkeys(tools + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin"]))
    return {"Label": LABEL,
            "ProgramArguments": [shutil.which("python3") or sys.executable, "-I", str(pathlib.Path(__file__).resolve()), "run"],
            "StartInterval": int(interval), "RunAtLoad": True, "ProcessType": "Background", "LowPriorityIO": True,
            "EnvironmentVariables": {"PATH": path, "HOME": str(pathlib.Path.home())},
            "StandardOutPath": str(OUT), "StandardErrorPath": str(OUT)}


def cmd_install(here_ok):
    if sys.platform != "darwin":
        print("no launchd on this OS; run `reap-hard run` from cron instead", file=sys.stderr)
        return 1
    if not (HERE.parent / ".git").is_dir() and not here_ok:  # a worktree goes away under launchd
        print(f"{HERE.parent} is not a main git checkout; install from the checkout sessions load (or pass --here)",
              file=sys.stderr)
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
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


def main(argv):
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        with locked():
            res, code = run_pass("--dry-run" in argv)
        if "--json" in argv:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print_pass(res)
        return code
    if cmd == "last":
        print(last_line() or "reap-hard 가 아직 돈 적 없음")
        return 0
    if cmd == "install":
        return cmd_install("--here" in argv)
    if cmd == "uninstall":
        subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
        PLIST.unlink(missing_ok=True)
        print(f"removed {agent_target()}")
        return 0
    if cmd == "status":
        loaded = subprocess.run(["launchctl", "print", agent_target()], capture_output=True).returncode == 0
        print(f"{agent_target()}: {'loaded' if loaded else 'not loaded'}; " + (last_line() or "never ran"))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
