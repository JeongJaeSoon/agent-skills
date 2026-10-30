#!/usr/bin/env python3
"""Take stock of every Orca card (non-main worktree) and close the finished ones.

Usage:
  python3 cards.py scan [--plan PLAN.json] [--json] [--repo PATH]...   read only: one row per card
  python3 cards.py apply --plan PLAN.json                              act on that plan's rows, judged again

Each card gets one verdict: 삭제 (close its listed terminals, ignored files to the trash, `orca worktree rm
--run-hooks`), 터미널만 닫기 (an open PR: the worktree stays, its idle terminals go), 남김, or 사람 결정 (nothing is
done; the reason says what to decide). The rules are in ../references/cards.md and in judge() below.
`apply` reads the card again and acts only when the verdict and HEAD are what the plan says.
The janitor's `card` kind (reap.py) is this module.
Config: ~/.claude/agent-skills.json → "reap": {"idle_minutes": 30, "cards": {"protect_prefixes": ["lead-"],
"done_patterns": [...], "question_patterns": [...]}}.
"""
import json, os, pathlib, re, shlex, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reap  # noqa: E402

REMOVE, CLOSE, KEEP, ASK = "삭제", "터미널만 닫기", "남김", "사람 결정"
ACTING = (REMOVE, CLOSE)
# A working Claude screen redraws its spinner every second; an idle one still writes about every 30 minutes.
AGENT_BUSY_S = 5 * 60
SCREEN_LINES = 40
DONE_PATTERNS = (r"닫아도\s*(된|돼|좋|괜찮)", r"종료해도\s*(된|돼|좋|괜찮)", r"(can|may) (now )?be closed",
                 r"safe to close", r"閉じて(も)?(大丈夫|よい|いい|構いません)")
# An AskUserQuestion dialog, a permission dialog, a y/n prompt.
QUESTION_PATTERNS = (r"Enter to select", r"Do you want to (proceed|make this edit|create|allow)", r"\(y/n\)",
                     r"Esc to cancel")


def sh(cmd, cwd=None, input=None, errors=None):
    """stdout, or None on failure with the first stderr line appended to errors: a reason is evidence."""
    try:
        r = subprocess.run(cmd, cwd=cwd, input=input, capture_output=True, text=True, env=reap.ENV, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        if errors is not None:
            errors.append(f"{cmd[0]}: {type(e).__name__}")
        return None
    if r.returncode:
        if errors is not None:
            errors.append(f"{' '.join(cmd[:3])}: {(r.stderr.strip().splitlines() or ['exit ' + str(r.returncode)])[0][:160]}")
        return None
    return r.stdout


def settings(cfg):
    c = cfg.get("cards") or {}
    return {"idle_s": float(cfg.get("idle_minutes") or 30) * 60,
            "prefixes": tuple(c.get("protect_prefixes") or ("lead-",)),
            "done": [re.compile(p, re.I) for p in c.get("done_patterns") or DONE_PATTERNS],
            "question": [re.compile(p) for p in c.get("question_patterns") or QUESTION_PATTERNS]}


# ---------------------------------------------------------------- facts

def gh_repo(path, run):
    url = (run(["git", "-C", path, "remote", "get-url", "origin"]) or "").strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?$", url)
    return m and m.group(1)


def landed(path, head, run):
    """(on_base, via): HEAD is on the remote default branch. A failed fetch (an SSO re-authorization, say) leaves
    the local origin/HEAD behind, so a local "no" is asked of GitHub again; a 404 there is a commit never pushed."""
    default = (run(["git", "-C", path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]) or "").strip()
    if default and run(["git", "-C", path, "merge-base", "--is-ancestor", head, default]) is not None:
        return True, "로컬 " + default
    repo = gh_repo(path, run)
    base = default.partition("/")[2] or (repo and (run(["gh", "api", f"repos/{repo}", "--jq", ".default_branch"]) or "").strip())
    if not repo or not base:
        return None, "기본 브랜치를 모름"
    errs = []
    out = run(["gh", "api", f"repos/{repo}/compare/{base}...{head}", "--jq", ".ahead_by"], errors=errs)
    if out is None:
        return (False, "GitHub 에 없는 커밋") if any("404" in e or "Not Found" in e for e in errs) else \
            (None, "GitHub compare 를 읽지 못함")
    return out.strip() == "0", f"GitHub compare {base}"


def patch_id(path, sha, run):
    diff = run(["git", "-C", path, "show", "--format=", "-p", sha])
    out = diff is not None and run(["git", "patch-id", "--stable"], input=diff)
    return out.split()[0] if out else None


def unpushed(path, head, pr_head, run):
    """'none', 'copy' (a commit whose message is on the remote with another patch: a copy from before a rebase, for a
    person to judge), 'local' (work only here), or None when unreadable. A commit whose patch is on the remote under
    another sha (a cherry-pick, a rebase that changed nothing) counts as pushed."""
    if pr_head and head == pr_head:
        return "none"  # the PR carried HEAD; a squash merge and a deleted branch leave its commits off every remote
    out = run(["git", "-C", path, "rev-list", "HEAD", "--not", "--remotes"])
    if out is None:
        return None
    verdict = "none"
    for sha in out.split():
        subject = (run(["git", "-C", path, "log", "-1", "--format=%s", sha]) or "").strip()
        found = run(["git", "-C", path, "log", "--remotes", "-F", f"--grep={subject}", "--format=%H%x00%s", "-n", "20"]) or ""
        same = [h for h, _, s in (line.partition("\0") for line in found.splitlines()) if s == subject]
        if not subject or not same:
            return "local"
        mine = patch_id(path, sha, run)
        if mine is None or mine not in {patch_id(path, h, run) for h in same}:
            verdict = "copy"
    return verdict


def classify_screen(lines, st):
    text = "\n".join(lines[-SCREEN_LINES:])
    if any(p.search(text) for p in st["question"]):
        return "question"
    if any(p.search(text) for p in st["done"]):
        return "done"
    return None


def processes_in(path, rows, cwd_of):
    """(claude pids, busy shells a Claude session runs, other pids): every Bash call and background shell of a session
    is its shell child; an Orca terminal's shell at its prompt counts as nothing, and so do a session's other children
    (MCP servers). Anything else (a dev server, a person's command) is "other"."""
    inside = [p for p, c in cwd_of.items() if reap.under(reap.real(c), path) and p in rows]
    claude = {p for p in inside if reap.is_claude(rows[p]["cmd"])}
    busy = sorted(q for q, r in rows.items() if r["ppid"] in claude
                  and os.path.basename((r["cmd"].split() or [""])[0]).lstrip("-") in reap.SHELLS)

    def agents_child(p):
        seen = set()
        while p in rows and p not in seen and p > 1:
            seen.add(p)
            p = rows[p]["ppid"]
            if p in claude:
                return True
        return False
    other = sorted(p for p in inside if p not in claude and not agents_child(p)
                   and not (reap.human_shell(p, rows) and reap.orca_shell(p, rows)))
    return sorted(claude), busy, other


def gather(w, m, run=None):
    """Facts for one row of `orca worktree list`. m: what a scan reads once (read_machine)."""
    errs = []
    run = run or (lambda cmd, cwd=None, input=None, errors=None:
                  sh(cmd, cwd=cwd, input=input, errors=errs if errors is None else errors))
    p = reap.real(w["path"])
    f = {"path": p, "name": w.get("displayName") or os.path.basename(p), "head": "", "pr": None, "state": None,
         "branch": (w.get("branch") or "").removeprefix("refs/heads/"), "repo": os.path.basename(os.path.dirname(p)),
         "terminals": [], "errors": errs}
    base = os.path.basename(p)
    if w.get("isMainWorktree"):
        return {**f, "protected": "메인 체크아웃"}
    if w.get("isPinned"):
        return {**f, "protected": "고정된 카드"}
    if any(base.startswith(x) or f["name"].startswith(x) for x in m["st"]["prefixes"]):
        return {**f, "protected": "보호 이름(" + ", ".join(m["st"]["prefixes"]) + ")"}
    if m["self"] and reap.under(m["self"], p):
        return {**f, "protected": "이 정리를 돌리는 세션의 카드"}
    if p in m["runners"]:
        return {**f, "protected": "automation runner worktree"}
    if p in m["live"]:
        return {**f, "protected": m["live"][p]}
    if (children := [c for c in map(lambda i: (i or "").split("::")[1] if "::" in (i or "") else "",
                                    w.get("childWorktreeIds") or ()) if c and os.path.isdir(c)]):
        return {**f, "protected": f"하위 카드 {os.path.basename(children[0])} 가 열려 있음(리드)"}
    if (coord := reap.coordinator_in(p, m["rows"], m["cwd_of"], m["projects"])):
        return {**f, "protected": f"리드·오케스트레이터: {coord}"}
    head = (run(["git", "-C", p, "rev-parse", "HEAD"]) or "").strip()
    status = run(["git", "-C", p, "status", "--porcelain"])
    if not head or status is None:
        return {**f, "unreadable": "git 상태를 읽지 못함"}
    copies = reap.regenerated_copies(p, lambda c, check=False: run(c)) \
        if status and all(line.startswith("?? ") for line in status.splitlines()) else None
    f.update(head=head, dirty=bool(status) and not copies, regenerated=copies or [])
    ref, unread = reap.branch_pr(p, lambda c, cwd=None, check=False: run(c, cwd=cwd), head)
    if unread:
        return {**f, "pr_unread": "브랜치의 PR 을 읽지 못함(gh" + (f": {errs[-1]}" if errs else "") + ")"}
    pr = m["pr_state"](ref) if ref else None
    if ref and pr is None:
        return {**f, "pr": ref, "pr_unread": f"PR {ref} 상태를 읽지 못함"}
    f.update(pr=ref, state=pr and pr["state"], pr_head=pr and pr.get("headRefOid"))
    if ref and f["pr_head"] and head != f["pr_head"]:
        # HEAD behind the PR's head is the PR's work; HEAD past it is new work on a reused branch name.
        f["carried"] = run(["git", "-C", p, "merge-base", "--is-ancestor", head, f["pr_head"]]) is not None
    else:
        f["carried"] = bool(ref)
    if not ref or not f["carried"]:
        f["landed"], f["landed_via"] = landed(p, head, run)
    f["unpushed"] = "none" if f.get("landed") else unpushed(p, head, f["pr_head"], run)
    claude, busy, other = processes_in(p, m["rows"], m["cwd_of"])
    f.update(claude=claude, busy=busy, other=other, quiet=m["quiet"](p))
    terms = run(["orca", "terminal", "list", "--worktree", f"path:{p}", "--json"])
    try:
        res = json.loads(terms)["result"]
        if res.get("truncated"):
            raise ValueError
    except (TypeError, ValueError, KeyError):
        return {**f, "unreadable": "터미널 목록을 읽지 못함"}
    for t in res.get("terminals") or ():
        agent = bool(t.get("agentIdentity"))
        silent = m["now"] - (t.get("lastOutputAt") or 0) / 1000
        screen = None
        if agent:
            out = run(["orca", "terminal", "read", "--terminal", t["handle"], "--screen", "--limit", str(SCREEN_LINES), "--json"])
            try:
                screen = classify_screen(json.loads(out)["result"]["terminal"]["tail"], m["st"])
            except (TypeError, ValueError, KeyError):
                screen = None
        f["terminals"].append({"handle": t["handle"], "agent": agent, "silent": silent, "screen": screen})
    return f


# ---------------------------------------------------------------- the rule

def judge(f, idle_s):
    """(verdict, why, handles to close). Pure: every fact comes from gather()."""
    if f.get("protected"):
        return KEEP, f"보호: {f['protected']}", []
    if f.get("unreadable"):
        return KEEP, f["unreadable"], []
    agents = [t for t in f["terminals"] if t["agent"]]
    shells = [t for t in f["terminals"] if not t["agent"]]
    if f.get("busy") or any(t["silent"] < AGENT_BUSY_S for t in agents):
        return KEEP, "에이전트 턴 진행 중", []
    if any(t["screen"] == "question" for t in agents):
        return ASK, "에이전트가 사람의 답을 기다림(화면)", []
    if f.get("other"):
        return KEEP, f"카드 안에 프로세스 pid {f['other'][0]}", []
    if (loud := [t for t in shells if t["silent"] < idle_s]):
        return KEEP, f"셸 터미널 {loud[0]['handle']} 이 {int(idle_s // 60)}분 안에 출력함", []
    quiet = f.get("quiet")
    # A Claude process with no Orca agent terminal (headless `claude -p`, another tool's session) needs idle evidence
    # too; with neither a transcript nor a terminal to read, it never counts as idle.
    live = agents or f.get("claude")
    said_done = bool(agents) and all(t["screen"] == "done" for t in agents)
    # A session that wrote "done, close me" is idle evidence on its own; otherwise its transcript must be quiet.
    agents_idle = not live or said_done or (
        (quiet if isinstance(quiet, (int, float)) else min((t["silent"] for t in agents), default=0)) >= idle_s)
    if isinstance(quiet, str) and live and not said_done:
        return KEEP, quiet, []
    handles = [t["handle"] for t in f["terminals"]]
    if f.get("pr_unread"):
        return KEEP, f["pr_unread"], []
    if f.get("state") == "OPEN":
        if agents_idle and handles:
            return CLOSE, f"열린 PR {f['pr']}: worktree 는 두고 idle 터미널 {len(handles)}개만 닫음", handles
        return KEEP, f"열린 PR {f['pr']}" + ("" if agents_idle else ", 세션이 아직 조용하지 않음"), []
    if f.get("dirty"):
        return KEEP, "커밋 안 된 변경(추적 안 되는 파일 포함, ignored 제외)", []
    if f.get("unpushed") is None:
        return KEEP, "미push 커밋을 읽지 못함", []
    if f["unpushed"] == "copy":
        return ASK, "미push 커밋이 원격의 같은 메시지 커밋과 patch 가 다름(rebase 전 사본?): 지울지 사람이 판단", []
    if f["unpushed"] == "local":
        return KEEP, "원격에 없는 커밋", []
    tail = (", 터미널 " + str(len(handles)) + "개 닫고" if handles else "") + (", 화면에 끝났다고 씀" if said_done else "")
    if f.get("pr"):
        if not f.get("carried") and not f.get("landed"):
            return KEEP, f"PR {f['pr']} {f['state'].lower()}, 그 뒤 HEAD 가 바뀜", []
        if not agents_idle:
            return KEEP, f"PR {f['pr']} {f['state'].lower()}, 세션이 아직 조용하지 않음", []
        return REMOVE, f"PR {f['pr']} {f['state'].lower()}, 깨끗함{tail}", handles
    if f.get("landed") is None:
        return KEEP, f"PR 없음, {f.get('landed_via') or '기본 브랜치를 모름'}", []
    if not f["landed"]:
        return KEEP, f"PR 없음, HEAD 가 기본 브랜치에 없음({f['landed_via']})", []
    if live and not said_done:
        return KEEP, "PR 없음, Claude 세션이 있음(끝났다는 말 없음)", []
    return REMOVE, f"PR 없음, HEAD 가 기본 브랜치에 있음({f['landed_via']}){tail}", handles


# ---------------------------------------------------------------- scan / apply

def read_machine(cfg, now=None):
    now = now or time.time()
    workers = reap.orca_workers()
    live, _, _ = reap.worker_worktrees(workers or [])
    top = sh(["git", "rev-parse", "--show-toplevel"])
    projects = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"
    return {"now": now, "st": settings(cfg), "rows": reap.processes(), "cwd_of": reap.cwds(), "live": live,
            "workers_unread": workers is None, "runners": reap.automation_workspaces(reap.orca_json("automations", "list")),
            "self": top and reap.real(top.strip()), "pr_state": reap.pr_reader(), "projects": projects,
            "quiet": reap.transcript_quiet(projects, now, reasons=True)}


def cards_listing(repo_list=()):
    res = reap.orca_json("worktree", "list", "--limit", "10000")
    if not isinstance(res, dict) or res.get("truncated"):
        return None
    rows = [w for w in res.get("worktrees") or () if w.get("path") and w.get("hostId", "local") == "local"
            and os.path.isdir(w["path"])]
    if repo_list:
        # A card lives outside its repo's checkout; Orca's repoId ties it to the main worktree's path.
        wanted = {reap.real(r) for r in repo_list}
        ids = {w.get("repoId") for w in rows if w.get("isMainWorktree") and reap.real(w["path"]) in wanted}
        rows = [w for w in rows if w.get("repoId") in ids]
    return rows


def scan(cfg, repo_list=(), notes=None):
    notes = [] if notes is None else notes
    m = read_machine(cfg)
    rows = cards_listing(repo_list)
    if rows is None or m["cwd_of"] is None or m["workers_unread"] or m["runners"] is None:
        notes.append("orca worktree·워커·automation 목록이나 프로세스 cwd 를 읽지 못함: 카드는 판정하지 않는다")
        return [], notes
    items = []
    for w in rows:
        if w.get("isMainWorktree"):
            continue
        f = gather(w, m)
        verdict, why, close = judge(f, m["st"]["idle_s"])
        items.append({"path": f["path"], "name": f["name"], "repo": f["repo"], "branch": f["branch"], "pr": f["pr"],
                      "state": f["state"], "verdict": verdict, "why": why, "close": close, "head": f["head"],
                      "regenerated": f.get("regenerated") or []})
    return items, notes


def report(items, notes):
    order = {REMOVE: 0, CLOSE: 1, ASK: 2, KEEP: 3}
    print(f"# Orca 카드 점검 ({len(items)}개)\n")
    print("| 카드 | repo/branch | PR | 판정 | 이유 |\n|---|---|---|---|---|")
    for i in sorted(items, key=lambda i: (order[i["verdict"]], i["repo"], i["name"])):
        pr = f"{i['pr']} {i['state'].lower()}" if i["pr"] else "-"
        print(f"| {i['name']} | {i['repo']}/{i['branch'] or '-'} | {pr} | {i['verdict']} | {i['why']} |")
    counts = {v: sum(i["verdict"] == v for i in items) for v in order}
    print("\n" + " · ".join(f"{k} {n}" for k, n in counts.items()))
    for n in notes:
        print(f"- 메모: {n}")


def reread(path, cfg):
    """(verdict, why, handles, facts) for the card at path, read fresh; None when Orca no longer lists it."""
    m = read_machine(cfg)
    row = next((w for w in cards_listing() or () if reap.real(w["path"]) == path), None)
    if row is None:
        return None
    f = gather(row, m)
    return (*judge(f, m["st"]["idle_s"]), f)


def act(item, cfg, wait=5.0):
    """Judge the card again and do what its plan row says. Returns what happened; raises RuntimeError on failure."""
    got = reread(item["path"], cfg)
    if got is None:
        return "건너뜀: orca worktree list 에서 다시 찾지 못함"
    verdict, why, close, f = got
    if verdict != item["verdict"] or f["head"] != item["head"]:
        return f"건너뜀: 다시 보니 {verdict}({why})"
    close = [h for h in close if h in item["close"]]
    for h in close:
        reap.run(["orca", "terminal", "close", "--terminal", h, "--json"])
    if verdict == CLOSE:
        return f"터미널 {len(close)}개 닫음, worktree 는 남김"
    # A closed terminal's shell or session takes a moment to exit; until it does it reads as a process in the card.
    deadline = time.time() + wait
    while (got := reread(item["path"], cfg)) and got[0] != REMOVE and time.time() < deadline:
        time.sleep(1)
    if not got or got[0] != REMOVE:
        return f"터미널 {len(close)}개 닫음, 삭제는 건너뜀: {got[1] if got else '다시 찾지 못함'}"
    out = reap.act({"kind": "orca-worktree", "key": f"orca-worktree:{item['path']}", "name": item["path"],
                    "regenerated": got[3].get("regenerated")}, [])
    return (f"터미널 {len(close)}개 닫음, " if close else "") + out


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "scan"
    cfg = reap.config()
    if cmd == "apply":
        plan = json.loads(pathlib.Path(reap.opt("--plan") or sys.exit("apply needs --plan PLAN.json")).read_text())
        failed = 0
        for item in plan["items"]:
            if item["verdict"] not in ACTING:
                continue
            try:
                print(f"- {item['name']}: {act(item, cfg)}")
            except (RuntimeError, OSError) as e:
                failed += 1
                print(f"- 실패 {item['name']}: {e}")
        sys.exit(1 if failed else 0)
    if cmd != "scan":
        sys.exit(__doc__)
    repo_list = [a for i, a in enumerate(argv) if i and argv[i - 1] == "--repo"]
    items, notes = scan(cfg, repo_list)
    data = {"at": time.strftime("%Y-%m-%dT%H:%M%z"), "items": items, "notes": notes}
    if reap.opt("--plan"):
        pathlib.Path(reap.opt("--plan")).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    if "--json" in argv:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        report(items, notes)
        if reap.opt("--plan"):
            print(f"\n계획: {reap.opt('--plan')} — 확인 뒤 `python3 {shlex.quote(__file__)} apply --plan {shlex.quote(reap.opt('--plan'))}`")


if __name__ == "__main__":
    main(sys.argv)
