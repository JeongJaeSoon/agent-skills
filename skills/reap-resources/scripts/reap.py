#!/usr/bin/env python3
"""Inventory and reap what dead sessions left behind on this machine.

Usage:
  python3 reap.py scan [--hours N] [--kinds orphan,docker,branch,worktree] [--repo PATH]...
                       [--plan OUT.json] [--json]
  python3 reap.py reap --plan PLAN.json
  python3 reap.py load [--top N] [--json]
  python3 reap.py ledger add --kind KIND (--path P | --id ID | --pid N) [--url U] [--pr owner/repo#N] [--worktree W] [--run R] [--by B]
                             compose-stack: --id PROJECT [--pid N] [--previewed] [--keep REASON --until ISO]
  python3 reap.py ledger list [--json]
  python3 reap.py precheck [--kinds ...] [--load] [--act]
  python3 reap.py schedule [--load] [--workspace SELECTOR] [--provider claude] [--write]
  python3 reap.py inventory --unlabeled-volumes [--json]
  python3 reap.py origin-label verify-<app>

scan only reads. reap re-scans and acts only on the plan's targets that are still targets with the
same identity (pid and start time, branch tip), so nothing outside the list is touched. On a plan named janitor-plan.json
it acts only on the scheduled kinds.
load only reads: the top CPU consumers, each classed ours-idle, ours-working, leftover or system, with the
action for its class.
--repo: repos for the branch and worktree kinds. Default: every repo in `orca repo list`, else
the repo of the current directory.
Config: ~/.claude/agent-skills.json → "reap": {"hours": 6, "orphan_paths": [...], "alert": {...},
"load_limit": <5-minute load; default the CPU count>, "load_cooldown_minutes": 60, "idle_minutes": 30, "evidence_roots": [...], "user_folders": [], "user_folder_days": 7,
"user_folder_screenshot_days": 1, "program_days": 7, "state_days": 7, "log_max_kb": 512, "inbox_to": "run:<id>", "plan_repeat": 2,
"preview_ports": [4500, 4599], "stack_volumes": {"prefixes": [...], "label": "...", "protect": [...]},
"stopped_containers": {"prefixes": [...], "protect": [...]}, "labeled_volumes": {"namespace": "...", "origins": [...], "protect": [...]}}.
Janitor kinds (card, orca-worker, tmpdir, gui-app, chrome-window, evidence, remote-branch, compose-stack, stack-volume,
stopped-container, labeled-volume; `--kinds janitor`): card is every Orca card, judged by cards.py; stack-volume only
volumes of stacks under reap.stack_volumes.prefixes; stopped-container only containers named under
reap.stopped_containers.prefixes that compose did not make; labeled-volume only volumes whose <namespace>.origin label is
in reap.labeled_volumes.origins, both only once the owner stack's card has closed (owner_hold); the rest cover only what the ledger lists or an Orca orchestration worker made. user-folder covers only top-level entries of explicitly configured reap.user_folders
untouched for reap.user_folder_days (default 7; screenshots and screen recordings reap.user_folder_screenshot_days,
default 1), and keeps them all while Finder empties the trash on its own, unless $AGENT_SKILLS_TRASH is set.
orca-worktree and done-card, the card kinds before card, are gone: --kinds or a plan naming them is refused. reap moves files to the trash and never empties it (the OS may: scan
alerts when Finder's auto-empty is on), and leaves a chrome-window to the agent's browser tools. $AGENT_SKILLS_TRASH replaces the trash with a plain folder.
The ledger is --ledger or $AGENT_SKILLS_LEDGER, default <state>/ledger.jsonl (state: --state or $AGENT_SKILLS_STATE,
default ~/.local/state/agent-skills). program moves a closed program untouched for
reap.program_days to <programs>/_archive unless it has a resume or handoff note; state-file trashes reorient session files
older than reap.state_days and rotates sync.log past reap.log_max_kb. precheck scans the janitor kinds, and user-folder,
program and state-file at most once a day, writes janitor-plan.json
to the state dir and exits 0 when there are targets, except: the set the last reap failed on, for a day; and a set already
planned reap.plan_repeat times with no reap since, retried once a day, with one message to reap.inbox_to. precheck --load
first exits 1 unless the 5-minute load is at reap.load_limit, the periodic janitor is on, and no precheck ran
within reap.load_cooldown_minutes (default 60). precheck --act is the scheduled round with no agent: under
<state>/janitor.lock it plans, reaps that plan in the same process (scheduled kinds only; a chrome-window and a target
whose last act failed less than a day ago are left), sends one message to reap.inbox_to when what a person has to look
at gained a key, logs <state>/janitor-runs.jsonl and always exits 1, so Orca starts no agent. schedule prints the `orca automations create` command (--load: the load job); only --write runs it.
"""
import contextlib, datetime as dt, functools, hashlib, json, os, pathlib, re, shlex, shutil, signal, stat, subprocess, sys, time, unicodedata

sys.path.append(str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
try:
    from sync import reload_row
except ImportError:  # an install without the repo's scripts/
    def reload_row(row):
        return False

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
KINDS = ( "orphan", "docker", "branch", "worktree")
ALERT = { "orphan_procs": 5, "dangling_volumes": 50,
         "stale_branches": 30, "stale_worktrees": 10}
SCRATCH = re.compile(r"/claude-\d+/([^/]+)/([0-9a-f-]{36})/scratchpad/")
ENV = {**os.environ, "LC_ALL": "C"}


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


# None waits for every command; patrol's candidate round sets seconds, so one hung command fails its source instead
# of the round.
RUN_TIMEOUT = None


def run(cmd, cwd=None, check=True, env=None, timeout=None):
    timeout = timeout or RUN_TIMEOUT
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env or ENV, timeout=timeout)
    except FileNotFoundError:
        if check:
            raise RuntimeError(f"{cmd[0]}: not installed")
        return None
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError(f"{' '.join(cmd[:4])}: timed out after {timeout}s")
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


def cwds(inodes=False):
    """pid → cwd for this user's processes; None unless every one of them was read.
    inodes: pid → (cwd, inode of the directory the process is in, or None when unknown)."""
    if os.path.isdir("/proc/self"):
        out = {}
        for d in os.listdir("/proc"):
            try:
                if d.isdigit() and os.stat(f"/proc/{d}").st_uid == os.getuid():
                    # A process left in a removed directory reads as "<path> (deleted)".
                    c = os.readlink(f"/proc/{d}/cwd").removesuffix(" (deleted)")
                    out[int(d)] = (c, None) if inodes else c
            except FileNotFoundError:
                pass
            except OSError:
                return None
        return out
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    listing = run([lsof, "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpin"], check=False)
    if not listing:
        return None
    out, pid, ino = {}, None, None
    for line in listing.splitlines():
        if line[:1] == "p":
            pid, ino = int(line[1:]), None
        elif line[:1] == "i" and line[1:].isdigit():
            ino = int(line[1:])
        elif line[:1] == "n" and pid:
            out[pid] = (line[1:], ino) if inodes else line[1:]
    return out


def cwd_gone(path, ino):
    """The directory a process sits in was removed: lsof still prints the old path. One recreated at the same path is
    another inode. lsof under LC_ALL=C prints a non-ASCII name as \\xNN escapes, which no stat finds: unknown, not gone."""
    if "\\" in path:
        return False
    try:
        return ino is not None and os.stat(path).st_ino != ino
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False


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


SHELLS = ("zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh", "nu")
TERMINAL_PARENTS = ("login", "tmux", "screen", "sshd", "sshd-session")


def human_shell(pid, rows):
    """A shell a person types into: a login shell (Orca and Terminal start `-zsh`) or a shell straight under a
    terminal multiplexer or sshd."""
    r = rows.get(pid)
    if not r:
        return False
    argv0 = r["cmd"].split()[0] if r["cmd"].split() else ""
    if argv0.startswith("-"):
        return True
    parent = rows.get(r["ppid"]) or {}
    pargv = (parent.get("cmd") or "").split()[:1]
    return os.path.basename(argv0) in SHELLS and bool(pargv) and os.path.basename(pargv[0]).lstrip("-") in TERMINAL_PARENTS


def human_owned(pid, rows):
    """The process is a person's: walking up from it, a human shell comes before any Claude session. A process an
    agent started (a Claude session between it and the shell) is not."""
    seen = set()
    while pid in rows and pid not in seen and pid > 1:
        seen.add(pid)
        if is_claude(rows[pid]["cmd"]):
            return False
        if human_shell(pid, rows):
            return True
        pid = rows[pid]["ppid"]
    return False


def human_in_tree(root, rows):
    """Pids in root's tree that belong to a person (see human_owned)."""
    return [p for p in tree(root, rows) if human_owned(p, rows)]


def judge_orphans(rows, prefixes, hours, cwd_of=None):
    """cwd_of: pid → (cwd, inode) from cwds(inodes=True). A process reparented to 1 whose directory was removed (a
    finished card's dev server, a review worktree's broker) has no session left to stop it."""
    pres = {x for p in prefixes for x in (p.rstrip("/"), real(p).rstrip("/"))}
    out = []
    for r in rows.values():
        if r["ppid"] != 1:
            continue
        # Only argv[0] and argv[1] (the script an interpreter runs) count. Looking past options would take an
        # option's value (a report file, a data dir) for the script.
        exe = r["cmd"].split()[:2]
        cwd = (cwd_of or {}).get(r["pid"])
        if any(under(e, p) for e in exe for p in pres):
            why = "ppid 1, 테스트 캐시 경로"
        elif cwd and cwd_gone(*cwd):
            why = f"ppid 1, cwd {cwd[0]} 가 사라짐"
        else:
            continue
        young = r["age"] < hours * 3600
        human = human_in_tree(r["pid"], rows)
        if human:
            why = f"트리에 사람이 띄운 셸·그 자식(pid {human[0]})"
        out.append(proc_item("orphan", r, rows, name=r["cmd"][:80], why=f"{hours}시간 미만" if young else why,
                             target=not young and not human))
    return out


def parse_ts(s):
    s = s.strip()
    m = re.match(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) ([+-]\d{4})", s)
    if m:
        return dt.datetime.strptime(" ".join(m.groups()), "%Y-%m-%d %H:%M:%S %z").timestamp()
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def judge_volumes(vols, active, hours, now, owner_label=None):
    """vols: [{name, labels, created}] of dangling volumes; active: compose projects that have any container.
    owner_label: reap.stack_volumes.label, the other owner label."""
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
                    "owners": volume_owners(v["labels"], (None, owner_label)), "why": why, "target": target})
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
    return owner_holds(judge_volumes(vols, set(states), hours, now, owner_label(config())), notes) + \
        judge_images(images, used, hours, now)


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
        try:
            return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=repo, capture_output=True,
                                  timeout=RUN_TIMEOUT).returncode == 0
        except subprocess.TimeoutExpired:
            return False  # not shown to be on the PR: the branch stays
    return judge_branches(repo, branches, checked, default, json.loads(prs), is_ancestor)


def orca_paths():
    """Paths of Orca-managed worktrees (their lifecycle is `orca worktree rm`); None when Orca is there but unreadable."""
    if not shutil.which("orca"):
        return set()
    out = run(["orca", "worktree", "list", "--limit", "10000", "--json"], check=False)
    try:
        res = json.loads(out)["result"]
        return None if res.get("truncated") else {w["path"] for w in res["worktrees"]}
    except (AttributeError, TypeError, ValueError, KeyError):
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
    rows = processes() if "orphan" in kinds else {}
    # One lsof serves both kinds: it is the slowest read of a scan.
    proc_cwds = cwds(inodes=True) if {"orphan", "worktree"} & kinds else {}
    cwd_of = None if proc_cwds is None else {pid: c for pid, (c, _) in proc_cwds.items()}
    if "orphan" in kinds:
        if not cfg.get("orphan_paths"):
            notes.append(f"orphan: {CONFIG} 의 reap.orphan_paths 가 비어 있어 cwd 가 사라진 고아만 본다")
        if proc_cwds is None:
            notes.append("orphan: 프로세스 cwd 를 읽지 못함, cwd 가 사라진 고아는 보지 않는다")
        items += judge_orphans(rows, cfg.get("orphan_paths") or [], hours, proc_cwds)
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
        items += janitor_scan(kinds, repo_list, ledger_read(ledger_path()), notes, hours=hours,
                              evidence_roots=cfg.get("evidence_roots"),
                              preview=tuple(cfg.get("preview_ports") or (4500, 4599)), cfg=cfg)
    if "user-folder" in kinds:
        days = user_folder_days(cfg, notes)
        shot_days = days and user_folder_days(cfg, notes, "user_folder_screenshot_days", 1,
                                              f"스크린샷도 {days:g}일 기준")
        folders = cfg.get("user_folders")
        if not isinstance(folders, list) or not folders or not all(isinstance(f, str) and f.strip() for f in folders):
            notes.append("user-folder: reap.user_folders is unset or invalid; skipped")
        elif days:
            items += user_folder_scan(days, notes, shot_days, folders=folders)
        if not os.environ.get("AGENT_SKILLS_TRASH") and trash_auto_empty():
            for i in items:
                if i["kind"] == "user-folder" and i["target"]:
                    i.update(target=False, why="휴지통 자동 비움이 켜져 있어 남김(AGENT_SKILLS_TRASH 로 일반 폴더를 주면 정리)")
    if "program" in kinds:
        items += judge_programs(PROGRAMS, float(cfg.get("program_days") or 7), time.time())
    if "state-file" in kinds:
        items += judge_state_files(STATE, float(cfg.get("state_days") or 7), float(cfg.get("log_max_kb") or 512), time.time())
    return items, notes


@functools.cache
def trash_auto_empty():
    """Finder's "Remove items from the Trash after 30 days": what goes to the trash is then deleted for good."""
    return sys.platform == "darwin" and \
        (run(["defaults", "read", "com.apple.finder", "FXRemoveOldTrashItems"], check=False) or "").strip() == "1"


# Janitor kinds: every Orca card (card, judged by cards.py), and of the rest only what an agent created (the ledger, or
# an Orca orchestration worker).
JANITOR = ("card", "orca-worker", "tmpdir", "gui-app", "chrome-window", "evidence", "remote-branch",
           "compose-stack", "stack-volume", "stopped-container", "labeled-volume")
# What the scheduled run covers: the janitor kinds, the user's download and desktop folders, closed programs and hook state.
SCHEDULED = JANITOR + ("user-folder", "program", "state-file")
# The card kinds before `card`, no longer scanned. A plan or --kinds naming them is refused rather than run as nothing.
# reap.act's "orca-worktree" removal stays: cards.py and reap_hard.py call it.
RETIRED_KINDS = ("orca-worktree", "done-card")
# What `ledger add` accepts: the kinds whose scan reads the ledger. `card` reads Orca instead.
LEDGER_KINDS = tuple(k for k in JANITOR if k not in ("card", "stack-volume", "stopped-container", "labeled-volume")) + ("stack-seen",)
STACK_LINES = ("compose-stack", "stack-seen")  # the ledger lines that say which card used a stack
# Kinds that would find something new every round; the precheck looks at them once a day.
DAILY = ("user-folder", "program", "state-file")
MEDIA = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".webm")
STATE_HOME = pathlib.Path("~/.local/state/agent-skills").expanduser()
STATE = pathlib.Path(opt("--state") or os.environ.get("AGENT_SKILLS_STATE") or STATE_HOME).expanduser()
PATROL_FRESH_S = 3600
PATROL_KEEP = ("working", "waiting", "run")  # a session moving, at a prompt for a human, or a Run worker its lead watches


def patrol_working(now=None):
    """Card paths `orch patrol` keeps (PATROL_KEEP, and the round's `activity` parked or unknown) in a round under an
    hour old: the same set cards.py's scan and the guard read. Read only to keep a card: a patrol verdict never makes
    anything a target, and a path missing here (a round with `failed` sources above all) is not evidence the card is
    safe to close."""
    return patrol_read(now, activity=True) or set()


PATROL_TERMINAL_READ = ("working", "run")  # verdicts read off a terminal: with none left, an hour-old one is not a live session
PATROL_KEEP_ACTIVITY = ("parked", "unknown")  # v2 `activity`: a session that wakes itself, or one patrol could not tell


def patrol_states(now=None, activity=False):
    """{card path: what patrol judged}, the cards PATROL_KEEP keeps; None when the verdicts file exists and cannot be
    read: a guard keeps the card then. No file, or one over an hour old, is {}. activity adds the cards the round's
    `activity` keeps (PATROL_KEEP_ACTIVITY). cards.py's scan and the guard right before a close both read it with
    activity, so a plan never holds a card that the close then refuses."""
    try:
        v = json.loads((STATE / "patrol-verdicts.json").read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    try:
        if (now or time.time()) - float(v.get("t") or 0) > PATROL_FRESH_S:
            return {}
        kept = {real(p): x for p, x in (v.get("cards") or {}).items() if x in PATROL_KEEP}
        if activity:
            act = v.get("activity")
            act = {} if act is None else act  # a file from before v2 has none
            if not isinstance(act, dict) or not all(isinstance(x, dict) and isinstance(x.get("state"), str)
                                                    for x in act.values()):
                return None  # an activity row that cannot be read is no proof the card may go
            for p, x in act.items():
                if x["state"] in PATROL_KEEP_ACTIVITY:
                    kept[real(p)] = x["state"]  # the stronger reason wins over a working or run the same round also gave
        return kept
    except (ValueError, TypeError, AttributeError):
        return None


def patrol_read(now=None, activity=False):
    """patrol_states as a set of paths (None when unreadable)."""
    kept = patrol_states(now, activity)
    return None if kept is None else set(kept)


PATROL_WHY = {"working": "작업 중", "waiting": "사람 대기", "run": "Run 워커", "parked": "스스로 깨울 예약을 걸어 둠(parked)",
              "unknown": "patrol 이 알 수 없음(unknown)"}


def patrol_hold(state=None):
    """Why patrol keeps a card, in patrol's word for it; every refusal of cards.py and its guard goes through here."""
    return "순찰 판정: " + PATROL_WHY.get(state, "작업 중·사람 대기·Run 워커") + "(orch patrol)"


ORCH = pathlib.Path(__file__).resolve().parents[2] / "orchestrate" / "scripts" / "prog.py"


# Results that say nothing was done: the second look left it, or it was gone, or a close-only card had no terminal.
NOOP_PREFIX = ("건너뜀", "사라졌거나", "거부됨", "실패", "dry-run")
NOOP_RESULT = re.compile(r"^터미널 0개 닫음, (worktree 는 남김|삭제는 건너뜀|건너뜀)")
# What names one target, beyond its key: logged with each act and compared by `sweep diff` (a list is the files or
# terminals one act covers). Patrol's candidates carry the same fields.
ACT_IDENTITY = ("head", "tip", "ino", "dir", "pid", "start", "cmd", "created", "flag", "port", "local", "paths")


def dir_ino(path):
    """The inode of a card's folder: a card made again on the same path (even at the same HEAD) has another one."""
    try:
        return os.stat(path).st_ino
    except (OSError, ValueError, TypeError):
        return None


def rm_refusal(path):
    """Why a card must not be removed by `orca worktree rm`: its removal was refused and recorded (rm_refused.py), or the
    record cannot be read. None when nothing stops it."""
    import rm_refused
    held = rm_refused.held(path, STATE)
    if held is None:
        return "worktree rm 거부 기록을 읽지 못함"
    return f"사람에게 넘김: worktree rm 이 거부된 카드. 사람이 실행: {rm_refused.command(path)}" if held else None


def acted(result):
    s = str(result or "")
    return bool(s) and not s.startswith(NOOP_PREFIX) and not NOOP_RESULT.match(s)


def act_identity(item):
    return {k: item[k] for k in ACT_IDENTITY if item.get(k) not in (None, "")}


REMOVED = "worktree 는 orca worktree rm 으로 제거"  # the orca-worktree act's own words, at the end of every removal
CARD_EFFECT = {"card:strict": "card:remove", "card:landed": "card:remove", "card:close-idle": "card:close"}


def act_effect(rule, kind, result):
    """What an act really did, by its result: a card removed, a card's terminals closed (its removal skipped, or
    close-only), the rule's own effect for any other kind; None when nothing was done."""
    s = str(result or "")
    if not acted(s):
        return None
    if kind not in ("card", "orca-worktree"):
        return rule
    if REMOVED in s:
        return "card:remove"
    m = re.match(r"터미널 (\d+)개 닫음", s)
    return "card:close" if m and int(m.group(1)) else None


def log_action(item, result, ok, rule=None, closed=None):
    """One line in <state>/reap-actions.jsonl per target reap --plan or cards.py apply acted on (or failed): what
    `sweep diff` matches the shadow's plan against. ok only when it raised nothing and its result says it did
    something. closed: the terminal handles the act really closed (a card). A line that cannot be written is let go."""
    kind = item.get("kind", "card")
    row = {"t": time.time(), "key": item.get("key") or f"card:{item.get('path')}", "kind": kind, "rule": rule,
           "identity": act_identity(item), "result": str(result)[:200], "ok": ok and acted(result),
           "effect": act_effect(rule, kind, result) if ok else None}
    if closed is not None:
        row["handles"] = sorted(closed)
    with contextlib.suppress(Exception):  # a log line is never worth an act left half-reported
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / "reap-actions.jsonl", "a", encoding="utf-8", errors="backslashreplace") as f:
            f.write(line)


SWEEP = pathlib.Path(__file__).resolve().parent / "sweep.py"


def spawn_sweep_shadow(reason, python=None):
    """Start `sweep.py run --shadow` detached after a cleaner's pass; it decides on the newest complete patrol round
    and acts on nothing. False when it could not start, or AGENT_SKILLS_SWEEP=off (tests)."""
    if os.environ.get("AGENT_SKILLS_SWEEP") == "off":
        return False
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / "sweep.log", "a") as out:
            out.write(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {reason}\n")
            out.flush()
            subprocess.Popen([python or sys.executable, str(SWEEP), "run", "--shadow"], stdout=out, stderr=out,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def spawn_patrol(reason, python=None):
    """Start `orch patrol --if-due` detached, so a LaunchAgent pass never waits on it; it prints to
    <state>/patrol.log and gives up on its own at 120 seconds. False when it could not start, or AGENT_SKILLS_PATROL=off
    (tests)."""
    if os.environ.get("AGENT_SKILLS_PATROL") == "off":
        return False
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / "patrol.log", "a") as out:
            out.write(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {reason}\n")
            out.flush()
            subprocess.Popen([python or sys.executable, str(ORCH), "patrol", "--if-due"], stdout=out, stderr=out,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")


# macOS 15+, keeps Finder's Put Back. Never a `trash` found on PATH: another one reads -s as "empty the trash".
SYSTEM_TRASH, TRASH_TIMEOUT = "/usr/bin/trash", 300


def trash(*paths):
    """Move paths to the trash; nothing here deletes for good."""
    folder = os.environ.get("AGENT_SKILLS_TRASH")
    if not folder and os.access(SYSTEM_TRASH, os.X_OK):
        try:
            r = subprocess.run([SYSTEM_TRASH, "-s", *paths], capture_output=True, text=True, timeout=TRASH_TIMEOUT,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"trash: {TRASH_TIMEOUT}초 안에 끝나지 않음")
        if r.returncode:
            raise RuntimeError(f"trash: {r.stderr.strip()[:200]}")
        return "휴지통으로 옮김"
    dest = pathlib.Path(folder or "~/.Trash").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    for path in paths:
        name = os.path.basename(path.rstrip("/"))
        target = dest / name
        if os.path.lexists(target):
            target = dest / f"{name} {dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        shutil.move(path, target)
    return f"휴지통으로 옮김({dest})"


def ledger_path():
    return pathlib.Path(opt("--ledger") or os.environ.get("AGENT_SKILLS_LEDGER") or STATE / "ledger.jsonl").expanduser()


def dir_gone(d):
    """True only for a directory that is not there; one that cannot be looked at (permissions, I/O) is not gone."""
    try:
        return not stat.S_ISDIR(os.stat(d).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except (OSError, ValueError):
        return False


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


def ledger_add(path, kind, rid, pr=None, worktree=None, run_id=None, by=None, **extra):
    """extra: url (chrome-window), or retired=True, which drops the id from every later scan."""
    if kind not in LEDGER_KINDS:
        raise SystemExit(f"--kind must be one of {', '.join(LEDGER_KINDS)}")
    entry = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "run": run_id, "kind": kind, "id": rid,
             "links": {"pr": pr, "worktree": worktree}, "by": by, **{k: v for k, v in extra.items() if v is not None}}
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
        """owner/repo#N → {"state", "headRefOid", "closedAt"}, or None when gh cannot read it."""
        if ref not in cache:
            p = parse_pr(ref)
            out = p and gh(["gh", "pr", "view", str(p[1]), "-R", p[0], "--json", "state,headRefOid,closedAt"], check=False)
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
    """The last ledger line per id of one kind (the ledger only appends), unless that line retires the id."""
    last = {e["id"]: e for e in entries if e.get("kind") == kind}
    return [e for e in last.values() if not e.get("retired")]


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
        how = [f"{LSREGISTER} -u {shlex.quote(a)}" for a in apps(path)] + [f"trash {shlex.quote(path)}"]
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
                    "target": bool(done), "how": [f"kill -TERM {pid}"], "gpid": int(pid), "start": start})
    return out


def judge_chrome(entries, pr_state, exists=os.path.exists):
    # No script can tell whether a tab is still open or close it: the agent does that with its browser tools.
    # Chrome reuses ids after a restart, so the recorded URL is what tells the agent it is the same tab.
    out = []
    for e in latest(entries, "chrome-window"):
        done, why = link_verdict(e.get("links") or {}, pr_state, exists)
        url = e.get("url")
        if done and not url:
            done, why = False, "URL 기록 없음, 같은 창·탭인지 확인 못 함(보고만)"
        out.append({"kind": "chrome-window", "key": f"chrome-window:{e['id']}", "name": e["id"], "why": why, "url": url,
                    "target": bool(done),
                    "how": [f"에이전트가 처리: 창·탭 {e['id']} 의 URL 이 {url} 일 때만 브라우저 도구로 닫는다. 다르면 닫지 않는다"]})
    return out


def media_files(path):
    """(media, other) file paths under path; a file path is its own list."""
    if not os.path.isdir(path):
        return ([path], []) if path.lower().endswith(MEDIA) else ([], [path])
    media, other = [], []
    for root, _, files in os.walk(path):
        for f in files:
            (media if f.lower().endswith(MEDIA) else other).append(os.path.join(root, f))
    return media, other


def judge_evidence(entries, pr_state, hours, now, exists=os.path.exists, files=media_files):
    """A local copy of PR evidence goes once its PR has been merged or closed for `hours`: by then it is on the PR."""
    out = []
    for e in latest(entries, "evidence"):
        path, ref = e["id"], (e.get("links") or {}).get("pr")
        if not exists(path):
            continue
        base = {"kind": "evidence", "key": f"evidence:{path}", "name": path, "links": e.get("links") or {}}
        s = ref and pr_state(ref)
        closed = s and s["state"] != "OPEN" and s.get("closedAt")
        if not ref:
            why = "연결된 PR 없음(보고만)"
        elif not s:
            why = f"PR {ref} 상태를 읽지 못함"
        elif s["state"] == "OPEN":
            why = f"열린 PR {ref}"
        elif not closed:
            why = f"PR {ref} 닫힌 시각을 읽지 못함"
        elif now - parse_ts(closed) < hours * 3600:
            why = f"PR {ref} {s['state'].lower()} 후 {hours:g}시간 미만"
        elif files(path)[1]:
            why = "이미지·영상이 아닌 파일이 섞임(보고만)"
        else:
            out.append({**base, "why": f"PR {ref} {s['state'].lower()} 후 {hours:g}시간 지남", "target": True,
                        "how": [f"trash {shlex.quote(path)}"]})
            continue
        out.append({**base, "why": why, "target": False, "how": []})
    return out


def stray_evidence(roots, registered, hours, now):
    """Unregistered images and videos directly in a root or one directory below it: one report-only item per directory."""
    out = []
    for root in roots:
        dirs = []
        with contextlib.suppress(OSError):
            dirs = [root] + sorted(d.path for d in os.scandir(root) if d.is_dir(follow_symlinks=False))
        for d in dirs:
            if any(under(d, r) for r in registered):
                continue
            n = size = 0
            with contextlib.suppress(OSError):
                for f in os.scandir(d):
                    if f.name.lower().endswith(MEDIA) and f.is_file(follow_symlinks=False) and f.path not in registered:
                        st = f.stat(follow_symlinks=False)
                        if now - st.st_mtime >= hours * 3600:
                            n, size = n + 1, size + st.st_size
            if n:
                out.append({"kind": "evidence", "key": f"evidence-stray:{d}", "name": d, "target": False, "how": [],
                            "why": f"ledger 에 없는 이미지·영상 {n}개, {human(size)}(보고만)"})
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


COORDINATOR_ARGV = re.compile(r"--agent[ =]agent-skills:(lead|orchestrator)\b|--name[ =]lead:")
COORDINATOR_ROLES = ("lead", "orchestrator")


def lstart_epoch(s):
    try:
        return time.mktime(time.strptime(s, "%a %b %d %H:%M:%S %Y"))
    except (ValueError, TypeError):
        return None


# Where the reorient hook writes them, which `--state` does not move.
ROLES = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or STATE_HOME).expanduser() / "roles"


def coordinator_in(path, rows, cwd_of, projects, roles=None):
    """Why a live Claude session in path is a lead or the orchestrator, or None: started with `--agent
    agent-skills:lead|orchestrator` (or `--name lead:<slug>`), or a session of this card that has written to its
    transcript since that process started declared `ROLE: lead|orchestrator` (the reorient hook's record).
    Anything it cannot read is an answer too: a reason that keeps the card, never None."""
    inside = [pid for pid, c in cwd_of.items() if under(real(c), path)]
    if missing := [pid for pid in inside if pid not in rows]:
        return f"pid {missing[0]} 의 프로세스 행을 읽지 못함"
    live = [pid for pid in inside if is_claude(rows[pid]["cmd"])]
    for pid in live:
        if m := COORDINATOR_ARGV.search(rows[pid]["cmd"]):
            return f"pid {pid} {m.group(0)}"
    starts = [t for t in (lstart_epoch(rows[pid]["start"]) for pid in live) if t is not None]
    if not live:
        return None
    if not starts:
        return "claude 시작 시각을 읽지 못함"
    try:
        files = session_files(pathlib.Path(projects) / re.sub(r"[^A-Za-z0-9]", "-", path), subagents=False)
    except OSError as e:
        return f"세션 기록 폴더를 읽지 못함({type(e).__name__})"
    for f in files:
        try:
            if f.stat().st_mtime < min(starts) - 60:
                continue
            role = json.loads((pathlib.Path(roles or ROLES) / f"{f.stem}.json").read_text()).get("role")
        except FileNotFoundError:
            continue  # a transcript gone mid-scan, or a session that never declared a role
        except (OSError, ValueError, AttributeError) as e:
            return f"세션 {f.stem[:8]} 의 ROLE 기록을 읽지 못함({type(e).__name__})"
        if role in COORDINATOR_ROLES:
            return f"세션 {f.stem[:8]} ROLE 선언"
    return None


# Orca's terminal host and the login wrapper it starts shells with. A newer Orca that renames them keeps every card.
ORCA_TERMINAL = re.compile(r"(?i)orca[ -]terminal[ -]host|orca-tcc-login")


def orca_shell(pid, rows):
    seen = set()
    while pid in rows and pid not in seen and pid > 1:
        seen.add(pid)
        if ORCA_TERMINAL.search(rows[pid]["cmd"]):
            return True
        pid = rows[pid]["ppid"]
    return False


def regenerated_copies(path, git=run):
    """Untracked `.agents/skills/<name>/` directories whose `.claude/skills/<name>` is tracked: Orca regenerates them
    from the tracked skill. [] when nothing is untracked; None when anything else is, or a read failed."""
    untracked = git(["git", "-C", path, "ls-files", "--others", "--exclude-standard", "-z"], check=False)
    if untracked is None:
        return None
    names = set()
    for f in filter(None, untracked.split("\0")):
        parts = f.split("/")
        if len(parts) < 4 or parts[:2] != [".agents", "skills"]:
            return None
        names.add(parts[2])
    if not names:
        return []
    tracked = git(["git", "-C", path, "ls-files", "-z", "--", ".claude/skills", ".agents/skills"], check=False)
    if tracked is None:
        return None
    have = {tuple(f.split("/")[:3]) for f in filter(None, tracked.split("\0"))}
    # A tracked file beside them makes the directory the repo's own, not a copy.
    if any((".claude", "skills", n) not in have or (".agents", "skills", n) in have for n in names):
        return None
    return sorted(f".agents/skills/{n}" for n in names)


def branch_pr(path, git=run, head=None):
    """(owner/repo#N or None, unread) for the branch checked out at path, else for a PR whose head commit is HEAD:
    a card that pushed HEAD under another branch name has its PR only there."""
    branch = (git(["git", "-C", path, "branch", "--show-current"], check=False) or "").strip()
    prs = branch and git(["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number,url,state,isCrossRepository"],
                          cwd=path, check=False)
    # A failed lookup is not an empty answer: a card with an open PR would pass as one without.
    unread = bool(branch) and prs is None
    # A fork's PR with the same head name is not this branch's.
    own = [p for p in json.loads(prs) if not p.get("isCrossRepository")] if prs else []
    if not own and not unread and head:
        found = git(["gh", "pr", "list", "--search", head, "--state", "all",
                     "--json", "number,url,state,isCrossRepository,headRefOid"], cwd=path, check=False)
        unread = found is None
        # The search also matches a PR that only contains the commit; the one that carried HEAD is the card's.
        own = [p for p in json.loads(found or "[]") if not p.get("isCrossRepository") and p.get("headRefOid") == head]
    own.sort(key=lambda p: p.get("state") != "OPEN")
    m = own and re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", own[0]["url"])
    return (m and f"{m.group(1)}#{m.group(2)}") or None, unread


def worktree_facts(path, ledger_pr, live_turn, users_cwd, git=run):
    common = git(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"], check=False)
    if common is None:
        return None
    main = os.path.dirname(common.strip().rstrip("/"))
    if real(main) == real(path):
        return {"main": True}
    users = [pid for pid, c in users_cwd.items() if under(real(c), real(path))]
    head = (git(["git", "-C", path, "rev-parse", "HEAD"], check=False) or "").strip()
    landed = None
    if not ledger_pr:
        default = git(["git", "-C", path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
        # A failed check reads as not an ancestor, so the checkout stays.
        landed = None if not default else git(["git", "-C", path, "merge-base", "--is-ancestor", "HEAD", default.strip()],
                                              check=False) is not None
    # A HEAD already on the default branch is the head of any PR merged by fast-forward, a fresh lead card's included.
    ref, pr_unread = (ledger_pr, False) if ledger_pr else branch_pr(path, git, None if landed else head)
    if ref:
        landed = None
    # Untracked files count: `orca worktree rm` would take new, never-added work with it.
    status = git(["git", "-C", path, "status", "--porcelain"], check=False)
    copies = None
    if status and all(line.startswith("?? ") for line in status.splitlines()):
        copies = regenerated_copies(path, git)
    ahead = git(["git", "-C", path, "rev-list", "-n1", "HEAD", "--not", "--remotes"], check=False)
    return {"main": False, "repo": main, "pr": ref, "pr_unread": pr_unread, "landed": landed, "turn": live_turn, "users": users,
            "live": live_turn or (f"pid {users[0]} 이 이 worktree 를 cwd 로 사용 중" if users else None),
            # None is a failed read, not an empty answer.
            "unreadable": status is None or ahead is None,
            "dirty": bool(status) and not copies, "regenerated": copies or [], "unpushed": bool(ahead), "head": head}


def orca_json(*args, env=None, cwd=None, timeout=None):
    out = run(["orca", *args, "--json"], cwd=cwd, check=False, env=env, timeout=timeout) if shutil.which("orca") else None
    try:
        return json.loads(out)["result"] if out else None
    except (ValueError, KeyError):
        return None


def automation_workspaces(res):
    """Real paths of the worktrees Orca automations run in (`--workspace`); None when the list is unreadable."""
    if not isinstance(res, dict):
        return None
    return {real(p) for a in res.get("automations") or [] if (p := (a.get("workspaceId") or "").partition("::")[2])}


def orca_workers(state=None):
    """worker-list as this terminal sees it, every page; None when any page fails: a partial list could miss a retained
    row and count a user's worktree as made."""
    import cardguard
    try:
        return cardguard.worker_pages(state, every_run=False)
    except (SystemExit, Exception):
        return None


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
            live[p] = f"살아 있는 워커 턴({w.get('dispatchId')}, {w.get('runId')})"
    return live, made, users


RESTART_LOST = ("abandoned", "interrupted")  # dispatch statuses of a worker whose terminal a restart took


def recovering_worktrees(workers):
    """{real path: dispatch id} of cards whose newest Dispatch is abandoned or interrupted: the worker was cut off and
    its card waits for a resume (reboot SKILL.md), so nothing there is finished. worker-list is newest first, so a
    later Dispatch on the card (the resume, or work that completed) means the wait is over."""
    newest = {}
    for w in workers:
        p = ((w.get("resource") or {}).get("worktreeId") or "").partition("::")[2].partition("::")[0]  # no `::workspace:<n>`
        if p:
            newest.setdefault(real(p), w)
    return {p: w.get("dispatchId") for p, w in newest.items() if w.get("dispatchStatus") in RESTART_LOST}


def unreleased_settled(workers):
    """{real path: [{dispatch, run, outcome}]}: Run workers whose dispatch settled (succeeded or failed) but whose terminal
    Orca does not list as released. They keep nothing: patrol and the live set read `in_progress` only. Releasing one is
    its Run owner's call (`worker-release`), so a cleaner only names it. Only a local dispatch that still owns a terminal
    resource counts: a row with no resource (nothing shows a terminal is left), a retained one (a context-only dispatch or
    a card the user took over, which release refuses) and another host's are no leak."""
    out = {}
    for w in workers:
        pj, res = w.get("projection") or {}, w.get("resource") or {}
        p = (res.get("worktreeId") or "").partition("::")[2]
        outcome = pj.get("outcome")
        local = ((pj.get("host") or {}).get("kind") or "local") == "local"
        if p and local and outcome in ("succeeded", "failed") and not res.get("retainedReason") \
                and w.get("terminalState") not in (None, "released", "retained"):
            out.setdefault(real(p), []).append({"dispatch": w.get("dispatchId"), "run": w.get("runId"), "outcome": outcome})
    return out


COMPOSE_PROJECT = "com.docker.compose.project"
STOPPED_STATES = ("exited", "created")
ORIGIN_RESERVED = ("com.docker.", "io.docker.", "org.dockerproject.")  # Docker keeps these label namespaces for itself
ANON_VOLUME = re.compile(r"[0-9a-f]{64}")
CONTAINER_SHAPE = {"Name": str, "State": dict, "Config": dict, "HostConfig": dict, "Mounts": list}


def docker_ts(s):
    """Docker's RFC 3339 with nanoseconds; Python before 3.11 reads at most microseconds."""
    return dt.datetime.fromisoformat(re.sub(r"\.\d+", "", s).replace("Z", "+00:00")).timestamp()


def docker_containers():
    """`docker inspect` of every container in any state: [] without docker or with none, None when it cannot be read."""
    if not shutil.which("docker"):
        return []
    ids = run(["docker", "ps", "-aq", "--no-trunc"], check=False)
    if ids is None:
        return None
    if not ids.split():
        return []
    out = run(["docker", "inspect", *ids.split()], check=False)
    try:
        found = None if out is None else json.loads(out)
        return found if {c["Id"] for c in found or ()} == set(ids.split()) else None
    except (ValueError, TypeError, KeyError):
        return None


def compose_stacks():
    """{project: {running, stopped, first (oldest container created), last (newest start or stop), ports, dirs (compose
    working directories), nodir (containers without one)}}; None when docker cannot be read."""
    found = docker_containers()
    if found is None:
        return None
    stacks = {}
    for c in found:
        labels = (c.get("Config") or {}).get("Labels") or {}
        proj = labels.get(COMPOSE_PROJECT)
        # An app can copy the project and service labels onto containers it starts; `compose down` matches only the
        # ones compose created, which carry the oneoff label, and leaves the rest.
        if not proj or "com.docker.compose.oneoff" not in labels:
            continue
        s = stacks.setdefault(proj, {"running": 0, "stopped": 0, "first": None, "last": 0.0, "ports": set(), "dirs": set(), "nodir": 0})
        if labels.get("com.docker.compose.project.working_dir"):
            s["dirs"].add(labels["com.docker.compose.project.working_dir"])
        else:
            s["nodir"] += 1
        st = c.get("State") or {}
        s["running" if st.get("Status") in ("running", "restarting", "paused") else "stopped"] += 1
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            created = docker_ts(c.get("Created"))
            s["first"] = created if s["first"] is None else min(s["first"], created)
        for k in ("StartedAt", "FinishedAt"):
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                s["last"] = max(s["last"], docker_ts(st.get(k)))
        for binds in ((c.get("NetworkSettings") or {}).get("Ports") or {}).values():
            s["ports"] |= {int(b["HostPort"]) for b in binds or () if str(b.get("HostPort", "")).isdigit()}
    return stacks


def folded(entries, kind):
    """One record per id. A line with a PR or worktree link is a registration and starts the record over (a stack
    brought up again under the same name, after a reap retired it); a keep or previewed line adds to it."""
    out = {}
    for e in entries:
        if e.get("kind") != kind:
            continue
        fresh = e["id"] not in out or any((e.get("links") or {}).values())
        prev = {"registered": e.get("ts")} if fresh else out[e["id"]]
        links = {**(prev.get("links") or {}), **{k: v for k, v in (e.get("links") or {}).items() if v}}
        out[e["id"]] = {**prev, **{k: v for k, v in e.items() if v is not None}, "links": links}
    return [e for e in out.values() if not e.get("retired")]


def stack_owner(links, pr_state, pr_of=branch_pr, exists=os.path.exists, head=None):
    """(done, why) for the card that started a stack: its PR merged or closed, or its worktree gone. A worktree link
    with no PR link asks the branch's PR, which counts only when it carried the worktree's HEAD: an old closed PR on a
    reused branch name says nothing about the work in progress."""
    done, why = link_verdict(links, pr_state, exists)
    wt = links.get("worktree")
    if done is False and not links.get("pr") and wt:
        ref, unread = pr_of(wt)
        if unread:
            return False, f"worktree {wt} 의 PR 을 읽지 못함"
        if not ref:
            return False, f"worktree {wt} 있음, PR 없음"
        done, why = link_verdict({"pr": ref}, pr_state, exists)
        tip = (head or (lambda p: (run(["git", "-C", p, "rev-parse", "HEAD"], check=False) or "").strip()))(wt)
        if done and (pr_state(ref) or {}).get("headRefOid") != tip:
            return False, f"{why}, 그 뒤 worktree HEAD 가 바뀜"
    return done, why


def stack_records(path):
    """Who used each stack, as the ledger says: {stack: [links, ...]}, from every line, retired or not. None when it
    cannot be read, a line included: ledger_read skips a damaged line, and the one skipped may be the stack's current
    owner, leaving an older one looking like it."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, ValueError):
        return None
    stacks = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if e.get("kind") not in STACK_LINES:
                continue
            links = {k: e["links"].get(k) for k in ("pr", "worktree")}
            if not isinstance(e["id"], str) or not all(isinstance(v, (str, type(None))) for v in links.values()):
                return None
            stacks.setdefault(e["id"], []).append(links)
        except (ValueError, AttributeError, KeyError, TypeError):
            return None
    return stacks


def owner_facts():
    """What owner_hold reads, fresh; None when any of it cannot be read. Without orca there is no card list, and a card
    Orca knows may sit on a volume not mounted right now, so that counts as unreadable."""
    cards = orca_paths() if shutil.which("orca") else None
    stacks, procs = stack_records(ledger_path()), cwds()
    if cards is None or stacks is None or procs is None:
        return None
    plain = os.path.isdir("/proc/self")  # cwds() read /proc, not lsof
    # realpath, not real(): a recorded folder may be gone, and a symlink above it must still resolve the same way.
    return {"cards": {os.path.realpath(p) for p in cards}, "stacks": stacks, "pr": pr_reader(),
            "cwds": {os.path.realpath(c if plain else unescaped(c)) for c in procs.values() if c}}


def unescaped(path):
    """A path as lsof prints it, each non-ASCII byte as \\xNN, in the characters the ledger has. Raises ValueError on
    bytes that are not text: a path that cannot be read may be a session's."""
    return re.sub(rb"\\x([0-9a-fA-F]{2})", lambda m: bytes([int(m[1], 16)]), path.encode()).decode()


def owner_hold(owners, facts):
    """Why a volume of these stacks stays, or None once the card of every one has closed. Every volume rule goes through
    this. A stack's containers name its card only while they exist; its volumes outlive them, so the ledger is the
    record. A worktree still there holds whatever its PR says: closing a card weighs its PR, sessions and terminals
    (cards.py, reap-hard), and its worktree going is the answer. What cannot be proved closed stays."""
    if facts is None:
        return "카드 목록이나 ledger 를 읽지 못함"
    if not owners:
        return "소유 스택을 모름(보고만)"
    for o in owners:
        records = facts["stacks"].get(o) or ()
        worktrees = {w for r in records if (w := r.get("worktree"))}
        if not records:
            return f"스택 {o} 의 소유 카드 기록이 없음(보고만)"
        if not worktrees:
            return f"스택 {o} 에 worktree 연결이 없음(보고만)"
        for w in sorted(worktrees):
            path = os.path.realpath(w)
            if any(under(path, c) for c in facts["cards"]) or not dir_gone(w):
                return f"스택 {o} 의 카드 {w} 가 남아 있음"
            if any(under(c, path) for c in facts["cwds"]):
                return f"스택 {o} 의 카드 {w} 안에서 프로세스가 돎"
        for ref in sorted({p for r in records if (p := r.get("pr"))}):
            s = facts["pr"](ref)
            if s and s.get("state") == "OPEN":
                return f"스택 {o} 의 열린 PR {ref}"
            if not s or s.get("state") not in ("MERGED", "CLOSED"):
                return f"스택 {o} 의 PR {ref} 상태를 읽지 못함"
    return None


def owner_holds(items, notes):
    """owner_hold over a scan's volume items, so a held volume is reported, not listed as a target."""
    if targets := [i for i in items if i["target"]]:
        try:
            facts = owner_facts()
        except Exception:  # one bad read holds the volumes; it must not end the round for every other kind
            facts = None
        if facts is None:
            notes.append("볼륨: 카드 목록이나 ledger 를 읽지 못함, 모두 남김")
        for i in targets:
            try:
                why = owner_hold(i.get("owners"), facts)
            except Exception as e:
                why = f"소유 카드를 판정하지 못함({type(e).__name__})"
            if why:
                i.update(target=False, why=why)
    return items


def note_stack_dirs(stacks):
    """A line for each card or other working directory a stack's containers name and the ledger does not. Only the first
    card that brings a stack up registers it, so a card that reuses the name is recorded here. The card, not a folder
    inside it: a session sits at the card's root. Without the card list nothing is written: a folder recorded as if it
    were a card would stay in the ledger as one."""
    path = ledger_path()
    ledger = stack_records(path)
    listed = orca_paths() if shutil.which("orca") else None
    if ledger is None or listed is None:
        return
    cards = {os.path.realpath(p) for p in listed}
    for name, st in sorted(stacks.items()):
        known = {os.path.realpath(r["worktree"]) for r in ledger.get(name, ()) if r["worktree"]}
        found = {os.path.realpath(d) for d in st["dirs"] if os.path.isabs(d)}
        for d in sorted({max((c for c in cards if under(p, c)), key=len, default=p) for p in found} - known):
            ledger_add(path, "stack-seen", name, worktree=d, by="reap-hard")


def judge_compose(entries, stacks, owner, hours, now, preview=(4500, 4599)):
    """Registered stacks only. Target: its owner is done, and it is all stopped or nothing restarted it for `hours`;
    a stack on a preview port also needs a `previewed` line. A keep line holds it until its deadline."""
    out, lo, hi = [], *preview
    for e in folded(entries, "compose-stack"):
        name, s = e["id"], stacks.get(e["id"])
        if not s:
            continue  # already down
        item = {"kind": "compose-stack", "key": f"compose-stack:{name}", "name": name, "target": False,
                "age": now - s["last"] if s["last"] > 0 else None, "start": str(s["last"]),
                "how": [f"docker compose -p {shlex.quote(name)} down"]}
        out.append(item)
        until = None
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            until = parse_ts(e.get("until"))
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            # Containers created after the registration are a new stack under a reused name.
            if s["first"] is not None and s["first"] > parse_ts(e["registered"]) + 60:
                item["why"] = "등록 뒤 같은 이름으로 다시 만들어진 스택(보고만)"
                continue
        if e.get("keep") and (until is None or now < until):
            item["why"] = f"남김: {e['keep']}" + (f" ({e['until']}까지)" if until else " (기한을 읽지 못함)")
            continue
        done, why = owner(e.get("links") or {})
        if not done:
            item["why"] = why if done is False else "소유 카드·PR 링크 없음(보고만)"
            continue
        ports = sorted(p for p in s["ports"] if lo <= p <= hi)
        if ports and not e.get("previewed"):
            item["why"] = f"{why}, 미리보기 포트 {ports[0]}: 미리보기 확인 기록 전"
            continue
        if s["running"] and now - s["last"] < hours * 3600:
            item["why"] = f"{why}, 실행 중(마지막 시작·정지 {hours:g}시간 미만)"
            continue
        item.update(target=True, why=f"{why}, " + ("컨테이너 모두 멈춤" if not s["running"] else
                                                   f"{hours:g}시간 넘게 시작·정지 없음"))
    return out


def stack_volume_spec(cfg):
    """reap.stack_volumes → (prefixes, label, protect), or a string saying why the kind is off."""
    spec = cfg.get("stack_volumes")
    if not spec:
        return "reap.stack_volumes 가 비어 있어 건너뜀"
    if not isinstance(spec, dict):
        return "reap.stack_volumes 는 객체여야 함, 건너뜀"
    prefixes, label, protect = spec.get("prefixes"), spec.get("label"), spec.get("protect", [])
    # A short prefix would match stacks nobody declared throwaway.
    if not isinstance(prefixes, list) or not prefixes or \
            not all(isinstance(p, str) and len(p) >= 4 and p.endswith("-") for p in prefixes):
        return "reap.stack_volumes.prefixes 는 '-' 로 끝나는 4자 이상 문자열 목록이어야 함, 건너뜀"
    if label is not None and (not isinstance(label, str) or not re.fullmatch(r"[\w.-]+", label)
                              or label.startswith("com.docker.")):
        return "reap.stack_volumes.label 이 올바르지 않음, 건너뜀"
    if not isinstance(protect, list) or not all(isinstance(p, str) for p in protect):
        return "reap.stack_volumes.protect 는 문자열 목록이어야 함, 건너뜀"
    return tuple(prefixes), label, frozenset(protect)


def volume_owners(labels, spec):
    labels = labels or {}
    keys = ("com.docker.compose.project", spec[1]) if spec[1] else ("com.docker.compose.project",)
    return list(dict.fromkeys(labels[k] for k in keys if labels.get(k)))


def owner_label(cfg):
    """reap.stack_volumes.label, whatever else in that setting is wrong: an owner it names still counts as one."""
    spec = cfg.get("stack_volumes")
    label = spec.get("label") if isinstance(spec, dict) else None
    return label if isinstance(label, str) else None


def stack_volume_recheck(item, spec):
    """Read again right before the rm: the same volume (creation time), the same owners, and still no container of any
    owner. Returns why it stays, or None."""
    out = run(["docker", "volume", "inspect", item["name"]], check=False)
    if out is None:
        return "볼륨을 다시 읽지 못함"
    (v,) = json.loads(out)
    found = volume_owners(v.get("Labels"), spec)
    if parse_ts(v["CreatedAt"]) != item["created"] or found != item["owners"]:
        return "다시 만들어졌거나 소유자가 바뀐 볼륨"
    for o in found:
        for key in ["com.docker.compose.project"] + ([spec[1]] if spec[1] else []):
            ids = run(["docker", "ps", "-aq", "--filter", f"label={key}={o}"], check=False)
            if ids is None:
                return "컨테이너를 다시 읽지 못함"
            if ids.strip():
                return f"컨테이너가 생긴 스택 {o}"
    return None


def judge_stack_volumes(vols, owners, spec, hours, now):
    """vols: [{name, labels, created}]; owners: every compose project and `label` value any container carries, in any
    state. Only volumes owned (compose project or `label`) by a stack under a configured prefix are listed at all."""
    prefixes, _, protect = spec
    out = []
    for v in vols:
        found = volume_owners(v["labels"], spec)
        if not any(o.startswith(prefixes) and o in v["name"] for o in found):
            continue
        owner = " / ".join(found)
        age = now - v["created"]
        item = {"kind": "stack-volume", "key": f"stack-volume:{v['name']}", "name": v["name"], "age": age,
                "created": v["created"], "owners": found, "target": False,
                "how": [f"docker volume rm {shlex.quote(v['name'])}"]}
        if not all(o.startswith(prefixes) for o in found):
            item["why"] = f"소유자 라벨이 엇갈림 {owner}(보고만)"
        elif protect & set(found):
            item["why"] = f"보호 목록의 스택 {owner}"
        elif owners & set(found):
            item["why"] = f"컨테이너가 남은 스택 {owner}"
        elif age < hours * 3600:
            item["why"] = f"{hours:g}시간 미만"
        else:
            item.update(target=True, why=f"컨테이너 없는 스택 {owner}")
        out.append(item)
    return out


def stack_volume_scan(spec, hours, notes, now=None):
    if not shutil.which("docker"):
        return []
    _, label, _ = spec
    fmt = '{{.Label "com.docker.compose.project"}}' + (f'\t{{{{.Label "{label}"}}}}' if label else "")
    ps = run(["docker", "ps", "-a", "--no-trunc", "--format", fmt], check=False)
    names = set()
    for f in ["label=com.docker.compose.project"] + ([f"label={label}"] if label else []):
        out = None if ps is None else run(["docker", "volume", "ls", "-q", "--filter", f], check=False)
        if out is None:
            notes.append("stack-volume: docker 를 읽지 못함, 건너뜀")
            return []
        names |= set(out.split())
    vols = []
    if names:
        found = run(["docker", "volume", "inspect", *sorted(names)], check=False)
        if found is None:
            notes.append("stack-volume: docker volume inspect 실패, 건너뜀")
            return []
        for v in json.loads(found):
            vols.append({"name": v["Name"], "labels": v.get("Labels") or {}, "created": parse_ts(v["CreatedAt"])})
    owners = {o for line in ps.splitlines() for o in line.split("\t") if o}
    return owner_holds(judge_stack_volumes(vols, owners, spec, hours, now or time.time()), notes)


def stopped_container_spec(cfg):
    """reap.stopped_containers → (prefixes, protect); None when unset (the kind is off); a string when malformed."""
    spec = cfg.get("stopped_containers")
    if not spec:
        return None
    if not isinstance(spec, dict):
        return "reap.stopped_containers 는 객체여야 함, 건너뜀"
    prefixes, protect = spec.get("prefixes"), spec.get("protect", [])
    if not isinstance(prefixes, list) or not prefixes or \
            not all(isinstance(p, str) and len(p) >= 4 and p.endswith("-") for p in prefixes):
        return "reap.stopped_containers.prefixes 는 '-' 로 끝나는 4자 이상 문자열 목록이어야 함, 건너뜀"
    if not isinstance(protect, list) or not all(isinstance(p, str) for p in protect):
        return "reap.stopped_containers.protect 는 문자열 목록이어야 함, 건너뜀"
    return tuple(prefixes), frozenset(protect)


def origin_key(cfg):
    """`<reap.labeled_volumes.namespace>.origin`, the provenance label key; None when the namespace is unset or invalid."""
    spec = cfg.get("labeled_volumes")
    ns = spec.get("namespace") if isinstance(spec, dict) else None
    if not isinstance(ns, str) or not re.fullmatch(r"[a-z0-9]+([.-][a-z0-9]+)*", ns) or \
            f"{ns}.".startswith(ORIGIN_RESERVED):
        return None
    return f"{ns}.origin"


def labeled_volume_spec(cfg):
    """reap.labeled_volumes → (label key, origins, protect); None when unset (the kind is off); a string when malformed."""
    spec = cfg.get("labeled_volumes")
    if not spec:
        return None
    key = origin_key(cfg)
    if not key:
        return "reap.labeled_volumes.namespace 가 올바르지 않음(소문자·숫자·점·하이픈, Docker 예약 이름 제외), 건너뜀"
    origins, protect = spec.get("origins"), spec.get("protect", [])
    if not isinstance(origins, list) or not origins or not all(isinstance(o, str) and o for o in origins):
        return "reap.labeled_volumes.origins 는 문자열 목록이어야 함, 건너뜀"
    if not isinstance(protect, list) or not all(isinstance(p, str) for p in protect):
        return "reap.labeled_volumes.protect 는 문자열 목록이어야 함, 건너뜀"
    return key, frozenset(origins), frozenset(protect)


def container_row(c):
    """One `docker inspect` object in the fields the leftover kinds judge."""
    labels = (c.get("Config") or {}).get("Labels") or {}
    st = c.get("State") or {}
    bound = ((c.get("HostConfig") or {}).get("PortBindings") or {}).values()
    return {"id": c.get("Id") or "", "name": (c.get("Name") or "").lstrip("/"), "status": st.get("Status"),
            "labels": labels, "project": labels.get(COMPOSE_PROJECT) or "", "created": c.get("Created"),
            "finished": st.get("FinishedAt"),
            "ports": sorted({int(b["HostPort"]) for binds in bound for b in binds or ()
                             if str(b.get("HostPort", "")).isdigit()}),
            "volumes": [m["Name"] for m in c.get("Mounts") or () if m.get("Type") == "volume" and m.get("Name")]}


def container_rows():
    """container_row of every container, None unless each came back whole: Mounts, Labels or PortBindings missing from
    what Docker answered would read as no reference, no stack and no protected port."""
    found = docker_containers()
    if found is None:
        return None
    try:
        if all(isinstance(c.get(k), t) for c in found for k, t in CONTAINER_SHAPE.items()) and \
                all("Labels" in c["Config"] and "PortBindings" in c["HostConfig"] and all("Type" in m for m in c["Mounts"])
                    for c in found):
            return [container_row(c) for c in found]
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def stopped_at(r):
    """When a stopped container stopped (a `created` one never ran: when it was made). None when Docker's time cannot
    be read, its zero time included."""
    try:
        t = docker_ts(r["created"] if r["status"] == "created" else r["finished"])
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None
    return t if t > 0 else None


def judge_stopped_containers(rows, spec, hours, now, keep=frozenset(), ports=frozenset()):
    """rows: container_row of every container. Only names under a configured prefix are listed. keep: compose projects
    and names that stay (the protect file, always-on services); ports: protected host ports."""
    prefixes, protect = spec
    live = {r["project"] for r in rows if r["project"] and r["status"] not in STOPPED_STATES}
    held = protect | keep
    out = []
    for r in rows:
        if not r["name"].startswith(prefixes):
            continue
        since = stopped_at(r) if r["status"] in STOPPED_STATES else None
        item = {"kind": "stopped-container", "key": f"stopped-container:{r['id']}", "name": r["name"], "cid": r["id"],
                "project": r["project"], "ports": r["ports"], "created": r["created"], "start": str(r["finished"]),
                "age": now - since if since else None, "target": False, "how": [f"docker rm {r['id']}"]}
        out.append(item)
        if "com.docker.compose.oneoff" in r["labels"]:
            item["why"] = "compose 가 만든 컨테이너(스택 정리 몫)"
        elif r["status"] not in STOPPED_STATES:
            item["why"] = f"멈추지 않음({r['status']})"
        elif held & {r["name"], r["project"]}:
            item["why"] = "보호 목록·상시 서비스"
        elif hit := sorted(set(r["ports"]) & ports):
            item["why"] = f"보호 포트 {hit[0]}"
        elif r["project"] in live:
            item["why"] = f"스택 {r['project']} 이 아직 돎"
        elif since is None:
            item["why"] = "멈춘 시각을 읽지 못함"
        elif now - since < hours * 3600:
            item["why"] = f"멈춘 지 {hours:g}시간 미만"
        else:
            item.update(target=True, why=f"{r['status']}, 멈춘 지 {hours:g}시간 넘음: {r['name']}")
    return out


def judge_labeled_volumes(vols, rows, spec, hours, now, keep=frozenset(), owner_label=None):
    """vols: [{name, labels, created}]; rows: container_row of every container. The origin label is the proof a
    verification stack made the volume, so a volume without it is never listed: that no container uses a volume, or that
    one mounted it once, says nothing about who made it. owner_label: reap.stack_volumes.label, the other owner label."""
    key, origins, protect = spec
    used = {n for r in rows for n in r["volumes"]}
    stacks = {r["project"] for r in rows if r["project"]}
    held = protect | keep
    out = []
    for v in vols:
        origin = v["labels"].get(key)
        if origin is None:
            continue
        project = v["labels"].get(COMPOSE_PROJECT) or ""
        age = now - v["created"]
        item = {"kind": "labeled-volume", "key": f"labeled-volume:{v['name']}", "name": v["name"], "origin": origin,
                "project": project, "owners": volume_owners(v["labels"], (None, owner_label)), "created": v["created"],
                "age": age, "target": False, "how": [f"docker volume rm {shlex.quote(v['name'])}"]}
        out.append(item)
        if origin not in origins:
            item["why"] = f"목록에 없는 출처 {origin}(보고만)"
        elif v["name"] in used:
            item["why"] = "컨테이너가 참조함(멈춘 것 포함)"
        elif held & {v["name"], project}:
            item["why"] = "보호 목록·상시 서비스"
        elif project in stacks:
            item["why"] = f"컨테이너가 남은 스택 {project}"
        elif age < hours * 3600:
            item["why"] = f"{hours:g}시간 미만"
        else:
            item.update(target=True, why=f"출처 {origin}, 참조하는 컨테이너 없음")
    return out


def labeled_volumes_read(key):
    """[{name, labels, created}] of volumes carrying the label key, None when docker cannot be read."""
    names = run(["docker", "volume", "ls", "-q", "--filter", f"label={key}"], check=False)
    if names is None:
        return None
    return volumes_read(sorted(names.split())) if names.split() else []


def volumes_read(names):
    """[{name, labels, created}] of exactly these volumes, None unless each came back whole."""
    out = run(["docker", "volume", "inspect", *names], check=False)
    try:
        vols = [{"name": v["Name"], "labels": v.get("Labels") or {}, "created": parse_ts(v["CreatedAt"])}
                for v in json.loads(out)]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return vols if {v["name"] for v in vols} == set(names) else None


def leftovers(cfg, hours, now, notes, keep=frozenset(), ports=frozenset(), kinds=("stopped-container", "labeled-volume")):
    """The stopped-container and labeled-volume items, for reap.py's and reap-hard's scans alike. Without docker, or with
    what it reads unreadable, a kind lists nothing: a reference it could not read may be the one that keeps a volume."""
    want = {"stopped-container": stopped_container_spec(cfg) if "stopped-container" in kinds else None,
            "labeled-volume": labeled_volume_spec(cfg) if "labeled-volume" in kinds else None}
    notes += [f"{k}: {s}" for k, s in want.items() if isinstance(s, str)]
    if not any(isinstance(s, tuple) for s in want.values()) or not shutil.which("docker"):
        return []
    rows = container_rows()
    if rows is None:
        notes.append("docker 를 읽지 못함: 멈춘 컨테이너·라벨 볼륨은 건너뜀")
        return []
    items = []
    if isinstance(want["stopped-container"], tuple):
        items += judge_stopped_containers(rows, want["stopped-container"], hours, now, keep, ports)
    if isinstance(spec := want["labeled-volume"], tuple):
        vols = labeled_volumes_read(spec[0])
        if vols is None:
            notes.append("docker 볼륨을 읽지 못함: 라벨 볼륨은 건너뜀")
        else:
            items += owner_holds(judge_labeled_volumes(vols, rows, spec, hours, now, keep, owner_label(cfg)), notes)
    return items


def leftover_recheck(item, cfg, protect_path=None):
    """Judged again from a fresh read, right before the rm, with no age left to wait: why it stays, or None. A container
    run again, a volume a container took up since the scan, or a protect line added since keeps it. protect_path: the
    file a cleaner requires; without it, the shared file, which may be missing."""
    import guard
    try:
        protect = guard.read_protect(protect_path or guard.protect_file(), missing_ok=protect_path is None)
        keep = protect["project"] | guard.always_on()
    except (guard.NoProtect, ValueError) as e:
        return f"보호 목록을 다시 읽지 못함({e})"
    rows = container_rows()
    if rows is None:
        return "컨테이너를 다시 읽지 못함"
    if "cid" in item:
        spec = stopped_container_spec(cfg)
        if not isinstance(spec, tuple):
            return "reap.stopped_containers 를 읽지 못함"
        judged = judge_stopped_containers(rows, spec, 0, time.time(), keep, protect["port"])
        same = next((i for i in judged if i["cid"] == item["cid"]), None)
        if same is None or same["name"] != item["name"] or same["start"] != item["start"]:
            return "사라졌거나 다시 시작된 컨테이너"
        return None if same["target"] else same["why"]
    spec = labeled_volume_spec(cfg)
    if not isinstance(spec, tuple):
        return "reap.labeled_volumes 를 읽지 못함"
    vols = volumes_read([item["name"]])
    if vols is None:
        return "볼륨을 다시 읽지 못함"
    (same,) = judge_labeled_volumes(vols, rows, spec, 0, time.time(), keep) or (None,)
    if same is None or same["created"] != item["created"] or same["origin"] != item["origin"]:
        return "다시 만들어졌거나 출처가 바뀐 볼륨"
    return None if same["target"] else same["why"]


def remove_leftover(item, cfg, protect_path=None):
    """One container or volume, by its exact id or name, never -f or -v: Docker itself refuses a running container and a
    volume any container references, so what starts between the recheck and the rm still stays."""
    if why := leftover_recheck(item, cfg, protect_path):
        return f"건너뜀: {why}"
    if "cid" in item:
        run(["docker", "rm", item["cid"]])
        return f"docker rm {item['cid']} ({item['name']})"
    run(["docker", "volume", "rm", item["name"]])
    return f"docker volume rm {item['name']}"


def unlabeled_volumes():
    """Read only: anonymous volumes with no label of their own, for the owner to look at. {total, referenced, volumes:
    the unreferenced ones}, None when docker cannot be read. Never a target: nothing proves who made them."""
    if not shutil.which("docker"):
        return None
    names, rows = run(["docker", "volume", "ls", "-q"], check=False), container_rows()
    if names is None or rows is None:
        return None
    anon = [n for n in names.split() if ANON_VOLUME.fullmatch(n)]
    vols = volumes_read(anon) if anon else []
    if vols is None:
        return None
    bare = [v for v in vols if not set(v["labels"]) - {"com.docker.volume.anonymous"}]
    used = {n for r in rows for n in r["volumes"]}
    free = [{"name": v["name"], "created": dt.datetime.fromtimestamp(v["created"]).isoformat(timespec="seconds")}
            for v in bare if v["name"] not in used]
    return {"total": len(bare), "referenced": len(bare) - len(free), "volumes": free}


def janitor_scan(kinds, repo_list, entries, notes, pr_state=None, hours=6.0, now=None, evidence_roots=None,
                 preview=(4500, 4599), cfg=None):
    pr_state = pr_state or pr_reader()
    items = []
    if "card" in kinds:
        import cards
        found, card_notes = cards.scan(config(), repo_list)
        notes += card_notes
        # path and dirty are read back from the saved plan by orchestrate's rowstate.card_dirty.
        items += [{"kind": "card", "key": f"card:{c['path']}", "name": c["path"], "path": c["path"], "dirty": c.get("dirty"),
                   "target": c["verdict"] in cards.ACTING,
                   **{k: c[k] for k in ("verdict", "why", "head", "close", "pr", "state")},
                   **({"ino": c["ino"]} if c.get("ino") is not None else {})} for c in found]
    if "compose-stack" in kinds:
        stacks = compose_stacks()
        if stacks is None:
            notes.append("compose-stack: docker 를 읽지 못함, 건너뜀")
        items += judge_compose(entries, stacks or {}, functools.partial(stack_owner, pr_state=pr_state), hours,
                               now or time.time(), preview)
    if "stack-volume" in kinds:
        spec = stack_volume_spec(cfg or {})
        if isinstance(spec, str):
            notes.append(f"stack-volume: {spec}")
        else:
            items += stack_volume_scan(spec, hours, notes, now)
    if want := {"stopped-container", "labeled-volume"} & kinds:
        import guard
        try:
            protect, always = guard.read_protect(guard.protect_file(), missing_ok=True), guard.always_on()
        except (guard.NoProtect, ValueError) as e:
            notes.append(f"보호 목록을 읽지 못함, 멈춘 컨테이너·라벨 볼륨은 건너뜀: {e}")
        else:
            items += leftovers(cfg or {}, hours, now or time.time(), notes, protect["project"] | always, protect["port"], want)
    rows = processes() if {"gui-app", "tmpdir"} & kinds else {}
    cwd_of = cwds() if "tmpdir" in kinds else {}
    if "tmpdir" in kinds:
        items += judge_tmpdirs(entries, pr_state, cwd_of, rows)
    if "gui-app" in kinds:
        items += judge_gui_apps(entries, pr_state, rows)
    if "chrome-window" in kinds:
        items += judge_chrome(entries, pr_state)
    if "evidence" in kinds:
        now = now or time.time()
        items += judge_evidence(entries, pr_state, hours, now)
        # /var/folders as a whole holds every app's caches; only the session's own temp dirs are searched.
        roots = evidence_roots or sorted({real("/tmp"), real(os.environ.get("TMPDIR", "/tmp"))})
        registered = {real(e["id"]) for e in entries if e.get("kind") in ("evidence", "tmpdir")}
        items += stray_evidence([real(os.path.expanduser(r)) for r in roots], registered, hours, now)
    if "remote-branch" in kinds:
        items += judge_remote_branches(entries)
    if "orca-worker" in kinds:
        reclaim = orca_workers("reclaimable")
        if reclaim is None:
            notes.append("orca-worker: worker-list 를 읽지 못함, 건너뜀")
        items += judge_orca_worker(reclaim or [])
    return items


USER_FOLDERS = ()  # Explicit reap.user_folders configuration opts into personal-folder cleanup.
PARTIAL = (".download", ".crdownload", ".part")
# 23.5 keeps a daily sweep from slipping a round each day.
USER_FOLDER_EVERY = 23.5 * 3600


WALK_CAP = 20000


class TooBig(Exception):
    pass


def last_touch(path, newer, cap=WALK_CAP):
    """Newest mtime or ctime of path and everything under it; stops at the first one past `newer`.
    ctime counts because an unpacked archive keeps old mtimes but arrives with a fresh ctime.
    Raises OSError for anything it cannot read and TooBig past `cap` entries: an unseen file may be a recent one."""
    st = os.lstat(path)
    t = max(st.st_mtime, st.st_ctime)
    if t >= newer or not stat.S_ISDIR(st.st_mode):
        return t
    seen = 0

    def fail(e):
        raise e
    for root, dirs, files in os.walk(path, onerror=fail):
        for n in dirs + files:
            seen += 1
            if seen > cap:
                raise TooBig
            s = os.lstat(os.path.join(root, n))
            t = max(t, s.st_mtime, s.st_ctime)
            if t >= newer:
                return t
    return t


def open_paths():
    """Every path this user's processes hold open (cwd included), or None when lsof cannot list them."""
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    out = run([lsof, "-n", "-P", "-u", str(os.getuid()), "-Fn"], check=False)
    return None if out is None else {line[1:] for line in out.splitlines() if line[:1] == "n"}


# macOS names screenshots and screen recordings this way (English and Korean system language).
SHOT_PREFIXES = ("screenshot ", "screen recording ", "스크린샷 ", "화면 기록 ")
SHOT_EXT = MEDIA + (".heic", ".tiff")


def is_screenshot(name):
    # macOS writes Korean file names decomposed (NFD); the prefixes here are composed.
    n = unicodedata.normalize("NFC", name).lower()
    return n.startswith(SHOT_PREFIXES) and n.endswith(SHOT_EXT)


def judge_user_folder(folder, days, now, opened, touched=last_touch, shot_days=None):
    """Top-level entries of one folder; a target is untouched for `days` (`shot_days` for a screenshot or screen
    recording file), not open, and not a download in progress."""
    out, general = [], days
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if name.startswith("."):
            continue  # .DS_Store, .localized: Finder's own
        days = shot_days if shot_days and is_screenshot(name) and not os.path.isdir(path) else general
        base = {"kind": "user-folder", "key": f"user-folder:{path}", "name": path, "how": [f"trash {shlex.quote(path)}"]}
        try:
            ino = os.lstat(path).st_ino
        except OSError:
            continue  # gone since listdir
        try:
            t = touched(path, now - days * 86400)
            tip = f"{ino}:{t:.0f}"
        except TooBig:
            out.append({**base, "why": f"항목이 {WALK_CAP}개를 넘어 다 보지 않음(남김)", "target": False})
            continue
        except OSError:
            out.append({**base, "why": "안을 다 읽지 못함(남김)", "target": False})
            continue
        if name.lower().endswith(PARTIAL):
            why = "다운로드 중"
        elif now - t < days * 86400:
            why = f"{days:g}일 안에 바뀜"
        elif opened is None:
            why = "열린 파일 목록을 읽지 못함"
        elif any(under(o, path) or under(o, real(path)) for o in opened):
            why = "열려 있음"
        else:
            out.append({**base, "why": f"{days:g}일 넘게 안 바뀜", "target": True, "age": now - t, "tip": tip})
            continue
        out.append({**base, "why": why, "target": False, "age": now - t})
    return out


def user_folder_days(cfg, notes, key="user_folder_days", default=7, off="꺼짐"):
    """A positive day count from reap.<key>; anything else is None with a note saying what happens instead."""
    v = cfg.get(key, default)
    try:
        days = float(v)
    except (TypeError, ValueError):
        notes.append(f"user-folder: reap.{key} {v!r} 가 숫자가 아님, {off}")
        return None
    if not days > 0:  # also catches NaN, which would make every entry old
        notes.append(f"user-folder: reap.{key} {v!r} 라 {off}")
        return None
    return days


def user_folder_scan(days, notes, shot_days=None, folders=USER_FOLDERS):
    opened, now, items = open_paths(), time.time(), []
    for f in folders:
        folder = os.path.expanduser(f)
        try:
            items += judge_user_folder(folder, days, now, opened, shot_days=shot_days)
        except OSError as e:
            notes.append(f"user-folder: {f} 을 읽지 못함({e.strerror}; 권한이면 사람이 허용한다)")
    return items


def stamp_user_folder(state_dir, now=None, kind="user-folder"):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{kind}-last.json").write_text(json.dumps({"at": now or time.time()}))


def user_folder_due(state_dir, now=None, kind="user-folder"):
    with contextlib.suppress(OSError, ValueError):
        return (now or time.time()) - json.loads((state_dir / f"{kind}-last.json").read_text())["at"] >= USER_FOLDER_EVERY
    return True


PROGRAMS = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
# orchestrate's slug rule: `_archive`, `_locks` and loose files are not programs.
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
RESUME_NOTE = re.compile(r"resume|handoff", re.I)


def final_check_current(events):
    """orchestrate's prog.final_check_current, which defines a closed program (test_reap checks they agree)."""
    since = max((i for i, e in enumerate(events) if e.get("ev") == "landed" or (e.get("ev") == "admitted" and e.get("ticket"))
                 or (e.get("ev") == "config" and (e.get("note") or "").startswith("predicate="))), default=-1)
    return any(e.get("ev") == "predicate_verified" for e in events[since + 1:])


def program_events(path):
    out = []
    with contextlib.suppress(OSError):
        for line in (path / "ledger.jsonl").read_text(errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                if isinstance(e := json.loads(line) if line.strip() else None, dict):
                    out.append(e)
    return out


def judge_programs(home, days, now, touched=last_touch):
    """A closed program (its final check current) whose record is untouched for `days` moves to <home>/_archive. One with a
    resume or handoff note was parked to be picked up again and stays, closed or not."""
    out = []
    try:
        names = sorted(os.listdir(home))
    except OSError:
        return out
    for name in names:
        path = home / name
        if not SLUG_RE.fullmatch(name) or not (path / "program.json").is_file():
            continue
        base = {"kind": "program", "key": f"program:{name}", "name": str(path), "dir": real(str(path)),
                "how": [f"mv {shlex.quote(str(path))} {shlex.quote(str(home / '_archive'))}/"]}
        try:
            names_in = os.listdir(path)
            # A lead's resume note may sit in briefs/, not only at the top.
            notes = [n for _, _, files in os.walk(path) for n in files if RESUME_NOTE.search(n)]
            # The dashboard and `orch status` rewrite dashboard/ and the top-level .json caches of every program, closed
            # ones too; the program's own record is the rest (ledger, decisions, reflect/ that Close writes last).
            t = max((touched(path / n, now - days * 86400) for n in names_in
                     if n != "dashboard" and not n.endswith(".json")), default=0)
        except (OSError, TooBig):
            out.append({**base, "why": "안을 다 읽지 못함(남김)", "target": False})
            continue
        events = program_events(path)
        if notes:
            why = f"재개 노트 {notes[0]} 있음"
        elif not final_check_current(events):
            why = f"닫히지 않음(마지막 기록 {events[-1].get('ev') if events else '없음'})"
        elif now - t < days * 86400:
            why = f"닫힌 뒤 {days:g}일 안에 바뀜"
        else:
            out.append({**base, "why": f"닫힌 지 {days:g}일 넘게 안 바뀜", "target": True, "age": now - t})
            continue
        out.append({**base, "why": why, "target": False, "age": now - t})
    return out


def archive_program(path):
    path = pathlib.Path(path)
    dest = path.parent / "_archive"
    dest.mkdir(exist_ok=True)
    target = dest / path.name
    if os.path.lexists(target):
        target = dest / f"{path.name}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    os.rename(path, target)
    return f"보관함으로 옮김({target})"


def judge_state_files(state_dir, days, log_kb, now):
    """The reorient hook's per-session files untouched for `days` (a session older than that just asks Orca once more),
    as one item; and sync.log past `log_kb`, which nothing else trims."""
    out = []
    with contextlib.suppress(OSError):
        old = sorted(str(f) for f in (state_dir / "reorient").glob("*.json") if now - f.stat().st_mtime >= days * 86400)
        if old:
            out.append({"kind": "state-file", "key": "state-file:reorient", "name": str(state_dir / "reorient"),
                        "paths": old, "target": True, "why": f"{days:g}일 넘은 세션 파일 {len(old)}개, 휴지통으로",
                        "how": [f"trash <{len(old)}개>"]})
    log = state_dir / "sync.log"
    with contextlib.suppress(OSError):
        if (size := log.stat().st_size) > log_kb * 1024:
            out.append({"kind": "state-file", "key": "state-file:sync.log", "name": str(log), "target": True,
                        "why": f"{human(size)}, {log_kb:g}KB 넘음", "how": [f"mv {log} {log}.1 (이전 .1 은 휴지통)"]})
    return out


def reap_state_file(item):
    if item["key"] == "state-file:sync.log":
        log = pathlib.Path(item["name"])
        prev = log.with_name(log.name + ".1")
        if prev.exists():
            trash(str(prev))
        # launchd and cron open the log afresh on every run, so a renamed one only takes the rest of a run in progress.
        os.rename(log, prev)
        return f"{prev.name} 로 회전"
    # One folder in the trash, not hundreds of one-line files.
    bundle = pathlib.Path(item["name"]).parent / f"reorient-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    bundle.mkdir()
    for p in item["paths"]:
        os.rename(p, bundle / os.path.basename(p))
    return trash(str(bundle))


# A target reap could not handle is tried again once this has passed, even if nothing changed.
RETRY_AFTER = 86400


def precheck(items, state_dir, now=None, repeat=2, notify=None):
    """Exit code for an Orca automation precheck: 0 when there are targets, unless they are exactly what the last reap
    left behind (it failed on them) less than RETRY_AFTER ago, or the same set was already planned `repeat` times with
    no reap since: a run that keeps getting refused would otherwise call the model every round. That set is retried
    once per RETRY_AFTER, and notify(count) is called until it returns true, once per set: precheck output reaches
    nobody."""
    targets = sorted(i["key"] for i in items if i["target"])
    if not targets:
        return 1, targets
    now = now or time.time()
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        last = json.loads((state_dir / "janitor-last.json").read_text())
        if last["targets"] == targets and now - last["t"] < RETRY_AFTER:
            return 1, targets
    path, digest = state_dir / "janitor-plan-log.json", hashlib.sha256("\n".join(targets).encode()).hexdigest()
    log = {}
    with contextlib.suppress(OSError, ValueError):
        log = json.loads(path.read_text())
    if not isinstance(log, dict) or log.get("set") != digest:
        log = {"set": digest, "count": 0, "t": 0}
    state_dir.mkdir(parents=True, exist_ok=True)
    if log["count"] >= repeat and now - log["t"] < RETRY_AFTER:
        if not log.get("notified") and notify and notify(log["count"]):
            log["notified"] = True
            path.write_text(json.dumps(log))
        return 1, targets
    log.update(count=log["count"] + 1, t=now)
    path.write_text(json.dumps(log))
    return 0, targets


def record_reap(state_dir, left, now=None):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "janitor-plan-log.json").unlink(missing_ok=True)
    (state_dir / "janitor-last.json").write_text(json.dumps({"t": now or time.time(), "targets": sorted(left)}))


def round_cmd(script, state_dir, ledger, *sub):
    """A scheduled round's command starts with python3: a session may refuse an `env` or `VAR=` prefix as its own
    command. Flags follow the subcommand and appear only for a state or ledger off the default."""
    flags = [] if state_dir == STATE_HOME else ["--state", str(state_dir)]
    if ledger and pathlib.Path(ledger) != state_dir / "ledger.jsonl":
        flags += ["--ledger", str(ledger)]
    return shlex.join(["python3", str(script), *sub, *flags])


def schedule_prompt(script, state_dir, ledger=None, inbox=None):
    """The job's prompt. Its precheck (`precheck --act`) reaps and always exits 1, so a scheduled run never reaches
    it; only Orca's Run now, which skips the precheck, starts the agent with it."""
    act_line = round_cmd(script, state_dir, ledger, "precheck", "--act")
    return (f"reap-resources janitor 를 손으로 돌린 회차다(예약 회차는 precheck 가 정리를 끝내고 agent 를 띄우지 않는다). "
            f"`{act_line}` 를 한 번 실행하고, 출력의 정리·건너뜀·실패·사람이 볼 것을 한국어로 보고한다. "
            "다른 명령으로 정리하지 않고, rm 으로 지우거나 휴지통을 비우지 않는다. "
            "그 명령이 권한·분류기에 거부되면 우회하지 않고 거부된 명령 한 줄을 보고 첫 줄에 적는다.")


def notify_unrun(inbox, command, send=run):
    """The precheck's own alarm for a plan that keeps not running: a line on stdout, and one Orca message when
    reap.inbox_to is set. True once the message went out."""
    def notify(count):
        line = f"janitor 계획이 {count}번 실행되지 않음(reap 거부 가능성). 직접 실행: {command}"
        print(line)
        if not inbox:
            return False
        # status, not escalation: an escalation needs an active Dispatch, which an automation round has none of.
        if send(["orca", "orchestration", "send", "--to", inbox, "--type", "status",
                 "--subject", "reap-resources janitor: 계획이 실행되지 않음", "--body", line, "--json"], check=False) is None:
            print(f"메모: {inbox} 로 보내지 못함", file=sys.stderr)
            return False
        return True
    return notify


TRIGGER = "*/30 * * * *"
INTERVAL_S = 1800
JOB_RUNNER = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "orca_job.py"
# A busy machine is when finished cards cost the most, so a second job looks at the load between rounds; its precheck
# reads only the load average unless the load is at the limit, and wakes nothing within the cooldown of any round.
# Off the minutes of the periodic job.
LOAD_TRIGGER = "27,57 * * * *"
LOAD_COOLDOWN_MINUTES = 60
PRECHECK_TIMEOUT = 600
JANITOR_NAME = "agent-skills janitor"
LOAD_NAME = "agent-skills janitor load"


def job_cmd(name, command):
    return shlex.join(["python3", "-I", str(JOB_RUNNER), "run", name, "--interval", str(INTERVAL_S),
                       "--timeout", str(PRECHECK_TIMEOUT - 20), "--ok", "0", "--", *shlex.split(command)])


def schedule_cmd(script, state_dir, workspace, provider="claude", ledger=None, inbox=None, load=False):
    return ["orca", "automations", "create", "--name", LOAD_NAME if load else JANITOR_NAME,
            "--trigger", LOAD_TRIGGER if load else TRIGGER,
            "--provider", provider, "--workspace-mode", "existing", "--workspace", workspace,
            "--precheck", job_cmd("janitor-load" if load else "janitor",
                                  round_cmd(script, state_dir, ledger, "precheck", *(["--load"] if load else []), "--act")),
            "--precheck-timeout", str(PRECHECK_TIMEOUT),
            "--prompt", schedule_prompt(script, state_dir, ledger, inbox), "--json",
            *([f"--extra-agent-args=--model {config()['model']}"] if provider == "claude" and config().get("model") else [])]


def load_due(state_dir, cfg, now=None, load5=None, autos=None):
    """(due, why) for the load job: the 5-minute load is at reap.load_limit (default the CPU count), the periodic
    janitor is registered and on (autos: `orca automations list`, read when the load is high; None when unreadable), and
    no janitor round of either job scanned within reap.load_cooldown_minutes."""
    now = now or time.time()
    load5 = os.getloadavg()[1] if load5 is None else load5
    limit = float(cfg.get("load_limit") or os.cpu_count() or 1)
    if load5 < limit:
        return False, f"load5 {load5:.1f} < 한계 {limit:g}"
    if autos is None:
        autos = orca_json("automations", "list")
    if not isinstance(autos, dict):
        return False, "orca automations list 를 읽지 못해 janitor 가 켜져 있는지 모름"
    janitor = next((a for a in autos.get("automations") or [] if a.get("name") == JANITOR_NAME), None)
    if not janitor or not janitor.get("enabled"):
        return False, "janitor 가 꺼져 있거나 없음"
    v = cfg.get("load_cooldown_minutes")
    try:
        cooldown = float(LOAD_COOLDOWN_MINUTES if v is None else v) * 60
    except (TypeError, ValueError):
        cooldown = -1.0
    if not 0 <= cooldown < float("inf"):  # nan fails both comparisons
        # A bad value must not drop the cooldown: that would run a round every 30 minutes under load.
        print(f"reap.load_cooldown_minutes {v!r} 는 0 이상의 유한한 숫자가 아니라 기본 {LOAD_COOLDOWN_MINUTES}분을 쓴다",
              file=sys.stderr)
        cooldown = LOAD_COOLDOWN_MINUTES * 60
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        last = json.loads((state_dir / "janitor-round.json").read_text())["t"]
        # A stamp from the future (a clock set back) does not hold the job off.
        if 0 <= now - last < cooldown:
            return False, f"load5 {load5:.1f} ≥ 한계 {limit:g} 이나 지난 회차가 {age(now - last)} 전"
    return True, f"load5 {load5:.1f} ≥ 한계 {limit:g}"


def stamp_round(state_dir, now=None):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "janitor-round.json").write_text(json.dumps({"t": now or time.time()}))


def runner_hint(checkout):
    return ("\n모델: automation 은 --workspace 체크아웃의 Claude Code 설정을 따른다. reap.model 을 넣으면 schedule 이 --extra-agent-args 로 넘긴다. 넣지 않을 때는 아래 방법을 쓴다. "
            "메인 체크아웃에 model 을 넣으면 거기서 여는 대화형 세션도 바뀌므로, 전용 runner worktree 를 한 번 만든다:\n"
            f"  orca worktree create --repo {shlex.quote('path:' + str(checkout))} --name automations-runner --no-parent --setup skip --json\n"
            '  <runner>/.claude/settings.local.json 에 {"model": "<고른 모델>"} (gitignored)\n'
            "그 뒤 schedule --workspace path:<runner> 로 다시 출력한다. 이미 있는 automation 은 "
            "orca automations edit --id <id> --workspace path:<runner>")


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


TAIL_BYTES = 256 * 1024


class Unreadable(Exception):
    pass


def last_entry_time(path):
    """Time of the newest entry with a timestamp in a transcript's tail. An idle Claude session appends untimed
    metadata lines (`atis-latch`) about every 30 minutes, which move the file's mtime without any work; with no
    timestamp in the tail, the mtime stands, which reads as recent and keeps the card. A /reload-* row
    (sync.reload_row) is not an entry: the skills-sync broadcast types those into finished sessions; nor is the
    `pr-link` metadata row a reload rewrites. None when the file is gone, and when the tail's only timed entries are
    such rows: the time of the work is not there to read,
    and the mtime is the last reload's; the cwd's other transcripts and the terminals decide then. Unreadable when the
    file or its newest timestamp cannot be read, since an older entry would read as quieter."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
        mtime = os.stat(path).st_mtime
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Unreadable(f"세션 기록 {path.name} 을 읽지 못함({type(e).__name__})") from e
    times, skipped = [], False
    for line in lines:
        try:
            row = json.loads(line)
            ts = row.get("timestamp")
        except (ValueError, AttributeError):
            continue  # the tail's cut first line, or a non-object line
        if ts and (reload_row(row) or row.get("type") == "pr-link"):  # pr-link: rewritten when a reload runs
            skipped = True
        elif ts:
            try:
                times.append(parse_ts(ts))
            except (ValueError, TypeError, AttributeError) as e:
                raise Unreadable(f"세션 기록 {path.name} 의 시각 {str(ts)[:40]!r} 을 읽지 못함") from e
    # A line appended later can carry an older time; the last line's would read the session as quieter.
    return max(times, default=None if skipped else mtime)


def session_files(d, subagents=True):
    """A project dir's *.jsonl, and */subagents/*.jsonl with subagents; [] when the dir is absent. Raises OSError for
    a folder it cannot list, which Path.is_dir and Path.glob would pass over as empty."""
    def ls(p):
        try:
            with os.scandir(p) as it:
                return list(it)
        except (FileNotFoundError, NotADirectoryError):
            return []
    top = ls(d)
    files = [e.path for e in top if e.name.endswith(".jsonl")]
    if subagents:
        files += [s.path for e in top if e.is_dir() for s in ls(os.path.join(e.path, "subagents"))
                  if s.name.endswith(".jsonl")]
    return [pathlib.Path(f) for f in files]


def transcript_quiet(projects_dir, now, reasons=False):
    """Seconds since the cwd's sessions last wrote an entry, subagents' included; None when there is no transcript.
    A transcript that cannot be read gives None too (unknown keeps a card), or the reason with reasons=True."""
    def quiet(cwd):
        d = projects_dir / re.sub(r"[^A-Za-z0-9]", "-", cwd)
        try:
            times = [t for f in session_files(d) if (t := last_entry_time(f)) is not None]
        except Unreadable as e:
            return str(e) if reasons else None
        except OSError as e:
            return f"세션 기록 폴더를 읽지 못함({type(e).__name__})" if reasons else None
        # An entry written after the scan began is quiet for no time, not negative time.
        return max(0, now - max(times)) if times else None
    return quiet


def load_report(cfg, top):
    rows, cwd_of = processes(), cwds()
    hours = float(cfg.get("hours") or 6)
    leftovers = {i["pid"] for i in judge_orphans(rows, cfg.get("orphan_paths") or [], hours)
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
    got = {"orphan_procs": sum(i["procs"] for i in items if i["kind"] == "orphan"),
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
            print(f"  - 처리: `{how}`")
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
    # The tree is read again here: a person may have opened a shell under it since the scan.
    if human := human_in_tree(item["pid"], rows):
        return f"건너뜀: 트리에 사람이 띄운 셸·그 자식(pid {human[0]})"
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


def act(item, notes, done=None):
    """done, when given, gets what a card act really closed ("closed": handles)."""
    k, kind = item["key"], item["kind"]
    if kind in ("tmpdir", "evidence", "user-folder"):
        for how in item.get("how") or ():
            if how.startswith(LSREGISTER):
                run([LSREGISTER, "-u", shlex.split(how)[-1]], check=False)
        return trash(item["name"])
    if kind == "program":
        return archive_program(item["name"])
    if kind == "state-file":
        return reap_state_file(item)
    if kind == "gui-app":
        rows = processes()
        r = rows.get(item["gpid"])
        if not r or r["start"] != item["start"]:
            return "사라졌거나 다른 프로세스"
        if human_owned(item["gpid"], rows):
            return "건너뜀: 사람이 띄운 셸의 자식"
        os.kill(item["gpid"], signal.SIGTERM)
        return "SIGTERM 보냄"
    if kind == "chrome-window":
        # Handed to the agent once: retired here so the next run does not ask about the same id again.
        ledger_add(ledger_path(), "chrome-window", item["name"], url=item.get("url"), by="reap", retired=True)
        return item["how"][0] + " (ledger 에서 내림)"
    if kind == "card":
        import cards
        return cards.act({**item, "path": item["name"]}, config(), done=done)
    if kind == "compose-stack":
        stacks = compose_stacks()  # read again: what is really brought down, for `sweep diff`
        if stacks is None:
            return "건너뜀: docker 를 다시 읽지 못함"
        st = stacks.get(item["name"])
        if st is None:
            return "건너뜀: 스택이 이미 없음"
        if item.get("start") not in (None, "") and str(st.get("last")) != item["start"]:
            return "건너뜀: 판정 뒤 스택이 바뀜"
        if done is not None:
            done["identity"] = {"start": str(st.get("last"))}
        # No -v: the stack's volumes are data, and stay.
        run(["docker", "compose", "-p", item["name"], "down"])
        ledger_add(ledger_path(), "compose-stack", item["name"], by="reap", retired=True)
        return "docker compose down (볼륨 유지, ledger 에서 내림)"
    if kind == "stack-volume":
        spec = stack_volume_spec(config())
        why = "reap.stack_volumes 를 읽지 못함" if isinstance(spec, str) else stack_volume_recheck(item, spec)
        if why:
            return f"건너뜀: {why}"
        run(["docker", "volume", "rm", item["name"]])
        return "docker volume rm"
    if kind in ("stopped-container", "labeled-volume"):
        return remove_leftover(item, config())
    if kind == "orca-worker":
        run(["orca", "orchestration", "worker-release", "--dispatch", k.split(":", 1)[1], "--json"])
        return "터미널 회수"
    if kind == "orca-worktree":
        # `orca worktree rm` deletes ignored files (.env.local, notes, caches) for good; they go to the trash first.
        # Tracked files are in git, and anything untracked left makes the rm refuse.
        p = item["name"]
        # The refusal record is read again here and once more right before the rm: a person may record one while the
        # ignored files are being trashed, and a refused removal is not run again.
        if why := rm_refusal(p):
            return f"건너뜀: {why}"
        copies = []
        if item.get("regenerated"):
            # Judged again right here: a file written since the scan is someone's work, and goes nowhere.
            copies = regenerated_copies(p)
            if copies is None:
                raise RuntimeError("재생성 사본 밖의 추적 안 되는 파일이 생김, 남김")
            if copies:
                trash(*(os.path.join(p, c) for c in copies))
        out = run(["git", "-C", p, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"])
        ignored = [os.path.join(p, x.rstrip("/")) for x in out.split("\0") if x]
        if ignored:
            trash(*ignored)
        for h in item.get("terminals") or []:
            run(["orca", "terminal", "close", "--terminal", h, "--json"])
        if why := rm_refusal(p):
            return f"건너뜀: {why}"
        run(["orca", "worktree", "rm", "--worktree", f"path:{p}", "--run-hooks", "--json"])
        return (f"재생성 사본 {len(copies)}개, " if copies else "") + \
            f"ignored 항목 {len(ignored)}개는 휴지통으로, " + ("셸 터미널을 닫고 " if item.get("shells") else "") + \
            "worktree 는 orca worktree rm 으로 제거"
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


class PlanRefused(Exception):
    pass


IDENTITY_KEYS = ("cmd", "start", "tip", "head", "ino", "dir", "verdict", "created")


def reap_plan(plan, cfg, scheduled_only=False, deadline=None, out=print, res=None):
    """Re-scan the plan's kinds and act on each target still a target with the same identity, inside its lock and
    after guard.keep passes again. {acted, skipped, failed, left, unchecked, refused, notes}: keys per outcome; `left`
    are those the deadline (time.monotonic()) stopped before, never one cut in the middle of its act. `res`, when
    given, is filled as it goes, so a caller still has what was done when something raises."""
    if res is None:
        res = {}
    res.update(acted=[], skipped=[], failed=[], left=[], unchecked=False, refused=None, notes=[])
    wanted = {i["key"]: i for i in plan["items"] if i["target"]}
    # Before the janitor filter, so a plan naming them is refused whatever its file name. A re-scan finds none of
    # them, which would read as "no longer a target" and reap nothing in silence.
    if retired := sorted(i["key"] for i in wanted.values() if i["kind"] in RETIRED_KINDS):
        raise PlanRefused(f"reap: 이 계획에 더는 지원하지 않는 종류가 있어 아무것도 하지 않았다: {', '.join(retired)}. "
                          "scan --kinds card 로 계획을 다시 만든다")
    if scheduled_only:
        for key in [k for k, i in wanted.items() if i["kind"] not in SCHEDULED]:
            out(f"- 건너뜀 {wanted.pop(key)['key']}: janitor 계획에 들어갈 수 없는 종류")
            res["skipped"].append(key)
    import guard
    try:
        guard.ownership()
        protect = guard.read_protect(guard.protect_file(), missing_ok=True)
    except (guard.Unreadable, guard.NoProtect) as e:
        out(f"- 아무것도 하지 않았다: {e}")
        res["refused"] = str(e)
        return res
    kinds = {i["kind"] for i in wanted.values()}
    fresh, notes = scan(kinds, plan["hours"], cfg, plan["repos"]) if kinds else ([], [])
    res["notes"] = notes
    now = {i["key"]: i for i in fresh if i["target"]}
    for key, old in wanted.items():
        if deadline is not None and time.monotonic() > deadline:
            res["left"].append(key)
            continue
        new = now.get(key)
        if not new or any(new.get(k) != old.get(k) for k in IDENTITY_KEYS):
            out(f"- 건너뜀 {label(old)}: 더는 정리 대상이 아님")
            res["skipped"].append(key)
            continue
        if new["kind"] == "card" and set(new.get("close") or ()) - set(old.get("close") or ()):
            out(f"- 건너뜀 {label(old)}: 판정 뒤 터미널이 새로 열림")
            res["skipped"].append(key)
            continue
        res["current"] = key
        with guard.hold(guard.targets(new)) as got:
            why = guard.keep(new, protect, notes=notes) if got else guard.LOCK_BUSY
            if why:
                out(f"- 건너뜀 {label(new)}: {why}")
                if guard.check_error(why):
                    res["failed"].append(key)
                    res["unchecked"] = True
                else:
                    res["skipped"].append(key)
                continue
            done = {}
            try:
                got = act(new, notes, done)
            except BaseException as e:
                res["failed"].append(key)
                log_action({**new, **(done.get("identity") or {})}, e, False, guard.rule(new), done.get("closed"))
                if not isinstance(e, (RuntimeError, OSError)):
                    raise
                out(f"- 실패 {label(new)}: {e}")
            else:
                res["acted" if acted(got) else "skipped"].append(key)
                log_action({**new, **(done.get("identity") or {})}, got, True, guard.rule(new), done.get("closed"))
                out(f"- {label(new)}: {got}")
        res["current"] = None
    for k in set(DAILY) & set(plan.get("kinds") or ()) - {wanted[key]["kind"] for key in res["left"]}:
        try:
            stamp_user_folder(STATE, kind=k)
        except OSError as e:
            notes.append(f"{k}: 하루 기록을 쓰지 못함({e.strerror}), 다음 회차가 다시 본다")
    for n in notes:
        out(f"- 메모: {n}")
    return res


# Orca runs the precheck itself, outside any Claude session, so the reap there meets no permission prompt or
# classifier; it then exits 1 and Orca starts no agent.
ACT_BUDGET_S = 480  # no new target after this, counted from the round's start
HARD_STOP_S = 570  # an act still running here is cut, so the round is recorded before the runner's kill


class RoundCut(BaseException):  # not an Exception: no `except Exception` on the way may swallow it
    pass


class _Gate:
    """The first SIGALRM or SIGTERM cuts the round: raised while targets run, only marked while the round records, so
    a record is never left half written. Later signals are ignored; the cut already stands."""
    def __init__(self):
        self.held, self.cut, self.raised = False, None, False

    def __call__(self, signum, _frame):
        if self.cut:
            return
        self.cut = f"회차 중단({signal.Signals(signum).name})"
        if not self.held:
            self.raised = True
            raise RoundCut(self.cut)

    @contextlib.contextmanager
    def recording(self):
        held, self.held = self.held, True
        try:
            yield
        finally:
            self.held = held

    def check(self):
        if self.cut and not self.raised:
            self.raised = True
            raise RoundCut(self.cut)


@contextlib.contextmanager
def hard_stop(started):
    """A _Gate on SIGTERM and on a timer at started + HARD_STOP_S, so the round is recorded before Orca's kill.
    subprocess.run kills its child when interrupted this way. Off the main thread no signal is set."""
    import threading
    gate = _Gate()
    if threading.current_thread() is not threading.main_thread():
        yield gate
        return
    old = {s: signal.signal(s, gate) for s in (signal.SIGALRM, signal.SIGTERM)}
    t0 = time.monotonic()
    old_timer = signal.setitimer(signal.ITIMER_REAL, max(0.01, started + HARD_STOP_S - t0))
    try:
        yield gate
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for s, h in old.items():
            signal.signal(s, h)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, max(0.01, old_timer[0] - (time.monotonic() - t0)), old_timer[1])
RUNS_KEEP = 500
HUMAN_KINDS = ("chrome-window",)


@contextlib.contextmanager
def janitor_lock(state_dir):
    import fcntl
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "janitor.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True


def write_atomic(path, text):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def read_obj(path):
    with contextlib.suppress(OSError, ValueError):
        d = json.loads(path.read_text())
        if isinstance(d, dict):
            return d
    return {}


def resting_keys(fails, now):
    out = set()
    for k, f in fails.items():
        try:
            if float((f or {}).get("until") or 0) > now:
                out.add(k)
        except (TypeError, ValueError, AttributeError):
            out.add(k)
    return out


def update_fails(fails, res, now):
    for k in res["failed"]:
        f = fails.get(k) if isinstance(fails.get(k), dict) else {}
        fails[k] = {"n": int(f.get("n") or 0) + 1, "until": now + RETRY_AFTER}
    for k in res["acted"] + res["skipped"]:
        fails.pop(k, None)
    return fails


def human_items(items, resting, fails):
    out = []
    for i in items:
        if i["kind"] == "card" and i.get("verdict") == "사람 결정":
            out.append({"key": i["key"], "why": i["why"]})
        elif i["target"] and i["kind"] in HUMAN_KINDS:
            out.append({"key": i["key"], "why": f"URL {i.get('url') or '?'} 인 창·탭을 확인해 닫는다"})
        elif i["target"] and i["key"] in resting:
            out.append({"key": i["key"], "why": f"정리가 {fails[i['key']].get('n', 1)}번 실패해 하루 쉼: {i['why']}"})
    return out


def notify_human(state_dir, inbox, rows, send=run):
    path = state_dir / "janitor-human.json"
    keys = sorted(r["key"] for r in rows)
    last = set(read_obj(path).get("keys") or ())
    body = "\n".join(f"- {r['key']}: {r['why']}" for r in rows)
    if not set(keys) - last:
        if set(keys) != last:
            write_atomic(path, json.dumps({"keys": keys, "t": time.time()}))
        return False
    if not inbox:
        print(f"사람이 볼 것 {len(rows)}건(reap.inbox_to 없음, 보내지 않음):\n{body}")
        return False
    got = send(["orca", "orchestration", "send", "--to", inbox, "--type", "status",
                "--subject", f"reap-resources janitor: 사람이 볼 것 {len(rows)}건", "--body", body, "--json"], check=False)
    if got is None:
        print(f"메모: {inbox} 로 보내지 못함", file=sys.stderr)
        return False
    write_atomic(path, json.dumps({"keys": keys, "t": time.time()}))
    return True


def log_run(state_dir, row, keep=RUNS_KEEP):
    path = state_dir / "janitor-runs.jsonl"
    rows = []
    with contextlib.suppress(OSError, ValueError):
        rows = path.read_text().splitlines()[-(keep - 1):]
    write_atomic(path, "".join(r + "\n" for r in rows) + json.dumps(row, ensure_ascii=False) + "\n")


def act_round(data, cfg, state_dir, started, inbox=None, send=run, reap_fn=reap_plan, now=None):
    """Caller holds janitor_lock."""
    now = now or time.time()
    fails_path = state_dir / "janitor-fails.json"
    fails = read_obj(fails_path)
    resting = resting_keys(fails, now)
    items = data["items"]
    run_items = [i for i in items if i["target"] and i["kind"] not in HUMAN_KINDS and i["key"] not in resting]
    # A daily kind with a target still resting keeps its day open: stamped now, its retry would wait a second day.
    held = {i["kind"] for i in items if i["target"] and i["key"] in resting} & set(DAILY)
    plan = {**data, "items": run_items, "kinds": [k for k in data.get("kinds") or () if k not in held]}
    row = {"t": now, "source": "scan", "targets": sum(1 for i in items if i["target"]), "acted": [], "skipped": [],
           "failed": [], "left": [], "resting": sorted(resting & {i["key"] for i in items}), "human": 0, "error": None}
    res = {}
    with hard_stop(started) as gate:
        try:
            try:
                got = reap_fn(plan, cfg, scheduled_only=True, deadline=started + ACT_BUDGET_S, res=res)
                if isinstance(got, dict) and got is not res:
                    res.update(got)
            finally:
                with gate.recording():
                    # A target cut between its act and its outcome is not known to be done: it fails, and rests.
                    if (cur := res.get("current")) and not any(cur in (res.get(k) or ()) for k in ("acted", "skipped", "failed")):
                        res.setdefault("failed", []).append(cur)
                    row.update({k: list(res.get(k) or ()) for k in ("acted", "skipped", "failed", "left")},
                               refused=res.get("refused"))
                    if res and not res.get("refused"):
                        write_atomic(fails_path, json.dumps(update_fails(fails, row, now)))
                        # The old precheck's record too, so a job still on the agent round reads the same state.
                        record_reap(state_dir, row["failed"])
            gate.check()
            fails = read_obj(fails_path)
            rows = human_items(items, resting_keys(fails, now), fails)
            row["human"] = len(rows)
            row["notified"] = notify_human(state_dir, inbox, rows, send)
        except BaseException as e:
            row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            if not isinstance(e, (Exception, RoundCut)):
                raise
        finally:
            with gate.recording():
                row["seconds"] = round(time.monotonic() - started, 1)
                with contextlib.suppress(OSError):
                    log_run(state_dir, row)
            if gate.cut and not row["error"]:
                row["error"] = f"RoundCut: {gate.cut}"
    return row


def main():
    if any(a == "--under" or a.startswith("--under=") for a in sys.argv):
        sys.exit("--under is no longer supported; use an explicit plan and supported scope options")
    if any(a.startswith("--kinds=") for a in sys.argv):
        sys.exit("use --kinds VALUE with supported kinds; --kinds=VALUE is not supported")
    if "--kinds" in sys.argv:
        requested = opt("--kinds") if sys.argv.index("--kinds") + 1 < len(sys.argv) else None
        if retired := [k for k in (requested or "").split(",") if k in RETIRED_KINDS]:
            sys.exit(f"--kinds {','.join(retired)}: removed; the card kind replaces them (--kinds card)")
        if not requested or set(requested.split(",")) - set(KINDS + SCHEDULED + ("janitor",)):
            sys.exit("--kinds requires a comma-separated list of supported kinds: " +
                     ",".join(dict.fromkeys(KINDS + SCHEDULED + ("janitor",))))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    cfg = config()
    if cmd == "reap":
        path = pathlib.Path(opt("--plan") or sys.exit("reap needs --plan PLAN.json"))
        plan = json.loads(path.read_text())
        try:
            res = reap_plan(plan, cfg, scheduled_only=path.name == "janitor-plan.json")
        except PlanRefused as e:
            sys.exit(str(e))
        if res["refused"]:
            sys.exit(1)  # a check that could not run is not a clean round: the caller must see it
        if set(plan.get("kinds") or ()) & set(SCHEDULED):
            record_reap(STATE, res["failed"])
        if res["unchecked"]:
            sys.exit(1)
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
            extra = {"url": opt("--url")}
            if opt("--kind") == "compose-stack":
                if opt("--keep") and not opt("--until"):
                    sys.exit("--keep needs --until <ISO time>: a stack kept with no deadline stays forever")
                if opt("--until"):
                    try:
                        parse_ts(opt("--until"))
                    except ValueError:
                        sys.exit("--until must be an ISO time, e.g. 2026-10-27T18:00+09:00")
                pid = opt("--pid")
                if pid and not pid.isdigit():
                    sys.exit("--pid must be a number")
                extra.update(keep=opt("--keep"), until=opt("--until"), pid=pid and int(pid),
                             previewed=True if "--previewed" in sys.argv else None)
            print(json.dumps(ledger_add(path, opt("--kind"), rid, opt("--pr"), wt, opt("--run"), opt("--by"), **extra),
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
        argv = schedule_cmd(script, STATE, workspace, opt("--provider") or "claude", ledger_path(), cfg.get("inbox_to"),
                            load="--load" in sys.argv)
        print(" ".join(shlex.quote(a) for a in argv))
        if not opt("--workspace") and not cfg.get("model"):
            print(runner_hint(top.strip()))
        if "--write" in sys.argv:
            out = run(argv)
            print(out)
        else:
            print("\n(출력만 했다. 실제로 만들려면 --write)")
        return
    if cmd == "origin-label":
        origin, key = (sys.argv[2:3] or [""])[0], origin_key(cfg)
        if not re.fullmatch(r"[\w.-]+", origin):
            sys.exit("usage: reap.py origin-label verify-<app>")
        if not key:
            sys.exit("reap.labeled_volumes.namespace is unset or invalid: no provenance label")
        print(f"{key}={origin}")
        return
    if cmd == "inventory":
        if "--unlabeled-volumes" not in sys.argv:
            sys.exit(__doc__)
        data = unlabeled_volumes()
        if data is None:
            sys.exit("docker 를 읽지 못함")
        if "--json" in sys.argv:
            print(json.dumps(data, ensure_ascii=False, indent=1))
            return
        print(f"라벨 없는 익명 볼륨 {data['total']}개: 컨테이너 참조 없음 {len(data['volumes'])}개, 참조 있음 {data['referenced']}개. "
              "출처를 모르므로 어떤 정리도 지우지 않는다")
        for v in data["volumes"]:
            print(f"{v['name']}\t{v['created']}")
        return
    if cmd not in ("scan", "precheck"):
        sys.exit(__doc__)
    act = cmd == "precheck" and "--act" in sys.argv
    started, held = time.monotonic(), contextlib.ExitStack()
    # The whole round, load check included: a periodic and a load round never share janitor-fails or the run log.
    # Under --act the exit is the round's own (orca_job records it and exits 1 for Orca): a skip is 0, a failure 2.
    # Without it the precheck's exit still decides whether the agent starts.
    skip = 0 if act else 1
    if act and not held.enter_context(janitor_lock(STATE)):
        print("다른 janitor 회차가 도는 중: 건너뜀")
        sys.exit(skip)
    if cmd == "precheck" and "--load" in sys.argv:
        due, why = load_due(STATE, cfg)
        if not due:
            print(f"부하 회차 아님({why}): 건너뜀")
            sys.exit(skip)
        print(f"부하 회차: {why}")
    if cmd == "precheck":
        import guard
        try:
            guard.ownership()
        except guard.Unreadable as e:
            print(f"소유권 파일을 읽지 못함, 이번 회차는 건너뜀: {e}")
            if act:
                log_run(STATE, {"t": time.time(), "source": "scan", "error": f"소유권 파일을 읽지 못함: {e}",
                                "stage": "plan"})
            sys.exit(2 if act else 1)
        # Stamped before the scan, which takes seconds: a precheck starting meanwhile must already see it.
        stamp_round(STATE)
    names =(opt("--kinds") or ",".join(SCHEDULED if cmd == "precheck" else KINDS)).split(",")
    names = [k for n in names for k in (JANITOR if n == "janitor" else (n,))]
    kinds = set(names) & set(KINDS + SCHEDULED)
    pre_notes = []
    for k in DAILY:
        if cmd == "precheck" and k in kinds and not user_folder_due(STATE, kind=k):
            kinds.discard(k)
            pre_notes.append(f"{k}: 하루 한 번만 정리한다, 이번 회차는 건너뜀")
    hours = float(opt("--hours") or cfg.get("hours") or 6)
    repo_list = repos([a for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--repo"]) \
        if {"branch", "worktree", "card"} & kinds else []
    items, notes = scan(kinds, hours, cfg, repo_list)
    notes = pre_notes + notes
    msgs, totals = alerts(items, {**ALERT, **(cfg.get("alert") or {})})
    if trash_auto_empty():
        msgs.append("휴지통 자동 비움 켜짐(Finder: 30일 지난 항목 제거): 휴지통으로 옮긴 것은 30일 뒤 영구 삭제된다")
    data ={"at": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "hours": hours,
            "kinds": [k for k in KINDS + SCHEDULED if k in kinds], "repos": repo_list, "items": items, "notes": notes, "alerts": msgs, "totals": totals}
    if act:
        write_atomic(STATE / "janitor-plan.json", json.dumps(data, ensure_ascii=False, indent=1))
        for n in notes + [f"{i['key']}: {i['why']}" for i in items if "읽지 못함" in i["why"]]:
            print(f"메모: {n}", file=sys.stderr)
        for m in msgs:
            print(f"경보: {m}", file=sys.stderr)
        row = act_round(data, cfg, STATE, started, cfg.get("inbox_to"))
        print(f"janitor 회차: 대상 {row['targets']}개, 정리 {len(row['acted'])} · 건너뜀 {len(row['skipped'])} · "
              f"실패 {len(row['failed'])} · 예산 초과로 남김 {len(row['left'])} · 쉬는 중 {len(row['resting'])} · "
              f"사람이 볼 것 {row['human']}" + (f" · 오류 {row['error']}" if row["error"] else "") +
              (f" · 거부 {row['refused']}" if row.get("refused") else "") + f" ({row['seconds']}초)")
        sys.exit(2 if row["error"] or row.get("refused") else 0)
    if cmd == "precheck":
        (STATE / "janitor-plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
        command = round_cmd(pathlib.Path(__file__).resolve(), STATE, ledger_path(), "reap", "--plan", str(STATE / "janitor-plan.json"))
        code, targets = precheck(items, STATE, repeat=int(cfg.get("plan_repeat") or 2),
                                 notify=notify_unrun(cfg.get("inbox_to"), command))
        # With targets the stamp waits for reap; with none, the day's sweep is this check.
        for k in set(DAILY) & kinds:
            if not any(i["target"] for i in items if i["kind"] == k):
                stamp_user_folder(STATE, kind=k)
        for n in notes + [f"{i['key']}: {i['why']}" for i in items if "읽지 못함" in i["why"]]:
            print(f"메모: {n}", file=sys.stderr)
        for m in msgs:
            print(f"경보: {m}", file=sys.stderr)
        print(f"정리 대상 {len(targets)}개, " + ("실행" if code == 0 else
              "대상 없음, 지난 reap 이 실패한 대상과 같음, 또는 같은 계획이 실행되지 않고 쌓임: 건너뜀"))
        sys.exit(code)
    if opt("--plan"):
        pathlib.Path(opt("--plan")).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        report(data)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        if sys.argv[1:2] == ["precheck"] and "--act" in sys.argv:
            with contextlib.suppress(OSError):
                log_run(STATE, {"t": time.time(), "source": "scan", "error": f"{type(e).__name__}: {str(e)[:200]}",
                                "stage": "plan"})
        raise
