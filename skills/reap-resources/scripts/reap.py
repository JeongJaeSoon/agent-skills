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
  python3 reap.py precheck [--kinds ...]
  python3 reap.py schedule [--workspace SELECTOR] [--provider claude] [--write]

scan only reads. reap re-scans and acts only on the plan's targets that are still targets with the
same identity (pid and start time, branch tip), so nothing outside the list is touched. On a plan named janitor-plan.json
it acts only on the scheduled kinds.
load only reads: the top CPU consumers, each classed ours-idle, ours-working, leftover or system, with the
action for its class.
--repo: repos for the branch and worktree kinds. Default: every repo in `orca repo list`, else
the repo of the current directory.
Config: ~/.claude/agent-skills.json → "reap": {"hours": 6, "orphan_paths": [...], "alert": {...},
"load_limit": <5-minute load; default the CPU count>, "idle_minutes": 30, "evidence_roots": [...], "user_folders": [], "user_folder_days": 7,
"user_folder_screenshot_days": 1, "program_days": 7, "state_days": 7, "log_max_kb": 512, "done_card_hours": 2, "inbox_to": "run:<id>", "plan_repeat": 2,
"regenerated_untracked": true, "preview_ports": [4500, 4599]}.
Janitor kinds (orca-worktree, done-card, orca-worker, tmpdir, gui-app, chrome-window, evidence, remote-branch, compose-stack;
`--kinds janitor`) cover only
what the ledger lists, an Orca orchestration worker made, or Orca records as made by `orca worktree create`
(cliProvenance created-by-cli, with or without --parent-worktree), never a worktree an Orca automation runs in. user-folder covers only top-level entries of explicitly configured reap.user_folders
untouched for reap.user_folder_days (default 7; screenshots and screen recordings reap.user_folder_screenshot_days,
default 1), and keeps them all while Finder empties the trash on its own, unless $AGENT_SKILLS_TRASH is set. done-card is an orca-worktree that only its idle Claude session keeps: reap asks that session once,
through `skills-sync nudge`, to close itself. reap moves files to the trash and never empties it (the OS may: scan
alerts when Finder's auto-empty is on), and leaves a chrome-window to the agent's browser tools. $AGENT_SKILLS_TRASH replaces the trash with a plain folder.
The ledger is --ledger or $AGENT_SKILLS_LEDGER, default <state>/ledger.jsonl (state: --state or $AGENT_SKILLS_STATE,
default ~/.local/state/agent-skills). program moves a closed program untouched for
reap.program_days to <programs>/_archive unless it has a resume or handoff note; state-file trashes reorient session files
older than reap.state_days and rotates sync.log past reap.log_max_kb. precheck scans the janitor kinds, and user-folder,
program and state-file at most once a day, writes janitor-plan.json
to the state dir and exits 0 when there are targets, except: the set the last reap failed on, for a day; and a set already
planned reap.plan_repeat times with no reap since, retried once a day, with one message to reap.inbox_to. schedule prints the
`orca automations create` command; only --write runs it.
"""
import concurrent.futures, contextlib, datetime as dt, functools, hashlib, json, os, pathlib, re, shlex, shutil, signal, stat, subprocess, sys, time, unicodedata

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
KINDS = ( "orphan", "docker", "branch", "worktree")
ALERT = { "orphan_procs": 5, "dangling_volumes": 50,
         "stale_branches": 30, "stale_worktrees": 10}
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
                              regenerated=cfg.get("regenerated_untracked", True) is not False,
                              evidence_roots=cfg.get("evidence_roots"),
                              done_idle_s=float(cfg.get("done_card_hours") or 2) * 3600,
                              preview=tuple(cfg.get("preview_ports") or (4500, 4599)))
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


# Janitor kinds: only what an agent created (the ledger, or an Orca orchestration worker).
JANITOR = ("orca-worktree", "done-card", "orca-worker", "tmpdir", "gui-app", "chrome-window", "evidence", "remote-branch",
           "compose-stack")
# What the scheduled run covers: the janitor kinds, the user's download and desktop folders, closed programs and hook state.
SCHEDULED = JANITOR + ("user-folder", "program", "state-file")
# Kinds that would find something new every round; the precheck looks at them once a day.
DAILY = ("user-folder", "program", "state-file")
MEDIA = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".webm")
STATE_HOME = pathlib.Path("~/.local/state/agent-skills").expanduser()
STATE = pathlib.Path(opt("--state") or os.environ.get("AGENT_SKILLS_STATE") or STATE_HOME).expanduser()
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
    if kind not in JANITOR:
        raise SystemExit(f"--kind must be one of {', '.join(JANITOR)}")
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


def judge_orca_worktree(path, source, facts, pr_state, min_age=0):
    """facts: the git and process state of one checkout, read by worktree_facts. A checkout with no PR goes only when
    its HEAD is on the remote default branch (end-session §4) and its sessions' transcripts have been quiet for
    min_age: a new card, and a lead or a read-only card still at work, sit there too."""
    base = {"kind": "orca-worktree", "key": f"orca-worktree:{path}", "name": path,
            "how": [f"git -C {shlex.quote(path)} ls-files --others --ignored --exclude-standard --directory 의 항목을 휴지통으로",
                    f"orca worktree rm --worktree {shlex.quote('path:' + path)} --run-hooks --json"]}
    if facts.get("regenerated"):
        base["regenerated"] = facts["regenerated"]
        base["how"].insert(0, "다시 판정한 뒤 재생성 사본 " + " ".join(shlex.quote(c) for c in facts["regenerated"]) + " 을 휴지통으로")
    ref = facts.get("pr")
    s = pr_state(ref) if ref else None
    idle = facts.get("idle")
    if facts.get("live"):
        why = facts["live"]
    elif not ref and facts.get("pr_unread"):
        why = "브랜치의 PR 을 읽지 못함(gh)"
    elif not ref and facts.get("landed") is None:
        why = "연결된 PR 없음, 기본 브랜치(origin/HEAD)를 모름"
    elif not ref and not facts["landed"]:
        why = "연결된 PR 없음"
    elif ref and s is None:
        why = f"PR {ref} 상태를 읽지 못함"
    elif ref and s["state"] == "OPEN":
        why = f"열린 PR {ref}"
    elif facts.get("unreadable"):
        why = "git 상태를 읽지 못함"
    elif facts.get("dirty"):
        why = "변경 있음(추적 안 되는 파일 포함)"
    elif not ref and not facts.get("worker_card"):
        # A lead or a card opened by hand also sits on the default branch with no PR; only a PR tells it is done.
        why = "연결된 PR 없음(워커가 만든 카드가 아님)"
    elif (not ref or facts.get("quiet_needed")) and (idle is None or idle < min_age):
        why = (f"PR {ref} {s['state'].lower()}" if ref else "연결된 PR 없음") + ", 세션이 " + \
            ("조용한지 알 수 없음" if idle is None else f"{age(idle)} 만 조용")
    elif not ref:
        return {**base, "why": f"PR 없음, HEAD 가 기본 브랜치에 있고 세션이 {age(idle)} 조용 ({source})", "target": True}
    elif facts.get("unpushed") and facts.get("head") != s.get("headRefOid"):
        why = "원격에 없는 커밋"
    else:
        return {**base, "why": f"PR {ref} {s['state'].lower()} ({source})", "target": True}
    return {**base, "why": why, "target": False}


def judge_done_card(path, source, facts, pr_state, quiet, idle_s, asked):
    """A card only a process keeps (its Claude session idle at the prompt) that would otherwise be an orca-worktree
    target: the session is asked once to close itself by end-session §8. None when something else keeps it."""
    # No PR: an idle lead waiting on its workers looks the same, and a card is asked only once.
    if facts.get("turn") or not facts.get("users") or facts.get("unreadable") or not facts.get("pr"):
        return None
    settled = judge_orca_worktree(path, source, {**facts, "live": None}, pr_state)
    if not settled["target"]:
        return None
    line = close_line(settled["why"])
    base = {"kind": "done-card", "key": f"done-card:{path}", "name": path, "line": line,
            "how": [f"skills-sync nudge <이 카드의 claude 터미널> {shlex.quote(line)}"]}
    if asked:
        return {**base, "why": f"닫기를 이미 요청함({asked}), 아직 열려 있음", "target": False}
    quiet_s = quiet(path)
    if quiet_s is None or quiet_s < idle_s:
        return {**base, "why": "세션이 " + ("조용한지 알 수 없음" if quiet_s is None else f"{age(quiet_s)} 만 조용"),
                "target": False}
    return {**base, "why": f"{settled['why']}, 세션이 {age(quiet_s)} 조용", "target": True}


def close_line(why):
    return (f"[reap-resources] 이 카드의 일은 끝난 것으로 보인다({why}). 남은 일이 없으면 end-session §8 로 지금 닫고, "
            "남은 일이 있으면 그것을 한 줄로 답한다")


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


def branch_pr(path, git=run):
    """(owner/repo#N or None, unread) for the branch checked out at path."""
    branch = (git(["git", "-C", path, "branch", "--show-current"], check=False) or "").strip()
    prs = branch and git(["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number,url,state,isCrossRepository"],
                          cwd=path, check=False)
    # A failed lookup is not an empty answer: a card with an open PR would pass as one without.
    unread = bool(branch) and prs is None
    # A fork's PR with the same head name is not this branch's.
    own = [p for p in json.loads(prs) if not p.get("isCrossRepository")] if prs else []
    own.sort(key=lambda p: p.get("state") != "OPEN")
    m = own and re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", own[0]["url"])
    return (m and f"{m.group(1)}#{m.group(2)}") or None, unread


def worktree_facts(path, ledger_pr, live_turn, users_cwd, git=run, regenerated=True):
    common = git(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"], check=False)
    if common is None:
        return None
    main = os.path.dirname(common.strip().rstrip("/"))
    if real(main) == real(path):
        return {"main": True}
    users = [pid for pid, c in users_cwd.items() if under(real(c), real(path))]
    ref, pr_unread = (ledger_pr, False) if ledger_pr else branch_pr(path, git)
    landed = None
    if not ref:
        default = git(["git", "-C", path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
        # A failed check reads as not an ancestor, so the checkout stays.
        landed = None if not default else git(["git", "-C", path, "merge-base", "--is-ancestor", "HEAD", default.strip()],
                                              check=False) is not None
    # Untracked files count: `orca worktree rm` would take new, never-added work with it.
    status = git(["git", "-C", path, "status", "--porcelain"], check=False)
    copies = None
    if regenerated and status and all(line.startswith("?? ") for line in status.splitlines()):
        copies = regenerated_copies(path, git)
    ahead = git(["git", "-C", path, "rev-list", "-n1", "HEAD", "--not", "--remotes"], check=False)
    return {"main": False, "repo": main, "pr": ref, "pr_unread": pr_unread, "landed": landed, "turn": live_turn, "users": users,
            "live": live_turn or (f"pid {users[0]} 이 이 worktree 를 cwd 로 사용 중" if users else None),
            # None is a failed read, not an empty answer.
            "unreadable": status is None or ahead is None,
            "dirty": bool(status) and not copies, "regenerated": copies or [], "unpushed": bool(ahead),
            "head": (git(["git", "-C", path, "rev-parse", "HEAD"], check=False) or "").strip()}


NO_PR_MIN_HOURS = 2


def cli_cards(res):
    """Real paths of worktrees an agent made with `orca worktree create`. Orca records cliProvenance "created-by-cli"
    with or without --parent-worktree (lineage origin "cli" only with it); the app's own New Worktree records neither.
    None when the list is unreadable or truncated."""
    if not isinstance(res, dict) or res.get("truncated"):
        return None
    return {real(w["path"]) for w in res.get("worktrees") or []
            if w.get("path") and not w.get("isMainWorktree") and not w.get("isPinned")
            and w.get("hostId", "local") == "local"
            and ((w.get("cliProvenance") or {}).get("kind") == "created-by-cli"
                 or ((w.get("lineage") or {}).get("origin") == "cli" and (w.get("lineage") or {}).get("parentWorktreeId")))}


def orca_json(*args):
    out = run(["orca", *args, "--json"], check=False) if shutil.which("orca") else None
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


# Each checkout costs a `gh pr list` and a few git reads, about a second; one after another, 40 cards ran past the
# automation's precheck timeout.
FACT_WORKERS = 8


def docker_ts(s):
    """Docker's RFC 3339 with nanoseconds; Python before 3.11 reads at most microseconds."""
    return dt.datetime.fromisoformat(re.sub(r"\.\d+", "", s).replace("Z", "+00:00")).timestamp()


def compose_stacks():
    """{project: {running, stopped, first (oldest container created), last (newest start or stop), ports, dirs (compose
    working directories), nodir (containers without one)}}; None when docker cannot be read."""
    if not shutil.which("docker"):
        return {}
    ids = run(["docker", "ps", "-aq", "--no-trunc"], check=False)
    if ids is None:
        return None
    if not ids.split():
        return {}
    out = run(["docker", "inspect", *ids.split()], check=False)
    if out is None:
        return None
    stacks = {}
    for c in json.loads(out):
        labels = (c.get("Config") or {}).get("Labels") or {}
        proj = labels.get("com.docker.compose.project")
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


def janitor_scan(kinds, repo_list, entries, notes, pr_state=None, hours=6.0, now=None, evidence_roots=None,
                 done_idle_s=2 * 3600, quiet=None, regenerated=True, preview=(4500, 4599)):
    pr_state = pr_state or pr_reader()
    items = []
    if "compose-stack" in kinds:
        stacks = compose_stacks()
        if stacks is None:
            notes.append("compose-stack: docker 를 읽지 못함, 건너뜀")
        items += judge_compose(entries, stacks or {}, functools.partial(stack_owner, pr_state=pr_state), hours,
                               now or time.time(), preview)
    rows = processes() if {"gui-app", "tmpdir"} & kinds else {}
    cwd_of = cwds() if {"tmpdir", "orca-worktree", "done-card"} & kinds else {}
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
    if {"orca-worktree", "done-card"} & kinds:
        workers = orca_workers() if shutil.which("orca") else []
        if workers is None:
            notes.append("orca-worktree: 오케스트레이션 워커 목록을 읽지 못함, ledger worktree 는 보존한다")
        live, made, users = worker_worktrees(workers or [])
        cards = cli_cards(orca_json("worktree", "list", "--limit", "10000")) if shutil.which("orca") else set()
        if cards is None:
            notes.append("orca-worktree: orca worktree list 를 읽지 못함, CLI 로 만든 카드는 ledger 에 있는 것만 본다")
        cards = cards or set()
        runners = automation_workspaces(orca_json("automations", "list")) if shutil.which("orca") else set()
        if runners is None:
            notes.append("orca-worktree: orca automations list 를 읽지 못함, automation 이 도는 worktree 를 가려내지 못한다")
        ledger = {real(e["id"]): e for e in latest(entries, "orca-worktree")}
        # A done-card line in the ledger only records that the card was asked once.
        asked = {real(e["id"]): e.get("ts") for e in latest(entries, "done-card")}
        quiet = quiet or transcript_quiet(pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
                                          / "projects", now or time.time())
        repos_real = {real(r) for r in repo_list}

        def read(path):
            e = ledger.get(path)
            facts = worktree_facts(path, e and (e.get("links") or {}).get("pr"), live.get(path), cwd_of or {},
                                   regenerated=regenerated)
            if facts and facts.get("pr"):
                pr_state(facts["pr"])  # fills the reader's cache here, in parallel
            return facts
        paths = [p for p in sorted((set(ledger) | made | cards) - users - (runners or set())) if os.path.isdir(p)]
        with concurrent.futures.ThreadPoolExecutor(FACT_WORKERS) as pool:
            all_facts = list(pool.map(read, paths))
        for path, facts in zip(paths, all_facts):
            e = ledger.get(path)
            if not facts or facts["main"] or (repos_real and real(facts["repo"]) not in repos_real):
                continue
            if cwd_of is None and not facts["live"]:
                facts["live"] = "프로세스 cwd 를 읽지 못함"
            if workers is None and not facts["live"]:
                # Without the list, a user's takeover and a live turn look the same as a finished worker.
                facts["live"] = "워커 목록을 읽지 못함"
            if runners is None and not facts["live"]:
                facts["live"] = "automation 목록을 읽지 못함"
            source = "ledger" if e else "오케스트레이션 워커" if path in made else "CLI 로 만든 카드"
            facts["worker_card"] = path in made
            # A PR found by branch name alone may be an old merged one whose name a lead reused: such a card waits for quiet.
            facts["quiet_needed"] = path not in made and not (e and (e.get("links") or {}).get("pr"))
            if facts["quiet_needed"] or (not facts.get("pr") and facts["worker_card"]):
                facts["idle"] = quiet(path)
            if "orca-worktree" in kinds:
                # `--hours 0` must not let a card that just went quiet go.
                items.append(judge_orca_worktree(path, source, facts, pr_state, max(hours, NO_PR_MIN_HOURS) * 3600))
            if "done-card" in kinds and workers is not None and runners is not None and (d := judge_done_card(path, source, facts, pr_state, quiet, done_idle_s,
                                                              asked.get(path))):
                items.append(d)
    return items


USER_FOLDERS = ()  # Explicit reap.user_folders configuration opts into personal-folder cleanup.
PARTIAL = (".download", ".crdownload", ".part")
# The automation runs every 30 minutes; 23.5 keeps a daily sweep from slipping a slot each day.
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
        base = {"kind": "program", "key": f"program:{name}", "name": str(path),
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
    plan = state_dir / "janitor-plan.json"
    reap_line = round_cmd(script, state_dir, ledger, "reap", "--plan", str(plan))
    prompt = (f"reap-resources janitor 회차다. 정리 계획 {plan} 의 대상을 `{reap_line}` 로 한 번 정리한다. "
              "reap 은 계획의 대상을 다시 확인한 뒤 닫거나 휴지통으로 옮기고, 바뀐 것은 건너뛴다. "
              "출력에 '에이전트가 처리'로 남은 chrome-window 는 브라우저 도구가 있고 그 창·탭의 URL 이 출력에 적힌 URL 과 같을 때만 닫는다. "
              "다르거나 브라우저 도구가 없으면 남긴다. "
              "그 뒤 정리한 것, 건너뛴 것, 남긴 것과 이유를 한국어로 보고한다. "
              "계획에 없는 것은 건드리지 않고, rm 으로 지우거나 휴지통을 비우지 않는다. "
              "reap 이 권한·분류기에 거부되면 다른 명령으로 우회하지 않는다. "
              + (f"거부된 명령 한 줄을 `orca orchestration send --to {shlex.quote(inbox)} --type escalation "
                 "--subject 'reap-resources janitor: reap 거부됨' --body '<명령>'` 로 보내고, 보고 첫 줄에도 적는다."
                 if inbox else "거부된 명령 한 줄을 보고 첫 줄에 적는다."))
    return prompt


def notify_unrun(inbox, command, send=run):
    """The precheck's own alarm for a plan that keeps not running: a line on stdout, and one Orca message when
    reap.inbox_to is set. True once the message went out."""
    def notify(count):
        line = f"janitor 계획이 {count}번 실행되지 않음(reap 거부 가능성). 직접 실행: {command}"
        print(line)
        if not inbox:
            return False
        if send(["orca", "orchestration", "send", "--to", inbox, "--type", "escalation",
                 "--subject", "reap-resources janitor: 계획이 실행되지 않음", "--body", line, "--json"], check=False) is None:
            print(f"메모: {inbox} 로 보내지 못함", file=sys.stderr)
            return False
        return True
    return notify


# Compose stacks a finished card left running hold gigabytes; a precheck with no targets never wakes the model.
TRIGGER = "7,37 * * * *"
# Orca's default of 60 seconds is shorter than a precheck that reads `gh` and lsof for dozens of cards.
PRECHECK_TIMEOUT = 300


def schedule_cmd(script, state_dir, workspace, provider="claude", ledger=None, inbox=None):
    return ["orca", "automations", "create", "--name", "agent-skills janitor", "--trigger", TRIGGER,
            "--provider", provider, "--workspace-mode", "existing", "--workspace", workspace,
            "--precheck", round_cmd(script, state_dir, ledger, "precheck"), "--precheck-timeout", str(PRECHECK_TIMEOUT),
            "--prompt", schedule_prompt(script, state_dir, ledger, inbox), "--json"]


def runner_hint(checkout):
    return ("\n모델: automation 은 --workspace 체크아웃의 Claude Code 설정을 따른다(create 에 모델 옵션이 없다). "
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


SKILLS_SYNC = pathlib.Path(__file__).resolve().parents[3] / "bin" / "skills-sync"


def nudge_card(item):
    """Type the close request into the card's idle Claude session once; skills-sync refuses a busy screen."""
    res = orca_json("terminal", "list", "--worktree", f"path:{item['name']}")
    handles = [t["handle"] for t in (res or {}).get("terminals") or [] if t.get("agentIdentity") == "claude"]
    if len(handles) != 1:
        raise RuntimeError(f"claude 터미널이 {len(handles)}개, 보내지 않음")
    out = subprocess.run([str(SKILLS_SYNC), "nudge", handles[0], item["line"]], capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"보내지 않음: {(out.stdout or out.stderr).strip()[:200]}")
    ledger_add(ledger_path(), "done-card", item["name"], by="reap")
    return f"{handles[0]} 에 닫기를 한 번 요청함(ledger 에 기록)"


def act(item, notes):
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
    if kind == "done-card":
        return nudge_card(item)
    if kind == "compose-stack":
        # No -v: the stack's volumes are data, and stay.
        run(["docker", "compose", "-p", item["name"], "down"])
        ledger_add(ledger_path(), "compose-stack", item["name"], by="reap", retired=True)
        return "docker compose down (볼륨 유지, ledger 에서 내림)"
    if kind == "orca-worker":
        run(["orca", "orchestration", "worker-release", "--dispatch", k.split(":", 1)[1], "--json"])
        return "터미널 회수"
    if kind == "orca-worktree":
        # `orca worktree rm` deletes ignored files (.env.local, notes, caches) for good; they go to the trash first.
        # Tracked files are in git, and anything untracked left makes the rm refuse.
        p = item["name"]
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
        run(["orca", "worktree", "rm", "--worktree", f"path:{p}", "--run-hooks", "--json"])
        return (f"재생성 사본 {len(copies)}개, " if copies else "") + \
            f"ignored 항목 {len(ignored)}개는 휴지통으로, worktree 는 orca worktree rm 으로 제거"
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
    if any(a == "--under" or a.startswith("--under=") for a in sys.argv):
        sys.exit("--under is no longer supported; use an explicit plan and supported scope options")
    if any(a.startswith("--kinds=") for a in sys.argv):
        sys.exit("use --kinds VALUE with supported kinds; --kinds=VALUE is not supported")
    if "--kinds" in sys.argv:
        requested = opt("--kinds") if sys.argv.index("--kinds") + 1 < len(sys.argv) else None
        if not requested or set(requested.split(",")) - set(KINDS + SCHEDULED + ("janitor",)):
            sys.exit("--kinds requires a comma-separated list of supported kinds: " +
                     ",".join(dict.fromkeys(KINDS + SCHEDULED + ("janitor",))))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    cfg = config()
    if cmd == "reap":
        path = pathlib.Path(opt("--plan") or sys.exit("reap needs --plan PLAN.json"))
        plan = json.loads(path.read_text())
        wanted = {i["key"]: i for i in plan["items"] if i["target"]}
        # The janitor round's autoMode rule covers only the scheduled kinds; a manual kind in that plan was put there by hand.
        if path.name == "janitor-plan.json":
            for key in [k for k, i in wanted.items() if i["kind"] not in SCHEDULED]:
                print(f"- 건너뜀 {wanted.pop(key)['key']}: janitor 계획에 들어갈 수 없는 종류")
        kinds = {i["kind"] for i in wanted.values()}
        fresh, notes = scan(kinds, plan["hours"], cfg, plan["repos"])
        failed = []
        now = {i["key"]: i for i in fresh if i["target"]}
        for key, old in wanted.items():
            new = now.get(key)
            if not new or any(new.get(k) != old.get(k) for k in ("cmd", "start", "tip")):
                print(f"- 건너뜀 {label(old)}: 더는 정리 대상이 아님")
                continue
            try:
                print(f"- {label(new)}: {act(new, notes)}")
            except (RuntimeError, OSError) as e:
                failed.append(key)
                print(f"- 실패 {label(new)}: {e}")
        if set(plan.get("kinds") or ()) & set(SCHEDULED):
            record_reap(STATE, failed)
        for k in set(DAILY) & set(plan.get("kinds") or ()):
            stamp_user_folder(STATE, kind=k)
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
        argv = schedule_cmd(script, STATE, workspace, opt("--provider") or "claude", ledger_path(), cfg.get("inbox_to"))
        print(" ".join(shlex.quote(a) for a in argv))
        if not opt("--workspace"):
            print(runner_hint(top.strip()))
        if "--write" in sys.argv:
            out = run(argv)
            print(out)
        else:
            print("\n(출력만 했다. 실제로 만들려면 --write)")
        return
    if cmd not in ("scan", "precheck"):
        sys.exit(__doc__)
    names = (opt("--kinds") or ",".join(SCHEDULED if cmd == "precheck" else KINDS)).split(",")
    names = [k for n in names for k in (JANITOR if n == "janitor" else (n,))]
    kinds = set(names) & set(KINDS + SCHEDULED)
    pre_notes = []
    for k in DAILY:
        if cmd == "precheck" and k in kinds and not user_folder_due(STATE, kind=k):
            kinds.discard(k)
            pre_notes.append(f"{k}: 하루 한 번만 정리한다, 이번 회차는 건너뜀")
    hours = float(opt("--hours") or cfg.get("hours") or 6)
    repo_list = repos([a for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--repo"]) \
        if {"branch", "worktree", "orca-worktree", "done-card"} & kinds else []
    items, notes = scan(kinds, hours, cfg, repo_list)
    notes = pre_notes + notes
    msgs, totals = alerts(items, {**ALERT, **(cfg.get("alert") or {})})
    if trash_auto_empty():
        msgs.append("휴지통 자동 비움 켜짐(Finder: 30일 지난 항목 제거): 휴지통으로 옮긴 것은 30일 뒤 영구 삭제된다")
    data ={"at": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "hours": hours,
            "kinds": [k for k in KINDS + SCHEDULED if k in kinds], "repos": repo_list, "items": items, "notes": notes, "alerts": msgs, "totals": totals}
    if cmd == "precheck":
        STATE.mkdir(parents=True, exist_ok=True)
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
    main()
