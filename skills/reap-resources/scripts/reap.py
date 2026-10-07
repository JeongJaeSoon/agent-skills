#!/usr/bin/env python3
"""Inventory and reap what dead sessions left behind on this machine.

Usage:
  python3 reap.py scan [--hours N] [--kinds codex,orphan,docker,branch,worktree] [--repo PATH]...
                       [--plan OUT.json] [--json]
  python3 reap.py reap --plan PLAN.json
  python3 reap.py load [--top N] [--json]
  python3 reap.py ledger add --kind KIND (--path P | --id ID | --pid N) [--pr owner/repo#N] [--worktree W] [--run R] [--by B]
  python3 reap.py ledger list [--json]
  python3 reap.py precheck [--kinds ...]
  python3 reap.py schedule [--workspace SELECTOR] [--provider claude] [--write]

scan only reads. reap re-scans and acts only on the plan's targets that are still targets with the
same identity (pid and start time, branch tip), so nothing outside the list is touched.
load only reads: the top CPU consumers, each classed ours-idle, ours-working, leftover or system, with the
action for its class.
--repo: repos for the branch and worktree kinds. Default: every repo in `orca repo list`, else
the repo of the current directory.
Config: ~/.claude/agent-skills.json → "reap": {"hours": 6, "orphan_paths": [...], "alert": {...},
"load_limit": <5-minute load; default the CPU count>, "idle_minutes": 30}.
Janitor kinds (orca-worktree, orca-worker, tmpdir, gui-app, chrome-window, remote-branch; `--kinds janitor`) cover only
what the ledger lists or an Orca orchestration worker made, and are report-only: reap never acts on them.
The ledger is $AGENT_SKILLS_LEDGER or --ledger, default $AGENT_SKILLS_STATE/ledger.jsonl (state default
~/.local/state/agent-skills). precheck scans the janitor kinds, writes janitor-plan.json to the state dir and exits 0 only
when the target set is non-empty and changed since the last report. schedule prints the `orca automations create`
command; only --write runs it.
"""
import contextlib, datetime as dt, json, os, pathlib, re, shlex, shutil, signal, subprocess, sys, time

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
KINDS = ("codex", "orphan", "docker", "branch", "worktree")
ALERT = {"codex_procs": 300, "codex_rss_mb": 4096, "orphan_procs": 5, "dangling_volumes": 50,
         "stale_branches": 30, "stale_worktrees": 10}
# argv[0] is node and argv[1] the broker script: a session whose prompt quotes this line is not a broker.
BROKER = re.compile(r"^\S+ \S*/app-server-broker\.mjs serve\b")
BROKER_CWD = re.compile(r"--cwd (.+?)(?= --[a-z][a-z-]*|$)")
SCRATCH = re.compile(r"/claude-\d+/([^/]+)/([0-9a-f-]{36})/scratchpad/")
ENV = {**os.environ, "LC_ALL": "C"}


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def run(cmd, cwd=None, check=True):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=ENV)
    except FileNotFoundError:
        if check:
            raise RuntimeError(f"{cmd[0]}: not installed")
        return None
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}: {r.stderr.strip()[:200]}")
    return r.stdout if r.returncode == 0 else None


def config():
    try:
        return json.loads(CONFIG.read_text()).get("reap") or {}
    except (OSError, ValueError):
        return {}


def etime_s(et):
    days, _, rest = et.rpartition("-")
    parts = [int(x) for x in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return (int(days) if days else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def processes():
    """pid → row. `start` is ps's lstart, exact to the second, so a reused pid does not pass as the same process."""
    rows = {}
    for line in run(["ps", "-axo", "pid=,ppid=,uid=,lstart=,etime=,rss=,%cpu=,command="]).splitlines():
        p = line.split(None, 11)
        if len(p) == 12:
            rows[int(p[0])] = {"pid": int(p[0]), "ppid": int(p[1]), "uid": int(p[2]), "start": " ".join(p[3:8]),
                               "age": etime_s(p[8]), "rss": int(p[9]) * 1024, "cpu": float(p[10]), "cmd": p[11]}
    return rows


def cwds():
    """pid → cwd for this user's processes; None unless every one of them was read."""
    if os.path.isdir("/proc/self"):
        out = {}
        for d in os.listdir("/proc"):
            try:
                if d.isdigit() and os.stat(f"/proc/{d}").st_uid == os.getuid():
                    # A process left in a removed directory reads as "<path> (deleted)".
                    out[int(d)] = os.readlink(f"/proc/{d}/cwd").removesuffix(" (deleted)")
            except FileNotFoundError:
                pass
            except OSError:
                return None
        return out
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    listing = run([lsof, "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpn"], check=False)
    if not listing:
        return None
    out, pid = {}, None
    for line in listing.splitlines():
        if line[:1] == "p":
            pid = int(line[1:])
        elif line[:1] == "n" and pid:
            out[pid] = line[1:]
    return out


def tree(root, rows):
    kids = {}
    for r in rows.values():
        kids.setdefault(r["ppid"], []).append(r["pid"])
    out, stack = [], [root]
    while stack:
        p = stack.pop()
        if p not in out:
            out.append(p)
            stack += kids.get(p, [])
    return out


def under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def real(p):
    return os.path.realpath(p) if os.path.exists(p) else p


def proc_item(kind, r, rows, **kw):
    pids = tree(r["pid"], rows)
    return {"kind": kind, "key": f"proc:{r['pid']}", "pid": r["pid"], "start": r["start"], "cmd": r["cmd"],
            "age": r["age"], "procs": len(pids), "rss": sum(rows[p]["rss"] for p in pids),
            "cpu": sum(rows[p]["cpu"] for p in pids), **kw}


def is_claude(cmd):
    """The native binary, or an npm install run as `node …/@anthropic-ai/claude-code/cli.js`."""
    argv = cmd.split()[:2]
    return bool(argv) and (os.path.basename(argv[0]) == "claude" or "/@anthropic-ai/claude-code/" in argv[-1])


def judge_brokers(rows, cwd_of, hours, exists=os.path.isdir):
    claude = {pid: real(c) for pid, c in (cwd_of or {}).items() if pid in rows and is_claude(rows[pid]["cmd"])}
    # An own claude with no cwd on record could be the one using any broker.
    blind = cwd_of is None or any(is_claude(r["cmd"]) and r.get("uid", os.getuid()) == os.getuid() and pid not in cwd_of
                                  for pid, r in rows.items())
    out = []
    for r in rows.values():
        if not BROKER.match(r["cmd"]):
            continue
        m = BROKER_CWD.search(r["cmd"])
        cwd = m.group(1) if m else None
        live = [pid for pid, c in claude.items() if cwd and under(c, real(cwd))]
        if live:
            why, target = f"cwd에 살아 있는 claude(pid {live[0]})", False
        elif blind:
            why, target = "claude 의 cwd 를 모두 읽지 못함", False
        elif not cwd or not exists(cwd):
            why, target = "cwd 없음", True
        elif r["age"] < hours * 3600:
            why, target = f"{hours}시간 미만", False
        else:
            why, target = "cwd에 claude 없음", True
        out.append(proc_item("codex", r, rows, name=cwd or "?", why=why, target=target))
    return out


def judge_orphans(rows, prefixes, hours):
    pres = {x for p in prefixes for x in (p.rstrip("/"), real(p).rstrip("/"))}
    out = []
    for r in rows.values():
        # Only argv[0] and argv[1] (the script an interpreter runs) count. Looking past options would take an
        # option's value (a report file, a data dir) for the script.
        exe = r["cmd"].split()[:2]
        if r["ppid"] != 1 or not any(under(e, p) for e in exe for p in pres):
            continue
        young = r["age"] < hours * 3600
        out.append(proc_item("orphan", r, rows, name=r["cmd"][:80], why=f"{hours}시간 미만" if young else "ppid 1, 테스트 캐시 경로",
                             target=not young))
    return out


def parse_ts(s):
    s = s.strip()
    m = re.match(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) ([+-]\d{4})", s)
    if m:
        return dt.datetime.strptime(" ".join(m.groups()), "%Y-%m-%d %H:%M:%S %z").timestamp()
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def judge_volumes(vols, active, hours, now):
    """vols: [{name, labels, created}] of dangling volumes; active: compose projects that have any container."""
    out = []
    for v in vols:
        proj = (v["labels"] or {}).get("com.docker.compose.project")
        age = now - v["created"]
        hit = next((p for p in active if proj == p or (len(p) >= 3 and p in v["name"])), None)
        if hit:
            why, target = f"컨테이너가 있는 project {hit}", False
        elif not proj:
            why, target = "compose project 라벨 없음(보고만)", False
        elif age < hours * 3600:
            why, target = f"{hours}시간 미만", False
        else:
            why, target = f"컨테이너 없는 project {proj}", True
        out.append({"kind": "docker", "key": f"volume:{v['name']}", "name": f"volume {v['name']}", "age": age,
                    "why": why, "target": target})
    return out


def judge_images(images, used, hours, now):
    out = []
    for i in images:
        age = now - i["created"]
        if any(u.startswith(i["id"]) or u.startswith("sha256:" + i["id"]) for u in used):
            why, target = "컨테이너가 쓰는 이미지", False
        elif age < hours * 3600:
            why, target = f"{hours}시간 미만", False
        else:
            why, target = f"태그 없음 {i['size']}", True
        out.append({"kind": "docker", "key": f"image:{i['id']}", "name": f"image {i['id']}", "age": age,
                    "why": why, "target": target})
    return out


def docker_scan(hours, notes):
    if not shutil.which("docker"):
        return []
    now = time.time()
    ps = run(["docker", "ps", "-a", "--no-trunc", "--format", '{{.ID}}\t{{.Label "com.docker.compose.project"}}\t{{.State}}'], check=False)
    if ps is None:
        notes.append("docker: 데몬 응답 없음, 건너뜀")
        return []
    states, ids = {}, []
    for line in ps.splitlines():
        cid, proj, state = (line.split("\t") + ["", ""])[:3]
        ids.append(cid)
        if proj:
            states.setdefault(proj, []).append(state)
    for proj, st in sorted(states.items()):
        if all(s in ("exited", "dead", "created") for s in st):
            notes.append(f"docker: 끝난 compose 스택 {proj} (컨테이너 {len(st)}개, 모두 멈춤). 지우려면 주인이 `docker compose -p {proj} down`")
    names = (run(["docker", "volume", "ls", "-q", "-f", "dangling=true"]) or "").split()
    vols = []
    if names:
        for v in json.loads(run(["docker", "volume", "inspect", *names])):
            vols.append({"name": v["Name"], "labels": v.get("Labels") or {}, "created": parse_ts(v["CreatedAt"])})
    used = (run(["docker", "inspect", "--format", "{{.Image}}", *ids]) or "").split() if ids else []
    images = []
    for line in (run(["docker", "images", "-f", "dangling=true", "--format", "{{json .}}"]) or "").splitlines():
        i = json.loads(line)
        images.append({"id": i["ID"], "created": parse_ts(i["CreatedAt"]), "size": i.get("Size", "")})
    return judge_volumes(vols, set(states), hours, now) + judge_images(images, used, hours, now)


def repos(given):
    if given:
        return [os.path.abspath(p) for p in given]
    out = run(["orca", "repo", "list", "--json"], check=False) if shutil.which("orca") else None
    if out:
        paths = [r["path"] for r in json.loads(out)["result"]["repos"]]
        return [p for p in paths if os.path.exists(os.path.join(p, ".git"))]
    top = run(["git", "rev-parse", "--show-toplevel"], check=False)
    return [top.strip()] if top else []


def worktrees(repo):
    out, cur = [], None
    for line in run(["git", "worktree", "list", "--porcelain"], cwd=repo).splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[9:]}
            out.append(cur)
        elif cur is not None and line:
            k, _, v = line.partition(" ")
            cur[k] = v or True
    return out


def judge_branches(repo, branches, checked_out, default, prs, is_ancestor):
    """branches: {name: tip}; prs: gh pr list rows. A branch goes only when its PRs are merged or closed,
    no worktree has it, and its tip is what a PR carried (no local commits after it)."""
    out = []
    for name, tip in sorted(branches.items()):
        mine = [p for p in prs if p["headRefName"] == name and not p.get("isCrossRepository")]
        if name == default or name in checked_out or not mine:
            continue
        done = [p for p in mine if p["state"] in ("MERGED", "CLOSED")]
        if any(p["state"] == "OPEN" for p in mine) or not done:
            why, target = "열린 PR", False
        elif not any(tip == p["headRefOid"] or is_ancestor(tip, p["headRefOid"]) for p in done):
            why, target = "PR 이후 로컬 커밋", False
        else:
            p = done[0]
            why, target = f"PR #{p['number']} {p['state'].lower()}", True
        out.append({"kind": "branch", "key": f"branch:{repo}:{name}", "repo": repo, "name": name, "tip": tip,
                    "why": why, "target": target})
    return out


def branch_scan(repo, notes):
    wts = worktrees(repo)
    checked = {w["branch"].removeprefix("refs/heads/") for w in wts if isinstance(w.get("branch"), str)}
    branches = dict(l.split("\t") for l in run(["git", "for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads"], cwd=repo).splitlines())
    if len(branches) <= 1:
        return []
    head = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo, check=False)
    if not head:
        notes.append(f"branch: {repo} 의 기본 브랜치를 모름(origin/HEAD 없음), 건너뜀. `git remote set-head origin -a` 로 정한다")
        return []
    default = head.strip().split("/", 1)[-1]
    prs = run(["gh", "pr", "list", "--state", "all", "--limit", "1000", "--json",
               "number,state,headRefName,headRefOid,isCrossRepository"], cwd=repo, check=False)
    if prs is None:
        notes.append(f"branch: {repo} 의 PR 을 읽지 못함(gh), 건너뜀")
        return []

    def is_ancestor(a, b):
        return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=repo, capture_output=True).returncode == 0
    return judge_branches(repo, branches, checked, default, json.loads(prs), is_ancestor)


def orca_paths():
    """Paths of Orca-managed worktrees (their lifecycle is `orca worktree rm`); None when Orca is there but unreadable."""
    if not shutil.which("orca"):
        return set()
    out = run(["orca", "worktree", "list", "--limit", "10000", "--json"], check=False)
    try:
        res = json.loads(out)["result"]
        return None if res.get("truncated") else {w["path"] for w in res["worktrees"]}
    except (TypeError, ValueError, KeyError):
        return None


TEMP_ROOTS = tuple({real(p) for p in ("/tmp", "/private/tmp", "/var/folders", os.environ.get("TMPDIR", "/tmp"))})


def identity(w):
    """HEAD plus the inode of the checkout's .git file, so a checkout recreated at the same path is another one."""
    try:
        return f"{w.get('HEAD')}:{os.stat(os.path.join(w['path'], '.git')).st_ino}"
    except OSError:
        return w.get("HEAD")


def judge_worktree(repo, w, hours, cwd_of, now, projects_dir, orca=frozenset(), git=run):
    path = w["path"]
    base = {"kind": "worktree", "key": f"worktree:{repo}:{path}", "repo": repo, "name": path, "tip": identity(w)}
    m = SCRATCH.search(path + "/")
    if w.get("prunable"):
        # Only a checkout that lived in a temp dir: one elsewhere may be Orca's, and Orca removes its own.
        if not any(under(real(os.path.dirname(path)), t) for t in TEMP_ROOTS):
            return None
        if orca is None:
            return {**base, "why": "Orca 목록을 읽지 못함", "target": False}
        if path in orca:
            return None
        return {**base, "why": "디렉터리 없음", "target": True}
    if not m or w.get("bare"):
        return None
    transcript = projects_dir / m.group(1) / f"{m.group(2)}.jsonl"
    idle = now - transcript.stat().st_mtime if transcript.exists() else None
    users = [pid for pid, c in (cwd_of or {}).items() if under(real(c), real(path))]
    if idle is None:
        why = "세션 transcript 없음(조용한지 알 수 없음)"
    elif idle < hours * 3600:
        why = f"세션이 {hours}시간 안에 활동"
    elif users:
        why = f"pid {users[0]} 이 cwd 로 사용 중"
    elif cwd_of is None:
        why = "프로세스 cwd 를 읽지 못함"
    elif w.get("locked"):
        why = "locked"
    elif w.get("branch"):
        why = f"브랜치 {w['branch'].removeprefix('refs/heads/')} 체크아웃(보고만)"
    elif git(["git", "status", "--porcelain"], cwd=path, check=False) != "":
        why = "변경 있음"
    elif not git(["git", "for-each-ref", "--count=1", "--contains", "HEAD", "refs/remotes"], cwd=path, check=False):
        why = "원격에 없는 커밋"
    else:
        return {**base, "why": "끝난 세션의 scratchpad worktree", "target": True, "age": idle}
    return {**base, "why": why, "target": False, "age": idle}


def scan(kinds, hours, cfg, repo_list):
    notes, items = [], []
    rows = processes() if {"codex", "orphan"} & kinds else {}
    cwd_of = cwds() if {"codex", "worktree"} & kinds else {}
    if "codex" in kinds:
        items += judge_brokers(rows, cwd_of, hours)
    if "orphan" in kinds:
        if not cfg.get("orphan_paths"):
            notes.append(f"orphan: {CONFIG} 의 reap.orphan_paths 가 비어 있어 건너뜀")
        items += judge_orphans(rows, cfg.get("orphan_paths") or [], hours)
    if "docker" in kinds:
        items += docker_scan(hours, notes)
    if {"branch", "worktree"} & kinds:
        projects_dir = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"
        orca = orca_paths() if "worktree" in kinds else set()
        for repo in repo_list:
            try:
                if "branch" in kinds:
                    items += branch_scan(repo, notes)
                if "worktree" in kinds:
                    items += [x for w in worktrees(repo)[1:]
                              if (x := judge_worktree(repo, w, hours, cwd_of, time.time(), projects_dir, orca))]
            except RuntimeError as e:
                notes.append(f"{repo}: {e}")
    if set(JANITOR) & kinds:
        items += janitor_scan(kinds, repo_list, ledger_read(ledger_path()), notes)
    return items, notes


# Janitor kinds: only what an agent created (the ledger, or an Orca orchestration worker). Report-only: `reap` never acts on them.
JANITOR = ("orca-worktree", "orca-worker", "tmpdir", "gui-app", "chrome-window", "remote-branch")
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")


def ledger_path():
    return pathlib.Path(opt("--ledger") or os.environ.get("AGENT_SKILLS_LEDGER") or STATE / "ledger.jsonl").expanduser()


def ledger_read(path):
    out = []
    with contextlib.suppress(OSError):
        for line in path.read_text(errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                e = json.loads(line) if line.strip() else None
                # A hand-edited or torn line must not stop every later precheck.
                if isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("links") or {}, dict):
                    out.append(e)
    return out


def ledger_add(path, kind, rid, pr=None, worktree=None, run_id=None, by=None):
    if kind not in JANITOR:
        raise SystemExit(f"--kind must be one of {', '.join(JANITOR)}")
    entry = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "run": run_id, "kind": kind, "id": rid,
             "links": {"pr": pr, "worktree": worktree}, "by": by}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def parse_pr(ref):
    """owner/repo#N → (owner/repo, N)."""
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", ref or "")
    return (m.group(1), int(m.group(2))) if m else None


def pr_reader(gh=run):
    cache = {}

    def state(ref):
        """owner/repo#N → {"state", "headRefOid"}, or None when gh cannot read it."""
        if ref not in cache:
            p = parse_pr(ref)
            out = p and gh(["gh", "pr", "view", str(p[1]), "-R", p[0], "--json", "state,headRefOid"], check=False)
            cache[ref] = json.loads(out) if out else None
        return cache[ref]
    return state


def link_verdict(links, pr_state, exists=os.path.exists):
    """(done, why): done when the linked PR is merged or closed, or the linked worktree is gone; None when nothing links it."""
    ref, wt = links.get("pr"), links.get("worktree")
    if ref:
        s = pr_state(ref)
        if s is None:
            return False, f"PR {ref} 상태를 읽지 못함"
        if s["state"] == "OPEN":
            return False, f"열린 PR {ref}"
        return True, f"PR {ref} {s['state'].lower()}"
    if wt:
        return (False, f"worktree {wt} 있음") if exists(wt) else (True, f"worktree {wt} 없어짐")
    return None, "연결된 PR·worktree 없음(보고만)"


def latest(entries, kind):
    """The last ledger line per id of one kind (the ledger only appends)."""
    return list({e["id"]: e for e in entries if e.get("kind") == kind}.values())


def apps_under(path):
    out = []
    for root, dirs, _ in os.walk(path):
        for d in [d for d in dirs if d.endswith(".app")]:
            out.append(os.path.join(root, d))
            dirs.remove(d)
    return sorted(out)


def judge_tmpdirs(entries, pr_state, cwd_of, rows=None, exists=os.path.exists, apps=apps_under, roots=None):
    out = []
    for e in latest(entries, "tmpdir"):
        path = e["id"]
        if not exists(path):
            continue
        base = {"kind": "tmpdir", "key": f"tmpdir:{path}", "name": path, "links": e.get("links") or {}}
        done, why = link_verdict(base["links"], pr_state, exists)
        # A GUI app runs with cwd /, so an app launched from a bundle in here counts by its executable.
        spellings = {path, real(path), re.sub(r"^/private(?=/(tmp|var)/)", "", real(path))}
        users = [pid for pid, c in (cwd_of or {}).items() if under(real(c), real(path))] + \
                [pid for pid, r in (rows or {}).items() if any(r["cmd"].startswith(p + "/") for p in spellings)]
        # Strictly below a temp root: the root itself (/tmp, $TMPDIR) is never one build's directory.
        if done and not any(under(real(path), t) and real(path) != t for t in (TEMP_ROOTS if roots is None else roots)):
            done, why = False, "임시 디렉터리 밖(보고만)"
        elif done and users:
            done, why = False, f"pid {users[0]} 이 cwd 로 사용 중"
        elif done and cwd_of is None:
            done, why = False, "프로세스 cwd 를 읽지 못함"
        how = [f"{LSREGISTER} -u {shlex.quote(a)}" for a in apps(path)] + [f"rm -rf {shlex.quote(path)}"]
        out.append({**base, "why": why, "target": bool(done), "how": how})
    return out


def judge_gui_apps(entries, pr_state, rows, exists=os.path.exists):
    """id is "<pid>@<ps lstart>", so a reused pid is not the registered app."""
    out = []
    for e in latest(entries, "gui-app"):
        pid, _, start = e["id"].partition("@")
        r = rows.get(int(pid)) if pid.isdigit() else None
        if not r or r["start"] != start:
            continue
        done, why = link_verdict(e.get("links") or {}, pr_state, exists)
        out.append({"kind": "gui-app", "key": f"gui-app:{e['id']}", "name": f"pid {pid} {r['cmd'][:60]}", "why": why,
                    "target": bool(done), "how": [f"kill -TERM {pid}"]})
    return out


def judge_chrome(entries, pr_state, exists=os.path.exists):
    # No script can tell whether a tab is still open or close it: the agent does that with its browser tools.
    out = []
    for e in latest(entries, "chrome-window"):
        done, why = link_verdict(e.get("links") or {}, pr_state, exists)
        out.append({"kind": "chrome-window", "key": f"chrome-window:{e['id']}", "name": e["id"], "why": why,
                    "target": bool(done), "how": ["에이전트가 처리: 브라우저 도구로 이 창·탭을 닫는다"]})
    return out


def judge_remote_branches(entries):
    return [{"kind": "remote-branch", "key": f"remote-branch:{e['id']}", "name": e["id"],
             "why": "판정 미구현(인터페이스만, 보고만)", "target": False, "how": []} for e in latest(entries, "remote-branch")]


def judge_orca_worker(rows):
    """rows: `orca orchestration worker-list --terminal-state reclaimable` workers."""
    rows = [w for w in rows if w.get("dispatchId")]
    return [{"kind": "orca-worker", "key": f"orca-worker:{w['dispatchId']}", "name": f"{w['dispatchId']} ({w.get('taskId')})",
             "why": "끝난 워커의 터미널(reclaimable)", "target": True,
             "how": [f"orca orchestration worker-release --dispatch {w['dispatchId']} --json"]} for w in rows]


def judge_orca_worktree(path, source, facts, pr_state):
    """facts: the git and process state of one checkout, read by worktree_facts."""
    base = {"kind": "orca-worktree", "key": f"orca-worktree:{path}", "name": path,
            "how": [f"orca worktree rm --worktree {shlex.quote('path:' + path)} --run-hooks --json"]}
    ref = facts.get("pr")
    s = pr_state(ref) if ref else None
    if facts.get("live"):
        why = facts["live"]
    elif not ref:
        why = "연결된 PR 없음"
    elif s is None:
        why = f"PR {ref} 상태를 읽지 못함"
    elif s["state"] == "OPEN":
        why = f"열린 PR {ref}"
    elif facts.get("unreadable"):
        why = "git 상태를 읽지 못함"
    elif facts.get("dirty"):
        why = "변경 있음(추적 안 되는 파일 포함)"
    elif facts.get("unpushed") and facts.get("head") != s.get("headRefOid"):
        why = "원격에 없는 커밋"
    else:
        return {**base, "why": f"PR {ref} {s['state'].lower()} ({source})", "target": True}
    return {**base, "why": why, "target": False}


def worktree_facts(path, ledger_pr, live_turn, users_cwd, git=run):
    common = git(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"], check=False)
    if common is None:
        return None
    main = os.path.dirname(common.strip().rstrip("/"))
    if real(main) == real(path):
        return {"main": True}
    users = [pid for pid, c in users_cwd.items() if under(real(c), real(path))]
    ref = ledger_pr
    if not ref:
        branch = (git(["git", "-C", path, "branch", "--show-current"], check=False) or "").strip()
        prs = branch and git(["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number,url,isCrossRepository"],
                              cwd=path, check=False)
        # A fork's PR with the same head name is not this branch's.
        own = [p for p in json.loads(prs) if not p.get("isCrossRepository")] if prs else []
        if own:
            url = own[0]["url"]
            m = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
            ref = m and f"{m.group(1)}#{m.group(2)}"
    # Untracked files count: `orca worktree rm` would take new, never-added work with it.
    status = git(["git", "-C", path, "status", "--porcelain"], check=False)
    ahead = git(["git", "-C", path, "rev-list", "-n1", "HEAD", "--not", "--remotes"], check=False)
    return {"main": False, "repo": main, "pr": ref,
            "live": live_turn or (f"pid {users[0]} 이 이 worktree 를 cwd 로 사용 중" if users else None),
            # None is a failed read, not an empty answer.
            "unreadable": status is None or ahead is None,
            "dirty": bool(status), "unpushed": bool(ahead),
            "head": (git(["git", "-C", path, "rev-parse", "HEAD"], check=False) or "").strip()}


def orca_json(*args):
    out = run(["orca", *args, "--json"], check=False) if shutil.which("orca") else None
    try:
        return json.loads(out)["result"] if out else None
    except (ValueError, KeyError):
        return None


def orca_workers(state=None):
    rows, cursor = [], None
    for _ in range(50):
        res = orca_json("orchestration", "worker-list", "--limit", "100",
                        *(["--terminal-state", state] if state else []), *(["--cursor", cursor] if cursor else []))
        if not res:
            return None  # a partial list could miss a retained row and count a user's worktree as made
        rows += res.get("workers") or []
        cursor = (res.get("page") or {}).get("nextCursor")
        if not cursor:
            break
    return rows


def worker_worktrees(workers):
    """(live, made, users) by real path. A retained row (a context-only dispatch into an existing worktree, or a card
    the user took over) marks the worktree as the user's: it never counts as made by a worker."""
    live, made, users = {}, set(), set()
    for w in workers:
        p = ((w.get("resource") or {}).get("worktreeId") or "").partition("::")[2]
        if not p:
            continue
        p = real(p)
        if w.get("terminalState") == "retained" or (w.get("resource") or {}).get("retainedReason"):
            users.add(p)
        else:
            made.add(p)
        if (w.get("projection") or {}).get("outcome") == "in_progress":
            live[p] = f"살아 있는 워커 턴({w.get('dispatchId')})"
    return live, made, users


def janitor_scan(kinds, repo_list, entries, notes, pr_state=None):
    pr_state = pr_state or pr_reader()
    items = []
    rows = processes() if {"gui-app", "tmpdir"} & kinds else {}
    cwd_of = cwds() if {"tmpdir", "orca-worktree"} & kinds else {}
    if "tmpdir" in kinds:
        items += judge_tmpdirs(entries, pr_state, cwd_of, rows)
    if "gui-app" in kinds:
        items += judge_gui_apps(entries, pr_state, rows)
    if "chrome-window" in kinds:
        items += judge_chrome(entries, pr_state)
    if "remote-branch" in kinds:
        items += judge_remote_branches(entries)
    if "orca-worker" in kinds:
        reclaim = orca_workers("reclaimable")
        if reclaim is None:
            notes.append("orca-worker: worker-list 를 읽지 못함, 건너뜀")
        items += judge_orca_worker(reclaim or [])
    if "orca-worktree" in kinds:
        workers = orca_workers() if shutil.which("orca") else []
        if workers is None:
            notes.append("orca-worktree: 오케스트레이션 워커 목록을 읽지 못함, ledger worktree 는 보존한다")
        live, made, users = worker_worktrees(workers or [])
        ledger = {real(e["id"]): e for e in latest(entries, "orca-worktree")}
        repos_real = {real(r) for r in repo_list}
        for path in sorted((set(ledger) | made) - users):
            if not os.path.isdir(path):
                continue
            e = ledger.get(path)
            facts = worktree_facts(path, e and (e.get("links") or {}).get("pr"), live.get(path), cwd_of or {})
            if not facts or facts["main"] or (repos_real and real(facts["repo"]) not in repos_real):
                continue
            if cwd_of is None and not facts["live"]:
                facts["live"] = "프로세스 cwd 를 읽지 못함"
            if workers is None and not facts["live"]:
                # Without the list, a user's takeover and a live turn look the same as a finished worker.
                facts["live"] = "워커 목록을 읽지 못함"
            items.append(judge_orca_worktree(path, "ledger" if e else "오케스트레이션 워커", facts, pr_state))
    return items


def precheck(items, state_dir):
    """Exit code for an Orca automation precheck: 0 only when the target set changed since the last report and is not empty."""
    targets = sorted(i["key"] for i in items if i["target"])
    last = state_dir / "janitor-last.json"
    with contextlib.suppress(OSError, ValueError):
        if json.loads(last.read_text()).get("targets") == targets:
            return 1, targets
    state_dir.mkdir(parents=True, exist_ok=True)
    last.write_text(json.dumps({"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "targets": targets}))
    return (0 if targets else 1), targets


def schedule_cmd(script, state_dir, workspace, provider="claude", ledger=None):
    plan = state_dir / "janitor-plan.json"
    prompt = (f"reap-resources janitor 회차다. 정리 계획 {plan} 을 읽고(없으면 python3 {script} scan --kinds janitor 를 돌린다) "
              "정리 대상과 남긴 것의 이유를 한국어로 보고한다. report-only: 아무것도 지우거나 닫지 않는다.")
    return ["orca", "automations", "create", "--name", "agent-skills janitor", "--trigger", "17 */3 * * *",
            "--provider", provider, "--workspace-mode", "existing", "--workspace", workspace,
            "--precheck", " ".join(["env", f"AGENT_SKILLS_STATE={shlex.quote(str(state_dir))}",
                                    f"AGENT_SKILLS_LEDGER={shlex.quote(str(ledger or state_dir / 'ledger.jsonl'))}",
                                    "python3", shlex.quote(str(script)), "precheck"]),
            "--prompt", prompt, "--json"]


BUSY_CHILD_CPU = 25
LOAD_ACTIONS = {
    "ours-idle": "먼저 남은 일이 있는지 묻고, 없으면 스스로 닫게 한다(자기 점검). 죽이지 않는다",
    "leftover": "reap-resources 로 정리한다: scan --plan 뒤 reap --plan",
    "ours-working": "건드리지 않는다. 새 dispatch 를 멈추고, 무거운 실행은 `orch heavy` 잠금 뒤에 줄 세운다",
    "system": "우리 것 아님으로 보고만 한다. 절대 건드리지 않는다",
}


def judge_load(rows, cwd_of, leftovers, quiet, idle_s, me=None, top=None):
    me = os.getuid() if me is None else me
    claimed, out = set(), []

    def take(root, cls, why, **kw):
        pids = [p for p in tree(root, rows) if p not in claimed]
        claimed.update(pids)
        out.append({"cls": cls, "pid": root, "procs": len(pids), "cpu": sum(rows[p]["cpu"] for p in pids),
                    "rss": sum(rows[p]["rss"] for p in pids), "cmd": rows[root]["cmd"][:80], "why": why, **kw})

    for pid in sorted(leftovers):
        if pid in rows:
            take(pid, "leftover", "reap-resources 정리 대상")
    ours = lambda r: r.get("uid", me) == me
    for pid, r in sorted(rows.items()):
        parent = rows.get(r["ppid"])
        if pid in claimed or not ours(r) or not is_claude(r["cmd"]) or (parent and ours(parent) and is_claude(parent["cmd"])):
            continue
        cwd = (cwd_of or {}).get(pid)
        q = quiet(cwd) if cwd else None
        busy = sum(rows[p]["cpu"] for p in tree(pid, rows) if p != pid and p not in claimed)
        if q is None:
            take(pid, "ours-working", "세션 transcript 없음(조용한지 알 수 없음)", cwd=cwd)
        elif busy >= BUSY_CHILD_CPU:
            take(pid, "ours-working", f"transcript 는 조용하지만 자식이 CPU {busy:.0f}% 사용", cwd=cwd)
        elif q >= idle_s:
            take(pid, "ours-idle", f"세션이 {age(q)} 조용", cwd=cwd)
        else:
            take(pid, "ours-working", "세션 활동 중", cwd=cwd)
    for pid, r in sorted(rows.items()):
        if pid in claimed:
            continue
        claimed.add(pid)
        out.append({"cls": "ours-working" if ours(r) else "system", "pid": pid, "procs": 1, "cpu": r["cpu"], "rss": r["rss"],
                    "cmd": r["cmd"][:80], "why": "이 사용자의 프로세스" if ours(r) else "다른 사용자의 프로세스"})
    return sorted(out, key=lambda i: -i["cpu"])[:top]


def transcript_quiet(projects_dir, now):
    def quiet(cwd):
        d = projects_dir / re.sub(r"[^A-Za-z0-9]", "-", cwd)
        times = []
        for f in (f for g in ("*.jsonl", "*/subagents/*.jsonl") for f in d.glob(g)) if d.is_dir() else ():
            with contextlib.suppress(OSError):  # a transcript removed mid-scan, or a dangling link
                times.append(f.stat().st_mtime)
        return now - max(times) if times else None
    return quiet


def load_report(cfg, top):
    rows, cwd_of = processes(), cwds()
    hours = float(cfg.get("hours") or 6)
    leftovers = {i["pid"] for i in judge_brokers(rows, cwd_of, hours) + judge_orphans(rows, cfg.get("orphan_paths") or [], hours)
                 if i["target"]}
    projects_dir = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"
    items = judge_load(rows, cwd_of or {}, leftovers, transcript_quiet(projects_dir, time.time()),
                       float(cfg.get("idle_minutes") or 30) * 60)
    totals = {c: {"count": sum(i["cls"] == c for i in items), "cpu": round(sum(i["cpu"] for i in items if i["cls"] == c))}
              for c in LOAD_ACTIONS}
    load5, limit = os.getloadavg()[1], float(cfg.get("load_limit") or os.cpu_count() or 1)
    return {"at": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "load5": round(load5, 1),
            "limit": limit, "high": load5 >= limit, "totals": totals, "items": items[:top],
            "idle": [{"pid": i["pid"], "cwd": i["cwd"]} for i in items if i["cls"] == "ours-idle"]}


def print_load(data):
    print(f"# 머신 부하 ({data['at']}): 5분 부하 {data['load5']}, 한도 {data['limit']:g}"
          + (" — 높음" if data["high"] else " — 정상") + "\n")
    print("| 분류 | pid | 프로세스 | CPU | RSS | 명령 | 근거 |\n|---|---|---|---|---|---|---|")
    for i in data["items"]:
        print(f"| {i['cls']} | {i['pid']} | {i['procs']} | {i['cpu']:.0f}% | {human(i['rss'])} | `{i['cmd']}` | {i['why']} |")
    print("\n분류별 합계: " + ", ".join(f"{c} {t['count']}개 {t['cpu']}%" for c, t in data["totals"].items()))
    if data["high"]:
        print("\n## 조치\n")
        for cls, action in LOAD_ACTIONS.items():
            if data["totals"][cls]["count"]:
                print(f"- {cls} {data['totals'][cls]['count']}개: {action}")
            if cls == "ours-idle" and data["idle"]:
                print("\n".join(f"  - pid {i['pid']}: {i['cwd']}" for i in data["idle"]))
        print("- 부하가 한도 이상인 동안 잰 벤치마크와 성능 수치는 증거가 아니다. 한도 아래로 내려간 뒤 다시 잰다")


def alerts(items, limits):
    codex = [i for i in items if i["kind"] == "codex"]
    got = {"codex_procs": sum(i["procs"] for i in codex),
           "codex_rss_mb": sum(i["rss"] for i in codex) // 2**20,
           "orphan_procs": sum(i["procs"] for i in items if i["kind"] == "orphan"),
           "dangling_volumes": sum(1 for i in items if i["key"].startswith("volume:")),
           "stale_branches": sum(1 for i in items if i["kind"] == "branch" and i["target"]),
           "stale_worktrees": sum(1 for i in items if i["kind"] == "worktree" and i["target"])}
    return [f"{k} {v} ≥ {limits[k]}" for k, v in got.items() if v >= limits[k]], got


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def age(s):
    if s is None:
        return "-"
    s = int(s)
    return f"{s // 86400}d{s % 86400 // 3600}h" if s >= 86400 else f"{s // 3600}h{s % 3600 // 60}m"


def label(i):
    if "pid" in i:
        return f"{i['kind']} pid {i['pid']} (프로세스 {i['procs']}, {human(i['rss'])}, CPU {i['cpu']:.0f}%, {age(i['age'])}) {i['name']}"
    where = f" [{os.path.basename(i['repo'])}]" if "repo" in i else ""
    return f"{i['kind']}{where} {i['name']}" + (f" ({age(i['age'])})" if i.get("age") is not None else "")


def report(data):
    print(f"# 방치 리소스 점검 ({data['at']}, 기준 {data['hours']}시간)\n")
    print("| 종류 | 전체 | 정리 대상 | 프로세스 | RSS | CPU | 가장 오래된 것 |\n|---|---|---|---|---|---|---|")
    for k in data["kinds"]:
        xs = [i for i in data["items"] if i["kind"] == k]
        ages = [i["age"] for i in xs if i.get("age") is not None]
        print(f"| {k} | {len(xs)} | {sum(i['target'] for i in xs)} | {sum(i.get('procs', 0) for i in xs) or '-'} | "
              f"{human(sum(i.get('rss', 0) for i in xs)) if any('rss' in i for i in xs) else '-'} | "
              f"{str(round(sum(i.get('cpu', 0) for i in xs))) + '%' if any('cpu' in i for i in xs) else '-'} | {age(max(ages)) if ages else '-'} |")
    targets = [i for i in data["items"] if i["target"]]
    print(f"\n## 정리 대상 {len(targets)}개\n")
    for i in targets:
        print(f"- {label(i)}: {i['why']}")
        for how in i.get("how") or ():
            print(f"  - 처리(계획만, 실행 안 함): `{how}`")
    kept = [i for i in data["items"] if not i["target"]]
    if kept:
        print(f"\n## 남김 {len(kept)}개\n")
        by = {}
        for i in kept:
            by.setdefault((i["kind"], i["why"]), []).append(i)
        for (k, why), xs in by.items():
            print(f"- {k} · {why}: {len(xs)}개" + ("" if len(xs) > 3 else " — " + "; ".join(label(x) for x in xs)))
    for title, lines in (("메모", data["notes"]), ("경보", data["alerts"])):
        if lines:
            print(f"\n## {title}\n")
            print("\n".join(f"- {line}" for line in lines))


def kill_tree(item, notes):
    rows = processes()
    r = rows.get(item["pid"])
    if not r or r["cmd"] != item["cmd"] or r["start"] != item["start"]:
        return "사라졌거나 다른 프로세스"
    pids = tree(item["pid"], rows)
    ids = {p: (rows[p]["cmd"], rows[p]["start"]) for p in pids}
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            return f"거부됨: {e}"
    deadline = time.time() + 5
    while time.time() < deadline:
        now = processes()
        left = [p for p in pids if p in now and (now[p]["cmd"], now[p]["start"]) == ids[p]]
        if not left:
            return "종료"
        time.sleep(0.5)
    for p in left:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
    notes.append(f"pid {item['pid']} 트리에서 {len(left)}개는 SIGKILL")
    return "종료(SIGKILL 포함)"


def act(item, notes):
    k = item["key"]
    if k.startswith("proc:"):
        return kill_tree(item, notes)
    if k.startswith("volume:"):
        run(["docker", "volume", "rm", k.split(":", 1)[1]])
    elif k.startswith("image:"):
        run(["docker", "image", "rm", k.split(":", 1)[1]])
    elif k.startswith("branch:"):
        tip = run(["git", "rev-parse", f"refs/heads/{item['name']}"], cwd=item["repo"]).strip()
        if tip != item["tip"]:
            return "tip 이 바뀜, 남김"
        run(["git", "branch", "-D", item["name"]], cwd=item["repo"])
        return f"삭제 (복구: git -C {item['repo']} branch {item['name']} {tip})"
    else:
        run(["git", "worktree", "remove", item["name"]], cwd=item["repo"])
    return "삭제"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    cfg = config()
    if cmd == "reap":
        plan = json.loads(pathlib.Path(opt("--plan") or sys.exit("reap needs --plan PLAN.json")).read_text())
        wanted = {}
        for i in plan["items"]:
            if i["target"] and i["kind"] in JANITOR:
                print(f"- 보고만 {label(i)}: report-only 종류라 reap 이 처리하지 않음")
            elif i["target"]:
                wanted[i["key"]] = i
        fresh, notes = scan({i["kind"] for i in wanted.values()}, plan["hours"], cfg, plan["repos"])
        now = {i["key"]: i for i in fresh if i["target"]}
        for key, old in wanted.items():
            new = now.get(key)
            if not new or any(new.get(k) != old.get(k) for k in ("cmd", "start", "tip")):
                print(f"- 건너뜀 {label(old)}: 더는 정리 대상이 아님")
                continue
            try:
                print(f"- {label(new)}: {act(new, notes)}")
            except RuntimeError as e:
                print(f"- 실패 {label(new)}: {e}")
        for n in notes:
            print(f"- 메모: {n}")
        return
    if cmd == "load":
        data = load_report(cfg, int(opt("--top") or 10))
        if "--json" in sys.argv:
            print(json.dumps(data, ensure_ascii=False, indent=1))
        else:
            print_load(data)
        return
    if cmd == "ledger":
        path = ledger_path()
        if sys.argv[2:3] == ["add"]:
            rid = opt("--path") or opt("--id")
            if opt("--kind") == "gui-app" and opt("--pid"):
                r = processes().get(int(opt("--pid"))) if opt("--pid").isdigit() else None
                rid = r and f"{r['pid']}@{r['start']}"
            if not rid:
                sys.exit("ledger add needs --path, --id, or --pid (gui-app) of a live process")
            rid = os.path.realpath(rid) if opt("--path") else rid
            wt = opt("--worktree") and os.path.realpath(opt("--worktree"))
            print(json.dumps(ledger_add(path, opt("--kind"), rid, opt("--pr"), wt, opt("--run"), opt("--by")),
                             ensure_ascii=False))
        elif sys.argv[2:3] == ["list"]:
            entries = ledger_read(path)
            if "--json" in sys.argv:
                print(json.dumps(entries, ensure_ascii=False, indent=1))
            else:
                for e in entries:
                    links = " ".join(f"{k}={v}" for k, v in (e.get("links") or {}).items() if v)
                    print(f"{e.get('ts')}\t{e.get('kind')}\t{e.get('id')}\t{links}")
        else:
            sys.exit(__doc__)
        return
    if cmd == "schedule":
        script = pathlib.Path(__file__).resolve()
        top = run(["git", "-C", str(script.parent), "rev-parse", "--show-toplevel"], check=False)
        workspace = opt("--workspace") or (top and f"path:{top.strip()}")
        if not workspace:
            sys.exit("schedule needs --workspace <selector>: the checkout the automation runs in")
        argv = schedule_cmd(script, STATE, workspace, opt("--provider") or "claude", ledger_path())
        print(" ".join(shlex.quote(a) for a in argv))
        if "--write" in sys.argv:
            out = run(argv)
            print(out)
        else:
            print("\n(출력만 했다. 실제로 만들려면 --write)")
        return
    if cmd not in ("scan", "precheck"):
        sys.exit(__doc__)
    names = (opt("--kinds") or ",".join(JANITOR if cmd == "precheck" else KINDS)).split(",")
    names = [k for n in names for k in (JANITOR if n == "janitor" else (n,))]
    kinds = set(names) & set(KINDS + JANITOR)
    hours = float(opt("--hours") or cfg.get("hours") or 6)
    repo_list = repos([a for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--repo"]) \
        if {"branch", "worktree", "orca-worktree"} & kinds else []
    items, notes = scan(kinds, hours, cfg, repo_list)
    msgs, totals = alerts(items, {**ALERT, **(cfg.get("alert") or {})})
    data = {"at": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "hours": hours,
            "kinds": [k for k in KINDS + JANITOR if k in kinds], "repos": repo_list, "items": items, "notes": notes, "alerts": msgs, "totals": totals}
    if cmd == "precheck":
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "janitor-plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
        code, targets = precheck(items, STATE)
        for n in notes + [f"{i['key']}: {i['why']}" for i in items if "읽지 못함" in i["why"]]:
            print(f"메모: {n}", file=sys.stderr)
        print(f"정리 대상 {len(targets)}개, " + ("지난 보고 이후 바뀜: 실행" if code == 0 else "바뀐 것 없음 또는 대상 없음: 건너뜀"))
        sys.exit(code)
    if opt("--plan"):
        pathlib.Path(opt("--plan")).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        report(data)


if __name__ == "__main__":
    main()
