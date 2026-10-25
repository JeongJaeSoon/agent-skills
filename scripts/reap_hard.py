#!/usr/bin/env python3
"""Close what finished cards left behind, by rules fixed in code, with no model in the loop.

Usage:
  reap-hard run [--dry-run] [--json]     one pass; --dry-run decides and logs, and touches nothing (no argument: usage)
  reap-hard last                         the last pass in one line (`orch brief` shows it)
  reap-hard install [--here] | uninstall | status   macOS LaunchAgent, a pass every `interval` seconds

A pass, in order (each later step sees what the earlier ones closed):
  card      an Orca worktree under `workspaces` whose branch PR (its own, never a fork's) is merged or closed and
            carried the worktree's HEAD, with no open PR on the branch; clean, untracked files included; nothing off
            the remote unless the PR carried HEAD; every Orca terminal silent and its sessions' transcripts quiet
            for `idle_minutes`; no process in it that a person started from their own shell; no live lead or orchestrator
            session in it (launch flags or ROLE record), however quiet; no child card left in Orca (a lead's). The whole verdict is taken again right before closing.
            -> `orca terminal close --worktree path:P --all`, ignored files to the trash, `orca worktree rm --run-hooks`
  stack     a compose project (containers compose created) whose working directories, every one absolute and labelled,
            are all gone or cards really closed in this pass -> `docker compose -p P down`. Never -v.
  listener  a node/bun/deno/vite process reparented to pid 1, listening on TCP, at least `idle_minutes` old, whose
            cwd was removed or lies in a card that is gone -> SIGTERM to that pid alone, after pid and start time match.
  serve     a `tailscale serve` entry in `serve_ranges` whose same local port has refused connections two passes in a row
            -> `tailscale serve --https=<port> off` (or --tcp)
Before any rule, the protect file is read; without it nothing runs (exit 2). It holds `port N`, `pid N`, `path P`
(a card or any prefix) and `project NAME` lines; `#` starts a comment. Pinned cards, the main checkout, the
compose projects in reap.pressure.always_on and anything a person's shell started are always kept.
Each decision that acts, or would act under --dry-run, and each failure is one JSON line in
<state>/reap-hard.jsonl; the pass's summary is <state>/reap-hard-last.json.
Settings: "reap-hard" in ~/.claude/agent-skills.json (DEFAULTS).
"""
import contextlib, fcntl, functools, json, os, pathlib, plistlib, re, shutil, signal, socket, subprocess, sys, time, uuid

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

def judge_card(card, facts, pr, terminals, quiet_s, now, idle_s, protect, people, holders, children=(), coordinator=None):
    """(target, why). card: a row of `orca worktree list`; facts: reap.worktree_facts; pr: reap's PR state of
    facts["pr"]; terminals: the card's rows of `orca terminal list`, None when unreadable; quiet_s: seconds since
    its transcripts last changed, None when it has none; people: pids in it a person started; holders: pids in it
    that are protected or listen on a protected port; children: the cards it made that still exist (a lead's);
    coordinator: why a live Claude session in it is a lead or the orchestrator, which keeps it whatever else holds."""
    p = reap.real(card["path"])
    if card.get("isMainWorktree") or card.get("isPinned"):
        return False, "메인 체크아웃 또는 고정된 카드"
    if protected_path(p, protect):
        return False, "보호 목록의 경로"
    if coordinator:
        # A lead waits on its inbox for hours; quiet is its normal state, so no idle rule may close it.
        return False, f"리드·오케스트레이터 보호: {coordinator}"
    if children:
        return False, f"하위 카드 {children[0]} 가 남아 있음(리드)"
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
    if not stack["dirs"] or stack.get("nodir"):
        return False, "작업 폴더 라벨 없는 컨테이너가 있음"
    if rel := sorted(d for d in stack["dirs"] if not os.path.isabs(d)):
        return False, f"상대 경로 작업 폴더 {rel[0]}"
    if any(protected_path(reap.real(d), protect) for d in stack["dirs"]):
        return False, "보호 목록의 경로"
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
    if protected_path(cwd[0], protect):
        return False, "보호 목록의 경로"
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


def serve_key(entry):
    return f"{entry[0]}={entry[1]}>{entry[2]}"


def judge_serve(entry, is_open, dark_before, ranges, protect):
    """(target, why). is_open(port): something answers on it; dark_before: serve_key of each entry the last pass saw
    with nothing answering, so a public port pointed elsewhere since starts over."""
    flag, public, local = entry
    if public in protect["port"] or local in protect["port"]:
        return False, "보호 포트"
    if not any(lo <= local <= hi for lo, hi in ranges):
        return False, "미리보기 포트 범위 밖"
    if is_open(local):
        return False, "리스너 있음"
    if serve_key(entry) not in dark_before:
        return False, "리스너 없음(첫 회차, 다음 회차에 다시 본다)"
    return True, f"127.0.0.1:{local} 리스너가 두 회차 연속 없음"


# ---------------------------------------------------------------- reading the machine

def listeners_all():
    """{port: pid} for every TCP listener of this user, any address; None when lsof failed (it also exits 1, silently,
    when nothing listens)."""
    try:
        p = subprocess.run(["lsof", "-nP", "-a", "-u", str(os.getuid()), "-iTCP", "-sTCP:LISTEN", "-F", "pn"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode and p.stderr.strip():
        return None
    out, found, pid = p.stdout, {}, 0
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


def port_open(port):
    """Something accepts on the loopback port, whoever owns it: lsof sees only this user's sockets."""
    for host in ("127.0.0.1", "::1"):
        with contextlib.suppress(OSError):
            socket.create_connection((host, port), timeout=1).close()
            return True
    return False


def card_dir(path, root):
    """<root>/<repo>/<card> that path lies in, or None."""
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return os.path.join(root, parts[0], parts[1]) if len(parts) >= 2 and parts[0] != ".." else None


def orca(*args):
    return reap.orca_json(*args)


def wt_path(wid):
    """The path in an Orca worktree id `<repo>::<path>[::workspace:<id>]`."""
    return (wid or "").split("::")[1] if "::" in (wid or "") else ""


def read_machine(now):
    """What every card verdict reads once per pass; None for a part that could not be read."""
    cwd_pairs = reap.cwds(inodes=True)
    listening = listeners_all()
    return {"rows": reap.processes(), "cwd_pairs": cwd_pairs,
            "cwd_of": None if cwd_pairs is None else {p: c for p, (c, _) in cwd_pairs.items()},
            "listening": listening, "pr_state": reap.pr_reader(),
            "quiet": reap.transcript_quiet(pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
                                           / "projects", now)}


COORDINATOR_ARGV = re.compile(r"--agent[ =]agent-skills:(lead|orchestrator)\b|--name[ =]lead:")
COORDINATOR_ROLES = ("lead", "orchestrator")
ROLES = STATE_DIR / "roles"


def lstart_epoch(s):
    try:
        return time.mktime(time.strptime(s, "%a %b %d %H:%M:%S %Y"))
    except (ValueError, TypeError):
        return None


def coordinator_in(path, rows, cwd_of, projects, roles=ROLES):
    """Why a live Claude session in path is a lead or the orchestrator, or None: started with `--agent
    agent-skills:lead|orchestrator` (or `--name lead:<slug>`), or a session of this card that has written to its
    transcript since that process started declared `ROLE: lead|orchestrator` (the reorient hook's record).
    Anything it cannot read is an answer too: a reason that keeps the card, never None."""
    inside = [pid for pid, c in cwd_of.items() if reap.under(reap.real(c), path)]
    if missing := [pid for pid in inside if pid not in rows]:
        return f"pid {missing[0]} 의 프로세스 행을 읽지 못함"
    live = [pid for pid in inside if reap.is_claude(rows[pid]["cmd"])]
    for pid in live:
        if m := COORDINATOR_ARGV.search(rows[pid]["cmd"]):
            return f"pid {pid} {m.group(0)}"
    starts = [t for t in (lstart_epoch(rows[pid]["start"]) for pid in live) if t is not None]
    if not live:
        return None
    if not starts:
        return "claude 시작 시각을 읽지 못함"
    d = pathlib.Path(projects) / re.sub(r"[^A-Za-z0-9]", "-", path)
    for f in d.glob("*.jsonl") if d.is_dir() else ():
        try:
            if f.stat().st_mtime < min(starts) - 60:
                continue
            role = json.loads((pathlib.Path(roles) / f"{f.stem}.json").read_text()).get("role")
        except FileNotFoundError:
            continue  # a transcript gone mid-scan, or a session that never declared a role
        except (OSError, ValueError, AttributeError) as e:
            return f"세션 {f.stem[:8]} 의 ROLE 기록을 읽지 못함({type(e).__name__})"
        if role in COORDINATOR_ROLES:
            return f"세션 {f.stem[:8]} ROLE 선언"
    return None


def card_verdict(w, m, cfg, protect, now):
    """(target, why, facts) for one row of `orca worktree list`, read fresh from the machine state m."""
    p = reap.real(w["path"])
    cwd_of = m["cwd_of"]
    if cwd_of is None or m["listening"] is None:
        return False, "프로세스 cwd·리스너를 읽지 못함", None
    facts = reap.worktree_facts(p, None, None, cwd_of)
    pr = m["pr_state"](facts["pr"]) if facts and facts.get("pr") else None
    term = orca("terminal", "list", "--worktree", f"path:{p}")
    terminals = None if not isinstance(term, dict) else term.get("terminals") or []
    held = {pid for port, pid in m["listening"].items() if port in protect["port"]}
    inside = [pid for pid, c in cwd_of.items() if reap.under(reap.real(c), p)]
    holders = sorted(pid for pid in inside if pid in protect["pid"] or pid in held)
    children = sorted(c for c in map(wt_path, w.get("childWorktreeIds") or ()) if c and os.path.isdir(c))
    projects = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"
    ok, why = judge_card(w, facts, pr, terminals, m["quiet"](p), now, float(cfg["idle_minutes"]) * 60, protect,
                         people_in(p, m["rows"], cwd_of), holders, children,
                         coordinator_in(p, m["rows"], cwd_of, projects))
    return ok, why, facts


def local_cards(wt, root):
    return [w for w in wt.get("worktrees") or [] if w.get("hostId", "local") == "local"
            and card_dir(reap.real(w.get("path") or "/"), root) == reap.real(w.get("path") or "/")
            and os.path.isdir(reap.real(w["path"]))]


def scan_cards(cfg, protect, now):
    """Card decisions, and the machine state the rest of the pass reads."""
    root = reap.real(os.path.expanduser(cfg["workspaces"]))
    items, notes = [], []
    m = read_machine(now)
    if m["cwd_pairs"] is None:
        notes.append("프로세스 cwd 를 읽지 못함: 카드·리스너는 건너뜀")
    if m["listening"] is None:
        notes.append("lsof 로 리스너를 읽지 못함: 카드·리스너는 건너뜀")
    wt = orca("worktree", "list", "--limit", "10000")
    if not isinstance(wt, dict) or wt.get("truncated"):
        notes.append("orca worktree list 를 읽지 못함 또는 잘림: 카드는 건너뜀")
    elif m["cwd_of"] is not None and m["listening"] is not None:
        for w in local_cards(wt, root):
            ok, why, facts = card_verdict(w, m, cfg, protect, now)
            item = {"kind": "card", "id": reap.real(w["path"]), "target": ok, "why": why}
            if ok:
                item.update(head=facts["head"], regenerated=facts.get("regenerated") or [])
            items.append(item)
    return items, notes, m


def scan_rest(cfg, protect, dark_before, closed, m, now):
    """Stacks, listeners and serve entries, given the cards closed in this pass (planned ones under --dry-run)."""
    idle_s = float(cfg["idle_minutes"]) * 60
    root = reap.real(os.path.expanduser(cfg["workspaces"]))
    items, notes = [], []
    stacks = reap.compose_stacks()
    if stacks is None:
        notes.append("docker 를 읽지 못함: 스택은 건너뜀")
    keep = always_on()
    downing = set()
    for name, st in sorted((stacks or {}).items()):
        ok, why = judge_stack(name, st, lambda d: not os.path.isdir(d) or any(reap.under(reap.real(d), c) for c in closed),
                              protect, keep)
        items.append({"kind": "stack", "id": name, "target": ok, "why": why})
        if ok:
            downing |= st["ports"]

    alive = {reap.real(os.path.join(root, r, c)) for r in (os.listdir(root) if os.path.isdir(root) else ())
             if os.path.isdir(os.path.join(root, r)) for c in os.listdir(os.path.join(root, r))
             if os.path.isdir(os.path.join(root, r, c))} - set(closed)
    stopping = set()
    rows, cwd_pairs = m["rows"], m["cwd_pairs"]
    for port, pid in sorted((m["listening"] or {}).items()):
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
    status = None
    if cli:
        try:
            status = pw.serve_status(cli)
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
            notes.append(f"tailscale serve status 를 읽지 못함: {e}")
    ranges = [tuple(r) for r in cfg["serve_ranges"]]
    # What this pass stops counts as gone already: under --dry-run nothing was stopped.
    is_open = functools.lru_cache(None)(lambda port: port not in stopping | downing and port_open(port))
    for e in serve_entries(status or {}):
        if not is_open(e[2]):
            dark.add(serve_key(e))
        ok, why = judge_serve(e, is_open, dark_before, ranges, protect)
        items.append({"kind": "serve", "id": f"{e[0]}={e[1]}", "flag": e[0], "port": e[1], "local": e[2],
                      "target": ok, "why": why})
    return items, notes, dark


# ---------------------------------------------------------------- acting

def act(item, cfg, protect):
    k = item["kind"]
    if k == "card":
        p = item["id"]
        # Everything is judged again here: a PR reopened, a session resumed or a person's command since the scan
        # keeps the card.
        wt = orca("worktree", "list", "--limit", "10000")
        row = next((w for w in (wt or {}).get("worktrees") or [] if reap.real(w.get("path") or "") == p), None)
        if not isinstance(wt, dict) or wt.get("truncated") or row is None:
            return "건너뜀: orca worktree list 에서 다시 찾지 못함"
        ok, why, facts = card_verdict(row, read_machine(time.time()), cfg, protect, time.time())
        if not ok:
            return f"건너뜀: 다시 보니 {why}"
        if facts.get("head") != item["head"]:
            return "건너뜀: 판정 뒤 HEAD 가 바뀜"
        reap.run(["orca", "terminal", "close", "--worktree", f"path:{p}", "--all", "--json"])
        return "터미널 닫음, " + reap.act({"kind": "orca-worktree", "key": f"orca-worktree:{p}", "name": p,
                                           "regenerated": facts.get("regenerated")}, [])
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
        entry = (item["flag"], item["port"], item["local"])
        if entry not in serve_entries(pw.serve_status(cli)):
            return "건너뜀: 판정 뒤 serve 설정이 바뀜"
        if port_open(item["local"]):
            return "건너뜀: 판정 뒤 리스너가 생김"
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
    rows = []

    def do(batch):
        for i in batch:
            if not i["target"]:
                continue
            row = {"ts": stamp, "run": run_id, "dry_run": dry, "kind": i["kind"], "id": i["id"], "why": i["why"]}
            if dry:
                row["result"] = "dry-run"
            else:
                try:
                    row["result"] = act(i, cfg, protect)
                except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
                    row["error"] = str(e)[:300]
            i["result"] = row.get("result") or "실패: " + row.get("error", "")
            rows.append(row)

    items, notes, m = scan_cards(cfg, protect, now)
    do(items)
    # Only a card that is really gone frees its stack and dev servers; a skipped or failed close keeps them.
    closed = [i["id"] for i in items if i["target"] and (dry or (not i["result"].startswith(("건너뜀", "실패"))
                                                                 and not os.path.isdir(i["id"])))]
    rest, more, dark = scan_rest(cfg, protect, set(last.get("dark") or ()), closed, m, now)
    do(rest)
    items, notes = items + rest, notes + more
    log(rows)
    summary = {"t": now, "at": stamp, "run": run_id, "dry_run": dry,
               "acted": sum(1 for r in rows if "result" in r and not r["result"].startswith("건너뜀")),
               "skipped": sum(1 for r in rows if r.get("result", "").startswith("건너뜀")), "failed": sum(1 for r in rows if "error" in r),
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
    if not argv:  # a bare call must not clean: only `run` does
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[0]
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
