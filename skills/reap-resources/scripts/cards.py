#!/usr/bin/env python3
"""Take stock of every Orca card (non-main worktree) and close the finished ones.

Usage:
  python3 cards.py scan [--plan PLAN.json] [--json] [--repo PATH]...   read only: one row per card
  python3 cards.py apply --plan PLAN.json                              act on that plan's rows, judged again

Each card gets one verdict: 삭제 (close its listed terminals, ignored files to the trash, `orca worktree rm
--run-hooks`), 터미널만 닫기 (an open PR: the worktree stays, its idle terminals go), 남김, or 사람 결정 (nothing is
done; the reason says what to decide). The rules are in ../references/cards.md and in judge() below.
`apply` first refuses the whole plan, closing nothing, unless this module's scan sealed that exact text and every acting
row names a card Orca lists (not the main checkout). Then it reads the card again and acts only when the verdict and
HEAD are what the plan says, before closing its terminals and again before removing it, holding the card's lock and
passing cardguard.guard() each time. What cannot be read keeps the card: an agent's screen, the worktree, worker or
automation list, process cwds.
The janitor's `card` kind (reap.py) is this module.
Config: ~/.claude/agent-skills.json → "reap": {"idle_minutes": 30, "cards": {"protect_prefixes": ["lead-"],
"done_patterns": [...], "question_patterns": [...]}}.
"""
import fcntl, hashlib, json, os, pathlib, re, shlex, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cardguard, reap, rm_refused  # noqa: E402

REMOVE, CLOSE, KEEP, ASK = "삭제", "터미널만 닫기", "남김", "사람 결정"
ACTING = (REMOVE, CLOSE)
SCREEN_LINES = 40
DONE_PATTERNS = (r"닫아도\s*(된|돼|좋|괜찮)", r"종료해도\s*(된|돼|좋|괜찮)", r"(can|may) (now )?be closed",
                 r"safe to close", r"閉じて(も)?(大丈夫|よい|いい|構いません)")
QUESTION_PATTERNS = cardguard.QUESTION_PATTERNS


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


def under_pr_head(path, head, pr_head, run):
    """HEAD is the PR head or a step on the way to it. `is-ancestor` fails the same way for "no" (exit 1) and for a
    head commit this clone never had (exit 128: the remote branch was deleted at merge), so only the first is a "no";
    for the second GitHub is asked. Unreadable counts as no: the card is kept."""
    if run(["git", "-C", path, "merge-base", "--is-ancestor", head, pr_head]) is not None:
        return True
    if run(["git", "-C", path, "cat-file", "-e", pr_head + "^{commit}"]) is not None:
        return False
    repo = gh_repo(path, run)
    out = repo and run(["gh", "api", f"repos/{repo}/compare/{head}...{pr_head}", "--jq", ".status"])
    return (out or "").strip() in ("ahead", "identical")


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


# "commit", "commit (merge)", "commit (amend)"; not "merge origin/main: Fast-forward" or "rebase (start)", which only
# move HEAD onto the base's history.
OWN_COMMIT = re.compile(r"^(commit( \([^)]*\))?|cherry-pick|revert|am):")
NEW_CARD_S = 24 * 3600


def history(path, run):
    """(has_own_commit, born): from HEAD's reflog, whether this card ever made a commit, and when it began. None when
    the reflog is unreadable or empty. A card with no commit has HEAD on the base branch without anything having
    landed, so HEAD on the base proves nothing for it."""
    # %gd with --date=unix is the time the reflog entry was written (HEAD@{<unix>}); %ct would be the commit's own date.
    out = run(["git", "-C", path, "reflog", "show", "--date=unix", "--format=%gd%x09%gs", "HEAD"])
    rows = [(m.group(1), s) for line in (out or "").splitlines()
            if (m := re.match(r"HEAD@\{(\d+)\}\t(.*)", line)) and (s := m.group(2)) is not None]
    if not rows or len(rows) != len([line for line in (out or "").splitlines() if line.strip()]):
        return None, None
    return any(OWN_COMMIT.match(s) for _, s in rows), min(int(t) for t, _ in rows)


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
    inside, claude = cardguard.claude_in(path, rows, cwd_of)
    busy = cardguard.busy_shells(path, rows, cwd_of)

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
         "terminals": [], "errors": errs, "release": m.get("unreleased", {}).get(p, [])}
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
    if m["refused"] is None:
        return {**f, "protected": "worktree rm 거부 기록을 읽지 못함"}
    if p in m["refused"]:
        return {**f, "handed": cardguard.handed_off(p)}
    if m["patrol"] is None:
        return {**f, "protected": "순찰 판정 파일을 읽지 못함"}
    stale = None
    if p in m["patrol"]:
        if m["patrol"][p] not in reap.PATROL_TERMINAL_READ:
            return {**f, "protected": reap.patrol_hold(m["patrol"][p])}
        stale = m["patrol"][p]  # protects only while a terminal is left to be the session; checked once they are listed
    if (children := cardguard.child_cards(w)):
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
    if (d := m.get("recovering", {}).get(p)) and (status or f["state"] == "OPEN" or not ref):
        # Its terminals are the resume's to use, and the tree, the PR or, with no PR (an investigation), the directory
        # itself is what the resume goes on with. Only a PR that shipped or closed the work ends the wait.
        return {**f, "protected": f"복구 전 워커({d})의 카드: 미커밋 변경, 열린 PR 또는 PR 없는 조사 카드"}
    if ref and f["pr_head"] and head != f["pr_head"]:
        # HEAD behind the PR's head is the PR's work; HEAD past it is new work on a reused branch name.
        f["carried"] = under_pr_head(p, head, f["pr_head"], run)
    else:
        f["carried"] = bool(ref)
    if not ref or not f["carried"]:
        f["landed"], f["landed_via"] = landed(p, head, run)
        if not ref and f["landed"]:
            own, born = history(p, run)
            f["own_commit"], f["age"] = own, None if born is None else m["now"] - born
    f["unpushed"] = "none" if f.get("landed") else unpushed(p, head, f["pr_head"], run)
    if f["state"] == "OPEN" and f["pr_head"] and head != f["pr_head"] and f["carried"]:
        # HEAD is a step on the way to the PR's head: another card standing exactly at that head carries the PR on.
        f["handed_to"] = next((q for q, h in (m.get("heads") or {}).items()
                               if q != p and h == f["pr_head"]), None)
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
                screen = "unreadable"
        f["terminals"].append({"handle": t["handle"], "agent": agent, "silent": silent, "screen": screen})
    if stale and (f["terminals"] or f["claude"]):  # a Claude process is the session too, terminal or not
        return {**f, "protected": reap.patrol_hold(stale)}
    return f


# ---------------------------------------------------------------- the rule

def judge(f, idle_s):
    """(verdict, why, handles to close). Pure: every fact comes from gather()."""
    if f.get("protected"):
        return KEEP, f"보호: {f['protected']}", []
    if f.get("handed"):
        return ASK, f["handed"], []
    if f.get("unreadable"):
        return KEEP, f["unreadable"], []
    agents = [t for t in f["terminals"] if t["agent"]]
    shells = [t for t in f["terminals"] if not t["agent"]]
    if f.get("busy") or any(t["silent"] < cardguard.AGENT_BUSY_S for t in agents):
        return KEEP, "에이전트 턴 진행 중", []
    if any(t["screen"] == "question" for t in agents):
        return ASK, "에이전트가 사람의 답을 기다림(화면)", []
    if (unread := [t for t in agents if t["screen"] == "unreadable"]):
        return KEEP, f"에이전트 화면을 읽지 못함({unread[0]['handle']}): 질문이 없다고 볼 수 없음", []
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
        # Nothing here is lost and the PR's head is held by another card; a person decides, since an open PR keeps a
        # card for good and the pair could be a deliberate second checkout.
        if f.get("handed_to") and not handles and not live and not f.get("dirty") and f.get("unpushed") == "none":
            return ASK, f"PR {f['pr']} 는 카드 {os.path.basename(f['handed_to'])} 가 이어받은 듯함(HEAD 가 PR head 의 조상, " \
                        "미커밋·미push 없음): 이 카드를 닫아도 되는지 사람이 판단", []
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
    # HEAD on the base branch is "landed" only if the card's own commits went in; a card that never committed sits
    # there from the day it was made (and its PR may come hours later).
    if f.get("own_commit") is None or f.get("age") is None:
        return KEEP, "PR 없음, 커밋 이력(reflog)을 읽지 못함", []
    if not f["own_commit"] and f["age"] < NEW_CARD_S:
        return KEEP, f"PR 없음, 새 카드, 아직 커밋 없음({int(f['age'] // 3600)}시간 전 생성)", []
    return REMOVE, f"PR 없음, HEAD 가 기본 브랜치에 있음({f['landed_via']}){tail}", handles


# ---------------------------------------------------------------- scan / apply

def read_machine(cfg, now=None):
    now = now or time.time()
    try:
        workers = cardguard.worker_pages()  # every Run's: a list bound to this terminal's Run would miss live turns
    except (SystemExit, Exception):
        workers = None
    live, _, _ = reap.worker_worktrees(workers or [])
    unreleased = reap.unreleased_settled(workers or [])
    top = sh(["git", "rev-parse", "--show-toplevel"])
    projects = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects"
    # The patrol read the guard before a close makes (activity included): a card patrol keeps is a keep here, not a
    # delete that apply then refuses.
    return {"now": now, "st": settings(cfg), "rows": reap.processes(), "cwd_of": reap.cwds(), "live": live,
            "unreleased": unreleased, "recovering": reap.recovering_worktrees(workers or []),
            "patrol": reap.patrol_states(now, activity=True), "refused": rm_refused.read(reap.STATE),
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


BLIND = "orca worktree·워커·automation 목록이나 프로세스 cwd 를 읽지 못함"


def blind(m, rows):
    return rows is None or m["cwd_of"] is None or m["workers_unread"] or m["runners"] is None


def card_heads(rows):
    """{card path: HEAD} for each listed card; one whose HEAD cannot be read is left out (no evidence, no hand-over)."""
    out = {}
    for w in rows:
        if not w.get("isMainWorktree") and (h := (sh(["git", "-C", w["path"], "rev-parse", "HEAD"]) or "").strip()):
            out[reap.real(w["path"])] = h
    return out


def scan(cfg, repo_list=(), notes=None):
    notes = [] if notes is None else notes
    m = read_machine(cfg)
    rows = cards_listing(repo_list)
    if blind(m, rows):
        notes.append(BLIND + ": 카드는 판정하지 않는다")
        return [], notes
    m["heads"] = card_heads(rows)
    items = []
    for w in rows:
        if w.get("isMainWorktree"):
            continue
        f = gather(w, m)
        verdict, why, close = judge(f, m["st"]["idle_s"])
        items.append({"path": f["path"], "name": f["name"], "repo": f["repo"], "branch": f["branch"], "pr": f["pr"],
                      "state": f["state"], "verdict": verdict, "why": why, "close": close, "head": f["head"],
                      "ino": reap.dir_ino(f["path"]), "regenerated": f.get("regenerated") or [],
                      # None: gather() stopped before git status (a protected card, an unreadable one), so not known.
                      # False also covers a tree whose only untracked files are `regenerated` copies.
                      "dirty": f.get("dirty"),
                      "release": f.get("release") or []})
    return items, notes


def release_lines(items):
    """One line per card whose settled worker dispatch was never released: the Run that owns it and the command. This
    never releases anything; the Run's lead or the orchestrator does."""
    return [f"- {i['name']}: dispatch {r['dispatch']} ({r['outcome']}, Run {r['run']}) 의 터미널이 release 되지 않음 → "
            f"소유 Run 의 lead·오케스트레이터가 `orca orchestration worker-release --dispatch {r['dispatch']}`"
            for i in items for r in i.get("release") or ()]


def report(items, notes):
    order = {REMOVE: 0, CLOSE: 1, ASK: 2, KEEP: 3}
    print(f"# Orca 카드 점검 ({len(items)}개)\n")
    print("| 카드 | repo/branch | PR | 판정 | 이유 |\n|---|---|---|---|---|")
    for i in sorted(items, key=lambda i: (order[i["verdict"]], i["repo"], i["name"])):
        pr = f"{i['pr']} {i['state'].lower()}" if i["pr"] else "-"
        print(f"| {i['name']} | {i['repo']}/{i['branch'] or '-'} | {pr} | {i['verdict']} | {i['why']} |")
    counts = {v: sum(i["verdict"] == v for i in items) for v in order}
    print("\n" + " · ".join(f"{k} {n}" for k, n in counts.items()))
    if (lines := release_lines(items)):
        print("\n## release 필요 (끝난 워커의 dispatch)\n")
        print("\n".join(lines))
    for n in notes:
        print(f"- 메모: {n}")


def reread(path, cfg):
    """(verdict, why, handles, facts) for the card at path, read fresh; None when Orca no longer lists it. What scan
    would not judge on is a KEEP here."""
    m = read_machine(cfg)
    rows = cards_listing()
    if blind(m, rows):
        return KEEP, BLIND, [], {"head": None}
    row = next((w for w in rows if reap.real(w["path"]) == path), None)
    if row is None:
        return None
    m["heads"] = card_heads(rows)  # the verdict is read again from fresh facts, this one too
    f = gather(row, m)
    return (*judge(f, m["st"]["idle_s"]), f)


def act(item, cfg, wait=5.0, done=None):
    """Judge the card again and do what its plan row says, holding the card's lock. Returns what happened; raises
    RuntimeError on failure. done, when given, gets the handles it really closed ("closed")."""
    done = {} if done is None else done
    done["closed"] = []
    with cardguard.card_lock(item["path"]) as mine:
        if not mine:
            return "건너뜀: 다른 정리가 이 카드를 처리 중"
        return act_locked(item, cfg, wait, done)


def act_locked(item, cfg, wait, done=None):
    done = {"closed": []} if done is None else done
    got = reread(item["path"], cfg)
    if got is None:
        return "건너뜀: orca worktree list 에서 다시 찾지 못함"
    verdict, why, close, f = got
    if verdict != item["verdict"] or f["head"] != item["head"]:
        # Only something that really changed since the plan lands here; say what, per card.
        changed = ([f"판정 {item['verdict']}→{verdict}"] if verdict != item["verdict"] else []) + \
                  ([f"HEAD {(item['head'] or '-')[:8]}→{(f['head'] or '-')[:8]}"] if f["head"] != item["head"] else [])
        return f"건너뜀: plan 뒤에 바뀜({', '.join(changed)}): {why}"
    ino = reap.dir_ino(item["path"])
    if item.get("ino") is not None and ino != item["ino"]:
        return "건너뜀: 판정 뒤 카드 폴더가 다시 만들어짐"
    done["identity"] = {"ino": ino} if ino is not None else {}  # the folder really acted on, for `sweep diff`
    close = [h for h in close if h in item["close"]]
    ok, why = cardguard.guard(item["path"], item["head"], item["close"], ino=ino)
    if not ok:
        return f"건너뜀: {why}"
    for h in close:
        reap.run(["orca", "terminal", "close", "--terminal", h, "--json"])
        done.setdefault("closed", []).append(h)
    if verdict == CLOSE:
        return f"터미널 {len(close)}개 닫음, worktree 는 남김"
    # A closed terminal's shell or session takes a moment to exit; until it does it reads as a process in the card.
    deadline = time.time() + wait
    while (got := reread(item["path"], cfg)) and got[0] != REMOVE and time.time() < deadline:
        time.sleep(1)
    if not got or got[0] != REMOVE:
        return f"터미널 {len(close)}개 닫음, 삭제는 건너뜀: {got[1] if got else '다시 찾지 못함'}"
    if got[3]["head"] != item["head"]:
        return f"터미널 {len(close)}개 닫음, 삭제는 건너뜀: 판정 뒤 HEAD 가 바뀜"
    ok, why = cardguard.guard(item["path"], item["head"], item["close"], final=True, ino=ino)
    if not ok:
        return f"터미널 {len(close)}개 닫음, 삭제는 건너뜀: {why}"
    out = reap.act({"kind": "orca-worktree", "key": f"orca-worktree:{item['path']}", "name": item["path"],
                    "regenerated": got[3].get("regenerated")}, [])
    return (f"터미널 {len(close)}개 닫음, " if close else "") + out


SEALS, SEALS_KEPT = "card-plan-seals.json", 20


def seal_plan(text):
    """scan calls this with the exact text it wrote: apply accepts only a plan whose digest is here, so a plan written
    or edited by hand (or by an agent) after the scan is refused. The file sits beside the other state files; whoever
    can write it can forge a seal, which this does not stop."""
    path = reap.STATE / SEALS
    reap.STATE.mkdir(parents=True, exist_ok=True)
    # Two scans must not each read the old list and write back only their own seal.
    with open(path.with_name(SEALS + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        seals = reap.read_obj(path) or {}
        seals[hashlib.sha256(text.encode()).hexdigest()] = time.time()
        newest = sorted(seals.items(), key=lambda kv: kv[1], reverse=True)[:SEALS_KEPT]
        reap.write_atomic(path, json.dumps(dict(newest)))


def refuse_plan(text, rows):
    """Why apply must not act on this plan, or None. Checked for every row before the first card is touched, so a
    plan that fails closes nothing. rows: the cards Orca lists now (cards_listing), None when unreadable."""
    if hashlib.sha256(text.encode()).hexdigest() not in (reap.read_obj(reap.STATE / SEALS) or {}):
        return "이 계획은 cards.py scan 이 만든 것이 아니거나 만든 뒤 고쳐졌다(봉인 없음): scan --plan 으로 다시 만든다"
    try:
        items = json.loads(text)["items"]
        acting = [i for i in items if i["verdict"] in ACTING]
        for i in acting:
            if not (isinstance(i["path"], str) and isinstance(i["name"], str) and isinstance(i["head"], str)
                    and isinstance(i["close"], list) and all(isinstance(h, str) for h in i["close"])):
                return f"계획 행 모양이 잘못됨: {i.get('name')}"
    except (KeyError, TypeError, ValueError):
        return "계획 모양이 잘못됨"
    if acting:
        if rows is None:
            return "orca worktree 목록을 읽지 못해 대상의 소유를 확인할 수 없음"
        cards = {reap.real(w["path"]) for w in rows if not w.get("isMainWorktree")}
        if outside := [i["name"] for i in acting if reap.real(i["path"]) not in cards]:
            return f"Orca 가 나열하는 카드가 아님(main 체크아웃이거나 없음): {', '.join(outside)}"
    return None


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "scan"
    cfg = reap.config()
    if cmd == "apply":
        text = pathlib.Path(reap.opt("--plan") or sys.exit("apply needs --plan PLAN.json")).read_text()
        if why := refuse_plan(text, cards_listing()):
            sys.exit(f"아무것도 닫지 않았다: {why}")
        plan = json.loads(text)
        import guard
        try:
            guard.ownership()
        except guard.Unreadable as e:
            sys.exit(f"아무것도 하지 않았다: {e}")
        failed = 0
        for item in plan["items"]:
            if item["verdict"] not in ACTING:
                continue
            with guard.hold(guard.targets({"kind": "card", "path": item["path"]})) as got:
                if why := guard.keep({"kind": "card", "verdict": item["verdict"]}, None) if got else guard.LOCK_BUSY:
                    print(f"- 건너뜀 {item['name']}: {why}")
                    if guard.check_error(why):  # left, not decided: the others run, then the round ends non-zero
                        failed += 1
                    continue
                done = {}
                logged = {"key": f"card:{item['path']}", "kind": "card", "head": item.get("head"), "ino": item.get("ino")}
                rule = guard.rule({"kind": "card", "verdict": item["verdict"]})
                try:
                    got = act(item, cfg, done=done)
                except BaseException as e:  # logged whatever it was: terminals closed before it are still closed
                    reap.log_action({**logged, **(done.get("identity") or {})}, e, False, rule, done.get("closed"))
                    if not isinstance(e, (RuntimeError, OSError)):
                        raise
                    failed += 1
                    print(f"- 실패 {item['name']}: {e}")
                else:
                    reap.log_action({**logged, **(done.get("identity") or {})}, got, True, rule, done.get("closed"))
                    print(f"- {item['name']}: {got}")
        sys.exit(1 if failed else 0)
    if cmd != "scan":
        sys.exit(__doc__)
    repo_list = [a for i, a in enumerate(argv) if i and argv[i - 1] == "--repo"]
    items, notes = scan(cfg, repo_list)
    data = {"at": time.strftime("%Y-%m-%dT%H:%M%z"), "items": items, "notes": notes}
    if reap.opt("--plan"):
        text = json.dumps(data, ensure_ascii=False, indent=1)
        pathlib.Path(reap.opt("--plan")).write_text(text)
        seal_plan(text)
    if "--json" in argv:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        report(items, notes)
        if reap.opt("--plan"):
            print(f"\n계획: {reap.opt('--plan')} — 확인 뒤 `python3 {shlex.quote(__file__)} apply --plan {shlex.quote(reap.opt('--plan'))}`")


if __name__ == "__main__":
    main(sys.argv)
