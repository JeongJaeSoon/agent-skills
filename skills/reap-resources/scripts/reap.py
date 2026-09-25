#!/usr/bin/env python3
"""Inventory and reap what dead sessions left behind on this machine.

Usage:
  python3 reap.py scan [--hours N] [--kinds codex,orphan,docker,branch,worktree] [--repo PATH]...
                       [--plan OUT.json] [--json]
  python3 reap.py reap --plan PLAN.json

scan only reads. reap re-scans and acts only on the plan's targets that are still targets with the
same identity (pid and start time, branch tip), so nothing outside the list is touched.
--repo: repos for the branch and worktree kinds. Default: every repo in `orca repo list`, else
the repo of the current directory.
Config: ~/.claude/agent-skills.json → "reap": {"hours": 6, "orphan_paths": [...], "alert": {...}}.
"""
import datetime as dt, json, os, pathlib, re, shutil, signal, subprocess, sys, time

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
    now, rows = time.time(), {}
    for line in run(["ps", "-axo", "pid=,ppid=,etime=,rss=,%cpu=,command="]).splitlines():
        p = line.split(None, 5)
        if len(p) == 6:
            age = etime_s(p[2])
            rows[int(p[0])] = {"pid": int(p[0]), "ppid": int(p[1]), "age": age, "start": now - age,
                               "rss": int(p[3]) * 1024, "cpu": float(p[4]), "cmd": p[5]}
    return rows


def cwds():
    """pid → cwd for every process this user can see; None when that cannot be read."""
    if os.path.isdir("/proc/self"):
        out = {}
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    out[int(d)] = os.readlink(f"/proc/{d}/cwd")
                except OSError:
                    pass
        return out
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    try:
        # lsof exits 1 when some processes are unreadable; the rest of its output still holds.
        listing = subprocess.run([lsof, "-d", "cwd", "-Fpn"], capture_output=True, text=True, env=ENV).stdout
    except FileNotFoundError:
        listing = ""
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
    out = []
    for r in rows.values():
        if not BROKER.match(r["cmd"]):
            continue
        m = BROKER_CWD.search(r["cmd"])
        cwd = m.group(1) if m else None
        live = [pid for pid, c in claude.items() if cwd and under(c, real(cwd))]
        if not cwd or not exists(cwd):
            why, target = "cwd 없음", True
        elif live:
            why, target = f"cwd에 살아 있는 claude(pid {live[0]})", False
        elif cwd_of is None:
            why, target = "프로세스 cwd 를 읽지 못함", False
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
        # Only the executable or the script it runs counts as "under" a path, not an argument or a prompt.
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
    head = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo, check=False)
    default = head.strip().split("/", 1)[-1] if head else "main"
    branches = dict(l.split("\t") for l in run(["git", "for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads"], cwd=repo).splitlines())
    if len(branches) <= 1:
        return []
    prs = run(["gh", "pr", "list", "--state", "all", "--limit", "1000", "--json",
               "number,state,headRefName,headRefOid,isCrossRepository"], cwd=repo, check=False)
    if prs is None:
        notes.append(f"branch: {repo} 의 PR 을 읽지 못함(gh), 건너뜀")
        return []

    def is_ancestor(a, b):
        return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=repo, capture_output=True).returncode == 0
    return judge_branches(repo, branches, checked, default, json.loads(prs), is_ancestor)


def judge_worktree(repo, w, hours, cwd_of, now, projects_dir, git=run):
    path = w["path"]
    base = {"kind": "worktree", "key": f"worktree:{repo}:{path}", "repo": repo, "name": path}
    if w.get("prunable"):
        return {**base, "why": "디렉터리 없음", "target": True}
    m = SCRATCH.search(path + "/")
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
        for repo in repo_list:
            try:
                if "branch" in kinds:
                    items += branch_scan(repo, notes)
                if "worktree" in kinds:
                    items += [x for w in worktrees(repo)[1:]
                              if (x := judge_worktree(repo, w, hours, cwd_of, time.time(), projects_dir))]
            except RuntimeError as e:
                notes.append(f"{repo}: {e}")
    return items, notes


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
    if not r or r["cmd"] != item["cmd"] or abs(r["start"] - item["start"]) > 5:
        return "사라졌거나 다른 프로세스"
    pids = tree(item["pid"], rows)
    cmds = {p: rows[p]["cmd"] for p in pids}
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
        left = [p for p in pids if p in now and now[p]["cmd"] == cmds[p]]
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
        wanted = {i["key"]: i for i in plan["items"] if i["target"]}
        fresh, notes = scan({i["kind"] for i in wanted.values()}, plan["hours"], cfg, plan["repos"])
        now = {i["key"]: i for i in fresh if i["target"]}
        for key, old in wanted.items():
            new = now.get(key)
            if not new or new.get("cmd") != old.get("cmd") or new.get("tip") != old.get("tip") \
                    or abs(new.get("start", 0) - old.get("start", 0)) > 5:
                print(f"- 건너뜀 {label(old)}: 더는 정리 대상이 아님")
                continue
            try:
                print(f"- {label(new)}: {act(new, notes)}")
            except RuntimeError as e:
                print(f"- 실패 {label(new)}: {e}")
        for n in notes:
            print(f"- 메모: {n}")
        return
    if cmd != "scan":
        sys.exit(__doc__)
    kinds = set((opt("--kinds") or ",".join(KINDS)).split(",")) & set(KINDS)
    hours = float(opt("--hours") or cfg.get("hours") or 6)
    repo_list = repos([a for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--repo"]) \
        if {"branch", "worktree"} & kinds else []
    items, notes = scan(kinds, hours, cfg, repo_list)
    msgs, totals = alerts(items, {**ALERT, **(cfg.get("alert") or {})})
    data = {"at": dt.datetime.now().astimezone().isoformat(timespec="minutes"), "hours": hours,
            "kinds": [k for k in KINDS if k in kinds], "repos": repo_list, "items": items, "notes": notes, "alerts": msgs, "totals": totals}
    if opt("--plan"):
        pathlib.Path(opt("--plan")).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        report(data)


if __name__ == "__main__":
    main()
